"""Persistent branch consequences, team actions and bounded one-trip rewards."""
import asyncio
import copy
import random
from unittest.mock import patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from utils import adventure_party, db as storage, expedition_events, expeditions as trips


class BranchingExpeditions(DatabaseCase):
    async def ready(self, *, team=False, level=1):
        first = await self.beast('twine', starter=True)
        await storage.update_player(42, level=level)
        ids = [first]
        if team:
            for slot, beast in enumerate(('gloop', 'barkley'), 1):
                row_id = await self.beast(beast)
                await adventure_party.set_slot(42, row_id, slot)
                ids.append(row_id)
        return ids

    async def pick(self, trip, choice):
        return await trips.choose(42, trip['id'], trip['version'], choice)

    async def bank(self):
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,10000)')

    async def test_team_snapshot_and_exact_pages_survive_resume(self):
        ids = await self.ready(team=True)
        trip = await trips.begin(42, 'woods', rng=random.Random(11))
        self.assertEqual(trip['payload']['adventure_version'], 2)
        self.assertEqual(trip['payload']['party_ids'], ids)
        page = trips.presentation(trip)
        trip = await trips.read(42, trip['id'])
        self.assertEqual(trips.presentation(trip), page)
        self.assertEqual(page['progress'], dict(completed=0, total=3))
        self.assertEqual(trips.options(trip), ('tracks', 'gather', 'shrine', 'return_early'))
        page['choices'].clear()
        self.assertTrue(trips.presentation(trip)['choices'])

    async def test_region_pools_have_distinct_hints_and_seeds_vary_followups(self):
        def payload(seed, region):
            p = dict(gold=20, exp=30, base_exp=30, offered=['enchanted_bark'],
                     material='enchanted_bark', bond=0, happiness=0, notes=[])
            expedition_events.initialize(p, region, seed, [dict(id=1, beast_id='twine', type='nature')])
            return p
        pages = {expedition_events.presentation(dict(payload=payload(3, region), status='active'))['body']
                 for region in expedition_events.REGIONS}
        self.assertEqual(len(pages), 5)
        next_events = set()
        for seed in range(20):
            p = payload(seed, 'woods')
            expedition_events.resolve(p, 'woods', 'gather')
            expedition_events.resolve(p, 'woods', 'material:enchanted_bark')
            next_events.add(p['event'])
            same = payload(seed, 'woods')
            expedition_events.resolve(same, 'woods', 'gather')
            expedition_events.resolve(same, 'woods', 'material:enchanted_bark')
            self.assertEqual(p['page'], same['page'])
        self.assertEqual(next_events, {'obstacle', 'merchant', 'rescue'})

    async def test_choices_change_later_stops_and_unlock_sheltered_deep_route(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        trip = await self.pick(await self.pick(trip, 'shrine'), 'repair')
        self.assertEqual(trip['payload']['event'], 'obstacle')
        self.assertIn('earlier discovery', trips.presentation(trip)['body'])
        self.assertIn('detour', trips.options(trip))
        trip = await self.pick(trip, 'detour')
        self.assertEqual(trip['payload']['completed'], 3)
        self.assertEqual(trips.options(trip), ('return', 'push'))
        trip = await self.pick(trip, 'push')
        self.assertIn('sheltered', trips.options(trip))
        self.assertEqual(trip['payload']['target_stops'], 4)
        trip = await self.pick(trip, 'sheltered')
        self.assertEqual(trips.options(trip), ('return',))
        self.assertEqual(trip['payload']['completed'], 4)

    async def test_study_leads_to_discovery_and_cap_is_single_bundle(self):
        await self.ready(team=True)
        trip = await trips.begin(42, 'woods', rng=random.Random(2))
        base = copy.deepcopy(trip['payload'])
        before = await storage.get_player(42)
        trip = await self.pick(await self.pick(trip, 'shrine'), 'study')
        self.assertEqual(trip['payload']['event'], 'discovery')
        trip = await self.pick(trip, 'map')
        trip = await self.pick(trip, 'push')
        trip = await self.pick(trip, 'venture')
        self.assertEqual(trip['payload']['bonus'], 25)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        trip = await self.pick(trip, 'return')
        self.assertEqual(trip['payload']['gold'], int(base['base_gold'] * 1.25))
        self.assertEqual(trip['payload']['exp'], int(base['base_exp'] * 1.25))
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 3)
        self.assertEqual(len(trip['payload']['party_xp']), 3)
        self.assertTrue(all(row['amount'] <= 5 for row in trip['payload']['party_xp']))

    async def test_early_return_is_proportional_and_zero_stops_has_no_material_or_xp(self):
        await self.ready()
        trip = await trips.begin(42, 'woods', rng=random.Random(8))
        done = await self.pick(trip, 'return_early')
        self.assertEqual((done['payload']['gold'], done['payload']['exp'], done['payload']['party_xp']), (0, 0, []))
        self.assertFalse(done['payload']['material_granted'])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type IN ('material_collect','material_story')"), [])
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type='explore'"), [])
        self.assertEqual(await self.rows("SELECT * FROM achievements WHERE achievement_id='first_explore'"), [])
        self.assertEqual((await trips.energy(42)).charges, 0)
        await self.bank()
        trip = await trips.begin(42, 'woods', rng=random.Random(8))
        base = dict(trip['payload'])
        trip = await self.pick(trip, 'gather')
        done = await self.pick(trip, 'return_early')
        self.assertEqual(done['payload']['gold'], int(base['base_gold'] / 3))
        self.assertEqual(done['payload']['exp'], int(base['base_exp'] / 3))
        self.assertTrue(done['payload']['material_granted'])

    async def test_single_camp_is_atomic_and_cannot_revive_or_repeat(self):
        ids = await self.ready(team=True)
        await self.execute('UPDATE player_beasts SET hp=10 WHERE user_id=42')
        trip = await self.pick(await trips.begin(42, 'woods'), 'shrine')
        # A teammate may be knocked out between two stops; camp does not revive it.
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (ids[-1],))
        results = await asyncio.gather(self.pick(trip, 'rest'), self.pick(trip, 'rest'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        current = await trips.current(42)
        self.assertTrue(current['payload']['camp_used'])
        self.assertNotIn('camp', trips.options(current))
        rows = {row['id']: row for row in await storage.get_player_beasts(42)}
        for row_id in ids[:-1]:
            self.assertEqual(rows[row_id]['hp'], 10 + max(1, rows[row_id]['max_hp'] * 5 // 100))
        self.assertEqual(rows[ids[-1]]['hp'], 0)
        await self.pick(current, 'return_early')
        after = {row['id']: row for row in await storage.get_player_beasts(42)}
        # XP may level a creature and change its maximum; settlement itself
        # must not apply the five-percent camp a second time.
        for row_id in ids:
            self.assertEqual(after[row_id]['hp'], rows[row_id]['hp'])

    async def test_companion_ability_has_safe_alternative_and_checks_health(self):
        ids = await self.ready(team=True)
        trip = await self.pick(await self.pick(await trips.begin(42, 'woods'), 'shrine'), 'repair')
        actions = trips.options(trip)
        self.assertIn('detour', actions)
        helper = next(action for action in actions if action.startswith('help:'))
        helper_id = int(helper.split(':')[1])
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (helper_id,))
        with self.assertRaisesRegex(ValueError, 'safe detour'):
            await self.pick(trip, helper)
        trip = await self.pick(trip, 'detour')
        self.assertEqual(trip['payload']['completed'], 3)
        self.assertEqual(trip['payload']['party_ids'], ids)

    async def test_healthy_helper_participation_is_saved(self):
        ids = await self.ready(team=True)
        trip = await self.pick(await self.pick(await trips.begin(42, 'woods'), 'shrine'), 'repair')
        helper = 'help:' + str(ids[1])
        self.assertIn(helper, trips.options(trip))
        before = (await storage.get_player_beasts(42))[1]['hp']
        trip = await self.pick(trip, helper)
        self.assertEqual(trip['payload']['participations'][str(ids[1])], 1)
        self.assertEqual((await storage.get_player_beasts(42))[1]['hp'], before)

    async def test_team_social_rewards_are_bounded_and_exhausted_team_cannot_push(self):
        ids = await self.ready(team=True)
        await self.execute('UPDATE player_beasts SET happiness=50 WHERE user_id=42')
        trip = await self.pick(await self.pick(await trips.begin(42, 'woods'), 'shrine'), 'repair')
        trip = await self.pick(trip, 'detour')
        await self.execute('UPDATE player_beasts SET hp=1 WHERE user_id=42')
        with self.assertRaisesRegex(ValueError, 'too exhausted'):
            await self.pick(trip, 'push')
        trip = await self.pick(trip, 'return')
        members = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual((members[ids[0]]['bond'], members[ids[0]]['happiness']), (2, 55))
        for row_id in ids[1:]:
            self.assertEqual((members[row_id]['bond'], members[row_id]['happiness']), (1, 53))

    async def test_high_level_lead_cannot_farm_reserve_xp_in_low_region(self):
        ids = await self.ready(team=True, level=50)
        await self.execute('UPDATE player_beasts SET level=50 WHERE id=?', (ids[0],))
        trip = await self.pick(await self.pick(await trips.begin(42, 'woods'), 'shrine'), 'repair')
        trip = await self.pick(trip, 'detour')
        trip = await self.pick(trip, 'return')
        self.assertEqual(len(trip['payload']['party_xp']), 2)
        self.assertTrue(all(row['amount'] <= 1 for row in trip['payload']['party_xp']))

    async def test_saved_team_cannot_evolve_but_nontraveller_still_can(self):
        ids = await self.ready(team=True, level=20)
        outsider = await self.beast('twine')
        await self.execute('UPDATE player_beasts SET level=15,bond=40,happiness=100 WHERE user_id=42')
        trip = await trips.begin(42, 'woods')
        from utils.gameplay import evolve_beast
        for number in range(1, 4):
            with self.assertRaisesRegex(ValueError, 'saved expedition'):
                await evolve_beast(42, number)
        await evolve_beast(42, 4)
        members = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual(members[outsider]['beast_id'], 'timestitch')
        self.assertEqual(members[ids[0]]['beast_id'], 'twine')
        trip = await self.pick(trip, 'return_early')
        await evolve_beast(42, 1)

    async def test_saved_choices_reject_wrong_owner_stale_and_foreign_actions(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with self.assertRaises(ValueError):
            await trips.choose(43, trip['id'], trip['version'], 'gather')
        with self.assertRaises(ValueError):
            await self.pick(trip, 'sheltered')
        results = await asyncio.gather(self.pick(trip, 'gather'), self.pick(trip, 'shrine'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual((await trips.current(42))['version'], 1)
        self.assertEqual((await trips.energy(42)).charges, 0)

    async def test_claim_rolls_back_all_team_rewards_and_repeated_claim_is_rejected(self):
        await self.ready(team=True)
        trip = await self.pick(await trips.begin(42, 'woods'), 'gather')
        before = await storage.get_player_beasts(42)
        with patch('utils.rewards.player_exp', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                await self.pick(trip, 'return_early')
        self.assertEqual(await storage.get_player_beasts(42), before)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        results = await asyncio.gather(self.pick(trip, 'return_early'), self.pick(trip, 'return_early'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_challenge_uses_saved_team_and_no_extra_noncombat_xp(self):
        ids = await self.ready(team=True)
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')

        async def battle(interaction, row, data, perks, enemy, personality, title, win, loss, **kwargs):
            self.assertEqual([row['id'] for row in kwargs['party_rows']], ids)
            await win(discord.Embed(), {}, {'hp': 0}, False)

        with patch('cogs.battle.run_pve_battle', side_effect=battle) as runner:
            trip = await trips.run_battle(interaction(), trip)
            self.assertEqual(runner.await_count, 1)
        self.assertEqual(trip['payload']['event'], 'camp')
        self.assertTrue(trip['payload']['battle_awarded'])
        trip = await self.pick(trip, 'practice')
        trip = await self.pick(trip, 'return')
        self.assertEqual(trip['payload']['party_xp'], [])
        self.assertEqual(len(await storage.get_player_beasts(42)), 4)
        await trips.report({**trip, 'reported': 0})
        self.assertEqual(len(await storage.get_player_beasts(42)), 4)

    async def test_strong_reserve_sets_saved_difficulty_for_v2_and_legacy(self):
        ids = await self.ready(team=True)
        await self.execute('UPDATE player_beasts SET hp=1,max_hp=1,attack=1,defense=1,speed=1,mana=1,max_mana=1 WHERE id=?', (ids[0],))
        await self.execute('UPDATE player_beasts SET hp=1000,max_hp=1000,attack=500,defense=500,speed=100,mana=500,max_mana=500 WHERE id=?', (ids[1],))
        from utils.duels import prepare_state
        from utils.habitats import encounter_difficulty
        from utils.progression import encounter_trainer_xp

        for legacy in (False, True):
            await self.bank()
            trip = await trips.begin(42, 'woods', legacy=legacy)
            with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('fairy')):
                trip = await self.pick(trip, 'tracks')
            trip = await self.pick(trip, 'challenge')

            async def battle(click, row, data, perks, enemy, personality, title, win, loss, **kwargs):
                self.assertEqual([member['id'] for member in kwargs['party_rows']], ids)
                lead_state = await prepare_state(42, row, data, perks, consume=False, preserve_resources=True)
                self.assertNotEqual(encounter_difficulty(lead_state, enemy), 'trivial')
                saved = await trips.read(42, trip['id'])
                self.assertEqual(saved['payload']['battle_difficulty'], 'trivial')
                await win(discord.Embed(), {}, {'hp': 0}, False)

            with patch('utils.duels.prepare_state', wraps=prepare_state) as preview, \
                    patch('cogs.battle.run_pve_battle', side_effect=battle):
                trip = await trips.run_battle(interaction(), trip)
            self.assertEqual(preview.await_count, 3)
            for index, call in enumerate(preview.await_args_list):
                self.assertEqual(call.kwargs, dict(consume=False, party_reserve=index > 0, preserve_resources=True))
            self.assertEqual(trip['payload']['battle_difficulty'], 'trivial')
            if legacy:
                self.assertEqual(trip['payload']['exp'], encounter_trainer_xp(
                    trip['payload']['level'], trip['payload']['encounter_level'], difficulty='trivial'))
            else:
                trip = await self.pick(trip, 'practice')
            await self.pick(trip, 'return')

    async def test_restarted_battle_advances_once_and_never_offers_second_catch(self):
        await self.ready()
        trip = await trips.begin(42, 'woods')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')
        await storage.init_db()
        trip = await trips.current(42)
        self.assertEqual(trip['payload']['event'], 'rescue')
        self.assertEqual(trip['payload']['completed'], 2)
        self.assertFalse(trip['payload']['caught'])
        await storage.init_db()
        same = await trips.current(42)
        self.assertEqual(same['payload']['history'], trip['payload']['history'])
        self.assertNotIn('challenge', trips.options(same))
        trip = await self.pick(same, 'rescue')
        trip = await self.pick(trip, 'return')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_existing_saved_trip_keeps_legacy_resume_contract(self):
        await self.ready()
        trip = await trips.begin(42, 'woods', legacy=True)
        self.assertNotIn('adventure_version', trip['payload'])
        self.assertEqual(trips.options(trip), ('tracks', 'gather', 'shrine'))
        trip = await self.pick(trip, 'shrine')
        trip = await self.pick(trip, 'rest')
        self.assertEqual(trips.options(trip), ('return', 'rustle'))
        trip = await self.pick(trip, 'return')
        self.assertEqual(trip['status'], 'complete')

    async def test_story_commission_material_is_still_offered_and_granted_once(self):
        await self.ready()
        with patch('utils.story.quest_material_options', return_value=['spirit_crystal']):
            trip = await trips.begin(42, 'woods')
        trip = await self.pick(trip, 'gather')
        self.assertIn('material:spirit_crystal', trips.options(trip))
        trip = await self.pick(trip, 'material:spirit_crystal')
        trip = await self.pick(trip, 'return_early')
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'),
                         [dict(material_id='spirit_crystal', quantity=1)])
