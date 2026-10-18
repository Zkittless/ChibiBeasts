"""Regression checks for the offline audit's fidelity, not invented outcomes."""
from contextlib import asynccontextmanager
from copy import deepcopy
import random
import unittest
from unittest.mock import AsyncMock, patch

from scripts.audit_progression import (BEASTS, GEAR, Journey, band_matrix, battle_rewards,
                                       build_reachability, companion_state, destination_for,
                                       enemy_state, journey, resolve_battle, training_destination)
from cogs.battle import build_pve_beast_state
from utils import db as storage
from utils.duels import _duel
from utils.habitats import BIOMES, encounter_level, material_pool
from utils.economy_balance import recipe_level
from utils.progression import companion_growth, encounter_beast_xp


class AuditArithmetic(unittest.TestCase):
    def test_all_starters_reach_awakening_in_several_real_battle_days(self):
        with patch.object(storage, 'connect', side_effect=AssertionError('Audit touched DB')):
            for starter in ('prismite','twine','gloop','barkley'):
                for profile, upper_days in [('casual',9), ('active',6)]:
                    for seed in (67,206):
                        model = journey(starter, profile, seed, max_days=upper_days,
                                        destination_policy=training_destination)
                        self.assertIsNotNone(model.awakening, (starter,profile,seed))
                        self.assertGreater(model.awakening['day'], 2)
                        self.assertLessEqual(model.awakening['day'], upper_days)
                        self.assertGreaterEqual(model.awakening['trainer_level'], 8)
                        self.assertGreaterEqual(model.awakening['bond'], 40)
                        self.assertLessEqual(model.awakening['max_hp'], 220)
                        self.assertEqual(model.first_ten['species'], starter)
                        self.assertLess(model.first_ten['level'], 15)
                        self.assertLess(model.first_ten['hp'], 160)
                        self.assertGreater(model.counts['clean_wins'], 10)

    def test_training_policy_is_safe_until_awakening_and_flags_unreachable_gear(self):
        model = Journey('twine','casual',7,level=7,trainer=8)
        self.assertEqual(training_destination(model), 'woods')
        model.level = 8
        self.assertEqual(training_destination(model), 'woods_deep')
        model.level = 15
        self.assertEqual(training_destination(model), 'woods_deep')
        model.species = 'timestitch'
        self.assertEqual(training_destination(model), 'ember')
        self.assertFalse(build_reachability(['dragon_lord_aegis'],10)['valid'])
        self.assertTrue(build_reachability(['dragon_lord_aegis','iron_dust_chip'],30)['valid'])

    def test_journey_supplies_current_level_to_production_zone_sampler(self):
        with patch('scripts.audit_progression.encounter_level', wraps=encounter_level) as sampler:
            journey('twine', 'casual', 1201, max_days=1)
        self.assertEqual(sampler.call_count, 8)
        self.assertTrue(all(len(call.args) == 2 and 1 <= call.args[1] <= 50
                            for call in sampler.call_args_list))

    def test_checkpoint_gear_materials_are_reachable_at_reported_trainer_gate(self):
        for row in band_matrix(range(1)):
            sources = {material for key, region in BIOMES.items()
                       if region['min_level'] <= row['trainer_level']
                       for material in material_pool({'id':'','type':''}, key)}
            for item in row['gear']:
                self.assertLessEqual(recipe_level(GEAR[item]), row['trainer_level'])
                self.assertTrue(set(GEAR[item]['recipe']).issubset(sources), (row, item))

    def test_cached_enemies_match_production_and_never_share_mutations(self):
        for species, level in [('slime',3), ('hellhound',12), ('chronos',50)]:
            actual = enemy_state(species, level)
            expected = build_pve_beast_state(BEASTS[species], level)
            self.assertEqual(actual, expected)
            actual['hp'] = 0
            actual['status'] = 'burn'
            self.assertEqual(enemy_state(species, level), expected)

    def test_production_ai_guard_and_charge_paths_are_used(self):
        actor = companion_state('twine',15)
        enemy = companion_state('barkley',15)
        enemy['focus'] = 2
        random.seed(611)
        result = resolve_battle([actor, enemy])
        self.assertGreater(result['metrics']['guards'], 0)
        actor = companion_state('timestitch',15,charges=3)
        actor['mana'] = actor['max_mana']
        enemy = companion_state('rootwarden',15)
        result = resolve_battle([actor, enemy])
        self.assertGreater(result['metrics']['ultimates'], 0)

    def test_rows_use_canonical_storage_growth_for_evolved_forms(self):
        for species in ('prismara', 'timestitch', 'nebuloom', 'rootwarden'):
            for level in (15, 20, 30, 40, 50):
                state = companion_state(species, level)
                growth = companion_growth(species, level, BEASTS)
                self.assertEqual(state['max_hp'], BEASTS[species]['base_stats']['hp']+growth['hp'])
                self.assertEqual(state['max_mana'], BEASTS[species]['base_stats']['mana']+growth['mana'])

    def test_rewards_depend_on_actual_clean_ko(self):
        actor = companion_state('twine', 1)
        enemy = companion_state('dragon', 50)
        random.seed(721)
        result = resolve_battle([actor, enemy])
        self.assertFalse(result['clean_win'])
        self.assertEqual(result['winner'], 1)
        reduced, _ = battle_rewards(1, 1, 50, 'boss', result)
        self.assertEqual(reduced, encounter_beast_xp(1, 50, difficulty='boss', effort=.25))
        self.assertGreater(enemy['hp'], 0)
        actor = companion_state('timestitch', 15, charges=3)
        enemy = companion_state('slime', 1)
        result = resolve_battle([actor, enemy])
        self.assertTrue(result['clean_win'])
        self.assertEqual(enemy['hp'], 0)

    def test_standing_timeout_is_never_full_reward(self):
        states = [companion_state('barkley', 1) for _ in range(2)]
        for state in states:
            state['hp'] = state['max_hp'] = 100000
        random.seed(829)
        result = resolve_battle(states)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['clean_win'])
        self.assertTrue(all(state['hp'] > 0 for state in states))
        reward, _ = battle_rewards(1, 1, 1, 'normal', result)
        self.assertLess(reward, encounter_beast_xp(1, 1)/2)

    def test_destinations_obey_fixed_band_and_trainer_gate(self):
        for level in range(1, 51):
            for trainer in range(1, 51):
                destination = destination_for(level, trainer)
                self.assertLessEqual(BIOMES[destination]['min_level'], trainer)
                self.assertLessEqual(BIOMES[destination]['level_range'][0], level+1)

    def test_journeys_are_reproducible_and_never_open_database(self):
        with patch.object(storage, 'connect', side_effect=AssertionError('Audit touched DB')):
            a = journey('twine', 'casual', 932, max_days=2)
            b = journey('twine', 'casual', 932, max_days=2)
        self.assertEqual(a.first_ten, b.first_ten)
        self.assertEqual(a.fights, b.fights)
        self.assertEqual(a.counts['explore'], 12)
        self.assertEqual(a.counts['challenge'], 4)
        self.assertIsNotNone(a.first_ten)
        self.assertEqual(a.first_ten['species'], 'twine')
        self.assertLess(a.first_ten['hp'], 400)
        self.assertLess(a.first_ten['level'], 15)

    def test_awakening_does_not_ignore_trainer_or_bond_gate(self):
        model = Journey('twine', 'casual', 7, level=15)
        model.try_awakening(1)
        self.assertIsNone(model.awakening)
        model.trainer = 8
        model.try_awakening(1)
        self.assertIsNone(model.awakening)
        model.bond = 40
        model.try_awakening(3)
        self.assertEqual(model.species, 'timestitch')
        self.assertEqual(model.awakening['day'], 3)
        self.assertEqual(model.awakening['max_hp'], 204)


