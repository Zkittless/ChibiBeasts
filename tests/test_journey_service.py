"""Real writer integration: replay protection, team reservations and one budget."""
import asyncio
from copy import deepcopy
import json
import random
from unittest.mock import patch, AsyncMock

from tests.test_gameplay import DatabaseCase, interaction
from utils import adventure_party, db as storage, expeditions, journey_service, journeys, trial_combat, trial_locks


def content(*, battle=False, chains=False):
    roots = []
    for index in range(6):
        rid = f'woods.test_{index}'
        nodes = {}
        for number in range(3 if chains else 1):
            nid = f'{rid}.node_{number}'
            choices = [dict(id='help', label='Brace the bridge', description='Gain a little bond.',
                            outcome='The keeper fastens the rope and thanks the team.', effects={'bond': 1})]
            if chains and number < 2:
                choices[0]['next'] = f'{rid}.node_{number + 1}'
            choices.append(dict(id='fight', label='Defend the crossing' if battle else 'Record the marks',
                description='Fight a pack of three.' if battle else 'Study this site.',
                outcome='The team takes its place at the crossing.' if battle else 'The marks join your notes.',
                effects={'battle': 'pack3', 'attack_boon': 10} if battle else {'study': 1}))
            nodes[nid] = dict(body='The keeper waits beside a damaged bridge. A loose rope swings above the stream, while a line of prints leads into the reeds. Your companions listen from the bank as the keeper explains what happened here.', choices=choices)
        roots.append(dict(id=rid, title=f'The bridge {index}', kind='ruin', repeatable=False,
                          entry=f'{rid}.node_0', tags=['test'], nodes=nodes))
    return dict(version=1, biome='woods', encounters=roots)


