"""Distinct destinations keep their existing economy and saved-trip guarantees."""
import asyncio
import copy
import json
import random
import unittest
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import adventure_party, db as storage, expedition_events as events, expeditions as trips
from utils.events import record_event
from utils.habitats import (BASE_BIOMES, BIOMES, DESTINATION_ALIASES, biome_key,
                            destination_key, encounter_pool, material_pool)


NEW_NAMES = {
    'woods_deep': 'Moonveil Marsh', 'ember_deep': 'Sunforge Dunes',
    'glacial_deep': 'Stormglass Peaks', 'trenches_deep': 'Cinderreef Caldera',
    'loom_deep': 'Starfall Gardens',
}


def payload(destination, seed=14):
    p = dict(gold=60, exp=70, base_exp=70, level=50, offered=['enchanted_bark'],
             material='enchanted_bark', bond=0, happiness=0, notes=[])
    # A neutral element proves no signature requires a particular creature.
    events.initialize(p, destination, seed, [dict(id=1, beast_id='twine', type='neutral')])
    return p


class DistinctBiomeRules(unittest.TestCase):
    def test_names_change_without_changing_bands_gates_or_encounter_budgets(self):
        self.assertEqual([b['min_level'] for b in BIOMES.values()], [1, 3, 6, 9, 12, 16, 20, 25, 30, 38])
        self.assertEqual([b['level_range'] for b in BIOMES.values()], [(n, n + 4) for n in range(1, 50, 5)])
        expected = (
            {'common': .85, 'uncommon': .15}, {'common': .65, 'uncommon': .30, 'rare': .05},
            {'common': .20, 'uncommon': .65, 'rare': .15}, {'uncommon': .25, 'rare': .60, 'epic': .15},
            {'rare': .70, 'epic': .30}, {'rare': .35, 'epic': .60, 'legendary': .05},
            {'epic': .75, 'legendary': .25}, {'epic': .35, 'legendary': .65},
            {'rare': .05, 'epic': .70, 'divine': .25}, {'epic': .40, 'divine': .60},
        )
        self.assertEqual(tuple(b['weights'] for b in BIOMES.values()), expected)
        expected_types = (
            {'nature', 'earth', 'arcane', 'light', 'ice'},
            {'nature', 'earth', 'arcane', 'light', 'shadow', 'ice', 'wind', 'water'},
            {'fire', 'earth', 'shadow'}, {'fire', 'earth', 'shadow'},
            {'ice', 'wind'}, {'ice', 'wind'},
            {'water', 'shadow'}, {'water', 'shadow', 'fire'},
            {'cosmic', 'arcane', 'light'}, {'cosmic', 'arcane', 'light'},
        )
        self.assertEqual(tuple(b['types'] for b in BIOMES.values()), expected_types)
        for key, name in NEW_NAMES.items():
            self.assertIn(name, BIOMES[key]['name'])
            self.assertNotIn('Depths', BIOMES[key]['name'])
            for rarity in BIOMES[key]['weights']:
                self.assertTrue(encounter_pool(rarity, key), (key, rarity))
        self.assertEqual(set(BASE_BIOMES), {'woods', 'ember', 'glacial', 'trenches', 'loom'})

    def test_old_and_new_display_names_resolve_before_partial_matching(self):
        for alias, key in DESTINATION_ALIASES.items():
            self.assertEqual(destination_key(alias), key)
            self.assertEqual(destination_key(alias.upper()), key)
        for key, name in NEW_NAMES.items():
            self.assertEqual(destination_key(name), key)
            self.assertEqual(destination_key('The ' + name), key)
            self.assertEqual(destination_key(BIOMES[key]['name']), key)
            self.assertEqual(destination_key('  ' + BIOMES[key]['name'].upper() + '  '), key)
        self.assertEqual(destination_key('🌲 The Whispering Woods · Depths'), 'woods_deep')
        self.assertEqual(destination_key('Whispering Woods'), 'woods')
        self.assertEqual(destination_key('Moonveil'), 'woods_deep')
        self.assertEqual(destination_key(None), 'woods')

    def test_resources_and_nest_odds_remain_the_old_regional_contract(self):
        for key in NEW_NAMES:
            base = key.removesuffix('_deep')
            self.assertEqual(biome_key(key), base)
            self.assertEqual(set(events.DESTINATION_REGIONS[key]['ability']),
                             set(events.REGIONS[base]['ability']))
            self.assertEqual(material_pool({'id': '', 'type': ''}, key),
                             material_pool({'id': '', 'type': ''}, base))
            for seed in range(20):
                current = dict(seed=str(seed), level=50, base_gold=60)
                regional = copy.deepcopy(current)
                events.prepare_nest(current, key)
                events.prepare_nest(regional, base)
                self.assertEqual(current, regional)

    def test_ten_destinations_have_distinct_saved_flavour(self):
        pages = [payload(key)['page']['body'] for key in BIOMES]
        self.assertEqual(len(set(pages)), 10)
        for key in NEW_NAMES:
            p = payload(key)
            for event in ('creature', 'landmark', 'rescue', 'merchant', 'camp', 'discovery', 'deep'):
                events.page(p, key, event)
                current = copy.deepcopy(p['page'])
                legacy = payload(biome_key(key))
                events.page(legacy, biome_key(key), event)
                self.assertNotEqual(current['body'], legacy['page']['body'], (key, event))
                for _ in range(3):
                    shown = events.presentation(dict(payload=p, status='active'))
                    shown['choices'].clear()
                self.assertEqual(p['page'], current)

    def test_signature_choices_have_same_safe_and_risky_effects_as_old_crossings(self):
        for key in NEW_NAMES:
            for seed in range(4):
                original = payload(key, seed)
                events.resolve(original, key, 'shrine')
                events.resolve(original, key, 'repair')
                self.assertEqual(original['event'], 'obstacle')
                crossing = original['biome_crossing']
                self.assertTrue(crossing['clue'])
                self.assertEqual(len(original['page']['choices']), 2)
                for choice, old_choice in ((crossing['safe'], 'detour'), (crossing['risky'], 'cross')):
                    current = copy.deepcopy(original)
                    regional = copy.deepcopy(original)
                    regional.pop('biome_crossing')
                    effects = events.resolve(current, key, choice)
                    old_effects = events.resolve(regional, biome_key(key), old_choice)
                    self.assertEqual(effects, old_effects, (key, choice))
                    for field in ('completed', 'target_stops', 'bonus', 'bond', 'happiness',
                                  'material', 'flags', 'party_ids', 'participations'):
                        self.assertEqual(current[field], regional[field], (key, field))
                    self.assertEqual(current['history'][-1]['choice'], choice)
                saved = copy.deepcopy(original['page'])
                original['seed'] = 'a different seed must not alter a saved clue'
                events.page(original, key, 'obstacle')
                self.assertEqual(original['page'], saved)

    def test_starfall_memory_uses_the_teams_actual_earlier_route(self):
        for route, clue in (('gather', 'a bundle of leaves'), ('shrine', 'a ringing bell')):
            p = payload('loom_deep')
            p['route'] = route
            events.page(p, 'loom_deep', 'obstacle')
            self.assertIn(clue, p['page']['body'])

    def test_existing_trip_pages_and_followups_keep_legacy_choices(self):
        for key in NEW_NAMES:
            p = payload(key)
            p.pop('destination')
            p.pop('destination_version')
            events.resolve(p, key, 'shrine')
            events.resolve(p, key, 'repair')
            self.assertNotIn('biome_crossing', p)
            self.assertEqual([c['value'] for c in p['page']['choices']], ['detour', 'cross'])
            old = copy.deepcopy(p['page'])
            events.page(p, key, 'obstacle')
            self.assertEqual(p['page'], old)
            self.assertEqual(events.resolve(p, key, 'detour'), {})
            self.assertEqual(p['event'], 'homeward')


