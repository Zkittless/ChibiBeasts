"""Destination difficulty and encounter rewards use production rules and temp DBs."""
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.battle import Battle, NPC_TIERS, build_pve_beast_state, run_pve_battle
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage, expeditions
from utils import journal_state
from utils.habitats import (BASE_BIOMES, BIOMES, biome_key, encounter_difficulty,
                            encounter_level, encounter_pool, eligible, material_pool, pick_encounter, starter_training_destination)
from utils.progression import encounter_beast_xp


class DestinationBalance(unittest.TestCase):
    def test_ten_fixed_bands_cover_one_to_fifty_once(self):
        self.assertEqual(len(BIOMES), 10)
        self.assertEqual([b['level_range'] for b in BIOMES.values()], [(n, n + 4) for n in range(1, 50, 5)])
        self.assertEqual([BIOMES[k]['min_level'] for k in BASE_BIOMES], [1, 6, 12, 20, 30])
        rng = random.Random(14)
        for destination, region in BIOMES.items():
            observed = {encounter_level(destination, rng) for _ in range(200)}
            self.assertEqual(observed, set(range(region['level_range'][0], region['level_range'][1] + 1)))

    def test_every_advertised_tier_exists_and_drawn_species_obey_it(self):
        for destination, region in BIOMES.items():
            self.assertAlmostEqual(sum(region['weights'].values()), 1)
            for rarity in region['weights']:
                self.assertTrue(encounter_pool(rarity, destination), (destination, rarity))
            for _ in range(100):
                beast = pick_encounter(region['weights'], destination)
                self.assertIn(beast['rarity'], region['weights'])
                self.assertIn(beast['type'], region['types'])
        self.assertEqual(set(BIOMES['woods']['weights']), {'common', 'uncommon'})
        self.assertNotIn('legendary', BIOMES['loom']['weights'])
        self.assertNotIn('common', BIOMES['loom_deep']['weights'])

    def test_nearby_levels_respect_both_zone_boundaries(self):
        rng = random.Random(12)
        self.assertEqual({encounter_level('woods', 1, rng) for _ in range(100)}, {1, 2})
        self.assertEqual({encounter_level('woods_deep', 6, rng) for _ in range(100)}, {6, 7})
        self.assertEqual({encounter_level('woods_deep', 8, rng) for _ in range(100)}, {7, 8, 9})
        self.assertEqual(encounter_level('woods', 50, rng), 5)
        self.assertEqual(encounter_level('loom_deep', 1, rng), 46)

    def test_common_starter_training_recommendation_waits_for_readiness(self):
        for species in ('twine', 'prismite', 'gloop', 'barkley'):
            beast = storage.get_beast_data(species)
            self.assertEqual(starter_training_destination(beast, 7, 30), 'woods')
            self.assertEqual(starter_training_destination(beast, 8, 30), 'woods_deep')
            self.assertEqual(starter_training_destination(beast, 14, 30), 'woods_deep')
        self.assertIsNone(starter_training_destination(storage.get_beast_data('timestitch'), 15, 8))

    def test_depths_reuse_base_resources_and_regions(self):
        for base in BASE_BIOMES:
            deep = base + '_deep'
            self.assertEqual(biome_key(deep), base)
            self.assertEqual(material_pool({'id': '', 'type': ''}, deep), material_pool({'id': '', 'type': ''}, base))

    def test_every_eligible_wild_species_keeps_a_reachable_habitat(self):
        for beast in storage.load_beasts().values():
            if eligible(beast):
                self.assertTrue(journal_state.habitats(beast['id']), beast['id'])

    def test_legacy_pinned_hunt_survives_catalog_habitat_removal(self):
        with patch('utils.journal_state.habitats', return_value=[]):
            goal = journal_state.goal_status({'kind': 'creature', 'target': 'dragon'}, SimpleNamespace(companions=[], level=50))
        self.assertIn('no current wild habitat', goal['text'])
        self.assertIsNone(goal['realm'])

    def test_old_and_weak_encounters_pay_less_than_current_threats(self):
        actor = build_pve_beast_state(storage.get_beast_data('timestitch'), 40)
        weak = build_pve_beast_state(storage.get_beast_data('slime'), 40)
        dangerous = build_pve_beast_state(storage.get_beast_data('paradox'), 40)
        weak_xp = encounter_beast_xp(40, 40, difficulty=encounter_difficulty(actor, weak))
        dangerous_xp = encounter_beast_xp(40, 40, difficulty=encounter_difficulty(actor, dangerous))
        self.assertLess(weak_xp, dangerous_xp)
        self.assertLess(encounter_beast_xp(40, 5, difficulty='trivial'), weak_xp)

    def test_npc_tiers_have_real_partners_and_spread_access(self):
        self.assertEqual([t['level'] for t in NPC_TIERS.values()], [5, 15, 25, 35, 48])
        self.assertTrue(all(t['trainer'] > 1 for t in NPC_TIERS.values()))
        for tier in NPC_TIERS.values():
            self.assertTrue(storage.get_beast_data(tier['companion']))


