"""Party encounter fairness, visible-state decisions and saved-rule compatibility."""
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import patch

from tests.test_trial_combat import fighter
from utils import combat, journey_combat as journey, pve_tactics, trial_combat, trial_rules


class PvETactics(unittest.TestCase):
    def pair(self, species='goblin'):
        actor, target = fighter(species, identity=1), fighter(identity=2)
        actor.update(mana=100, max_mana=100, ultimate_charges=0, attack=25, base_attack=25)
        target.update(hp=10000, max_hp=10000, mana=100, max_mana=100, ultimate_charges=3)
        return actor, target

    def test_defense_answers_visible_charged_attack_without_guard_loop(self):
        actor, target = self.pair()
        guard = actor['moves'][2]
        self.assertTrue(combat.move_spec(actor, guard).get('guard_percent'))
        self.assertEqual(pve_tactics.choose(actor, target), (guard, False))
        actor['focus'] = 1.4
        self.assertNotEqual(pve_tactics.choose(actor, target), (guard, False))
        actor.update(focus=1, _pve_guards=2)
        self.assertNotEqual(pve_tactics.choose(actor, target), (guard, False))

    def test_finish_low_health_target_without_wasting_ultimate_or_defending(self):
        actor, target = self.pair()
        actor['ultimate_charges'] = 3
        target.update(hp=1, shield=0, guard=0)
        self.assertEqual(pve_tactics.choose(actor, target), (actor['moves'][0], False))

    def test_recovery_and_hard_control_limits_survive_json(self):
        actor, target = self.pair('fairy')
        actor.update(hp=1, _trial_heals=1)
        actor = json.loads(json.dumps(trial_combat._json(actor)))
        chosen = pve_tactics.choose(actor, target)
        self.assertFalse(combat.move_spec(actor, *chosen).get('heal_percent'))
        actor, target = self.pair('glacius')
        actor['_trial_controls'] = 1
        chosen = pve_tactics.choose(actor, target)
        self.assertNotIn(combat.move_spec(actor, *chosen).get('status'), {'freeze', 'sleep'})

    def test_decision_cannot_mutate_fighters_or_consume_combat_rng(self):
        actor, target = self.pair()
        before = deepcopy((actor, target))
        source = random.Random(73)
        saved = source.getstate()
        with combat.using_rng(source):
            choice = pve_tactics.choose(actor, target)
            self.assertIs(combat.rng(), source)
            self.assertEqual(choice, pve_tactics.choose(actor, target))
        self.assertEqual(source.getstate(), saved)
        self.assertEqual((actor, target), before)

    def test_guard_reduces_real_incoming_burst(self):
        actor, target = self.pair()
        target.update(attack=80, base_attack=80)
        unguarded = deepcopy(actor)
        with combat.using_rng(random.Random(2)):
            bare_damage = combat.resolve_move(deepcopy(target), unguarded, target['ultimate'], True, [])
        combat.resolve_move(actor, target, *pve_tactics.choose(actor, target), [])
        with combat.using_rng(random.Random(2)):
            guarded_damage = combat.resolve_move(target, actor, target['ultimate'], True, [])
        self.assertLess(guarded_damage, bare_damage * .7)


class EncounterBalance(unittest.TestCase):
    def test_solo_onboarding_keeps_original_enemy_budgets_and_decisions(self):
        for kind in ('single', 'pack2', 'pack3'):
            for seed in range(12):
                old = journey.plan('woods', seed, kind, 1, party_size=1, balance_version=1)
                new = journey.plan('woods', seed, kind, 1, party_size=1)
                for before, after in zip(old['waves'], new['waves']):
                    self.assertEqual(before['species'], after['species'])
                    for key in ('hp', 'max_hp', 'attack', 'base_attack', 'defense', 'mana'):
                        self.assertEqual(before['enemy_template'][key], after['enemy_template'][key])
                    self.assertEqual(after['tactics_version'], 1)

    def test_full_party_packs_have_more_health_than_one_enemy_and_keep_the_ticket(self):
        species = {'id': 'goblin'}
        from utils import db
        pool = [db.get_beast_data(species['id'])]
        single = journey.plan('woods', 42, 'single', 4, pool=pool)
        pack = journey.plan('woods', 42, 'pack3', 4, pool=pool, ticket='fairy')
        self.assertGreater(sum(w['enemy_template']['max_hp'] for w in pack['waves']),
                           single['waves'][0]['enemy_template']['max_hp'])
        self.assertEqual([w['species'] for w in pack['waves']], ['fairy', 'goblin', 'goblin'])
        self.assertTrue(all('reward' not in w for w in pack['waves']))

    def test_late_hostiles_include_regional_divines_and_avoid_adjacent_control(self):
        from utils import db
        seen = set()
        for seed in range(40):
            p = journey.plan('loom_deep', seed, 'pack3', 49)
            for wave in p['waves']:
                seen.add(wave['species'])
            for first, second in zip(p['waves'], p['waves'][1:]):
                self.assertNotEqual(first['species'], second['species'])
                flags = [trial_rules.categories(db.get_beast_data(w['species'])) for w in (first, second)]
                self.assertFalse(all(f['control'] for f in flags))
        self.assertTrue(seen & trial_rules.ORDINARY_DIVINES)
        self.assertNotIn('chronos', seen)

    def test_saved_waves_keep_stats_and_old_ai_after_rule_changes(self):
        plan = journey.plan('woods', 42, 'single', 4, balance_version=1)
        plan['waves'][0].pop('tactics_version')  # Actual pre-release save.
        member = fighter()
        member.update(speed=0, base_speed=0, hp=10000, max_hp=10000)
        saved = trial_combat.new_run([member], plan)
        before = deepcopy(plan['waves'][0]['enemy_template'])
        with patch('utils.pve_tactics.choose', side_effect=AssertionError('Old saves keep old decisions')):
            trial_combat.start_wave(saved)
        self.assertEqual(saved['plan']['waves'][0]['enemy_template'], before)
        self.assertEqual(saved, json.loads(json.dumps(saved)))

    def test_tower_legacy_profiles_and_new_guardians_are_separately_frozen(self):
        old = trial_rules.generate_plan('loom_deep', 73, balance_version=1)
        new = trial_rules.generate_plan('loom_deep', 73)
        self.assertEqual([w['species'] for w in old['waves']], [w['species'] for w in new['waves']])
        self.assertGreater(new['waves'][-1]['enemy_template']['max_hp'], old['waves'][-1]['enemy_template']['max_hp'])
        with patch.dict(trial_rules.PROFILES, guardian=dict(hp=9, attack=9, defense=9, round_limit=1)):
            self.assertEqual(trial_combat.build_enemy(new['waves'][-1])['max_hp'],
                             new['waves'][-1]['enemy_template']['max_hp'])
        with self.assertRaises(ValueError):
            trial_rules.generate_plan('woods', 1, balance_version=999)
