"""Pure production tower rules, cursor, replay, resource and RNG regressions.

Optional audit: python -m tests.test_trial_combat --audit ../output/tower-trials/core-audit.json
"""
import asyncio
from collections import Counter
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import patch

from utils import combat, trial_combat as tower, trial_rules as rules
from utils.catalog import load_catalog
from utils.habitats import BIOMES, eligible


def fighter(species='twine', level=7, identity=1):
    state = tower.build_enemy(dict(species=species, level=level, kind='guardian', index=identity))
    state.update(id=identity, hp=state['max_hp'], companion_id=identity)
    return state


def payload():
    party = [fighter('twine', identity=1), fighter('gloop', identity=2), fighter('barkley', identity=3)]
    for member in party:
        member.update(speed=10000, base_speed=10000, attack=5000, base_attack=5000,
                      hp=10000, max_hp=10000)
    return tower.new_run(party, rules.generate_plan('woods', 73))


class TrialRules(unittest.TestCase):
    def test_ten_bands_counts_level_curve_and_scaled_cooldowns(self):
        self.assertEqual(list(rules.WAVE_COUNTS.values()), [5, 6, 7, 8, 9, 10, 11, 12, 13, 15])
        for destination, total in rules.WAVE_COUNTS.items():
            plan = rules.generate_plan(destination, 73)
            cfg = rules.config(destination)
            self.assertEqual(plan['total'], total)
            self.assertEqual(len(plan['waves']), total)
            low, high = cfg['level_range']
            levels = [w['level'] for w in plan['waves']]
            self.assertEqual(levels, sorted(levels))
            self.assertEqual((levels[0], levels[-1]), (low, high))
            self.assertAlmostEqual(cfg['multiplier'] * 3600 / cfg['cooldown_seconds'], 3)
            self.assertAlmostEqual(cfg['multiplier'] * 3600 / (cfg['cooldown_seconds'] * .8), 3.75)
            self.assertEqual(sum(w['kind'] == 'guardian' for w in plan['waves']), 1)
            self.assertEqual(sum(w['kind'] == 'miniboss' for w in plan['waves']), int(total >= 8))

    def test_saved_plan_deterministic_varied_and_json_safe(self):
        plan = rules.generate_plan('loom_deep', 73)
        self.assertEqual(plan, rules.generate_plan('loom_deep', 73))
        self.assertEqual(plan, json.loads(json.dumps(plan)))
        self.assertNotEqual([w['species'] for w in plan['waves']],
                            [w['species'] for w in rules.generate_plan('loom_deep', 74)['waves']])

    def test_thousand_plans_obey_actual_catalog_and_composition_budgets(self):
        catalog = load_catalog('beasts.json')['beasts']
        for destination in rules.WAVE_COUNTS:
            for seed in range(100):
                plan = rules.generate_plan(destination, seed)
                waves = plan['waves']
                counts = Counter(w['species'] for w in waves)
                self.assertLessEqual(max(counts.values()), 2)
                self.assertLessEqual(sum(w['categories']['control'] for w in waves), (len(waves) + 3) // 4)
                for wave in waves:
                    beast = catalog[wave['species']]
                    self.assertTrue(eligible(beast))
                    self.assertIn(beast['type'], BIOMES[destination]['types'])
                    self.assertGreater(BIOMES[destination]['weights'].get(beast['rarity'], 0), 0)
                    self.assertLessEqual(wave['power_after'], wave['power_cap'])
                    self.assertLessEqual(wave['profile_scaling'], 1)
                    self.assertEqual(rules.power(tower.build_enemy(wave)), wave['power_after'])
                for previous, current in zip(waves, waves[1:]):
                    self.assertNotEqual(previous['species'], current['species'])
                    for category in ('control', 'heal'):
                        self.assertFalse(previous['categories'][category] and current['categories'][category])
                if plan['total'] >= 8:
                    self.assertNotEqual(waves[plan['camp_wave'] - 1]['species'], waves[-1]['species'])

    def test_progress_earns_thirty_then_fifty_then_full_without_per_wave_awards(self):
        for total in rules.WAVE_COUNTS.values():
            midpoint = (total + 1) // 2
            self.assertEqual(rules.progress_fraction(0, total), 0)
            self.assertAlmostEqual(rules.progress_fraction(midpoint, total), .3)
            self.assertAlmostEqual(rules.progress_fraction(total - 1, total), .5)
            self.assertEqual(rules.progress_fraction(total, total), 1)
            amounts = [rules.progress_fraction(c, total) for c in range(total + 1)]
            self.assertEqual(amounts, sorted(amounts))
        with self.assertRaises(ValueError):
            rules.config('untrusted-path')
        with self.assertRaises(ValueError):
            rules.progress_fraction(6, 5)


class TrialCombat(unittest.TestCase):
    def test_smaller_onboarding_teams_use_same_wave_flow_without_missing_slots(self):
        for size in (1, 2):
            p = payload()
            p['party'] = p['party'][:size]
            for _ in range(100):
                if p['status'] == 'between':
                    tower.start_wave(p)
                elif p['status'] == 'fighting':
                    tower.act(p, (tower.active(p)['moves'][0], False))
                else:
                    break
            self.assertEqual(p['status'], 'won')
            self.assertEqual(len(p['party']), size)

    def test_saved_opponents_do_not_change_with_a_catalog_refresh(self):
        plan = rules.generate_plan('loom_deep', 73)
        wave = plan['waves'][0]
        expected = tower.build_enemy(wave)
        with patch('utils.trial_rules.profiled_state', side_effect=AssertionError('Saved template is authoritative')):
            actual = tower.build_enemy(json.loads(json.dumps(wave)))
        self.assertEqual(actual, expected)
        actual['hp'] = 1
        self.assertEqual(wave['enemy_template']['hp'], expected['hp'])

    def test_complete_run_roundtrips_without_db_rewards_or_live_registration(self):
        p = payload()
        original_states, original_parties = dict(combat.active_states), dict(combat.active_parties)
        with patch('utils.db.connect', side_effect=AssertionError('Pure core must not access DB')):
            for _ in range(100):
                if p['status'] == 'between':
                    frames = tower.start_wave(p)
                    self.assertEqual(frames[0]['action']['kind'], 'enter')
                elif p['status'] == 'fighting':
                    tower.act(p, (tower.active(p)['moves'][0], False))
                else:
                    break
                for event in p['events']:
                    before, action, after = tower.decode_event(event)
                    self.assertEqual(len(before), 2)
                    self.assertEqual(len(after), 2)
                    self.assertIsInstance(action.name, str)
                p = json.loads(json.dumps(p))
        self.assertEqual(p['status'], 'won')
        self.assertEqual(p['cleared'], 5)
        self.assertEqual(combat.active_states, original_states)
        self.assertEqual(combat.active_parties, original_parties)

    def test_saved_action_cursor_replays_identically_without_repeating_begin(self):
        p = payload()
        tower.start_wave(p)
        p['enemy'].update(hp=100000, max_hp=100000)
        self.assertEqual(tower.active(p)['action_count'], 1)
        saved = json.loads(json.dumps(p))
        choice = (tower.active(p)['moves'][2], False)
        frames = tower.act(p, choice)
        replay_frames = tower.act(saved, choice)
        self.assertEqual(frames, replay_frames)
        self.assertEqual(p, saved)
        self.assertEqual(tower.active(p)['action_count'], 2)
        self.assertEqual(p['phase'], 'action')
        self.assertTrue(p['awaiting'])

    def test_enemy_first_kill_forces_swap_and_ko_remains_out_next_wave(self):
        p = payload()
        p['party'][0].update(hp=1, defense=0, speed=1)
        p['party'][1].update(speed=1)
        # A very fast, strong enemy gets the first turn.
        with patch('utils.trial_combat.build_enemy', wraps=tower.build_enemy) as build:
            def powerful(wave):
                enemy = build._mock_wraps(wave)
                enemy.update(speed=100000, attack=100000, base_attack=100000)
                return enemy
            build.side_effect = powerful
            frames = tower.start_wave(p)
        self.assertEqual(p['party'][0]['hp'], 0)
        self.assertEqual(p['slot'], 1)
        self.assertTrue(any(e['action']['kind'] == 'swap' for e in frames))
        p['enemy']['hp'] = 1
        tower.act(p, (tower.active(p)['moves'][0], False))
        self.assertEqual(p['status'], 'between')
        tower.start_wave(p)
        self.assertEqual(p['party'][0]['hp'], 0)
        self.assertNotIn(['swap', 1], [a['choice'] for a in tower.legal_actions(p)])

    def test_bench_resources_conditions_meter_and_counters_carry(self):
        p = payload()
        p['party'][1].update(hp=321, mana=17, status='burn', status_turns=2, ultimate_charges=2, revive_used=True)
        expected = deepcopy(p['party'][1])
        tower.start_wave(p)
        p['meter'] = 2
        tower.act(p, (tower.active(p)['moves'][0], False))
        self.assertEqual(p['status'], 'between')
        self.assertEqual(p['meter'], 3)
        self.assertEqual(p['party'][1], expected)
        count = p['party'][0]['action_count']
        charges = p['party'][0]['ultimate_charges']
        tower.start_wave(p)
        self.assertEqual(p['party'][1], expected)
        self.assertEqual(p['meter'], 3)
        self.assertEqual(p['party'][0]['action_count'], count + 1)
        self.assertEqual(p['party'][0]['ultimate_charges'], charges)

    def test_member_opening_occurs_once_for_run_not_each_wave_or_swap(self):
        p = payload()
        for member in p['party']:
            member['speed_debuff_on_battle_start_chance'] = 100
        tower.start_wave(p)
        self.assertEqual(p['enemy']['status'], 'slow')
        tower.act(p, (tower.active(p)['moves'][0], False))
        tower.start_wave(p)
        self.assertIsNone(p['enemy']['status'])
        p['enemy'].update(hp=100000, max_hp=100000)
        tower.act(p, ('swap', 2))
        self.assertEqual(p['enemy']['status'], 'slow')
        p['enemy'].update(status=None, status_turns=0)
        tower.act(p, ('swap', 1))
        self.assertIsNone(p['enemy']['status'])
        self.assertEqual(p['entered'], [1, 2])

    def test_round_limit_is_loss_even_with_a_health_lead(self):
        p = payload()
        p['plan']['waves'][0]['round_limit'] = 1
        tower.start_wave(p)
        p['enemy'].update(hp=500, max_hp=1000, attack=1)
        tower.act(p, (tower.active(p)['moves'][2], False))
        self.assertEqual(p['status'], 'lost')
        self.assertEqual(p['cleared'], 0)

    def test_enemy_healing_and_hard_control_are_capped_per_wave(self):
        p = payload()
        tower.start_wave(p)
        p['enemy'] = tower._json(fighter('slime', identity=-1))
        p['enemy'].update(_trial_heals=0, _trial_controls=0, hp=1, mana=1000, max_mana=1000)
        tower.act(p, (tower.active(p)['moves'][2], False))
        self.assertEqual(p['enemy']['_trial_heals'], 1)
        p['enemy']['hp'] = 1
        tower.act(p, (tower.active(p)['moves'][2], False))
        self.assertEqual(p['enemy']['_trial_heals'], 1)
        p = payload()
        for wave in p['plan']['waves']:
            wave.update(species='glacius', level=7)
            wave.pop('enemy_template', None)
        for member in p['party']:
            member.update(speed=1, base_speed=1)
        tower.start_wave(p)
        self.assertEqual(p['enemy']['_trial_controls'], 1)
        self.assertTrue(p['awaiting'])
        for _ in range(2):
            p['enemy'].update(hp=100000, max_hp=100000)
            p['party'][p['slot']].update(status=None, status_turns=0)
            tower.act(p, (tower.active(p)['moves'][2], False))
        self.assertEqual(p['enemy']['_trial_controls'], 1)

    def test_invalid_choice_does_not_mutate_persisted_payload_or_rng(self):
        p = payload()
        tower.start_wave(p)
        old = deepcopy(p)
        for choice in [('swap', 9999), ('assist', 2), ('invented_move', False)]:
            with self.assertRaises(ValueError):
                tower.act(p, choice)
            self.assertEqual(p, old)
        with self.assertRaises(ValueError):
            tower.start_wave(p)
        self.assertEqual(p, old)

    def test_local_rng_does_not_advance_global_rng_or_leak_context(self):
        p = payload()
        before = random.getstate()
        tower.start_wave(p)
        tower.act(p, (tower.active(p)['moves'][0], False))
        self.assertEqual(random.getstate(), before)
        self.assertIs(combat.rng(), random)
        async def check():
            async def task(seed):
                rng = random.Random(seed)
                with combat.using_rng(rng):
                    await asyncio.sleep(0)
                    self.assertIs(combat.rng(), rng)
                    return combat.rng().random()
            return await asyncio.gather(task(1), task(2))
        self.assertEqual(asyncio.run(check()), [random.Random(1).random(), random.Random(2).random()])
        with patch('utils.combat.random.random', return_value=.123):
            self.assertEqual(combat.rng().random(), .123)


def audit(output):
    from scripts.audit_tower_trials import audit as run
    return run(output)


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--audit':
        audit(sys.argv[2])
    else:
        unittest.main()