class WorldRewardFlow(DatabaseCase):
    async def ready(self, trainer=10):
        row_id = await self.beast('twine', starter=True)
        await storage.update_player(42, level=trainer)
        return row_id

    async def pick(self, trip, choice):
        return await expeditions.choose(42, trip['id'], trip['version'], choice)

    async def test_both_public_encounter_paths_supply_the_actual_companion_level(self):
        # Extension lifecycle tests replace cog modules; patch the exact live
        # class's globals rather than a class retained from test collection.
        from importlib import import_module
        battle_module = import_module('cogs.battle')
        row_id = await self.ready(trainer=3)
        await self.execute('UPDATE player_beasts SET level=6 WHERE id=?', (row_id,))
        trip = await expeditions.begin(42, 'woods_deep')
        with patch('utils.expeditions.encounter_level', wraps=encounter_level) as roll:
            trip = await self.pick(trip, 'tracks')
        self.assertEqual(roll.call_args.args[:2], ('woods_deep', 6))
        self.assertIn(trip['payload']['encounter_level'], (6, 7))
        with patch.object(battle_module, 'encounter_level', wraps=encounter_level) as roll, \
                patch('utils.gear_art.send_owned_portrait', new=AsyncMock()), \
                patch.object(battle_module, 'run_pve_battle', new=AsyncMock()) as battle:
            await battle_module.Battle(None)._challenge(interaction(), 'woods_deep')
        roll.assert_called_once_with('woods_deep', 6)
        self.assertIn(battle.call_args.args[4]['level'], (6, 7))

    async def test_depth_capture_retains_rolled_level_and_base_story_region(self):
        await self.ready()
        trip = await expeditions.begin(42, 'woods_deep', rng=random.Random(4))
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks', rng=random.Random(31))
        level = trip['payload']['encounter_level']
        self.assertIn(level, range(6, 11))
        # Choose a deterministic friendly result, then settle the saved encounter.
        trip['payload']['friendly'] = True
        import json
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(trip['payload']), trip['id']))
        trip = await self.pick(trip, 'approach')
        trip = await self.pick(trip, 'return')
        caught = next(r for r in await storage.get_player_beasts(42) if r['beast_id'] == 'wisp')
        self.assertEqual(caught['level'], level)
        self.assertEqual(trip['status'], 'complete')

    async def test_expedition_requires_actual_knockout_for_capture(self):
        await self.ready()
        trip = await expeditions.begin(42, 'woods')
        trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')

        async def standing_enemy(click, row, data, perks, enemy, personality, title, win, loss, **kwargs):
            await win(discord.Embed(), {}, {'hp': 1}, False)

        with patch('cogs.battle.run_pve_battle', side_effect=standing_enemy):
            result = await expeditions.run_battle(interaction(), trip)
        self.assertFalse(result['payload']['caught'])

    async def test_shared_pve_xp_is_identical_for_wild_and_aggressive_encounters(self):
        row_id = await self.ready()
        gains = []
        for personality in ('wild', 'aggressive'):
            row = await storage.get_active_beast(42)
            enemy = build_pve_beast_state(storage.get_beast_data('slime'), 3)

            async def finished(click, states, *args, **kwargs):
                states[1]['hp'] = 0
                return 0, states, [], False

            with patch('utils.duels.duel', side_effect=finished), patch('utils.duels.beast_exp', new=AsyncMock(return_value=1)) as grant:
                await run_pve_battle(interaction(), row, storage.get_beast_data('twine'), [], enemy, personality,
                                     'Wild Slime', AsyncMock(), AsyncMock())
                gains.append(grant.call_args.args[2])
        self.assertEqual(gains[0], gains[1])
        self.assertLess(gains[0], 40)

    async def test_standing_enemy_win_is_reduced_and_does_not_record_a_clean_win(self):
        await self.ready()
        row = await storage.get_active_beast(42)
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 3)
        win = AsyncMock()

        async def finished(click, states, *args, **kwargs):
            return 0, states, [], False

        with patch('utils.duels.duel', side_effect=finished), patch('utils.duels.beast_exp', new=AsyncMock(return_value=1)) as grant:
            await run_pve_battle(interaction(), row, storage.get_beast_data('twine'), [], enemy, 'aggressive',
                                 'Wild Slime', win, AsyncMock())
        self.assertTrue(win.call_args.args[-1])
        self.assertLess(grant.call_args.args[2], 10)
        self.assertEqual((await storage.get_player(42))['wins'], 0)

    async def test_fresh_trainers_cannot_claim_any_npc_daily_slot(self):
        await self.ready(trainer=1)
        for npc in NPC_TIERS:
            click = interaction()
            await Battle(None)._sparr(click, npc)
            self.assertIn('Recommended companion level', click.followup.send.call_args.args[0])
        self.assertEqual(await self.rows("SELECT * FROM daily_quests WHERE quest_id LIKE 'sparr_%'"), [])

    async def test_normal_and_ranked_duels_share_one_opponent_and_growth_budget(self):
        from cogs.battle import _start_battle
        await self.ready(trainer=1)
        await storage.get_or_create_player(77, 'Other trainer')
        await self.beast('prismite', uid=77, starter=True)

        async def finished(click, states, *args, **kwargs):
            states[1]['hp'] = 0
            return 0, states, [], False

        with patch('utils.duels.duel', side_effect=finished):
            await _start_battle(interaction(), 1001, {'ranked': False}, [42, 77])
            first_player = await storage.get_player(42)
            first_beast = await storage.get_active_beast(42)
            await _start_battle(interaction(), 1002, {'ranked': True}, [42, 77])
        self.assertEqual((await storage.get_player(42))['exp'], first_player['exp'])
        self.assertEqual((await storage.get_active_beast(42))['exp'], first_beast['exp'])
        self.assertEqual((await storage.get_player(42))['gold'], first_player['gold'])
        budget = {r['stat_key'].split(':')[0]: r['value'] for r in await self.rows("SELECT * FROM player_progress WHERE user_id=42 AND stat_key LIKE 'pvp_%_xp:%'")}
        self.assertEqual(budget, {'pvp_beast_xp': 60, 'pvp_trainer_xp': 80})
        self.assertEqual((await storage.get_player(42))['wins'], 1)

    async def test_pvp_time_limit_lead_grants_no_xp_or_gold(self):
        from cogs.battle import _start_battle
        await self.ready(trainer=1)
        await storage.get_or_create_player(77, 'Other trainer')
        await self.beast('prismite', uid=77, starter=True)
        before = await storage.get_player(42)

        async def standing(click, states, *args, **kwargs):
            return 0, states, [], True

        with patch('utils.duels.duel', side_effect=standing):
            await _start_battle(interaction(), 1003, {'ranked': False}, [42, 77])
        after = await storage.get_player(42)
        self.assertEqual((after['level'], after['exp'], after['gold'], after['wins']),
                         (before['level'], before['exp'], before['gold'], before['wins']))
        self.assertEqual(await self.rows("SELECT * FROM player_progress WHERE stat_key LIKE 'pvp_%_xp:%'"), [])

    async def test_boosts_cannot_create_phantom_tiers_or_empty_the_woods(self):
        await self.ready(trainer=1)
        await self.perk('stardust_touch')
        await storage.update_player(42, ancient_compass_charges=1, incense_charges=1)
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Observatory')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,celestial_observatory) VALUES(3,1)')
        trip = await expeditions.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', wraps=pick_encounter) as pick:
            trip = await self.pick(trip, 'tracks')
        self.assertTrue(trip['payload']['encounter'])
        rates = pick.call_args.args[0]
        self.assertEqual(set(rates), {'common', 'uncommon'})
        self.assertAlmostEqual(sum(rates.values()), 1)
        player = await storage.get_player(42)
        self.assertEqual(player['ancient_compass_charges'], 1)
        self.assertEqual(player['incense_charges'], 0)

    async def test_incense_is_saved_when_destination_has_no_upgradable_tier(self):
        await self.ready(trainer=50)
        await storage.update_player(42, incense_charges=1)
        trip = await expeditions.begin(42, 'loom_deep')
        trip = await self.pick(trip, 'tracks')
        self.assertTrue(trip['payload']['encounter'])
        self.assertEqual((await storage.get_player(42))['incense_charges'], 1)

    async def test_active_story_materials_are_gatherable_in_base_and_deep_ember(self):
        from utils.story import load_questline, save_quest_state
        await self.ready(trainer=50)
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,?)', (10000,))
        for chapter_id, region in (('chapter_3', 'ember'), ('chapter_7', 'ember_deep')):
            await save_quest_state({'user_id': 42, 'current_chapter': chapter_id, 'completed_chapters': [],
                                    'step_progress': {chapter_id: {'_introduced': 1}},
                                    'collected_relics': [], 'npc_relationships': {}})
            required = load_questline()['chapters'][chapter_id]['consume_materials']
            trip = await expeditions.begin(42, region, now=10000, rng=random.Random(1))
            self.assertTrue(set(required).issubset(trip['payload']['offered']))
            target = next(iter(required))
            trip = await self.pick(trip, 'gather')
            trip = await self.pick(trip, 'material:' + target)
            before = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
            await self.pick(trip, 'return')
            after = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
            self.assertEqual(after - before, 1)
