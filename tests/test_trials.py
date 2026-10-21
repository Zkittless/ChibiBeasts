"""Persistent Trials use disposable DBs and the production pure combat engine."""
import asyncio
import copy
import json
import time
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import adventure_party, db as storage, trial_combat, trial_locks, trial_rules, trials


class Trials(DatabaseCase):
    async def ready(self, *, team=1, level=5, hp=None):
        await storage.update_player(42, level=50)
        ids = []
        for index, species in enumerate(('twine', 'gloop', 'barkley')[:team]):
            row_id = await self.beast(species, starter=index == 0)
            ids.append(row_id)
            await self.execute('UPDATE player_beasts SET level=?,attack=9999,defense=9999 WHERE id=?', (level, row_id))
            if hp is not None:
                await self.execute('UPDATE player_beasts SET hp=? WHERE id=?', (hp, row_id))
            if index:
                await adventure_party.set_slot(42, row_id, index)
        return ids

    async def begin(self, destination='woods', **kwargs):
        return await trials.begin(42, destination, seed=73, **kwargs)

    async def drive(self, run, *, until=None):
        for _ in range(300):
            if run['status'] == 'complete' or until is not None and run['payload']['cleared'] >= until:
                return run
            if run['status'] == 'between':
                run = await trials.next_wave(42, run['id'], run['version'])
            elif run['status'] == 'paused':
                run = await trials.resume(42, run['id'], run['version'])
            else:
                choice = next(action['choice'] for action in trial_combat.legal_actions(run['payload'])
                              if action['enabled'] and action['kind'] == 'move')
                run = await trials.act(42, run['id'], run['version'], choice)
        self.fail('Fixture did not reach a wave boundary.')

    async def payload(self, run, update):
        p = copy.deepcopy(run['payload'])
        update(p)
        await self.execute('UPDATE trial_runs SET payload=? WHERE id=?', (json.dumps(p), run['id']))
        return await trials.read(42, run['id'])

    async def test_board_browsing_spends_nothing_and_keeps_gate_meanings(self):
        await self.ready()
        before = await storage.get_player(42)
        board = await trials.board(42)
        self.assertEqual([value['waves'] for value in board['destinations']], [5, 6, 7, 8, 9, 10, 11, 12, 13, 15])
        self.assertEqual([value['min_level'] for value in board['destinations']], [1, 3, 6, 9, 12, 16, 20, 25, 30, 38])
        self.assertEqual(before, await storage.get_player(42))
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        self.assertIsNone(board['run'])

    async def test_entry_snapshots_resources_boosts_gear_and_no_capture_consumables(self):
        ids = await self.ready(team=3, hp=11)
        await self.execute('UPDATE player_beasts SET mana=7 WHERE user_id=42')
        await storage.update_player(42, next_defense_boost=10, next_speed_boost=15,
                                    status_ward_charges=2, ultimate_charges=2,
                                    catch_boost_charges=3, incense_charges=4, ancient_compass_charges=5)
        run = await self.begin()
        p = run['payload']
        self.assertEqual(p['party_ids'], ids)
        self.assertEqual([member['hp'] for member in p['party']], [11, 11, 11])
        self.assertEqual([member['mana'] for member in p['party']], [7, 7, 7])
        self.assertEqual([member['ultimate_charges'] for member in p['party']], [2, 0, 0])
        self.assertTrue(p['party'][0]['status_ward'])
        self.assertFalse(p['party'][1]['status_ward'])
        player = await storage.get_player(42)
        self.assertEqual((player['next_defense_boost'], player['next_speed_boost'], player['status_ward_charges']), (0, 0, 1))
        self.assertEqual((player['catch_boost_charges'], player['incense_charges'], player['ancient_compass_charges']), (3, 4, 5))
        self.assertTrue(trial_locks.locked(42))
        with self.assertRaises(ValueError):
            await self.begin()
        self.assertEqual((await storage.get_player(42))['status_ward_charges'], 1)

    async def test_genesis_mana_enters_once_without_a_cancel_refill(self):
        await self.ready(team=2)
        await self.perk('genesis_spark')
        await self.execute('UPDATE player_beasts SET mana=1 WHERE user_id=42')
        run = await self.begin()
        self.assertTrue(all(member['mana'] == 21 for member in run['payload']['party']))
        await trials.retreat(42, run['id'], run['version'])
        self.assertEqual([row['mana'] for row in await storage.get_player_beasts(42)], [1, 1])

    async def test_old_cooldown_is_honored_and_scaled_saved_deadline_is_frozen(self):
        await self.ready()
        clock = time.time()
        await storage.update_player(42, challenge_last_at=clock - 100)
        with self.assertRaisesRegex(ValueError, 'cooling down'):
            await self.begin(now=clock)
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        await storage.update_player(42, challenge_last_at=0)
        await self.perk('chronos_hourglass')
        run = await self.begin('loom_deep', now=clock)
        ready = (await trials.board(42, now=clock))['ready_at']
        self.assertEqual(ready, clock + 2880)
        await trials.retreat(42, run['id'], run['version'], now=clock)
        with self.assertRaisesRegex(ValueError, 'cooling down'):
            await self.begin('woods', now=clock + 100)
        self.assertEqual((await trials.board(42, now=clock))['ready_at'], ready)

    async def test_board_quotes_hourglass_cooldown_that_entry_actually_reserves(self):
        await self.ready()
        plain = await trials.board(42)
        self.assertEqual([row['effective_cooldown_seconds'] for row in plain['destinations']],
                         [1800] * 3 + [2400] * 4 + [3600] * 3)
        await self.perk('chronos_hourglass')
        quoted = await trials.board(42)
        self.assertEqual([row['effective_cooldown_seconds'] for row in quoted['destinations']],
                         [1440] * 3 + [1920] * 4 + [2880] * 3)
        clock = time.time()
        run = await self.begin('glacial', now=clock)
        self.assertEqual((await trials.board(42, now=clock))['ready_at'] - clock,
                         next(row['effective_cooldown_seconds'] for row in quoted['destinations'] if row['key'] == 'glacial'))

    async def test_frozen_bonus_quotes_match_full_and_partial_actual_settlements(self):
        await self.ready(team=2)
        await self.perk('fable_scholar')
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Library')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,arcane_library) VALUES(3,1)')
        run = await self.begin()
        full = copy.deepcopy(run['payload']['reward_preview'])
        base = run['payload']['reward_baseline']
        self.assertEqual(full['trainer_xp'], round(int(base['trainer_xp'] * 1.5) * 1.15))
        for quote, baseline in zip(full['party_xp'], base['party']):
            self.assertEqual(quote['amount'], round(round(int(baseline['idle'] * 1.5) * 1.1) * 1.15))
            self.assertGreaterEqual(quote['contributed_amount'], quote['amount'])
        # Guild/perk changes outside the run cannot alter the frozen quote.
        await self.execute('UPDATE guild_sanctuary SET arcane_library=0 WHERE guild_id=3')
        await self.execute("UPDATE player_perks SET equipped=0 WHERE user_id=42 AND perk_id='fable_scholar'")
        run = await self.drive(run, until=2)
        bank = copy.deepcopy(run['payload']['bank_quote'])
        run = await trials.retreat(42, run['id'], run['version'])
        for key in ('gold', 'trainer_xp'):
            self.assertEqual(run['payload']['settlement'][key], bank[key])
        self.assertEqual([row['amount'] for row in run['payload']['settlement']['party_xp']],
                         [row['amount'] for row in bank['party_xp']])
        await self.execute('UPDATE trial_cooldowns SET ready_at=0 WHERE user_id=42')
        await self.execute('UPDATE guild_sanctuary SET arcane_library=1 WHERE guild_id=3')
        await self.execute("UPDATE player_perks SET equipped=1 WHERE user_id=42 AND perk_id='fable_scholar'")
        run = await self.begin()
        full = copy.deepcopy(run['payload']['reward_preview'])
        run = await self.drive(run)
        self.assertEqual(run['payload']['outcome'], 'won')
        for key in ('gold', 'trainer_xp'):
            self.assertEqual(run['payload']['settlement'][key], full[key])
        self.assertEqual([row['amount'] for row in run['payload']['settlement']['party_xp']],
                         [row['amount'] for row in full['party_xp']])

    async def test_preparation_failure_rolls_back_cooldown_and_boosts(self):
        await self.ready(team=2)
        await storage.update_player(42, next_defense_boost=10, status_ward_charges=1)
        with patch('utils.duels.prepare_state', side_effect=ValueError('failed preparation')):
            with self.assertRaisesRegex(ValueError, 'failed preparation'):
                await self.begin()
        self.assertIsNone(await trials.current(42))
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        self.assertFalse(trial_locks.locked(42))
        player = await storage.get_player(42)
        self.assertEqual((player['next_defense_boost'], player['status_ward_charges']), (10, 1))

    async def test_pending_raid_settlement_blocks_entry_without_spending(self):
        ids = await self.ready()
        await storage.update_player(42, next_defense_boost=10, status_ward_charges=1)
        payload = dict(users=[42], party={'42': [dict(id=ids[0], hp=1)]})
        await self.execute('INSERT INTO raid_settlements(raid_id,payload) VALUES(812,?)', (json.dumps(payload),))
        with self.assertRaisesRegex(ValueError, 'raid rewards are still settling'):
            await self.begin()
        self.assertIsNone(await trials.current(42))
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        self.assertEqual((await storage.get_player(42))['next_defense_boost'], 10)
        self.assertEqual((await storage.get_player(42))['status_ward_charges'], 1)
        await self.execute('UPDATE raid_settlements SET completed=1 WHERE raid_id=812')
        run = await self.begin()
        self.assertEqual(run['status'], 'between')

    async def test_deferred_raid_cannot_modify_trial_then_recovers_once_after_bank(self):
        from cogs.guilds import RAID_BOSSES
        from utils.raids import RaidSession, recover_raids
        ids = await self.ready(hp=1)
        run = await self.begin()
        boss = RAID_BOSSES['ancient'][0]
        payload = dict(boss=boss, users=[42], guild_id=None, server_id=None,
                       party={'42': [dict(id=ids[0], hp=1)]}, damage={'42': 1},
                       support={'42': 0}, raid_level=10)
        await self.execute('INSERT INTO raid_settlements(raid_id,payload) VALUES(9182,?)', (json.dumps(payload),))
        session = RaidSession(None, boss, [42], None)
        session.id, session.raid_level = 9182, 10
        session.party, session.damage, session.support = {42: payload['party']['42']}, {42: 1}, {42: 0}
        before = (await storage.get_player_beasts(42))[0]
        with self.assertRaises(ValueError):
            await session._settle_rewards(None)
        unchanged = (await storage.get_player_beasts(42))[0]
        for key in ('hp', 'mana', 'exp', 'level', 'max_hp'):
            self.assertEqual(unchanged[key], before[key], key)
        self.assertEqual((await self.rows('SELECT completed FROM raid_settlements WHERE raid_id=9182'))[0]['completed'], 0)
        await trials.retreat(42, run['id'], run['version'])
        settled = (await storage.get_player_beasts(42))[0]
        self.assertEqual((await self.rows('SELECT completed FROM raid_settlements WHERE raid_id=9182'))[0]['completed'], 1)
        self.assertEqual(settled['hp'], settled['max_hp'])
        await self.execute('UPDATE player_beasts SET hp=1 WHERE id=?', (ids[0],))
        await recover_raids(user_id=42)
        await session._settle_rewards(None)
        after = (await storage.get_player_beasts(42))[0]
        self.assertEqual(after['hp'], 1)
        self.assertEqual((after['exp'], after['level']), (settled['exp'], settled['level']))

    async def test_entry_revalidates_a_changed_preparation_snapshot(self):
        await self.ready()
        await storage.update_player(42, next_speed_boost=12)
        from utils.duels import prepare_state
        async def changed(*args, **kwargs):
            prepared = await prepare_state(*args, **kwargs)
            await self.execute('UPDATE player_beasts SET mana=mana-1 WHERE user_id=42')
            return prepared
        with patch('utils.duels.prepare_state', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed while entering'):
                await self.begin()
        self.assertIsNone(await trials.current(42))
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        self.assertEqual((await storage.get_player(42))['next_speed_boost'], 12)

    async def test_saves_rng_enemy_cursor_and_resources_before_returning(self):
        await self.ready()
        run = await self.begin()
        run = await trials.next_wave(42, run['id'], run['version'])
        saved = copy.deepcopy(run['payload'])
        with patch('utils.trial_rules.generate_plan', side_effect=AssertionError('rerolled')):
            self.assertEqual((await trials.read(42, run['id']))['payload'], saved)
        async with storage.connect() as db:
            await db.execute("UPDATE trial_runs SET payload=? WHERE id=?", (json.dumps(saved), run['id']))
            await db.commit()
        choice = next(action['choice'] for action in trial_combat.legal_actions(saved) if action['enabled'])
        run = await trials.act(42, run['id'], run['version'], choice)
        self.assertNotEqual(run['payload']['rng_state'], saved['rng_state'])
        self.assertEqual((await trials.read(42, run['id']))['payload'], run['payload'])
        self.assertEqual(run['payload']['actions'], 1)

    async def test_duplicate_foreign_and_forged_actions_do_not_mutate(self):
        await self.ready()
        run = await trials.next_wave(42, (run := await self.begin())['id'], run['version'])
        before = copy.deepcopy(run)
        with self.assertRaises(ValueError):
            await trials.act(99, run['id'], run['version'], ['Tick Bite', False])
        with self.assertRaises(ValueError):
            await trials.act(42, run['id'], run['version'], ['not a move', False])
        self.assertEqual((await trials.read(42, run['id']))['payload'], before['payload'])
        choice = next(action['choice'] for action in trial_combat.legal_actions(run['payload']) if action['enabled'])
        results = await asyncio.gather(trials.act(42, run['id'], run['version'], choice),
                                       trials.act(42, run['id'], run['version'], choice), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        current = await trials.current(42)
        self.assertEqual(current['payload']['actions'], 1)
        self.assertEqual(current['version'], run['version'] + 1)

    async def test_restart_pauses_exact_fighting_state_then_resumes_it(self):
        await self.ready()
        run = await self.begin()
        run = await trials.next_wave(42, run['id'], run['version'])
        run = await trials.arm_turn(42, run['id'], run['version'])
        saved = copy.deepcopy(run['payload'])
        await storage.init_db()
        restored = await trials.current(42)
        self.assertEqual(restored['status'], 'paused')
        self.assertIsNone(restored['turn_deadline'])
        self.assertTrue(trial_locks.locked(42))
        restored = await trials.resume(42, restored['id'], restored['version'])
        for key in ('party', 'enemy', 'rng_state', 'cursor', 'turn_order', 'phase', 'round'):
            self.assertEqual(restored['payload'][key], saved[key], key)
        self.assertEqual(restored['status'], 'fighting')

    async def test_pause_pays_nothing_expiry_banks_progress_once(self):
        await self.ready()
        run = await self.drive(await self.begin(), until=2)
        before = await storage.get_player(42)
        run = await trials.pause(42, run['id'], run['version'])
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        self.assertIsNone(run['payload']['settlement'])
        self.assertIsNone(await trials.current(42, now=run['expires_at'] + 1))
        expired = await trials.read(42, run['id'])
        self.assertEqual(expired['payload']['outcome'], 'expired')
        paid = (await storage.get_player(42))['gold']
        self.assertGreater(paid, before['gold'])
        await trials.read(42, run['id'])
        self.assertEqual((await storage.get_player(42))['gold'], paid)
        self.assertFalse(trial_locks.locked(42))

    async def test_camp_is_once_cleanses_survivors_and_never_revives(self):
        await self.ready(team=3)
        run = await self.begin()
        def checkpoint(p):
            p['cleared'] = p['plan']['camp_wave']
            p['party'][0].update(hp=10, mana=1, status='blight', status_turns=2)
            p['party'][1].update(hp=0, mana=0, status='poison')
            p['party'][2].update(hp=10, mana=1, status='slow')
        run = await self.payload(run, checkpoint)
        run = await trials.camp(42, run['id'], run['version'])
        first = run['payload']['party'][0]
        self.assertEqual(first['hp'], min(first['max_hp'], 10 + first['max_hp'] // 4))
        self.assertEqual(first['mana'], min(first['max_mana'], 1 + first['max_mana'] // 4))
        self.assertIsNone(first['status'])
        self.assertEqual(run['payload']['party'][1]['hp'], 0)
        with self.assertRaisesRegex(ValueError, 'only once'):
            await trials.camp(42, run['id'], run['version'])
        await trials.pause(42, run['id'], run['version'])
        self.assertEqual((await trials.current(42))['payload']['party'][1]['hp'], 0)

    async def test_three_items_are_atomic_between_waves_with_no_revive_or_growth(self):
        ids = await self.ready(hp=1)
        await storage.add_item(42, 'pixie_dust_potion', 4)
        run = await self.begin()
        for bad in ('phoenix_elixir', 'gnome_grown_carrot', 'chrono_biscuit'):
            with self.assertRaises(ValueError):
                await trials.use_item(42, run['id'], run['version'], bad, ids[0])
        with self.assertRaises(ValueError):
            await trials.use_item(42, run['id'], run['version'], 'pixie_dust_potion', 999)
        results = await asyncio.gather(
            trials.use_item(42, run['id'], run['version'], 'pixie_dust_potion', ids[0]),
            trials.use_item(42, run['id'], run['version'], 'pixie_dust_potion', ids[0]), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        run = await trials.current(42)
        for _ in range(2):
            run = await trials.use_item(42, run['id'], run['version'], 'pixie_dust_potion', ids[0])
        self.assertEqual(run['payload']['items_used'], 3)
        self.assertEqual((await self.rows("SELECT SUM(quantity) AS q FROM player_inventory WHERE item_id='pixie_dust_potion'"))[0]['q'], 1)
        with self.assertRaisesRegex(ValueError, 'three'):
            await trials.use_item(42, run['id'], run['version'], 'pixie_dust_potion', ids[0])

    async def test_rendering_time_does_not_consume_turn_window_or_extend_it_on_refresh(self):
        await self.ready()
        run = await self.begin()
        run = await trials.next_wave(42, run['id'], run['version'])
        clock = time.time()
        self.assertIsNone(run['turn_deadline'])
        run = await trials.arm_turn(42, run['id'], run['version'], now=clock + 10)
        self.assertEqual(run['turn_deadline'], clock + 55)
        same = await trials.arm_turn(42, run['id'], run['version'], now=clock + 20)
        self.assertEqual(same['turn_deadline'], clock + 55)
        not_due = await trials.timeout(42, run['id'], run['version'], now=clock + 54)
        self.assertEqual(not_due['status'], 'fighting')
        done = await trials.timeout(42, run['id'], run['version'], now=clock + 55)
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(done['payload']['settlement']['gold'], 0)
        self.assertEqual((await trials.timeout(42, run['id'], run['version'], now=clock + 60))['version'], done['version'])

    async def test_no_wave_payout_full_clear_pays_and_records_once(self):
        await self.ready(team=3)
        run = await self.begin()
        before = await storage.get_player(42)
        run = await self.drive(run, until=4)
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        self.assertEqual((await storage.get_player(42))['wins'], before['wins'])
        self.assertEqual(await self.rows('SELECT * FROM trial_stamps'), [])
        run = await self.drive(run)
        self.assertEqual(run['payload']['outcome'], 'won')
        self.assertEqual(run['payload']['settlement']['fraction'], 1)
        self.assertTrue(run['payload']['settlement']['stamp'])
        self.assertEqual((await storage.get_player(42))['wins'], before['wins'] + 1)
        self.assertEqual(len(await self.rows('SELECT * FROM trial_stamps')), 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 3)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        paid = (await storage.get_player(42))['gold']
        for _ in range(3):
            await trials.report(run)
            await trials.read(42, run['id'])
        self.assertEqual((await storage.get_player(42))['gold'], paid)
        self.assertEqual((await storage.get_player(42))['wins'], before['wins'] + 1)
        self.assertFalse(trial_locks.locked(42))

    async def test_actual_final_wave_defeat_keeps_earned_half_without_a_stamp(self):
        await self.ready()
        run = await self.drive(await self.begin(), until=4)
        def vulnerable(p):
            p['party'][0].update(hp=1, defense=0, speed=0, shield=0, guard=0,
                                 status=None, status_ward=False, mana=0)
        run = await self.payload(run, vulnerable)
        run = await trials.next_wave(42, run['id'], run['version'])
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(run['payload']['outcome'], 'lost')
        self.assertEqual(run['payload']['settlement']['fraction'], .5)
        self.assertGreater(run['payload']['settlement']['gold'], 0)
        self.assertEqual((await storage.get_player(42))['wins'], 0)
        self.assertEqual(await self.rows('SELECT * FROM trial_stamps'), [])
        beast = (await self.rows('SELECT hp,knocked_out_until FROM player_beasts WHERE user_id=42'))[0]
        self.assertEqual(beast['hp'], 0)
        self.assertIsNotNone(beast['knocked_out_until'])

    async def test_auxiliary_reporting_failure_returns_committed_result_and_retries(self):
        await self.ready()
        run = await self.drive(await self.begin(), until=2)
        with patch('utils.progress.check_achievements', side_effect=RuntimeError('temporary report outage')):
            with self.assertLogs('chibibeasts.trials', level='ERROR'):
                run = await trials.retreat(42, run['id'], run['version'])
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(run['reported'], 0)
        receipt = copy.deepcopy(run['payload']['settlement'])
        await trials.board(42)
        reported = await trials.read(42, run['id'])
        self.assertEqual(reported['reported'], 1)
        self.assertEqual(reported['payload']['settlement'], receipt)

    async def test_long_tower_multiplies_the_bounded_reserve_baseline(self):
        ids = await self.ready(team=2, level=10)
        await self.execute('UPDATE player_beasts SET level=1 WHERE id=?', (ids[1],))
        run = await self.begin('loom_deep')
        baseline = run['payload']['reward_baseline']['party'][1]
        run = await self.drive(run)
        self.assertEqual(run['payload']['outcome'], 'won')
        reserve = next(row for row in run['payload']['settlement']['party_xp'] if row['id'] == ids[1])
        self.assertEqual(reserve['amount'], int(baseline['idle'] * 3))
        self.assertGreater(reserve['amount'], baseline['idle'])

    async def test_persistent_member_guard_allows_unrelated_companions_only(self):
        ids = await self.ready()
        other = await self.beast('fairy')
        run = await self.begin()
        run = await trials.pause(42, run['id'], run['version'])
        async with storage.connect() as db:
            with self.assertRaisesRegex(ValueError, 'saved Biome Trial'):
                await trials.assert_available(db, 42)
            with self.assertRaises(ValueError):
                await trials.assert_available(db, 42, ids)
            await trials.assert_available(db, 42, [other])
