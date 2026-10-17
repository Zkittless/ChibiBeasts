"""Gameplay regressions run against disposable databases, never a live Discord bot."""
import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import aiosqlite
from utils import db as storage, combat, story
from utils.artwork import PROJECT_ROOT
from utils.events import record_event
from utils.gameplay import add_stack, evolve_beast, grant_material, grant_loot
from utils.items import use_item
from utils.rewards import shards

def interaction(uid=42):
    response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock())
    response.is_done = lambda: bool(response.defer.await_count or response.send_message.await_count or response.edit_message.await_count)
    return SimpleNamespace(user=SimpleNamespace(id=uid, display_name='Trainer'), guild=None, response=response, followup=SimpleNamespace(send=AsyncMock()), channel=SimpleNamespace(send=AsyncMock(), get_partial_message=lambda _: SimpleNamespace(edit=AsyncMock())), edit_original_response=AsyncMock())

class DatabaseCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / 'game.db')
        self.patches = []
        for module_name in ('utils.db', 'utils.progress', 'cogs.battle', 'cogs.world', 'cogs.ranked', 'cogs.utilities', 'utils.sanctuary'):
            module = importlib.import_module(module_name)
            if hasattr(module, 'DB_PATH'):
                p = patch.object(module, 'DB_PATH', self.db_path)
                p.start()
                self.patches.append(p)
        await storage.init_db()
        await storage.get_or_create_player(42, 'Trainer')
        combat.active_states.clear()
        combat.active_parties.clear()
        from utils.duels import active_fighters
        active_fighters.clear()

    async def asyncTearDown(self):
        combat.active_states.clear()
        combat.active_parties.clear()
        from utils.duels import active_fighters
        active_fighters.clear()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    async def execute(self, sql, args=()):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(sql, args)
            await db.commit()

    async def rows(self, sql, args=()):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, args) as c:
                return [dict(r) for r in await c.fetchall()]

    async def beast(self, beast_id='prismite', uid=42, starter=False):
        return await storage.add_beast_to_player(uid, {**storage.get_beast_data(beast_id), 'caught_from': 'starter' if starter else 'hatch'}, starter=starter)

    async def perk(self, perk_id, uid=42):
        await self.execute('INSERT INTO player_perks(user_id,perk_id,equipped) VALUES(?,?,1)', (uid, perk_id))

