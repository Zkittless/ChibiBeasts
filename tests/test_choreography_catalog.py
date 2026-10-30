"""Authored staging stays complete and truthful as the beast roster grows."""
import unittest

from utils import combat, db, divine_effects, kinetic_effects, move_animations as fx


SUPPORT = frozenset({
    'seed_bloom', 'petal_mend', 'crystal_aegis', 'shell_bastion',
    'mirror_decoy', 'feather_ward', 'clock_rewind',
})
CONTEXTUAL_SUPPORT = frozenset({'sovereign_veil', 'nirvana_lotus'})
SUPPORT_FAMILIES = frozenset({
    'heal', 'guard', 'shelter', 'illusion', 'rewind', 'wind_guard',
})


class ChoreographyCatalog(unittest.TestCase):
    def test_roster_has_complete_registered_staging_and_four_distinct_moves(self):
        registered = divine_effects.SEQUENCES | kinetic_effects.SEQUENCES
        self.assertFalse(divine_effects.SEQUENCES & kinetic_effects.SEQUENCES)
        roster = db.load_beasts()
        self.assertEqual(set(fx.manifest()), set(roster))
        used = set()
        for species, beast in roster.items():
            names = beast['moves'] + [beast['ultimate']]
            self.assertEqual(set(fx.manifest()[species]), set(names))
            sequences = set()
            for name in names:
                with self.subTest(species=species, move=name):
                    record = fx.manifest()[species][name]
                    self.assertIn(record['sequence'], registered)
                    self.assertIs(type(record['accent']), int)
                    self.assertGreaterEqual(record['accent'], 0)
                    self.assertLessEqual(record['accent'], 65535)
                    self.assertIs(record['ultimate'], name == beast['ultimate'])
                    animation = fx.profile(species, name)
                    self.assertEqual(animation.sequence, record['sequence'])
                    self.assertEqual(animation.accent, record['accent'])
                    self.assertEqual(animation.ultimate, record['ultimate'])
                    used.add(animation.sequence)
                    sequences.add(animation.sequence)
            self.assertEqual(len(sequences), len(names), species)
        # Every implemented sequence has a real catalog example to review.
        self.assertEqual(used, registered)

    def test_support_staging_matches_resolved_effects_including_misleading_names(self):
        for species, beast in db.load_beasts().items():
            for name in beast['moves'] + [beast['ultimate']]:
                with self.subTest(species=species, move=name):
                    animation = fx.profile(species, name)
                    spec = combat.move_spec(beast, name, name == beast['ultimate'])
                    support = bool(spec.get('heal_percent') or spec.get('guard_percent'))
                    self.assertEqual(animation.family in SUPPORT_FAMILIES, support)
                    self.assertEqual(
                        animation.sequence in SUPPORT or (
                            animation.sequence in CONTEXTUAL_SUPPORT
                            and animation.family in SUPPORT_FAMILIES), support)
        self.assertEqual(fx.profile('twine', 'Borrowed Moment').sequence, 'clock_fracture')
        self.assertEqual(fx.profile('timestitch', 'Borrowed Moment').sequence, 'clock_rewind')
        self.assertEqual(fx.profile('gloop', 'Glitter Bounce').sequence, 'petal_mend')
        self.assertEqual(fx.profile('glacius', 'Ice Shell').sequence, 'crystal_prison')

    def test_same_name_retains_creature_identity_and_bonus_alias_is_stable(self):
        self.assertNotEqual(fx.profile('twine', 'Borrowed Moment'),
                            fx.profile('timestitch', 'Borrowed Moment'))
        self.assertNotEqual(fx.profile('prismite', 'Halo Glint'),
                            fx.profile('prismara', 'Halo Glint'))
        self.assertEqual(fx.profile('twine', 'Tick Bite'),
                         fx.profile('twine', 'Tick Bite (bonus)'))
        self.assertEqual(fx.profile('twine', 'Tick Bite', 'status').family, 'status')

    def test_every_canonical_effect_clears_before_and_after_playback_on_both_sides(self):
        for species, moves in fx.manifest().items():
            for name in moves:
                animation = fx.profile(species, name)
                for reverse in (False, True):
                    actor = fx.Pose(478 if reverse else 162, 218, flip=reverse)
                    target = fx.Pose(162 if reverse else 478, 218, flip=not reverse)
                    for t in (0, 1):
                        with self.subTest(species=species, move=name,
                                          reverse=reverse, t=t):
                            effects = fx.Effects((640, 248))
                            effects.effect(animation, actor, target, t, back=True)
                            effects.effect(animation, actor, target, t)
                            self.assertIsNone(effects.image.getbbox())


if __name__ == '__main__':
    unittest.main()
