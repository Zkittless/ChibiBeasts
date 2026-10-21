"""Authored pages persist without rerolls, duplicate stories or battle payouts."""
from copy import deepcopy
import json
import unittest

import aiosqlite

from tests.test_adventure_content import choice, document, root
from utils import journeys


PARTY = [dict(id=7, beast_id='twine', name='Twine', type='nature')]


class Journeys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(':memory:')
        await self.db.executescript('''
            CREATE TABLE adventure_progress(user_id,encounter_id,node_id,completed,snapshot,updated_at,
                PRIMARY KEY(user_id,encounter_id));
            CREATE TABLE adventure_nodes(user_id,node_id,resolved,PRIMARY KEY(user_id,node_id));
            CREATE TABLE adventure_flags(user_id,flag,PRIMARY KEY(user_id,flag));
            CREATE TABLE adventure_recent(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id,biome,encounter_id);
        ''')

    async def asyncTearDown(self):
        await self.db.close()

    async def begin(self, content, seed=73, uid=42, biome='woods'):
        payload = {}
        await journeys.begin(self.db, uid, payload, biome, seed, PARTY, content=content)
        return dict(user_id=uid, payload=payload, status='active')

    async def pick(self, trip, index=0):
        value = trip['payload']['page']['choices'][index]['value']
        return await journeys.resolve(self.db, trip, value)

    async def test_seed_pages_and_read_presentations_are_frozen_and_json_safe(self):
        content = document(*(root(f'trail{i}') for i in range(8)))
        first = await self.begin(content)
        second = await self.begin(content, uid=43)
        self.assertEqual(first['payload']['root_order'], second['payload']['root_order'])
        self.assertEqual(first['payload']['page'], second['payload']['page'])
        before = deepcopy(first)
        shown = journeys.presentation(first)
        shown['choices'][0]['label'] = 'Local mutation'
        self.assertEqual(first, before)
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertNotIn('hp', first['payload']['party'][0])
        other = await self.begin(content, seed=74, uid=44)
        self.assertNotEqual(first['payload']['root_order'], other['payload']['root_order'])

    async def test_unfinished_chain_resumes_frozen_next_node_after_catalog_changes(self):
        content = document(root(chain=3))
        trip = await self.begin(content)
        await self.pick(trip)
        next_page = deepcopy(trip['payload']['page'])
        changed = document(root('newly_added'))
        resumed = await self.begin(changed, seed=999)
        self.assertEqual(resumed['payload']['page'], next_page)
        self.assertEqual(resumed['payload']['current_node'], 'woods.grove.scene1')
        await self.pick(resumed)
        await self.pick(resumed)
        saved = await journeys.world(self.db, 42, 'woods')
        self.assertTrue(saved['progress']['woods.grove']['completed'])
        final = await self.begin(content)
        # The newly added location was offered after finishing the old chain,
        # so its pending scene also survives its later removal from the file.
        self.assertEqual(final['payload']['current_root'], 'woods.newly_added')
        self.assertNotIn('woods.grove', final['payload']['visited_roots'])

    async def test_durable_progress_is_transactional_and_namespace_is_exact(self):
        await self.begin(document(root(biome='woods_deep')), biome='woods_deep')
        await self.db.commit()
        trip = await self.begin(document(root()))
        await self.db.commit()
        await self.pick(trip)
        await self.db.rollback()
        saved = await journeys.world(self.db, 42, 'woods')
        self.assertEqual(list(saved['progress']), ['woods.grove'])
        self.assertFalse(saved['progress']['woods.grove']['completed'])
        self.assertFalse(saved['nodes']['woods.grove.scene0'])
        self.assertTrue((await journeys.world(self.db, 42, 'woods_deep'))['progress'])

    async def test_battle_failure_and_retreat_leave_unresolved_scene_without_rewards_or_flags(self):
        location = root(chain=3)
        first = location['nodes'][location['entry']]
        first['choices'][0] = choice('challenge', {'battle': 'pack2', 'bonus': 5, 'heal': 10,
                                                'set_flags': ['woods.proof']}, 'woods.grove.scene1')
        trip = await self.begin(document(location))
        intent = await self.pick(trip)
        p = trip['payload']
        self.assertEqual(intent, dict(battle=True, battle_kind='pack2', status='battle'))
        self.assertEqual((p['bonus'], p['recovery_used'], p['completed'], p['world_flags']), (0, 0, 0, []))
        self.assertEqual(p['history'], [])
        # Returning during combat does not call finish_battle: the durable
        # unresolved graph must already be enough to resume on another visit.
        abandoned = await self.begin(document(root('replacement')))
        self.assertEqual(abandoned['payload']['current_node'], location['entry'])
        result = await journeys.finish_battle(self.db, trip, False)
        self.assertEqual(result, dict(status='active'))
        saved = await journeys.world(self.db, 42, 'woods')
        self.assertFalse(saved['progress'][location['id']]['completed'])
        self.assertEqual(saved['progress'][location['id']]['node_id'], location['entry'])
        self.assertFalse(saved['nodes'][location['entry']])
        self.assertEqual(saved['flags'], [])
        self.assertEqual(p['completed'], 0)
        with self.assertRaises(ValueError):
            await journeys.finish_battle(self.db, trip, True)

    async def test_battle_victory_applies_deferred_effects_once_then_advances(self):
        location = root(chain=3)
        location['nodes'][location['entry']]['choices'][0] = choice(
            'challenge', {'battle': 'single', 'attack_boon': 10, 'bonus': 5, 'set_flags': ['woods.proof']},
            'woods.grove.scene1')
        trip = await self.begin(document(location))
        await self.pick(trip)
        result = await journeys.finish_battle(self.db, trip, True)
        self.assertEqual(result, dict(attack_boon=10, bonus=5, set_flags=['woods.proof'], status='active'))
        p = trip['payload']
        self.assertEqual((p['bonus'], p['boons']['attack'], p['completed']), (5, 10, 1))
        self.assertEqual(p['current_node'], 'woods.grove.scene1')
        self.assertEqual((await journeys.world(self.db, 42, 'woods'))['flags'], ['woods.proof'])
        self.assertEqual(len(p['history']), 1)
        with self.assertRaises(ValueError):
            await journeys.finish_battle(self.db, trip, True)

    async def test_flags_prioritize_newly_unlocked_root_and_completed_stories_never_restart(self):
        first = root('key', effects={'set_flags': ['woods.open']})
        unlocked = root('door')
        unlocked['requires_flags'] = ['woods.open']
        trip = await self.begin(document(first, unlocked))
        self.assertEqual(trip['payload']['current_root'], first['id'])
        await self.pick(trip)
        self.assertEqual(trip['payload']['current_root'], unlocked['id'])
        await self.pick(trip)
        next_trip = await self.begin(document(first, unlocked), seed=42)
        self.assertEqual(next_trip['payload']['visited_roots'], [])
        self.assertTrue(next_trip['payload']['exhausted'])

    async def test_repeats_are_explicit_unique_and_recent_window_shortens_exhausted_run(self):
        content = document(*(root(f'wilderness{i}', repeatable=True) for i in range(6)))
        trip = await self.begin(content)
        while trip['payload']['journey_status'] != 'homeward':
            self.assertTrue(trip['payload']['page']['repeatable'])
            self.assertTrue(trip['payload']['page']['title'].startswith('Familiar territory'))
            await self.pick(trip)
        first_ids = trip['payload']['visited_roots']
        self.assertEqual(len(first_ids), len(set(first_ids)))
        self.assertEqual(len(first_ids), 5)
        saved = await journeys.world(self.db, 42, 'woods')
        self.assertEqual(saved['recent'], first_ids[-3:])
        repeated = await self.begin(content)
        while repeated['payload']['journey_status'] != 'homeward':
            await self.pick(repeated)
        p = repeated['payload']
        self.assertFalse(set(p['visited_roots']).intersection(saved['recent']))
        self.assertEqual((p['completed'], p['total'], p['exhausted']), (3, 3, True))

    async def test_budget_limits_and_study_once_are_reflected_in_saved_controls(self):
        location = root(chain=3, effects={'heal': 10, 'attack_boon': 10, 'defense_boon': 10,
                                        'bonus': 5, 'bond': 1, 'study': 1})
        trip = await self.begin(document(location))
        await self.pick(trip)
        await self.pick(trip)
        self.assertIn('Run limit:', trip['payload']['page']['choices'][0]['description'])
        bounded = await self.pick(trip)
        self.assertEqual((bounded['heal'], bounded['attack_boon'], bounded['defense_boon'], bounded['study']), (0, 0, 0, 0))
        p = trip['payload']
        self.assertEqual((p['recovery_used'], p['boons'], p['bonus']), (20, dict(attack=20, defense=20), 20))
        # Reinitializing a reused dict cannot flush stale trainer flags.
        p['world_dirty']['set_flags'].append('woods.stale')
        journeys.initialize(p, 'woods', 73, PARTY, content=document(root()))
        self.assertEqual(p['world_dirty']['set_flags'], [])

    async def test_unoffered_choice_rejected_and_homeward_egg_is_one_saved_replacement(self):
        trip = await self.begin(document(root()))
        with self.assertRaises(ValueError):
            await journeys.resolve(self.db, trip, 'scene:woods.unknown:invented')
        self.assertNotIn('return_egg', journeys.options(trip))
        trip['payload']['egg_offer'] = dict(id='verdant_egg', name='Verdant Egg')
        await self.pick(trip)
        before = deepcopy(trip['payload'])
        self.assertIn('return_egg', journeys.options(trip))
        egg = [value for value in journeys.presentation(trip)['choices'] if value['value'] == 'return_egg']
        self.assertEqual(len(egg), 1)
        self.assertIn('instead', egg[0]['description'])
        self.assertEqual(before, trip['payload'])

    async def test_permanent_long_ids_fit_discord_control_value_budget(self):
        location = root('a_very_long_permanent_authored_location_name')
        node = location['nodes'][location['entry']]
        node['choices'][0]['id'] = location['entry'] + '_a_long_choice_identifier'
        trip = await self.begin(document(location))
        value = trip['payload']['page']['choices'][0]['value']
        self.assertLessEqual(len('f' * 32 + ':1000000:' + value), 100)
        self.assertEqual(trip['payload']['page']['choices'][0]['id'], node['choices'][0]['id'])
        await self.pick(trip)
        self.assertEqual(trip['payload']['history'][0]['choice'], node['choices'][0]['id'])


if __name__ == '__main__':
    unittest.main()