class Campaign(DatabaseCase):

    async def drive_campaign(self, uid, answer):
        from cogs.world import World
        await storage.get_or_create_player(uid, 'Campaign tester')
        await storage.update_player(uid, level=30)
        available = [k for k, b in storage.load_beasts().items() if not b.get('evolution_only') and b['rarity'] not in {'ancient', 'corrupted', 'dev'} and (k not in {'prismite', 'twine', 'gloop', 'barkley'})]
        for beast_id in available[:12]:
            await self.beast(beast_id, uid)
        for beast_id in available[:12]:
            await self.execute('INSERT OR IGNORE INTO bestiary(guild_id,beast_id,first_caught_by) VALUES(7,?,?)', (beast_id, uid))
        chapters = story.load_questline()['chapters']
        for chapter_id, chapter in chapters.items():
            state, current, intro = await story.journal(uid, 7)
            self.assertEqual(current['id'], chapter_id)
            for step in chapter['steps']:
                kind = step['type']
                target = step.get('target', 1)
                if kind == 'explore_count':
                    for _ in range(target):
                        await record_event(uid, 'explore', biome=step.get('track_biome', '🌲 The Whispering Woods'))
                elif kind == 'catch_count':
                    await record_event(uid, 'catch', amount=target, beast_id='slime', beast_type='nature', rarity='common')
                elif kind == 'catch_specific_beast':
                    await storage.add_beast_to_player(uid, {**storage.get_beast_data(step['target_beasts'][0]), 'caught_from': 'wild'})
                elif kind == 'material_collect':
                    await grant_material(uid, step['material_id'], target)
                elif kind == 'material_collect_specific':
                    for mid, amount in step['materials'].items():
                        await grant_material(uid, mid, amount)
                elif kind == 'relic_collect':
                    for _ in range(3):
                        await record_event(uid, 'explore', biome='The ' + step['biome'])
                elif kind == 'use_command':
                    await story.journal(uid, 7)
                elif kind == 'raid_participate':
                    await record_event(uid, 'raid_attack')
                elif kind == 'craft_count':
                    await record_event(uid, 'craft', amount=target)
                elif kind == 'battle_win':
                    await record_event(uid, 'battle_win', amount=target)
                elif kind in {'raid_count', 'raid_complete'}:
                    await record_event(uid, 'raid_complete', amount=target, raid_type='corrupted')
                elif kind == 'raid_damage':
                    await record_event(uid, 'raid_damage', amount=target)
                elif kind == 'seed_hatched':
                    fake = interaction(uid)
                    await World.incubate.callback(World(None), fake, 'world_tree_seed')
                    await self.execute("UPDATE incubating_eggs SET ready_at='2000-01-01 00:00:00',next_tend_at='2000-01-01 00:00:00' WHERE user_id=?", (uid,))
                    await World.tend.callback(World(None), fake)
            state, current, _ = await story.journal(uid, 7)
            self.assertTrue(story.complete(chapter, state), (chapter_id, state['step_progress'].get(chapter_id)))
            await story.claim_chapter(uid, chapter_id, 7, choice=answer if chapter_id == 'chapter_10' else None)
        state, current, _ = await story.journal(uid, 7)
        self.assertIsNone(current)
        self.assertEqual(len(state['completed_chapters']), 10)
        self.assertEqual(state['npc_relationships']['maren'], 'companion')
        self.assertEqual((await self.rows('SELECT choice FROM story_choices WHERE user_id=?', (uid,)))[0]['choice'], answer)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE user_id=? AND stat_key='loom_restored'", (uid,)))[0]['value'], 1)
        self.assertIn((await storage.get_player(uid))['title'], [v['title'] for v in chapters['chapter_10']['choices'].values()])
        self.assertEqual(len(await self.rows('SELECT * FROM story_claims WHERE user_id=?', (uid,))), 10)

    async def test_all_ten_chapters_and_all_three_final_answers(self):
        for uid, answer in enumerate(('care', 'curiosity', 'courage'), 42):
            with self.subTest(answer=answer):
                await self.drive_campaign(uid, answer)

    async def test_legacy_fifth_chapter_resumes_at_six(self):
        await storage.update_player(42, level=15)
        state = await story.get_quest_state(42)
        state.update(current_chapter='chapter_1', completed_chapters=[f'chapter_{i}' for i in range(1, 6)])
        await story.save_quest_state(state)
        state, chapter, _ = await story.journal(42)
        self.assertEqual(chapter['id'], 'chapter_6')

    async def test_simultaneous_chapter_claims_pay_once(self):
        await storage.update_player(42, level=3)
        await story.journal(42)
        await record_event(42, 'explore', amount=3, biome='woods')
        await record_event(42, 'catch', amount=3)
        results = await asyncio.gather(story.claim_chapter(42, 'chapter_1'), story.claim_chapter(42, 'chapter_1'), return_exceptions=True)
        self.assertEqual(sum((isinstance(r, ValueError) for r in results)), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM story_claims')), 1)

class CompanionsAndEvents(DatabaseCase):

    async def test_four_awakenings_preserve_investments_and_require_bond(self):
        from utils.gameplay import STARTER_FORMS, evolved_stats
        for uid, (before, after) in enumerate(STARTER_FORMS.items(), 42):
            await storage.get_or_create_player(uid, 'Bond tester')
            row_id = await self.beast(before, uid, True)
            await self.execute("UPDATE player_beasts SET level=15,bond=29,happiness=90,nickname='Old friend',attack=attack+40,train_atk=12,damage_bonus=10,raid_slot=2 WHERE id=?", (row_id,))
            with self.assertRaises(ValueError):
                await evolve_beast(uid, 1)
            await self.execute('UPDATE player_beasts SET bond=30 WHERE id=?', (row_id,))
            original = (await storage.get_player_beasts(uid))[0]
            expected = evolved_stats(original, storage.get_beast_data(before), storage.get_beast_data(after))
            await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(?,?,'bark_armor')", (uid, row_id))
            await evolve_beast(uid, 1)
            result = (await storage.get_player_beasts(uid))[0]
            self.assertEqual(result['beast_id'], after)
            for key in ('id', 'nickname', 'level', 'train_atk', 'bond', 'damage_bonus', 'raid_slot', 'player_number'):
                self.assertEqual(result[key], original[key])
            self.assertEqual(result['attack'], expected['attack'])
            self.assertEqual(len(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id=?', (row_id,))), 1)

    async def test_acquisition_and_duplicate_events_count_once(self):
        row_id = await storage.add_beast_to_player(42, {**storage.get_beast_data('slime'), 'caught_from': 'wild'}, grant_key='unique-catch')
        again = await storage.add_beast_to_player(42, {**storage.get_beast_data('slime'), 'caught_from': 'wild'}, grant_key='unique-catch')
        self.assertEqual(row_id, again)
        await record_event(42, 'catch', event_key=f'catch:{row_id}')
        self.assertEqual((await storage.get_player(42))['total_catches'], 1)
        await record_event(42, 'pvp_win', event_key='win:1')
        await record_event(42, 'pvp_win', event_key='win:1')
        self.assertEqual((await storage.get_player(42))['wins'], 1)

    async def test_play_and_training_grow_the_target_companion_bond(self):
        first = await self.beast(starter=True)
        second = await self.beast('slime')
        await record_event(42, 'play', beast_row_id=first)
        await record_event(42, 'train', beast_row_id=second)
        rows = await storage.get_player_beasts(42)
        self.assertEqual([r['bond'] for r in rows], [3, 1])

    async def test_exp_awards_are_atomic_and_starters_share_the_curve(self):
        from cogs.battle import award_player_exp
        await asyncio.gather(*(award_player_exp(42, 10) for _ in range(10)))
        p = await storage.get_player(42)
        accounted = p['exp'] + sum((storage.calc_player_exp_for_level(i) for i in range(1, p['level'])))
        self.assertEqual(accounted, 100)
        self.assertEqual(storage.get_beast_exp_for_level({'caught_from': 'starter'}, 15), storage.get_beast_exp_for_level({'caught_from': 'hatch'}, 15))

    async def test_perks_unlock_through_normal_milestones(self):
        from utils.progress import check_achievements
        await storage.update_player(42, level=25, total_catches=15, wins=15)
        await record_event(42, 'explore', amount=5)
        await record_event(42, 'raid_complete', amount=3)
        state = await story.get_quest_state(42)
        state['completed_chapters'] = [f'chapter_{i}' for i in range(1, 11)]
        await story.save_quest_state(state)
        await check_achievements(42)
        from utils.events import PERK_UNLOCKS
        self.assertEqual({r['perk_id'] for r in await self.rows('SELECT perk_id FROM player_perks')}, set(PERK_UNLOCKS))

    async def test_new_players_do_not_receive_inaccessible_daily_quests(self):
        from utils.progress import eligible_daily_set
        for day in range(1, 29):
            quests = await eligible_daily_set(42, f'2026-10-{day:02}')
            self.assertFalse({q['event'] for q in quests} & {'raid_attack', 'raid_damage', 'guild_action'})
            self.assertEqual(quests, await eligible_daily_set(42, f'2026-10-{day:02}'))

    async def test_ko_timer_is_not_reset_and_expired_beasts_recover(self):
        row_id = await self.beast()
        await storage.knockout_beast(row_id, 'common')
        first = (await storage.get_player_beasts(42))[0]
        await storage.knockout_beast(row_id, 'common')
        second = (await storage.get_player_beasts(42))[0]
        self.assertEqual(first['knocked_out_until'], second['knocked_out_until'])
        self.assertEqual(first['hp'], 0)
        await self.execute("UPDATE player_beasts SET knocked_out_until='2000-01-01T00:00:00+00:00' WHERE id=?", (row_id,))
        healed = (await storage.get_player_beasts(42))[0]
        self.assertEqual(healed['hp'], healed['max_hp'])
        self.assertIsNone(healed['knocked_out_until'])

class ItemsAndWorld(DatabaseCase):

    async def test_invalid_effect_does_not_consume_item(self):
        await self.beast(starter=True)
        for item_id in ('pixie_dust_potion', 'loom_fragment', 'genesis_fruit', 'sunforge_core'):
            await storage.add_item(42, item_id)
            with self.assertRaises(ValueError):
                await use_item(42, item_id)
        self.assertEqual(sum((r['quantity'] for r in await storage.get_inventory(42))), 4)

    async def test_healing_changes_the_live_battle_state(self):
        row_id = await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        state = combat.build_state(storage.get_beast_data(row['beast_id']), row)
        state['hp'] -= 20
        combat.active_states[42] = state
        await storage.add_item(42, 'pixie_dust_potion')
        old = state['hp']
        text = await use_item(42, 'pixie_dust_potion')
        self.assertGreater(state['hp'], old)
        self.assertNotIn('Restored 0', text)
        self.assertEqual((await storage.get_player_beasts(42))[0]['hp'], row['hp'])

    async def test_simultaneous_item_use_consumes_once(self):
        await self.beast(starter=True)
        await storage.add_item(42, 'star_candy_shards')
        results = await asyncio.gather(use_item(42, 'star_candy_shards'), use_item(42, 'star_candy_shards'), return_exceptions=True)
        self.assertEqual(sum((isinstance(r, ValueError) for r in results)), 1)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_time_skip_moves_hatching_and_tending_clocks(self):
        now = datetime.now(timezone.utc)
        fmt = lambda d: d.strftime('%Y-%m-%d %H:%M:%S')
        await self.execute('INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at,next_tend_at) VALUES(42,?,?,?,?,?)', ('common_incubation_egg', 'Test egg', 'common', fmt(now + timedelta(hours=12)), fmt(now + timedelta(hours=8))))
        await storage.add_item(42, 'loom_fragment')
        await use_item(42, 'loom_fragment')
        row = (await self.rows('SELECT * FROM incubating_eggs'))[0]
        for key, hours in [('ready_at', 6), ('next_tend_at', 2)]:
            self.assertAlmostEqual((datetime.strptime(row[key], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) - now).total_seconds() / 3600, hours, delta=0.01)

    async def test_tear_restores_party_and_ambrosia_is_per_beast(self):
        first = await self.beast(starter=True)
        second = await self.beast('slime')
        await self.execute('UPDATE player_beasts SET raid_slot=2 WHERE id=?', (second,))
        await storage.knockout_beast(second, 'common')
        await storage.add_item(42, 'tear_of_leviathan')
        await use_item(42, 'tear_of_leviathan')
        await storage.add_item(42, 'ambrosia_tart')
        await use_item(42, 'ambrosia_tart')
        rows = await storage.get_player_beasts(42)
        self.assertEqual(rows[1]['hp'], rows[1]['max_hp'])
        self.assertEqual(rows[0]['damage_bonus'], 5)
        self.assertEqual(rows[1]['damage_bonus'], 0)

    async def test_attunement_is_saved_until_a_matching_tier_exists(self):
        from utils.hatching import attune_hatch
        roster = storage.load_beasts()
        await storage.add_item(42, 'astral_reroll')
        await use_item(42, 'astral_reroll', choice='cosmic')
        await attune_hatch(42, roster['slime'], roster)
        self.assertEqual((await storage.get_player(42))['astral_type'], 'cosmic')
        result = await attune_hatch(42, roster['wyvern'], roster)
        self.assertEqual(result['type'], 'cosmic')
        self.assertEqual(result['rarity'], 'rare')
        self.assertIsNone((await storage.get_player(42))['astral_type'])

    async def test_astral_bonus_preserves_fractional_shards(self):
        await self.perk('astral_resonance')
        old = (await storage.get_player(42))['celestial_shards']
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            for _ in range(20):
                await shards(db, 42, 1)
            await db.commit()
        self.assertEqual((await storage.get_player(42))['celestial_shards'] - old, 23)

    async def test_crafting_runes_and_concurrent_material_spending(self):
        from cogs.world import World, load_equipment
        gear, runes = load_equipment()
        item_id, item = next(iter(runes.items()))
        for mid, amount in item['recipe'].items():
            await grant_material(42, mid, amount)
        with patch('cogs.world.get_user_sanctuary', AsyncMock(return_value={})):
            await asyncio.gather(*(World.craft.callback(World(None), interaction(), item_id) for _ in range(2)))
        self.assertEqual(sum((r['quantity'] for r in await storage.get_inventory(42) if r['item_id'] == item_id)), 1)
        await self.beast(starter=True)
        from utils.equipment import equip
        await equip(42, 1, item_id)
        self.assertEqual((await storage.get_player_beasts(42))[0]['rune_id'], item_id)
        self.assertFalse(any((r['quantity'] < 0 for r in await self.rows('SELECT * FROM player_materials'))))

    async def test_missing_materials_have_real_loot_routes(self):
        for mid in ('void_essence', 'spirit_crystal', 'sunforge_residue', 'chronos_gear', 'blighted_world_core', 'tear_of_the_creator'):
            await grant_loot(42, mid)
        self.assertEqual(len(await self.rows('SELECT * FROM player_materials')), 6)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_armor_copies_and_runes_survive_equipping_and_unequipping(self):
        from utils.equipment import equip, unequip, definitions
        armor = next((key for key, value in definitions().items() if value.get('slot') != 'rune'))
        rune = next((key for key, value in definitions().items() if value.get('slot') == 'rune'))
        first = await self.beast(starter=True)
        second = await self.beast('slime')
        for _ in range(2):
            await self.execute('INSERT INTO player_equipment(user_id,equipment_id) VALUES(42,?)', (armor,))
        await equip(42, 1, armor)
        await equip(42, 2, armor)
        self.assertEqual({r['beast_row_id'] for r in await self.rows('SELECT * FROM player_equipment')}, {first, second})
        await storage.add_item(42, rune)
        await equip(42, 1, rune)
        await asyncio.gather(unequip(42, 1), unequip(42, 1))
        self.assertEqual(sum((r['quantity'] for r in await storage.get_inventory(42) if r['item_id'] == rune)), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id=?', (second,))), 1)

    async def test_two_tends_hatch_a_ready_egg_once(self):
        from cogs.world import World
        await self.execute("INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at,next_tend_at,tends_required) VALUES(42,'common','Test egg','common','2000-01-01 00:00:00','2000-01-01 00:00:00',2)")
        await asyncio.gather(*(World.tend.callback(World(None), interaction()) for _ in range(2)))
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)

    async def test_shard_purchases_have_real_effects_and_enforce_the_weekly_limit(self):
        from utils.shops import purchase_shard
        await storage.update_player(42, celestial_shards=200, level=10)
        results = await asyncio.gather(purchase_shard(42, 'divine_compass'), purchase_shard(42, 'divine_compass'), return_exceptions=True)
        self.assertEqual(sum((isinstance(result, ValueError) for result in results)), 1)
        player = await storage.get_player(42)
        self.assertEqual(player['divine_compass_charges'], 3)
        self.assertEqual(player['celestial_shards'], 175)

    async def test_blueprint_builds_once_without_spending_guild_tokens(self):
        from cogs.world import World
        await self.execute("INSERT INTO guilds(id,name,leader_id,guild_tokens) VALUES(3,'Test guild',42,100)")
        await self.execute("INSERT INTO guild_members(guild_id,user_id,rank) VALUES(3,42,'leader')")
        await storage.add_item(42, 'sanctuary_blueprint')
        await asyncio.gather(*(World.build.callback(World(None), interaction(), 'fairy_garden') for _ in range(2)))
        self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds'))[0]['guild_tokens'], 100)
        self.assertEqual((await self.rows('SELECT fairy_garden FROM guild_sanctuary'))[0]['fairy_garden'], 1)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_all_seven_guild_shop_items_can_be_purchased(self):
        from utils.shops import GUILD_ITEMS, purchase_guild
        await self.execute("INSERT INTO guilds(id,name,level) VALUES(3,'Shop guild',20)")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await storage.update_player(42, guild_tickets=1000)
        for item_id in GUILD_ITEMS:
            await purchase_guild(42, item_id)
        self.assertEqual({r['item_id'] for r in await storage.get_inventory(42)}, set(GUILD_ITEMS))

class CombatRules(unittest.TestCase):

    def state(self, beast_id='slime'):
        data = storage.get_beast_data(beast_id)
        stats = data['base_stats']
        row = {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'], 'level': 1, 'happiness': 70}
        return combat.build_state(data, row)

    def test_armor_and_evasion_apply_before_hp_loss(self):
        attacker = self.state()
        defender = self.state()
        defender['damage_reduction_flat_percent'] = 50
        old = defender['hp']
        actual = combat.resolve_hit(attacker, defender, 20, [])
        self.assertEqual(actual, 10)
        self.assertEqual(old - defender['hp'], 10)
        defender['evasion_percent'] = 100
        old = defender['hp']
        self.assertEqual(combat.resolve_hit(attacker, defender, 20, []), 0)
        self.assertEqual(defender['hp'], old)

    def test_moves_have_distinct_effects_and_ultimate_owns_its_charge(self):
        attacker = self.state('prismite')
        defender = self.state('twine')
        attacker['mana'] = attacker['max_mana']
        defender['ultimate_charges'] = 3
        attacker['ultimate_charges'] = 2
        combat.resolve_move(attacker, defender, attacker['moves'][2], False, [])
        self.assertGreater(attacker.get('guard', 0), 0)
        old = attacker['mana']
        combat.resolve_move(attacker, defender, attacker['moves'][0], False, [])
        self.assertGreaterEqual(attacker['mana'], old)
        defender['mana'] = defender['max_mana'] = 100
        combat.resolve_move(defender, attacker, defender['ultimate'], True, [])
        self.assertEqual(defender['ultimate_charges'], 0)
        self.assertEqual(attacker['ultimate_charges'], 3)

    def test_survival_ward_and_support_passives(self):
        attacker = self.state()
        defender = self.state('rootwarden')
        defender['perks'] = {'aura_of_phoenix'}
        combat.resolve_hit(attacker, defender, 100000, [])
        self.assertEqual(defender['hp'], 1)
        defender['status_ward'] = True
        self.assertFalse(combat.apply_status(defender, 'poison', attacker, []))
        self.assertTrue(combat.apply_status(defender, 'poison', attacker, []))
        supporter = self.state('nebuloom')
        supporter['hp'] = 1
        supporter['mana'] = 100
        before = supporter['hp']
        combat.resolve_move(supporter, attacker, supporter['moves'][1], False, [])
        self.assertGreater(supporter['hp'] - before, int(supporter['max_hp'] * 0.2))

    def test_rarity_bonuses_never_downgrade_an_egg(self):
        from cogs.hatch import get_egg_pool
        perks = [{'perk_id': 'stardust_touch', 'equipped': 1}]
        for egg in ('celestial_egg', 'abyssal_egg'):
            plain = get_egg_pool(egg)
            improved = get_egg_pool(egg, perks, {'celestial_observatory': 1})
            self.assertAlmostEqual(sum(improved.values()), 1)
            self.assertFalse(set(improved) & {'common', 'uncommon', 'rare'})
            self.assertGreaterEqual(improved['divine'], plain['divine'])

    def test_evolution_forms_are_never_random_hatches_or_wild_encounters(self):
        from cogs.world import EGGS, pick_beast_for_rarity
        from utils.habitats import encounter_pool, BIOMES
        for rarity in ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine'):
            for biome in BIOMES:
                self.assertFalse(any((b.get('evolution_only') for b in encounter_pool(rarity, biome))))
            for egg in EGGS.values():
                for _ in range(3):
                    beast = pick_beast_for_rarity(rarity, egg)
                    if beast:
                        self.assertFalse(beast.get('evolution_only'))

    def test_every_creature_and_gear_effect_can_resolve_a_short_fight(self):
        from utils.equipment import definitions
        gear = list(definitions().values())
        for beast_id in storage.load_beasts():
            with self.subTest(beast=beast_id):
                attacker = self.state(beast_id)
                defender = self.state('atlas')
                for item in gear:
                    combat.apply_gear(attacker, item.get('effect', {}))
                attacker['mana'] = attacker['max_mana']
                attacker['ultimate_charges'] = 3
                log = []
                combat.start_battle(attacker, defender, log)
                for move in [*attacker['moves'], attacker['ultimate']]:
                    if not combat.begin_turn(attacker, defender, log):
                        continue
                    combat.resolve_move(attacker, defender, move, move == attacker['ultimate'], log)
                    combat.end_turn(attacker, defender, log)
                    self.assertGreaterEqual(attacker['hp'], 0)
                    self.assertLessEqual(attacker['hp'], attacker['max_hp'])

    def test_achievement_pages_fit_discord_limits(self):
        from utils.pages import achievement_pages
        from utils.progress import ACHIEVEMENTS
        pages = achievement_pages(SimpleNamespace(display_name='Trainer'), ACHIEVEMENTS, {})
        self.assertEqual(sum((len(page.fields) for page in pages)), len(ACHIEVEMENTS))
        for page in pages:
            self.assertLessEqual(len(page), 6000)
            self.assertLessEqual(len(page.fields), 25)
            self.assertTrue(all((len(field.value) <= 1024 for field in page.fields)))

class RaidsAndRanked(DatabaseCase):

    async def test_concurrent_perk_equips_respect_slot_capacity(self):
        from utils.gameplay import equip_perk
        await self.execute("INSERT INTO player_perks(user_id,perk_id) VALUES(42,'aura_of_phoenix')")
        await self.execute("INSERT INTO player_perks(user_id,perk_id) VALUES(42,'genesis_spark')")
        results=await asyncio.gather(equip_perk(42,'aura_of_phoenix'),equip_perk(42,'genesis_spark'),return_exceptions=True)
        self.assertEqual(sum(isinstance(result,ValueError) for result in results),1)
        perks=storage.load_perks()['perks']
        active=await self.rows('SELECT perk_id FROM player_perks WHERE equipped=1')
        self.assertLessEqual(sum(perks[row['perk_id']]['slot_cost'] for row in active),2)

    async def test_concurrent_guild_creation_spends_gold_once(self):
        from cogs.guilds import Guilds
        await storage.update_player(42,gold=2000)
        with patch('cogs.guilds.unlock_simple_achievement',AsyncMock(return_value=False)):
            await asyncio.gather(*(Guilds.guild_create.callback(Guilds(None),interaction(),name) for name in ('First guild','Second guild')))
        self.assertEqual(len(await self.rows('SELECT * FROM guilds')),1)
        self.assertEqual(len(await self.rows('SELECT * FROM guild_members')),1)
        self.assertEqual((await storage.get_player(42))['gold'],1000)

    async def test_interactive_duel_runner_uses_one_charge_owner_and_cleans_up(self):
        from utils.duels import duel, MoveView, prepare_state, active_fighters
        from cogs.battle import build_pve_beast_state
        await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        state = await prepare_state(42, row, storage.get_beast_data('prismite'), [])
        state['ultimate_charges'] = 2
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        enemy['hp'] = 1

        class AutomaticView(MoveView):

            async def wait(self):
                self.chosen_move = (state['moves'][0], False)
                self.stop()
                return False
        fake = interaction()
        fake.channel.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        winner, _, _, _ = await duel(fake, [state, enemy], [42, None], 'Test duel', view_class=AutomaticView)
        self.assertEqual(winner, 0)
        self.assertEqual((await storage.get_player(42))['ultimate_charges'], 3)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_invalid_raid_ultimate_does_not_tick_the_companion_turn(self):
        from utils.raids import RaidSession
        from cogs.guilds import RAID_BOSSES
        await self.beast(starter=True)
        state = combat.build_state(storage.get_beast_data('prismite'), (await storage.get_player_beasts(42))[0])
        state['mana'] = 0
        raid = RaidSession(SimpleNamespace(), RAID_BOSSES['corrupted'][0], {42}, None)
        raid.party = {42: [state]}
        with self.assertRaises(ValueError):
            await raid.action(42, 'ultimate')
        self.assertEqual(state['action_count'], 0)
        self.assertEqual(state['mana'], 0)

    async def test_ranked_seasons_archive_and_reward_once(self):
        from cogs.ranked import ensure_season, current_season
        await self.execute("INSERT INTO game_meta VALUES('ranked_season','1')")
        await storage.update_player(42, pvp_wins=8, pvp_losses=2, pvp_rating=1600, pvp_season=1)
        await ensure_season()
        first = await storage.get_player(42)
        await ensure_season()
        second = await storage.get_player(42)
        self.assertEqual(first['celestial_shards'], second['celestial_shards'])
        self.assertEqual(second['pvp_rating'], 1000)
        self.assertEqual(len(await self.rows('SELECT * FROM ranked_history')), 1)

    async def test_ranked_match_rewards_cannot_repeat(self):
        from cogs.ranked import record_result
        await storage.get_or_create_player(77, 'Opponent')
        await record_result(123, 42, 77)
        first = await storage.get_player(42)
        await record_result(123, 42, 77)
        second = await storage.get_player(42)
        self.assertEqual(first['pvp_rating'], second['pvp_rating'])
        self.assertEqual(second['pvp_wins'], 1)
        self.assertEqual(len(await self.rows("SELECT * FROM achievements WHERE achievement_id='first_ranked_win'")), 1)

    async def test_raid_catch_pity_and_reward_idempotency(self):
        from utils.raids import RaidSession
        from cogs.guilds import RAID_BOSSES
        boss = RAID_BOSSES['corrupted'][0]
        raid = RaidSession(SimpleNamespace(id=1), boss, {42}, 5)
        raid.id = 12
        await self.execute('INSERT INTO raid_catch_progress VALUES(?,?,7)', (42, boss['id']))
        caught, clears = await raid.reward(42)
        self.assertTrue(caught)
        self.assertEqual(clears, 0)
        gold = (await storage.get_player(42))['gold']
        await raid.reward(42)
        self.assertEqual((await storage.get_player(42))['gold'], gold)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)
        self.assertTrue(await self.rows('SELECT * FROM player_materials'))

    async def test_restart_refunds_a_summon_exactly_once(self):
        from utils.raids import recover_raids
        await self.execute("INSERT INTO raids(id,boss_id,status) VALUES(9,'ancient_chronos','active')")
        await self.execute("INSERT INTO raid_payments(raid_id,user_id,item_id) VALUES(9,42,'epoch_shard')")
        await recover_raids()
        await recover_raids()
        inventory = await storage.get_inventory(42)
        self.assertEqual([(r['item_id'], r['quantity']) for r in inventory], [('epoch_shard', 1)])
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'abandoned')

    async def test_support_alone_can_earn_a_raid_reward(self):
        from utils.raids import RaidSession
        from cogs.guilds import RAID_BOSSES
        row_id = await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        state = combat.build_state(storage.get_beast_data('prismite'), row)
        boss = RAID_BOSSES['corrupted'][0]
        channel = SimpleNamespace(send=AsyncMock(), guild=SimpleNamespace(id=7))
        raid = RaidSession(channel, boss, {42}, None)
        raid.id = 23
        raid.party = {42: [state]}
        raid.support[42] = 20
        raid.view = SimpleNamespace(stop=lambda: None)
        await raid.finish(True)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards WHERE user_id=42')), 1)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE stat_key='raid_complete'"))[0]['value'], 1)
if __name__ == '__main__':
    unittest.main()