class DistinctBiomeTrips(DatabaseCase):
    async def ready(self, *, team=False):
        lead = await self.beast('twine', starter=True)
        await storage.update_player(42, level=50)
        if team:
            helper = await self.beast('barkley')
            await adventure_party.set_slot(42, helper, 1)
        return lead

    async def pick(self, trip, choice):
        return await trips.choose(42, trip['id'], trip['version'], choice)

    async def crossing(self, destination):
        trip = await trips.begin(42, destination, rng=random.Random(8))
        trip = await self.pick(trip, 'shrine')
        return await self.pick(trip, 'repair')

    async def test_full_destination_flavour_and_clue_survive_read_without_any_reroll(self):
        await self.ready()
        regional_events = []
        for destination in NEW_NAMES:
            await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,10000)')
            trip = await self.crossing(destination)
            self.assertEqual(trip['payload']['destination'], destination)
            self.assertEqual(trip['payload']['biome_crossing']['destination'], destination)
            frozen = copy.deepcopy(trip['payload'])
            with patch('utils.expedition_events._variant', side_effect=AssertionError('rerolled')):
                for _ in range(3):
                    restored = await trips.read(42, trip['id'])
                    trips.presentation(restored)
                    self.assertEqual(restored['payload'], frozen)
            trip = await self.pick(trip, frozen['biome_crossing']['safe'])
            self.assertEqual(trip['payload']['bonus'], 0)
            with patch('utils.events.record_event', wraps=record_event) as recorded:
                done = await self.pick(trip, 'return')
            regional_events.extend(call.kwargs['biome'] for call in recorded.call_args_list
                                   if call.args[1] == 'explore')
            self.assertEqual(done['status'], 'complete')
            self.assertTrue(done['payload']['material_granted'])
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 5)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(set(regional_events), {BIOMES[base]['name'] for base in BASE_BIOMES})

    async def test_risky_saved_choice_is_atomic_and_stale_click_cannot_charge_twice(self):
        lead = await self.ready()
        trip = await self.crossing('woods_deep')
        before = next(row for row in await storage.get_player_beasts(42) if row['id'] == lead)
        choice = trip['payload']['biome_crossing']['risky']
        outcomes = await asyncio.gather(self.pick(trip, choice), self.pick(trip, choice), return_exceptions=True)
        self.assertEqual(sum(isinstance(outcome, ValueError) for outcome in outcomes), 1)
        current = await trips.current(42)
        after = next(row for row in await storage.get_player_beasts(42) if row['id'] == lead)
        self.assertEqual(after['hp'], max(1, before['hp'] - max(1, before['max_hp'] * 3 // 100)))
        self.assertEqual(current['payload']['bonus'], 5)
        self.assertEqual(current['payload']['completed'], 3)
        self.assertEqual(sum(row['choice'] == choice for row in current['payload']['history']), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_existing_optional_helper_health_guard_and_safe_route_remain(self):
        await self.ready(team=True)
        trip = await self.crossing('woods_deep')
        helper = next(value for value in trips.options(trip) if value.startswith('help:'))
        helper_id = int(helper.split(':')[1])
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (helper_id,))
        with self.assertRaisesRegex(ValueError, 'needs recovery|safe detour'):
            await self.pick(trip, helper)
        trip = await self.pick(trip, trip['payload']['biome_crossing']['safe'])
        self.assertEqual(trip['payload']['completed'], 3)
        self.assertEqual(trip['payload']['bonus'], 0)
        self.assertEqual(next(row['hp'] for row in await storage.get_player_beasts(42)
                              if row['id'] == helper_id), 0)

    async def test_saved_old_depths_page_accepts_its_original_buttons(self):
        await self.ready()
        trip = await self.crossing('woods_deep')
        p = trip['payload']
        for field in ('destination', 'destination_version', 'biome_crossing'):
            p.pop(field)
        events.page(p, 'woods', 'obstacle')
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(p), trip['id']))
        old_page = copy.deepcopy(p['page'])
        with patch('utils.expedition_events._crossing', side_effect=AssertionError('rebuilt page')):
            restored = await trips.read(42, trip['id'])
            self.assertEqual(restored['payload']['page'], old_page)
        self.assertIn('detour', trips.options(restored))
        done = await self.pick(restored, 'detour')
        self.assertEqual(done['payload']['completed'], 3)


if __name__ == '__main__':
    unittest.main()