class JourneyService(DatabaseCase):
    async def ready(self, *, battle=False, chains=False):
        ids = [await self.beast('twine', starter=True)]
        for slot, beast in enumerate(('gloop', 'barkley'), 1):
            row = await self.beast(beast)
            await adventure_party.set_slot(42, row, slot)
            ids.append(row)
        self.definition = content(battle=battle, chains=chains)
        return ids

    async def begin(self):
        with patch('utils.adventure_content.load', return_value=deepcopy(self.definition)):
            return await expeditions.begin(42, 'woods', rng=random.Random(9), adventure_version=3)

    async def pick(self, trip, index=0):
        choice = expeditions.options(trip)[index]
        return await expeditions.choose(42, trip['id'], trip['version'], choice)

    async def test_saved_first_scene_and_permanent_completion_do_not_reroll(self):
        ids = await self.ready()
        trip = await self.begin()
        self.assertEqual(trip['payload']['party_ids'], ids)
        first = trip['payload']['current_root']
        page = expeditions.presentation(trip)
        self.assertEqual(page, expeditions.presentation(await expeditions.current(42)))
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertEqual((await self.rows('SELECT completed FROM adventure_progress WHERE encounter_id=?', (first,)))[0]['completed'], 1)
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual(trip['status'], 'complete')
        await self.execute('UPDATE exploration_energy SET charges=3')
        fresh = await self.begin()
        self.assertNotEqual(fresh['payload']['current_root'], first)
        self.assertNotIn(first, [fresh['payload']['current_root']])

    async def test_frozen_team_blocks_global_mutations_but_not_unrelated_beasts(self):
        ids = await self.ready()
        other = await self.beast('pixie')
        await self.begin()
        from utils.trials import assert_available
        self.assertTrue(trial_locks.locked(42))
        async with storage.connect() as db:
            self.assertEqual(set(await trial_locks.member_ids(db, 42)), set(ids))
            with self.assertRaisesRegex(ValueError, 'explor'):
                await assert_available(db, 42)
            with self.assertRaises(ValueError):
                await assert_available(db, 42, [ids[1]])
            await assert_available(db, 42, [other])

    async def test_duplicate_choice_and_duplicate_return_have_one_atomic_receipt(self):
        await self.ready()
        trip = await self.begin()
        result = await asyncio.gather(self.pick(trip), self.pick(trip), return_exceptions=True)
        self.assertEqual(sum(isinstance(row, ValueError) for row in result), 1)
        saved = await expeditions.current(42)
        self.assertEqual(saved['payload']['completed'], 1)
        result = await asyncio.gather(*[expeditions.choose(42, saved['id'], saved['version'], 'return_early') for _ in range(2)], return_exceptions=True)
        self.assertEqual(sum(isinstance(row, ValueError) for row in result), 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertFalse(trial_locks.locked(42))

    async def test_zero_progress_return_pays_nothing_and_preserves_preparations(self):
        await self.ready()
        await storage.update_player(42, next_defense_boost=1, status_ward_charges=1)
        before = await storage.get_player(42)
        trip = await self.begin()
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        after = await storage.get_player(42)
        self.assertEqual((trip['payload']['gold'], trip['payload']['exp']), (0, 0))
        self.assertEqual(after['gold'], before['gold'])
        self.assertEqual(after['next_defense_boost'], 1)
        self.assertEqual(after['status_ward_charges'], 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type='explore'"), [])

    async def test_story_chain_resumes_the_next_page_across_journeys(self):
        await self.ready(chains=True)
        trip = await self.begin()
        trip = await self.pick(trip)
        node = trip['payload']['current_node']
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        await self.execute('UPDATE exploration_energy SET charges=3')
        # New catalog prose cannot overwrite a location already in progress.
        self.definition['encounters'][0]['nodes'].clear()
        resumed = await self.begin()
        self.assertEqual(resumed['payload']['current_node'], node)

    async def test_pack_carries_resources_capture_ticket_and_restarts_exact_cursor(self):
        await self.ready(battle=True)
        trip = await self.begin()
        # Strong test party lets us exercise three real production turns reliably.
        for member in trip['payload']['prepared_party']:
            member.update(attack=5000, base_attack=5000, speed=10000, base_speed=10000)
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(trip['payload']), trip['id']))
        with patch('utils.expeditions._encounter', wraps=expeditions._encounter) as roll:
            trip = await self.pick(trip, 1)
            self.assertEqual(trip['status'], 'battle')
            self.assertEqual(roll.await_count, 1)
            self.assertEqual(trip['payload']['completed'], 0)
            saved = deepcopy(trip['payload']['combat'])
            async with storage.connect() as db:
                await expeditions.recover_interrupted(db)
                await trial_locks.reload(db)
                await db.commit()
            self.assertEqual((await expeditions.current(42))['payload']['combat'], saved)
            for _ in range(30):
                if trip['status'] != 'battle':
                    break
                state = trip['payload']['combat']
                choice = 'next' if state['status'] == 'between' else trial_combat.legal_actions(state)[0]['choice']
                trip = await journey_service.battle_action(42, trip['id'], trip['version'], choice)
            self.assertEqual(trip['status'], 'active')
            self.assertEqual(trip['payload']['completed'], 1)
            self.assertEqual(trip['payload']['boons']['attack'], 10)
            self.assertEqual(trip['payload']['fights_won'], 1)
            self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
            entry = deepcopy(trip['payload']['combat'])
            trip = await self.pick(trip, 1)
            self.assertEqual(roll.await_count, 1)
            self.assertEqual(trip['payload']['combat']['entered'], entry['entered'])
            self.assertEqual(trip['payload']['combat']['meter'], entry['meter'])
            trip = await journey_service.battle_action(42, trip['id'], trip['version'], 'return')
            self.assertEqual(trip['status'], 'complete')
            self.assertEqual(len(await storage.get_player_beasts(42)), 4)

    async def test_forged_controls_cannot_claim_eggs_or_skip_unresolved_steps(self):
        await self.ready()
        trip = await self.begin()
        for choice in ('return_egg', 'return', 'scene:other:help'):
            with self.assertRaises(ValueError):
                await expeditions.choose(42, trip['id'], trip['version'], choice)
        self.assertEqual((await expeditions.current(42))['version'], trip['version'])

    async def test_real_journal_starts_version_three_and_keeps_combat_in_place(self):
        await self.ready(battle=True)
        from utils.journal import JournalView
        from utils import journal_state
        view = JournalView(42, await journal_state.snapshot(42), page='adventure')
        click = interaction()
        with patch('utils.adventure_content.load', return_value=self.definition), patch.object(view, 'publish', new=AsyncMock()):
            await view.handle(click, 'begin')
            self.assertEqual(view.trip['payload']['adventure_version'], 3)
            with patch('utils.journal_adventure.present', new=AsyncMock()) as present, patch('utils.expeditions.run_battle', new=AsyncMock()) as old:
                click.data = {'values': [f"{view.trip['id']}:{view.trip['version']}:{expeditions.options(view.trip)[1]}"]}
                await view.handle(click, 'path')
                self.assertEqual(view.trip['status'], 'battle')
                present.assert_awaited_once()
                old.assert_not_awaited()

    async def material_ready(self, *, rolled='pixie_silk', battle=False):
        await self.ready(battle=battle)
        for location in self.definition['encounters']:
            for node in location['nodes'].values():
                item = node['choices'][1 if battle else 0]
                item['effects'] = {'material': True, 'battle': 'pack3'} if battle else {'material': True, 'hp_cost': 3}
        with patch('utils.story.quest_material_options', new=AsyncMock(return_value=[rolled])):
            return await self.begin()

    async def test_material_choice_freezes_same_rarity_offers_and_only_replaces_one_bundle(self):
        from utils.catalog import load_catalog
        definitions = load_catalog('materials.json')['materials']
        trip = await self.material_ready()
        p = trip['payload']
        self.assertEqual(p['material'], 'pixie_silk')
        self.assertEqual(set(p['offered']), {'pixie_silk', 'gnome_iron_ore', 'enchanted_bark'})
        self.assertEqual({definitions[key]['rarity'] for key in p['offered']}, {'common'})
        self.assertIn('Pay 3% HP', p['page']['choices'][0]['description'])
        self.assertIn('same-rarity', p['page']['choices'][0]['description'])
        offered = deepcopy(p['offered'])
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertEqual(trip['payload']['page']['kind'], 'material')
        self.assertEqual(trip['payload']['offered'], offered)
        next_page = deepcopy(trip['payload']['material_next_page'])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        saved = await expeditions.read(42, trip['id'])
        self.assertEqual(saved['payload']['page'], trip['payload']['page'])
        from utils.journal import JournalView
        from utils import journal_state
        panel = JournalView(42, await journal_state.snapshot(42), page='adventure')
        self.addCleanup(panel.stop)
        self.assertEqual({option.label for option in panel.path_select.options[:-1]},
                         {definitions[key]['name'] for key in offered})
        self.assertLessEqual(panel.content_length(), 4000)
        self.assertLessEqual(panel.total_children_count, 40)
        self.assertTrue(all(len(option.value) <= 100 for option in panel.path_select.options))
        with self.assertRaises(ValueError):
            await expeditions.choose(42, trip['id'], trip['version'], 'material:dragon_scale')
        selected = offered[-1]
        prior = deepcopy(trip)
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'material:' + selected)
        self.assertEqual(trip['payload']['material'], selected)
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertEqual(trip['payload']['page'], next_page)
        self.assertNotIn('material_next_page', trip['payload'])
        self.assertEqual(trip['payload']['history'][-1]['choice'], 'material:' + selected)
        self.assertIn(definitions[selected]['name'], trip['payload']['history'][-1]['outcome'])
        for stale in (prior, trip):
            with self.assertRaises(ValueError):
                await expeditions.choose(42, stale['id'], stale['version'], 'material:' + selected)
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'), [dict(material_id=selected, quantity=1)])
        await expeditions.report(trip)
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'), [dict(material_id=selected, quantity=1)])

    async def test_returning_from_material_picker_keeps_original_and_does_not_count_another_step(self):
        trip = await self.material_ready()
        original = trip['payload']['material']
        trip = await self.pick(trip)
        self.assertIn('return_early', expeditions.options(trip))
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'), [dict(material_id=original, quantity=1)])

    async def test_final_material_picker_restores_homeward_controls_without_an_extra_stop(self):
        trip = await self.material_ready()
        for _ in range(4):
            trip = await self.pick(trip)
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'material:' + trip['payload']['offered'][0])
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['completed'], 5)
        self.assertEqual(trip['payload']['journey_status'], 'material')
        self.assertEqual(trip['payload']['material_next_status'], 'homeward')
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'material:' + trip['payload']['offered'][-1])
        self.assertEqual(trip['payload']['journey_status'], 'homeward')
        self.assertEqual(trip['payload']['completed'], 5)
        self.assertIn('return', expeditions.options(trip))
        self.assertNotIn('return_early', expeditions.options(trip))
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
        self.assertEqual(sum(row['quantity'] for row in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_singleton_material_has_honest_bounded_bonus_instead_of_noop_picker(self):
        trip = await self.material_ready(rolled='spirit_crystal')
        p = trip['payload']
        self.assertEqual(p['offered'], ['spirit_crystal'])
        self.assertIn('Bundle bonus +2%', p['page']['choices'][0]['description'])
        p['bonus'] = 24
        journeys._show(p, p['current_root'], p['current_node'])
        self.assertIn('Bundle bonus +1%', p['page']['choices'][0]['description'])
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(p), trip['id']))
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['bonus'], 25)
        self.assertNotIn('material_next_page', trip['payload'])
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertIn('Bundle bonus +0%', trip['payload']['page']['choices'][0]['description'])
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['bonus'], 25)
        self.assertEqual(trip['payload']['completed'], 2)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_battle_material_picker_is_deferred_until_victory_without_extra_progress(self):
        trip = await self.material_ready(battle=True)
        for member in trip['payload']['prepared_party']:
            member.update(attack=5000, base_attack=5000, speed=10000, base_speed=10000)
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(trip['payload']), trip['id']))
        trip = await self.pick(trip, 1)
        self.assertNotIn('material_next_page', trip['payload'])
        self.assertEqual(trip['payload']['completed'], 0)
        for _ in range(30):
            if trip['status'] != 'battle':
                break
            state = trip['payload']['combat']
            action = 'next' if state['status'] == 'between' else trial_combat.legal_actions(state)[0]['choice']
            trip = await journey_service.battle_action(42, trip['id'], trip['version'], action)
        self.assertEqual(trip['status'], 'active')
        self.assertEqual(trip['payload']['page']['kind'], 'material')
        self.assertEqual(trip['payload']['completed'], 1)
        selected = trip['payload']['offered'][-1]
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'material:' + selected)
        self.assertEqual(trip['payload']['completed'], 1)
        self.assertEqual(trip['payload']['material'], selected)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
