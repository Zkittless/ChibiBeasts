"""Timing, concurrent input and failure regressions; no Discord login or live DB."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import discord
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage, combat
from utils.catalog import clear_catalog_cache, load_catalog
from utils.transfers import create_listing, purchase_market, complete_trade


class CatalogAndReads(DatabaseCase):
    async def test_starter_info_shows_bond_requirements_without_claiming_it_is_ready(self):
        from cogs.profile import Profile
        await self.beast(starter=True)
        await self.execute('UPDATE player_beasts SET level=15,bond=0,happiness=100 WHERE user_id=42')
        fake = interaction()
        await Profile.beastinfo.callback(Profile(None), fake, 1)
        embed = fake.followup.send.call_args.kwargs['embed']
        evolution = next(field for field in embed.fields if 'Prismara' in field.name)
        self.assertIn('No item needed', evolution.value)
        self.assertIn('Bond: 0/40', evolution.value)
        self.assertNotIn('ready to evolve', evolution.value)

    async def test_repeated_startup_migrations_preserve_existing_players_and_companions(self):
        await self.beast(starter=True)
        player = await storage.get_player(42)
        beasts = await storage.get_player_beasts(42)
        await storage.init_db()
        await storage.init_db()
        self.assertEqual(await storage.get_player(42), player)
        self.assertEqual(await storage.get_player_beasts(42), beasts)

    async def test_missing_collection_number_cannot_target_an_unrelated_internal_id(self):
        row_id = await self.beast('slime')
        await self.execute('UPDATE player_beasts SET player_number=10 WHERE id=?', (row_id,))
        self.assertIsNone(await storage.get_beast_by_player_number(42, row_id))
        self.assertEqual((await storage.get_beast_by_player_number(42, 10))['id'], row_id)

    async def test_wild_catch_keeps_encounter_level_and_stat_growth(self):
        beast = {**storage.get_beast_data('slime'), 'level': 12, 'caught_from': 'wild_battle', 'disposition': 'steadfast'}
        row_id = await storage.add_beast_to_player(42, beast)
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        from utils.dispositions import apply_disposition
        expected = apply_disposition(beast['base_stats'], beast['disposition'])
        growth = storage.calc_stat_growth({'rarity': beast['rarity']}, 11)
        self.assertEqual(row['level'], 12)
        self.assertEqual(row['max_hp'], expected['hp'] + growth['hp'])
        self.assertEqual(row['attack'], expected['attack'] + growth['attack'])

    async def test_invalid_raid_assignment_preserves_the_existing_slot(self):
        row_id = await self.beast('slime')
        await storage.set_raid_slot(42, row_id, 1)
        for target, slot in ((999999, 1), (row_id, 0), (row_id, 4)):
            with self.assertRaises(ValueError):
                await storage.set_raid_slot(42, target, slot)
        self.assertEqual((await self.rows('SELECT raid_slot FROM player_beasts WHERE id=?', (row_id,)))[0]['raid_slot'], 1)

    async def test_cached_definitions_and_encounters_are_private(self):
        from utils.habitats import encounter_pool
        beast = storage.get_beast_data('prismite')
        beast['base_stats']['attack'] = -999
        beast['moves'].clear()
        self.assertGreater(storage.get_beast_data('prismite')['base_stats']['attack'], 0)
        self.assertEqual(len(storage.get_beast_data('prismite')['moves']), 3)
        items = load_catalog('items.json')
        items['items'].clear()
        self.assertTrue(storage.load_items())
        pool = encounter_pool('rare', 'loom')
        pool[0]['base_stats']['hp'] = -999
        self.assertTrue(all(b['base_stats']['hp'] > 0 for b in encounter_pool('rare', 'loom')))
        from utils.habitats import _pool_ids
        _pool_ids.cache_clear()
        encounter_pool('rare', 'loom')
        before = _pool_ids.cache_info().misses
        clear_catalog_cache()
        encounter_pool('rare', 'loom')
        self.assertGreater(_pool_ids.cache_info().misses, before)

    async def test_healthy_collection_reads_do_not_write_and_use_one_connection(self):
        await self.beast(starter=True)
        statements = []
        original = storage.connect
        calls = []

        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            calls.append(connection)
            return connection

        # Observe a real SQLite read, not an implementation-shaped fake result.
        import aiosqlite
        execute = aiosqlite.Connection.execute

        def traced(db, sql, *args):
            statements.append(sql.upper())
            return execute(db, sql, *args)

        with patch.object(storage, 'connect', connect), patch.object(aiosqlite.Connection, 'execute', traced):
            self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(sql.startswith(('UPDATE', 'BEGIN', 'INSERT')) for sql in statements))
        plan = await self.rows('EXPLAIN QUERY PLAN SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (42, 1))
        self.assertTrue(any('ix_beasts_owner_number' in row['detail'] for row in plan))
        self.assertEqual((await self.rows('PRAGMA journal_mode'))[0]['journal_mode'], 'wal')

    async def test_expired_knockout_recovers_once_on_read(self):
        row_id = await self.beast(starter=True)
        await self.execute("UPDATE player_beasts SET hp=0,knocked_out_until='2000-01-01' WHERE id=?", (row_id,))
        row = await storage.get_beast_by_player_number(42, 1)
        self.assertEqual(row['hp'], row['max_hp'])
        self.assertIsNone(row['knocked_out_until'])

    async def test_daily_champion_and_exp_are_awarded_once_under_concurrent_events(self):
        from utils.progress import _today_str, _track_daily_event, QUEST_POOL, get_daily_quests
        await storage.update_player(42, level=10)
        # Choose four real quests with independent events, regardless of display names.
        quests = [next(q for q in QUEST_POOL if q['event'] == event) for event in ('trade', 'equip', 'craft', 'tend')]
        ids = [q['id'] for q in quests]
        date = _today_str()
        await self.execute('INSERT INTO daily_sets VALUES(?,?,?)', (42, date, json.dumps(ids)))
        for q in quests[:3]:
            await _track_daily_event(42, q['event'], q['target'])
        before = await storage.get_player(42)
        final = quests[-1]
        results = await asyncio.gather(*(_track_daily_event(42, final['event'], final['target']) for _ in range(5)))
        after = await storage.get_player(42)
        self.assertEqual(sum(q['id'] == 'all_quests_bonus' for result in results for q in result), 1)
        self.assertEqual(after['gold'] - before['gold'], final['reward_gold'] + 500)
        self.assertEqual(after['celestial_shards'] - before['celestial_shards'], final.get('reward_shards', 0) + 5)
        self.assertTrue(all(q['completed'] for q in await get_daily_quests(42)))


class Transfers(DatabaseCase):
    async def test_release_returns_gear_and_cannot_replay_or_follow_a_trade(self):
        from utils.transfers import release_companion
        row_id = await self.beast('slime')
        await self.attach_gear(row_id, 42)
        await release_companion(42, row_id, expected_species='slime')
        after = await storage.get_player(42)
        self.assertIsNone((await self.rows('SELECT beast_row_id FROM player_equipment'))[0]['beast_row_id'])
        self.assertEqual((await storage.get_inventory(42))[0]['item_id'], 'rune_of_vitality')
        with self.assertRaises(ValueError):
            await release_companion(42, row_id, expected_species='slime')
        self.assertEqual((await storage.get_player(42))['gold'], after['gold'])
        next_id = await self.beast('slime')
        await complete_trade(42, 77, next_id, offer_key='gift-before-release')
        with self.assertRaises(ValueError):
            await release_companion(42, next_id, expected_species='slime')
        self.assertEqual((await self.rows('SELECT user_id FROM player_beasts WHERE id=?', (next_id,)))[0]['user_id'], 77)

    async def test_listed_or_busy_companion_cannot_become_active_or_join_party(self):
        from utils.duels import reserve_fighters
        row_id = await self.beast('slime')
        await create_listing(42, 1, 200)
        with self.assertRaises(ValueError):
            await storage.set_active_companion(42, 1)
        with self.assertRaises(ValueError):
            await storage.set_raid_slot(42, row_id, 1)
        await self.execute('DELETE FROM beast_market')
        with reserve_fighters([42]):
            with self.assertRaises(ValueError):
                await storage.set_active_companion(42, 1)
        self.assertEqual((await self.rows('SELECT is_active FROM player_beasts WHERE id=?', (row_id,)))[0]['is_active'], 0)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for uid in (77, 88):
            await storage.get_or_create_player(uid, f'Trainer {uid}')
            await storage.update_player(uid, gold=5000)
        await storage.update_player(42, gold=5000)

    async def attach_gear(self, row_id, uid):
        await self.execute("INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(?,'bark_armor',?)", (uid, row_id))
        await self.execute("UPDATE player_beasts SET rune_id='rune_of_vitality' WHERE id=?", (row_id,))

    async def test_two_market_buyers_can_only_pay_and_receive_once(self):
        row_id = await self.beast('slime')
        await self.attach_gear(row_id, 42)
        listing = await create_listing(42, 1, 200)
        self.assertFalse(await self.rows("SELECT 1 FROM achievements WHERE achievement_id='first_market_sale'"))
        results = await asyncio.gather(*(purchase_market(uid, listing['listing_id'], 200) for uid in (77, 88)), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        owner = (await self.rows('SELECT user_id,rune_id FROM player_beasts WHERE id=?', (row_id,)))[0]
        winner = owner['user_id']
        loser = 88 if winner == 77 else 77
        from utils.progress import ACHIEVEMENTS
        self.assertEqual((await storage.get_player(winner))['gold'], 5000 - 200 + ACHIEVEMENTS['first_market_buy']['reward'].get('gold', 0))
        self.assertEqual((await storage.get_player(loser))['gold'], 5000)
        self.assertEqual((await self.rows('SELECT user_id FROM player_equipment WHERE beast_row_id=?', (row_id,)))[0]['user_id'], winner)
        self.assertEqual(owner['rune_id'], 'rune_of_vitality')
        self.assertFalse(await self.rows('SELECT * FROM beast_market'))
        self.assertTrue(await self.rows("SELECT 1 FROM achievements WHERE user_id=42 AND achievement_id='first_market_sale'"))

    async def test_stale_listing_price_expiry_and_relist_cannot_charge(self):
        await self.beast('slime')
        listing = await create_listing(42, 1, 200)
        await self.execute('UPDATE beast_market SET ask_price=300 WHERE id=?', (listing['listing_id'],))
        with self.assertRaisesRegex(ValueError, 'price has changed'):
            await purchase_market(77, listing['listing_id'], 200)
        await self.execute("UPDATE beast_market SET expires_at='2000-01-01' WHERE id=?", (listing['listing_id'],))
        with self.assertRaises(ValueError):
            await purchase_market(77, listing['listing_id'], 300)
        await self.execute('DELETE FROM beast_market')
        replacement = await create_listing(42, 1, 500)
        self.assertNotEqual(replacement['listing_id'], listing['listing_id'])
        with self.assertRaises(ValueError):
            await purchase_market(77, listing['listing_id'], 200)
        self.assertEqual((await storage.get_player(77))['gold'], 5000)
        self.assertEqual((await storage.get_player(42))['gold'], 5000)

    async def test_swap_moves_gear_and_preserves_other_collection_numbers(self):
        offered = await self.beast('slime')
        kept = await self.beast('fairy')
        requested = await self.beast('slime', uid=77)
        await self.attach_gear(offered, 42)
        await self.attach_gear(requested, 77)
        await self.execute('UPDATE player_beasts SET player_number=9 WHERE id=?', (kept,))
        result = await complete_trade(42, 77, offered, requested, 100, offer_key='swap')
        self.assertEqual((await self.rows('SELECT player_number FROM player_beasts WHERE id=?', (kept,)))[0]['player_number'], 9)
        self.assertEqual(result['requested_number'], 10)
        self.assertEqual((await self.rows('SELECT user_id FROM player_equipment WHERE beast_row_id=?', (offered,)))[0]['user_id'], 77)
        self.assertEqual((await self.rows('SELECT user_id FROM player_equipment WHERE beast_row_id=?', (requested,)))[0]['user_id'], 42)
        self.assertEqual((await storage.get_player(42))['gold'], 4900)
        self.assertEqual((await storage.get_player(77))['gold'], 5100)
        self.assertEqual(len(await self.rows("SELECT * FROM trades WHERE status='completed'")), 1)

    async def test_offer_cannot_replay_even_after_companion_returns(self):
        offered = await self.beast('slime')
        await complete_trade(42, 77, offered, gold=50, offer_key='original')
        await complete_trade(77, 42, offered, offer_key='return')
        before = await storage.get_player(42)
        with self.assertRaisesRegex(ValueError, 'already been accepted'):
            await complete_trade(42, 77, offered, gold=50, offer_key='original')
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])

    async def test_changed_balances_ownership_and_party_assignments_abort_whole_trade(self):
        offered = await self.beast('slime')
        requested = await self.beast('fairy', uid=77)
        await storage.update_player(42, gold=10)
        with self.assertRaises(ValueError):
            await complete_trade(42, 77, offered, requested, 100, offer_key='too-poor')
        await storage.update_player(42, gold=5000)
        for column in ('is_active', 'raid_slot'):
            await self.execute(f'UPDATE player_beasts SET {column}=1 WHERE id=?', (requested,))
            with self.assertRaises(ValueError):
                await complete_trade(42, 77, offered, requested, 100, offer_key=column)
            await self.execute(f'UPDATE player_beasts SET {column}=? WHERE id=?', (0 if column == 'is_active' else None, requested))
        await create_listing(77, 1, 200)
        with self.assertRaises(ValueError):
            await complete_trade(42, 77, offered, requested, 100, offer_key='listed')
        self.assertEqual((await storage.get_player(77))['gold'], 5000)
        self.assertEqual((await self.rows('SELECT user_id FROM player_beasts WHERE id=?', (offered,)))[0]['user_id'], 42)
        self.assertFalse(await self.rows('SELECT * FROM trades'))

    async def test_soulbound_on_either_side_and_negative_gold_are_rejected(self):
        offered = await self.beast('slime')
        requested = await self.beast('fairy', uid=77)
        await self.execute("UPDATE player_beasts SET rarity='altered_divine',is_altered_divine=0 WHERE id=?", (requested,))
        with self.assertRaisesRegex(ValueError, 'soulbound'):
            await complete_trade(42, 77, offered, requested, offer_key='bound')
        with self.assertRaisesRegex(ValueError, 'soulbound'):
            await create_listing(77, 1, 200)
        with self.assertRaises(ValueError):
            await complete_trade(42, 77, offered, gold=-1, offer_key='negative')

    async def test_transfer_cannot_take_a_companion_during_battle(self):
        from utils.duels import reserve_fighters
        offered = await self.beast('slime')
        with reserve_fighters([77]):
            with self.assertRaises(ValueError):
                await complete_trade(42, 77, offered, offer_key='busy')
        self.assertFalse(await self.rows('SELECT * FROM trades'))

    async def test_trade_command_resolves_collection_numbers_and_labels_gifts(self):
        from cogs.social import Trading
        await self.beast('slime', uid=77)  # Make the internal ID differ from collection #1.
        offered = await self.beast('slime')
        fake = interaction()
        fake.id = 123456
        fake.user.mention = '<@42>'
        fake.user.display_avatar = SimpleNamespace(url='https://example.com/avatar.png')
        member = SimpleNamespace(id=77, display_name='Other trainer', mention='<@77>', display_avatar=fake.user.display_avatar)
        await Trading.trade.callback(Trading(None), fake, member, 1)
        kwargs = fake.followup.send.call_args.kwargs
        self.assertIn('this is a gift', kwargs['embed'].description)
        view = kwargs['view']
        click = interaction(77)
        click.response.edit_message = AsyncMock()
        with patch('cogs.social.notify_unlocks', AsyncMock()):
            await view.children[0].callback(click)
        self.assertEqual((await self.rows('SELECT user_id FROM player_beasts WHERE id=?', (offered,)))[0]['user_id'], 77)


class BattleTiming(DatabaseCase):
    async def test_busy_challenge_does_not_spend_its_cooldown(self):
        from cogs.battle import Battle
        from utils.duels import reserve_fighters
        with reserve_fighters([42]):
            with self.assertRaises(ValueError):
                await Battle.challenge.callback(Battle(None), interaction(), 'woods')
        self.assertEqual((await storage.get_player(42))['challenge_last_at'], 0)

    async def test_preparation_refreshes_stats_and_rejects_moved_companions(self):
        from utils.duels import prepare_state
        await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        await self.execute('UPDATE player_beasts SET attack=attack+100 WHERE id=?', (row['id'],))
        state = await prepare_state(42, row, storage.get_beast_data('prismite'), [])
        self.assertGreater(state['attack'], row['attack'] + 50)
        await self.execute('UPDATE player_beasts SET user_id=77 WHERE id=?', (row['id'],))
        with self.assertRaisesRegex(ValueError, 'changed'):
            await prepare_state(42, row, storage.get_beast_data('prismite'), [])

    async def test_overlapping_pve_is_rejected_before_consuming_preparation(self):
        from cogs.battle import run_pve_battle
        from utils.duels import active_fighters
        entered, release = asyncio.Event(), asyncio.Event()

        async def preparing(*args, **kwargs):
            entered.set()
            await release.wait()
            raise ValueError('Synthetic preparation failure')

        with patch('utils.duels.prepare_state', side_effect=preparing) as prepare:
            first = asyncio.create_task(run_pve_battle(interaction(), {}, {}, [], {}, 'wild', 'Test', AsyncMock(), AsyncMock()))
            await entered.wait()
            with self.assertRaisesRegex(ValueError, 'already'):
                await run_pve_battle(interaction(), {}, {}, [], {}, 'wild', 'Test', AsyncMock(), AsyncMock())
            self.assertEqual(prepare.await_count, 1)
            release.set()
            with self.assertRaises(ValueError):
                await first
        self.assertNotIn(42, active_fighters)

    async def test_failed_duel_charge_save_still_releases_trainers(self):
        from utils.duels import duel, MoveView, active_fighters
        from cogs.battle import build_pve_beast_state
        row_id = await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        state = combat.build_state(storage.get_beast_data('prismite'), row)
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        enemy['hp'] = 1
        class AutomaticView(MoveView):
            async def wait(self):
                self.chosen_move = (state['moves'][0], False)
                self.stop()
        fake = interaction()
        fake.channel.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        with patch.object(storage, 'connect', side_effect=OSError('Synthetic database failure')):
            with self.assertRaises(OSError):
                await duel(fake, [state, enemy], [42, None], 'Failure', view_class=AutomaticView)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_cancelled_duel_releases_live_state_and_reservation(self):
        from utils.duels import duel, MoveView, active_fighters
        from cogs.battle import build_pve_beast_state
        await self.beast(starter=True)
        state = combat.build_state(storage.get_beast_data('prismite'), (await storage.get_player_beasts(42))[0])
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        entered = asyncio.Event()

        class WaitingView(MoveView):
            async def wait(self):
                entered.set()
                await asyncio.Event().wait()

        fake = interaction()
        fake.channel.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        task = asyncio.create_task(duel(fake, [state, enemy], [42, None], 'Cancelled', view_class=WaitingView))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_unrelated_parties_do_not_share_a_combat_lock(self):
        entered = asyncio.Event()
        async with combat.lock_states([42]):
            async def other_battle():
                async with combat.lock_states([77]):
                    entered.set()
            await asyncio.wait_for(other_battle(), timeout=1)
        self.assertTrue(entered.is_set())

    async def test_reserved_preparation_does_not_consume_an_item(self):
        from utils.duels import reserve_fighters
        from utils.items import use_item
        item = next(key for key, item in storage.load_items().items() if item['effect'].get('status_ward'))
        await storage.add_item(42, item, 1)
        with reserve_fighters([42]):
            with self.assertRaisesRegex(ValueError, 'preparing'):
                await use_item(42, item)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)


class RaidTiming(DatabaseCase):
    async def test_items_follow_a_swapped_companion_and_party_revives_reach_the_bench(self):
        from utils.items import use_item
        raid = await self.make_raid()
        row_id = await self.beast('slime')
        await storage.set_raid_slot(42, raid.state(42)['id'], 1)
        await storage.set_raid_slot(42, row_id, 2)
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        bench = combat.build_state(storage.get_beast_data('slime'), row)
        raid.party[42].append(bench)
        raid.slots[42] = 1
        combat.active_states[42] = bench
        combat.active_parties[42] = raid.party[42]
        bench['hp'] = 1
        healing = next(key for key, item in storage.load_items().items() if item['effect'].get('heal_percent') and not item['effect'].get('revive'))
        await storage.add_item(42, healing)
        await use_item(42, healing)
        self.assertGreater(bench['hp'], 1)
        # A party-wide revive must affect a fainted benched state as well as SQLite.
        first = raid.party[42][0]
        first.update(hp=0, ko_recorded=True)
        await storage.knockout_beast(first['id'], 'common')
        revive = next(key for key, item in storage.load_items().items() if item['effect'].get('revive_party'))
        await storage.add_item(42, revive)
        await use_item(42, revive)
        self.assertEqual(first['hp'], first['max_hp'])
        self.assertFalse(first['ko_recorded'])
        self.assertTrue(all(state['hp'] == state['max_hp'] for state in raid.party[42]))
        self.assertFalse(await self.rows('SELECT 1 FROM player_beasts WHERE user_id=42 AND knocked_out_until IS NOT NULL'))

    async def test_new_pattern_gives_players_time_before_the_next_boss_tick(self):
        raid = await self.make_raid()
        elapsed = [100.0]
        raid.clock = lambda: elapsed[0]
        raid.enemy['hp'] = int(raid.enemy['max_hp'] * .7)
        await raid.action(42, 'guard')
        self.assertTrue(raid.pending)
        await raid.boss_turn()
        self.assertTrue(raid.pending)
        elapsed[0] += 8
        await raid.action(42, 'weave')
        await raid.boss_turn()
        self.assertIsNone(raid.pending)

    async def test_each_trainer_can_weave_once_per_pattern(self):
        raid = await self.make_raid(users=(42, 77))
        elapsed = [100.0]
        raid.clock = lambda: elapsed[0]
        raid.pending = True
        await raid.action(42, 'weave')
        support = raid.support[42]
        elapsed[0] += 9
        with self.assertRaisesRegex(ValueError, 'already helped'):
            await raid.action(42, 'weave')
        await raid.action(77, 'weave')
        self.assertEqual(raid.weaves, 2)
        self.assertEqual(raid.support[42], support)

    async def make_raid(self, *, paid=False, users=(42,)):
        from utils.raids import RaidSession
        from cogs.guilds import RAID_BOSSES
        channel = SimpleNamespace(id=123, guild=SimpleNamespace(id=7), send=AsyncMock())
        raid = RaidSession(channel, RAID_BOSSES['corrupted'][0], set(users), None)
        raid.id = 123
        raid.party = {}
        for uid in users:
            await storage.get_or_create_player(uid, f'Trainer {uid}')
            await self.beast(starter=True, uid=uid)
            row = (await storage.get_player_beasts(uid))[0]
            raid.party[uid] = [combat.build_state(storage.get_beast_data('prismite'), row)]
            raid.totems[uid] = False
        raid.enemy = combat.build_state(storage.get_beast_data(raid.boss['id']), {'hp': 10000, 'max_hp': 10000, 'attack': 20, 'defense': 20, 'speed': 1, 'mana': 100, 'max_mana': 100})
        raid.enemy['divine_passive'] = {}
        raid.view = SimpleNamespace(stop=lambda: None)
        await self.execute("INSERT INTO raids(id,boss_id,status) VALUES(123,?,'active')", (raid.boss['id'],))
        if paid:
            await self.execute("INSERT INTO raid_payments(raid_id,user_id,item_id) VALUES(123,42,'epoch_shard')")
        return raid

    async def test_fast_inputs_coalesce_to_one_edit_with_latest_state(self):
        raid = await self.make_raid()
        raid.refresh_interval = 0
        raid.message = SimpleNamespace(edit=AsyncMock())
        for number in range(20):
            raid.last_event = f'Action {number}'
            await raid.refresh()
        await raid._refresh_task
        self.assertEqual(raid.message.edit.await_count, 1)
        self.assertIn('Action 19', raid.message.edit.call_args.kwargs['embed'].description)

    async def test_deleted_result_message_does_not_prevent_any_players_reward(self):
        raid = await self.make_raid(users=(42, 77))
        raid.damage = {42: 50, 77: 25}
        error = discord.NotFound(SimpleNamespace(status=404, reason='Missing'), 'Synthetic deleted message')
        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=error))
        with self.assertLogs('utils.raids', level='INFO'):
            await raid.finish(True)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 2)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
        self.assertEqual((await self.rows('SELECT completed FROM raid_settlements'))[0]['completed'], 1)

    async def test_interrupted_payout_resumes_without_duplicate_currency_exp_or_catches(self):
        from utils.raids import recover_raids
        raid = await self.make_raid(paid=True, users=(42, 77))
        raid.support = {42: 10, 77: 10}
        for uid in (42, 77):
            await self.execute('INSERT INTO raid_catch_progress VALUES(?,?,11)', (uid, raid.boss['id']))
        reward = raid.reward

        async def interrupted(uid):
            if uid == 77:
                raise OSError('Synthetic shutdown between contributors')
            return await reward(uid)

        with patch.object(raid, 'reward', side_effect=interrupted):
            with self.assertRaises(OSError):
                await raid.finish(True)
        before = await storage.get_player(42)
        before_beasts = await storage.get_player_beasts(42)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'settling')
        await recover_raids()
        after = await storage.get_player(42)
        self.assertEqual((after['gold'], after['exp'], after['celestial_shards']), (before['gold'], before['exp'], before['celestial_shards']))
        self.assertEqual(await storage.get_player_beasts(42), before_beasts)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 2)
        self.assertEqual(len(await self.rows('SELECT * FROM gameplay_grants WHERE grant_key LIKE ?', ('raid-catch:%',))), 2)
        self.assertFalse(await self.rows("SELECT * FROM player_inventory WHERE item_id='epoch_shard'"))
        rewarded = await storage.get_player(77)
        self.assertGreater(rewarded['gold'], 500)
        self.assertEqual(len(await storage.get_player_beasts(77)), 2)
        await recover_raids()
        self.assertEqual(await storage.get_player(77), rewarded)

    async def test_initial_raid_send_failure_refunds_and_cleans_up(self):
        from utils.duels import active_fighters
        from utils.raids import active_raids
        raid = await self.make_raid(paid=True)
        active_raids[raid.id] = raid
        active_fighters.add(42)
        combat.active_states[42] = raid.state(42)
        raid.channel.send.side_effect = RuntimeError('Synthetic send failure')
        with self.assertRaises(RuntimeError):
            await raid.run()
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)
        self.assertNotIn(raid.id, active_raids)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_tending_cleanses_blight_before_healing(self):
        raid = await self.make_raid()
        state = raid.state(42)
        state.update(hp=10, status='blight', status_turns=3, mana=100)
        await raid.action(42, 'tend')
        self.assertGreater(state['hp'], 10)
        self.assertIsNone(state['status'])
        self.assertGreater(raid.support[42], 0)

    async def test_existing_pattern_survives_a_second_hp_threshold(self):
        raid = await self.make_raid()
        raid.enemy['hp'] = 100
        raid.pending = True
        raid.weaves = 1
        raid.phase = 1
        await raid.action(42, 'guard')
        self.assertEqual(raid.phase, 1)
        self.assertEqual(raid.weaves, 1)

    async def test_defeated_boss_rejects_extra_actions_during_settlement(self):
        raid = await self.make_raid()
        raid.enemy['hp'] = 0
        with self.assertRaisesRegex(ValueError, 'complete'):
            await raid.action(42, 'tend')
        self.assertEqual(raid.support[42], 0)

    async def test_join_finishing_eligibility_after_lobby_closes_is_rejected(self):
        from utils.raids import RaidLobby
        lobby = RaidLobby(42, None)
        entered, release = asyncio.Event(), asyncio.Event()

        async def eligible(uid):
            entered.set()
            await release.wait()

        click = interaction(77)
        with patch.object(lobby, 'eligible', side_effect=eligible):
            task = asyncio.create_task(lobby.children[0].callback(click))
            await entered.wait()
            lobby.closed = True
            release.set()
            await task
        lobby.stop()
        self.assertNotIn(77, lobby.users)
        self.assertIn('closed', click.followup.send.call_args.args[0])
