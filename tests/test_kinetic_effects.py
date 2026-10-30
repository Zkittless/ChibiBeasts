"""Presentation contracts for kinetic move choreography."""
import hashlib
from types import SimpleNamespace
import unittest

from PIL import Image

from utils import kinetic_effects as kinetic, move_animations as fx


class KineticChoreography(unittest.TestCase):
    def frame(self, sequence, t, *, target=None, scale=1, motif='arrow'):
        animation = SimpleNamespace(sequence=sequence, accent=13,
                                    palette='arcane', motif=motif)
        actor = fx.Pose(165, 218, height=130)
        target = target or fx.Pose(475, 218, height=130, flip=True)
        layer = fx.Effects((640, 248))
        for back in (True, False):
            self.assertTrue(kinetic.paint(layer, animation, actor, target, t,
                                         colors=fx.PALETTES['arcane'],
                                         back=back, scale=scale))
        return layer.image

    def test_every_sequence_releases_the_stage_and_is_deterministic(self):
        for seq in kinetic.SEQUENCES:
            with self.subTest(sequence=seq):
                for endpoint in (0, 1):
                    self.assertIsNone(self.frame(seq, endpoint).getbbox())
                self.assertEqual(self.frame(seq, .53).tobytes(),
                                 self.frame(seq, .53).tobytes())

    def test_choreography_is_distinct_when_palette_and_cast_are_identical(self):
        # This guards against regressing the repertoire to recolored bursts.
        signatures = {}
        for seq in kinetic.SEQUENCES:
            strip = Image.new('RGBA', (640, 248*4))
            for i, t in enumerate((.25, .45, .53, .72)):
                strip.paste(self.frame(seq, t), (0, i*248))
            digest = hashlib.sha256(strip.tobytes()).hexdigest()
            self.assertNotIn(digest, signatures,
                             (seq, signatures.get(digest)))
            signatures[digest] = seq

    def test_support_does_not_strike_or_follow_the_opponent(self):
        for seq in kinetic.SUPPORT:
            for t in (.25, .48, .72):
                with self.subTest(sequence=seq, time=t):
                    self.assertEqual(
                        self.frame(seq, t).tobytes(),
                        self.frame(seq, t, target=fx.Pose(560, 180, size=230)).tobytes())

    def test_concurrent_raid_scale_and_reverse_casts_are_safe(self):
        for seq in kinetic.SEQUENCES:
            for t in (.08, .25, .48, .6, .89):
                for scale in (.55, 1.12):
                    with self.subTest(sequence=seq, time=t, scale=scale):
                        self.frame(seq, t, scale=scale)
                        animation = SimpleNamespace(sequence=seq, accent=9999,
                                                    palette='arcane', motif='arrow')
                        layer = fx.Effects((640, 350))
                        for back in (True, False):
                            kinetic.paint(layer, animation,
                                          fx.Pose(475, 315, size=80),
                                          fx.Pose(320, 259, size=282), t,
                                          colors=fx.PALETTES['arcane'],
                                          back=back, scale=scale)

    def test_literal_arrow_feather_and_spirit_tail_silhouettes_differ(self):
        signatures = {self.frame('arrow_volley', .45, motif=motif).tobytes()
                      for motif in ('arrow', 'feather', 'spark')}
        self.assertEqual(len(signatures), 3)

    def test_unknown_sequence_yields_to_the_legacy_painter(self):
        layer = fx.Effects((640, 248))
        self.assertFalse(kinetic.paint(layer, SimpleNamespace(sequence='missing'),
                                      fx.Pose(165, 218), fx.Pose(475, 218), .5,
                                      colors=fx.PALETTES['arcane']))
        self.assertIsNone(layer.image.getbbox())


if __name__ == '__main__':
    unittest.main()
