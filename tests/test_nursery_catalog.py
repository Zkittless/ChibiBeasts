"""Nursery promises: exact odds, meaningful families and no release/research loop."""
from itertools import product
import random
import unittest
from unittest.mock import patch

from utils import nursery_catalog as nursery
from utils.economy_balance import INCUBATION_LEVELS, INCUBATION_PRICES


class NurseryCatalogTests(unittest.TestCase):
    def test_all_legacy_inventory_ids_and_price_anchors_survive(self):
        old_instant = {'common_egg': (200, 1), 'rare_egg': (1500, 12),
                       'celestial_egg': (8000, 30), 'abyssal_egg': (25000, 40)}
        old_timed = {'sprout_pod', 'pebble_shell', 'soot_hatchling', 'dewdrop_bulb',
                     'gale_nest', 'cavern_core', 'prism_sphere', 'glow_spore', 'eclipse_pebble',
                     'volcanic_core', 'nimbus_cloud', 'monolith_relic', 'abyssal_trench_orb',
                     'dragon_hoard_scale', 'glacial_monolith', 'genesis_matrix',
                     'constellation_spool', 'singularity_core', 'world_tree_seed'}
        self.assertEqual(set(nursery.incubation_definitions()), old_timed)
        for egg_id, (price, level) in old_instant.items():
            egg = nursery.definition(egg_id)
            self.assertEqual((egg['price'], egg['min_level']), (price, level))
        for egg_id in old_timed - {'world_tree_seed'}:
            egg = nursery.definition(egg_id)
            self.assertEqual(egg['price'], INCUBATION_PRICES[egg['grade']])
            self.assertEqual(egg['min_level'], INCUBATION_LEVELS[egg['grade']])

    def test_instant_quality_floors_and_midgrade_unlocks(self):
        eggs = nursery.instant_definitions()
        self.assertEqual([e['min_level'] for e in eggs.values()], [1, 6, 12, 20, 30, 40])
        self.assertEqual([e['grade'] for e in eggs.values()], list(nursery.RARITIES[:-1]))
        self.assertEqual(eggs['wildheart_egg']['price'], 700)
        self.assertEqual(eggs['resonant_egg']['price'], 4000)
        self.assertEqual(eggs['rare_egg']['pool'], {'rare': .8, 'epic': .2})
        self.assertEqual(eggs['abyssal_egg']['pool'], {'divine': .99, 'altered_divine': .01})

    def test_all_bonus_distributions_are_real_normalized_and_respect_the_grade(self):
        for egg_id, egg in nursery.definitions().items():
            for star, fortune, observatory in product((False, True), repeat=3):
                perks = [{'perk_id': key, 'equipped': 1} for key, active in
                         (('stardust_touch', star), ('glimmering_fortune', fortune)) if active]
                pool = nursery.rarity_pool(egg_id, perks, {'celestial_observatory': observatory})
                self.assertAlmostEqual(sum(pool.values()), 1, msg=egg_id)
                self.assertTrue(all(p > 0 for p in pool.values()), egg_id)
                for rarity in pool:
                    self.assertGreaterEqual(nursery.RARITIES.index(rarity), nursery.RARITIES.index(egg['grade']))
                    self.assertTrue(nursery.species_pool(egg_id, rarity), (egg_id, rarity))

    def test_fortune_is_a_single_displayed_jackpot_and_cannot_lower_top_grade(self):
        fortune = [{'perk_id': 'glimmering_fortune', 'equipped': 1}]
        pool = nursery.rarity_pool('common_egg', fortune)
        self.assertAlmostEqual(pool['divine'], .0005)
        self.assertAlmostEqual(pool['common'], .75 * .9995)
        self.assertEqual(nursery.rarity_pool('abyssal_egg', fortune), {'divine': .99, 'altered_divine': .01})
        self.assertEqual(nursery.rarity_pool('genesis_matrix', fortune), {'divine': 1})
        # A water/ice family without a Divine never silently adds cosmic beasts.
        self.assertNotIn('divine', nursery.rarity_pool('dewdrop_bulb', fortune))
        self.assertAlmostEqual(nursery.rarity_pool('abyssal_trench_orb', fortune)['altered_divine'], .02)

    def test_unequipped_bonuses_do_not_change_odds(self):
        perks = [{'perk_id': 'stardust_touch', 'equipped': 0},
                 {'perk_id': 'glimmering_fortune', 'equipped': 0}]
        self.assertEqual(nursery.rarity_pool('rare_egg', perks), nursery.rarity_pool('rare_egg'))

    def test_every_candidate_excludes_starters_evolutions_raids_and_dev(self):
        for egg_id in nursery.definitions():
            for rarity in nursery.RARITIES:
                for beast in nursery.species_pool(egg_id, rarity):
                    self.assertNotIn(beast['id'], nursery.STARTER_IDS)
                    self.assertFalse(beast.get('starter'))
                    self.assertFalse(beast.get('evolution_only'))
                    self.assertTrue(beast.get('wild_encounter', True))
                    self.assertGreater(beast['catch_rate'], 0)
                    self.assertEqual(beast['rarity'], rarity)

    def test_themed_eggs_have_distinct_actual_rosters(self):
        roster_sets = []
        for egg_id, egg in nursery.incubation_definitions().items():
            ids = frozenset(beast['id'] for r in egg['pool'] for beast in nursery.species_pool(egg_id, r))
            self.assertTrue(ids, egg_id)
            self.assertNotIn(ids, roster_sets, egg_id)
            roster_sets.append(ids)
            for rarity in egg['pool']:
                for beast in nursery.species_pool(egg_id, rarity):
                    if egg.get('types'):
                        self.assertIn(beast['type'], egg['types'])
                    if egg.get('candidate_ids'):
                        self.assertIn(beast['id'], egg['candidate_ids'][rarity])

    def test_curated_divine_eggs_offer_all_three_exact_targets(self):
        for egg_id, expected in (
            ('genesis_matrix', {'genesis', 'terminus', 'paradox'}),
            ('constellation_spool', {'zodiac', 'karma', 'horizon'}),
            ('singularity_core', {'supernova', 'nebula', 'abyss'}),
            ('world_tree_seed', {'atlas', 'asgard', 'nirvana'}),
        ):
            self.assertEqual({b['id'] for b in nursery.species_pool(egg_id, 'divine')}, expected)

    def test_missing_family_tier_is_disclosed_and_never_falls_back_off_theme(self):
        changed = nursery.definition('sprout_pod')
        changed['types'] = ('earth',)
        with patch.dict(nursery._DEFINITIONS, {'sprout_pod': changed}):
            egg = nursery.definition('sprout_pod')
            self.assertNotIn('rare', egg['pool'])
            self.assertTrue(egg['pool_notes'])
            self.assertEqual(nursery.species_pool('sprout_pod', 'rare'), [])
            self.assertAlmostEqual(sum(egg['pool'].values()), 1)

    def test_empty_family_blocks_hatching_instead_of_spending_an_egg(self):
        changed = nursery.definition('sprout_pod')
        changed['types'] = ('not-a-game-element',)
        with patch.dict(nursery._DEFINITIONS, {'sprout_pod': changed}):
            with self.assertRaisesRegex(ValueError, 'no eligible hatchlings'):
                nursery.rarity_pool('sprout_pod')

    def test_catalog_and_beast_results_are_private_copies(self):
        eggs = nursery.definitions()
        eggs['common_egg']['pool']['common'] = 0
        eggs['genesis_matrix']['candidate_ids']['divine'] = ()
        self.assertEqual(nursery.definition('common_egg')['pool']['common'], .75)
        self.assertEqual(len(nursery.species_pool('genesis_matrix', 'divine')), 3)
        beast = nursery.species_pool('common_egg', 'common')[0]
        beast['base_stats']['hp'] = -1
        self.assertGreater(nursery.species_pool('common_egg', 'common')[0]['base_stats']['hp'], 0)
        for bad in ('missing', None, ['bad']):
            with self.assertRaises(ValueError):
                nursery.definition(bad)

    def test_hatch_levels_follow_grade_and_research_cannot_select_jackpots(self):
        rng = random.Random(707)
        keys = set()
        for egg_id, egg in nursery.definitions().items():
            levels = {nursery.hatch_level(egg_id, rng) for _ in range(20)}
            self.assertGreaterEqual(min(levels), egg['level_range'][0])
            self.assertLessEqual(max(levels), egg['level_range'][1])
            self.assertTrue(all(b['rarity'] == egg['grade'] for b in nursery.research_pool(egg_id)))
            key = nursery.research_key(egg_id)
            self.assertEqual(key, egg['grade'] + ':' + egg['family'])
            self.assertNotIn(key, keys)
            keys.add(key)
        self.assertFalse(nursery.definition('world_tree_seed')['shop'])
        self.assertEqual(nursery.definition('world_tree_seed')['level_range'], (15, 18))
        self.assertEqual(nursery.RESEARCH_COST, 8)
        self.assertEqual(nursery.RESEARCH_MAX, 16)

    def test_rarity_roll_uses_exact_distribution_boundaries(self):
        class Roll:
            def __init__(self, roll):
                self.roll = roll
            def random(self):
                return self.roll
        self.assertEqual(nursery.choose_rarity('rare_egg', Roll(0)), 'rare')
        self.assertEqual(nursery.choose_rarity('rare_egg', Roll(.799999)), 'rare')
        self.assertEqual(nursery.choose_rarity('rare_egg', Roll(.8)), 'epic')
        self.assertEqual(nursery.choose_rarity('abyssal_egg', Roll(.99)), 'altered_divine')

    def test_full_production_odds_audit_has_no_profitable_release_or_research_cycle(self):
        from scripts.audit_nursery import build_report
        report = build_report()
        self.assertTrue(report['passed'], report['failures'])
        self.assertEqual(report['bonus_scenarios'], 24 * 16)
        self.assertLess(report['maximum_expected_net_gold_per_paid_hatch'], 0)
        self.assertLess(report['maximum_research_net_per_paid_hatch_upper_bound'], 0)
        self.assertTrue(all(row['hatch_max_below_regional_wild_max'] for row in report['level_comparisons']))


if __name__ == '__main__':
    unittest.main()
