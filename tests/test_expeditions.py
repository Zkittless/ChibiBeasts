"""Saved choices, bounded rewards and shared charges against a disposable DB."""
import asyncio
import random
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage, expeditions as trips
from utils.catalog import load_catalog
from utils.duels import active_fighters, reserve_fighters


class Expeditions(DatabaseCase):
    async def ready(self, level=1):
        row_id = await self.beast('twine', starter=True)
        await storage.update_player(42, level=level)
        return row_id

    async def pick(self, trip, choice, **kwargs):
        return await trips.choose(42, trip['id'], trip['version'], choice, **kwargs)

    async def bank(self, charges=3, now=10000):
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,?,?)', (charges, now))

    async def test_legacy_timer_migrates_without_free_refresh_and_banks_three(self):
        await storage.update_player(42, explore_last_at=1000)
        self.assertEqual((await trips.energy(42, now=1600)).charges, 0)
        self.assertEqual((await trips.energy(42, now=1600)).next_in, 1200)
        self.assertEqual((await trips.energy(42, now=2800)).charges, 1)
        self.assertEqual((await trips.energy(42, now=100000)).charges, 3)
        await self.ready()
        trip = await trips.begin(42, 'woods', now=100000)
        self.assertEqual((await trips.energy(42, now=100000)).next_in, 1800)
        await self.pick(trip, 'return_early')

    async def test_new_account_starts_with_one_charge_and_perks_shorten_regeneration(self):
        await self.perk('bramble_walker')
        await self.perk('chronos_hourglass')
        energy = await trips.energy(42, now=1000)
        self.assertEqual((energy.charges, energy.interval, energy.next_in), (1, 1296, 1296))
        self.assertEqual((await trips.energy(42, now=2296)).charges, 2)
        self.assertEqual((await trips.energy(42, now=3592)).charges, 3)

    async def test_invalid_destination_level_companion_and_busy_do_not_spend(self):
        await self.ready()
        for destination in ('wrong', 'loom'):
            with self.assertRaises(ValueError):
                await trips.begin(42, destination)
        self.assertEqual(await self.rows('SELECT * FROM exploration_energy'), [])
        with reserve_fighters([42]):
            with self.assertRaises(ValueError):
                await trips.begin(42, 'woods')
        await self.execute('UPDATE player_beasts SET hp=0 WHERE user_id=42')
        with self.assertRaisesRegex(ValueError, 'recovery'):
            await trips.begin(42, 'woods')
        self.assertEqual(await self.rows('SELECT * FROM exploration_energy'), [])

    async def test_two_panels_cannot_start_two_trips_or_double_spend(self):
        await self.ready()
        results = await asyncio.gather(trips.begin(42, 'woods'), trips.begin(42, 'woods'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assertEqual((await trips.energy(42)).charges, 0)
        self.assertNotIn(42, active_fighters)

    async def test_quick_and_branching_share_the_same_budget_and_active_trip_guard(self):
        await self.ready()
        await self.bank(1)
        quick = await trips.begin(42, 'woods', quick=True, now=10000)
        with self.assertRaisesRegex(ValueError, 'underway'):
            await trips.begin(42, 'woods', now=10000)
        await trips.finish_quick(42, quick['id'])
        with self.assertRaisesRegex(ValueError, 'next exploration charge'):
            await trips.begin(42, 'woods', now=10000)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertIsNone(await trips.read(42, quick['id']))

    async def test_wrong_owner_stale_revision_and_unoffered_choice_are_rejected(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with self.assertRaises(ValueError):
            await trips.choose(43, trip['id'], 0, 'gather')
        with self.assertRaises(ValueError):
            await self.pick(trip, 'material:dragon_scale')
        next_trip = await self.pick(trip, 'gather')
        with self.assertRaisesRegex(ValueError, 'older page'):
            await self.pick(trip, 'tracks')
        with self.assertRaises(ValueError):
            await self.pick(next_trip, 'material:dragon_scale')
        self.assertEqual((await trips.read(42, trip['id']))['version'], 1)

    async def test_gathering_preserves_material_rarity_and_grants_exactly_one(self):
        await self.ready()
        trip = await trips.begin(42, 'woods', rng=random.Random(7))
        mats = load_catalog('materials.json')['materials']
        self.assertEqual(len({mats[m]['rarity'] for m in trip['payload']['offered']}), 1)
        trip = await self.pick(trip, 'gather')
        chosen = trip['payload']['offered'][-1]
        trip = await self.pick(trip, 'material:' + chosen)
        trip = await self.pick(trip, 'return')
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'), [{'material_id': chosen, 'quantity': 1}])
        self.assertEqual(trip['status'], 'complete')

    async def test_landmark_benefits_apply_to_original_companion_only(self):
        first = await self.ready()
        second = await self.beast('slime')
        await self.execute('UPDATE player_beasts SET happiness=50 WHERE user_id=42')
        trip = await trips.begin(42, 'woods')
        trip = await self.pick(trip, 'shrine')
        trip = await self.pick(trip, 'repair')
        await storage.set_active_companion(42, 2)
        await self.pick(trip, 'return')
        rows = {r['id']: r for r in await storage.get_player_beasts(42)}
        self.assertEqual((rows[first]['bond'], rows[first]['happiness']), (2, 55))
        self.assertEqual((rows[second]['bond'], rows[second]['happiness']), (0, 50))

    async def test_optional_observation_adds_to_landmark_bond_without_an_extra_material(self):
        await self.ready()
        trip = await self.pick(await trips.begin(42, 'woods'), 'shrine')
        trip = await self.pick(trip, 'repair')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'rustle')
        trip = await self.pick(trip, 'observe')
        await self.pick(trip, 'return')
        self.assertEqual((await storage.get_active_beast(42))['bond'], 3)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_rest_is_integer_capped_and_does_not_revive(self):
        row_id = await self.ready()
        await self.execute('UPDATE player_beasts SET hp=max_hp-1 WHERE id=?', (row_id,))
        trip = await self.pick(await trips.begin(42, 'woods'), 'shrine')
        trip = await self.pick(trip, 'rest')
        await self.pick(trip, 'return')
        row = (await storage.get_player_beasts(42))[0]
        self.assertEqual(row['hp'], row['max_hp'])
        self.assertIsInstance(row['hp'], int)
        await self.bank()
        trip = await self.pick(await trips.begin(42, 'woods'), 'shrine')
        trip = await self.pick(trip, 'rest')
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (row_id,))
        await self.pick(trip, 'return')
        self.assertEqual((await storage.get_player_beasts(42))[0]['hp'], 0)

    async def test_return_is_atomic_idempotent_and_reports_progress_once(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        results = await asyncio.gather(self.pick(trip, 'return_early'), self.pick(trip, 'return_early'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        done = await trips.read(42, trip['id'])
        await trips.report(done)
        await trips.report({**done, 'reported': 0})
        counters = {r['stat_key']: r['value'] for r in await self.rows('SELECT * FROM player_progress')}
        self.assertEqual((counters['explore'], counters['material_collect']), (1, 1))

    async def test_failed_settlement_rolls_back_all_rewards_and_can_retry(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        before = await storage.get_player(42)
        with patch('utils.rewards.player_exp', side_effect=RuntimeError('transaction failure')):
            with self.assertRaises(RuntimeError):
                await self.pick(trip, 'return_early')
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        self.assertEqual((await trips.read(42, trip['id']))['status'], 'active')
        await self.pick(trip, 'return_early')

    async def test_single_friendly_roll_no_second_encounter_and_one_catch(self):
        await self.ready()
        trip = await trips.begin(42, 'woods', guild_id=7)
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')) as encounter:
            trip = await self.pick(trip, 'tracks', rng=random.Random(31))
            self.assertTrue(trip['payload']['friendly'])
            trip = await self.pick(trip, 'approach')
            self.assertNotIn('rustle', trips.options(trip))
            trip = await self.pick(trip, 'return')
            self.assertEqual(encounter.call_count, 1)
        self.assertEqual([r['beast_id'] for r in await storage.get_player_beasts(42)], ['twine', 'wisp'])
        await trips.report({**trip, 'reported': 0})
        self.assertEqual((await storage.get_player(42))['total_catches'], 1)
        self.assertEqual(len(await self.rows('SELECT * FROM bestiary WHERE guild_id=7')), 1)

    async def test_failed_approach_ends_encounter_and_keeps_material(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks', rng=random.Random(0))
        trip = await self.pick(trip, 'approach')
        self.assertFalse(trip['payload']['caught'])
        self.assertNotIn('rustle', trips.options(trip))
        await self.pick(trip, 'return')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_battle_uses_existing_runner_once_and_banks_catch_on_return(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')

        async def battle(interaction, row, data, perks, enemy, personality, title, win, loss, **kwargs):
            self.assertIn(42, active_fighters)
            self.assertEqual(kwargs, {'reserved': True, 'realm': 'woods'})
            await win(discord.Embed(), {}, {}, False)

        with patch('cogs.battle.run_pve_battle', side_effect=battle) as runner:
            result = await trips.run_battle(interaction(), trip)
            with self.assertRaises(ValueError):
                await trips.run_battle(interaction(), trip)
            self.assertEqual(runner.await_count, 1)
        self.assertTrue(result['payload']['caught'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        await self.pick(result, 'return')
        self.assertEqual(len(await storage.get_player_beasts(42)), 2)
        self.assertNotIn(42, active_fighters)

    async def test_restart_recovers_battle_without_replaying_or_losing_supplies(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')
        await storage.init_db()
        recovered = await trips.current(42)
        self.assertEqual(recovered['status'], 'active')
        self.assertEqual(recovered['payload']['encounter_result'], 'interrupted')
        self.assertEqual(trips.options(recovered), ('return',))
        await self.pick(recovered, 'return')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_deleted_battle_message_releases_fighter_and_resumes_trip(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')
        with patch('cogs.battle.run_pve_battle', side_effect=RuntimeError('message removed')):
            with self.assertRaises(RuntimeError):
                await trips.run_battle(interaction(), trip)
        self.assertNotIn(42, active_fighters)
        recovered = await trips.current(42)
        self.assertEqual(recovered['status'], 'active')
        self.assertEqual(recovered['payload']['encounter_result'], 'interrupted')

    async def test_cancelled_battle_and_duplicate_launch_keep_one_runner_and_release(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')
        started = asyncio.Event()

        async def battle(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch('cogs.battle.run_pve_battle', side_effect=battle):
            task = asyncio.create_task(trips.run_battle(interaction(), trip))
            try:
                await asyncio.wait_for(started.wait(), 2)
                with self.assertRaises(ValueError):
                    await trips.run_battle(interaction(), trip)
                self.assertEqual((await trips.current(42))['status'], 'battle')
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(42, active_fighters)
        self.assertEqual((await trips.current(42))['status'], 'active')

    async def test_story_guarantee_and_library_bonus_still_apply_once(self):
        await self.ready(10)
        state = {'user_id': 42, 'current_chapter': 'chapter_6', 'completed_chapters': ['chapter_' + str(n) for n in range(1, 6)],
                 'step_progress': {'chapter_6': {'explore_loom_3': 2}}, 'collected_relics': [], 'npc_relationships': {}}
        from utils.story import save_quest_state
        await save_quest_state(state)
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Library')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,arcane_library) VALUES(3,1)')
        trip = await trips.begin(42, 'loom')
        trip = await self.pick(trip, 'tracks')
        self.assertEqual(trip['payload']['encounter'], 'loom_fragment')
        trip = await self.pick(trip, 'observe')
        trip = await self.pick(trip, 'return')
        self.assertEqual(trip['payload']['exp_granted'], round(trip['payload']['exp'] * 1.15))
        from utils.story import get_quest_state
        self.assertEqual((await get_quest_state(42))['step_progress']['chapter_6']['explore_loom_3'], 3)

    async def test_loom_compass_precedence_and_observatory_match_existing_explore(self):
        await self.ready(10)
        await storage.update_player(42, prism_keys=1, divine_compass_charges=1, ancient_compass_charges=1, incense_charges=1, catch_boost_charges=1)
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Observatory')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,celestial_observatory) VALUES(3,1)')
        trip = await trips.begin(42, 'loom')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('genesis')) as encounter:
            await self.pick(trip, 'tracks')
        rates = encounter.call_args.args[0]
        self.assertAlmostEqual(rates['divine'], .3)
        player = await storage.get_player(42)
        self.assertEqual((player['prism_keys'], player['divine_compass_charges'], player['ancient_compass_charges'], player['incense_charges'], player['catch_boost_charges']), (0, 1, 1, 0, 0))