class CombatLoopParity(unittest.IsolatedAsyncioTestCase):
    async def test_offline_loop_matches_production_duel_with_same_seed(self):
        # Both combatants are AI so the real runner needs no Discord input.
        # Its final empty commit is replaced by an in-memory object, never SQLite.
        @asynccontextmanager
        async def fake_connect():
            yield AsyncMock()

        matchups = [('twine','fairy',1), ('gloop','hellhound',8),
                    ('prismara','unicorn',20), ('rootwarden','phoenix',30),
                    ('timestitch','chronos',50), ('nebuloom','kelpie',15)]
        with patch('utils.duels.BattlePresentation', return_value=None), patch.object(storage, 'connect', fake_connect):
            for seed in range(4):
                for player, enemy, level in matchups:
                    states = [companion_state(player, level, bond=40, charges=seed),
                              companion_state(enemy, level)]
                    expected_states = deepcopy(states)
                    random.seed(f'parity:{seed}:{player}')
                    expected = resolve_battle(expected_states, 'optimal')
                    random.seed(f'parity:{seed}:{player}')
                    winner, actual_states, log, timed_out = await _duel(
                        None, states, [None, None], 'Offline parity', 'optimal', None, 'woods')
                    self.assertEqual(winner, expected['winner'], (seed, player))
                    self.assertEqual(timed_out, expected['timed_out'], (seed, player))
                    self.assertEqual(log, expected['log'], (seed, player))
                    for actual, wanted in zip(actual_states, expected_states):
                        for field in ('hp','max_hp','mana','max_mana','ultimate_charges','status',
                                      'attack','defense','speed','action_count'):
                            self.assertEqual(actual[field], wanted[field], (seed, player, field))
