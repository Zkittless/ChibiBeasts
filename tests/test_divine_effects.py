"""Celestial sequences retain their staging, identity, and truthful recipients."""
import hashlib
from dataclasses import replace
import unittest

from PIL import Image

from utils import divine_effects, move_animations as fx


class DivineChoreography(unittest.TestCase):
    def layer(self, sequence, t, *, family='orbit', flip=False, scale=1):
        a = fx.Animation(family, 'arcane', sequence=sequence, accent=731)
        actor = fx.Pose(478 if flip else 162, 218, flip=flip, height=130)
        target = fx.Pose(162 if flip else 478, 218, flip=not flip, height=130)
        layer = fx.Effects((640, 248))
        for back in (True, False):
            self.assertTrue(divine_effects.paint(layer, a, actor, target, t,
                                                colors=fx.PALETTES['arcane'], back=back, scale=scale))
        return layer.image

    def test_each_sequence_settles_and_is_visible_on_either_side(self):
        for sequence in divine_effects.SEQUENCES:
            for flip in (False, True):
                with self.subTest(sequence=sequence, flip=flip):
                    for t in (0, 1):
                        self.assertIsNone(self.layer(sequence, t, flip=flip).getbbox())
                    for t in (.3, .48, .65):
                        self.assertIsNotNone(self.layer(sequence, t, flip=flip).getbbox())

    def test_all_sequences_have_different_geometry_in_one_palette(self):
        signatures = {}
        for sequence in divine_effects.SEQUENCES:
            strip = Image.new('RGBA', (640, 248*4))
            for i, t in enumerate((.27, .43, .51, .7)):
                strip.paste(self.layer(sequence, t), (0, i*248))
            signature = hashlib.sha256(strip.tobytes()).hexdigest()
            self.assertNotIn(signature, signatures, (sequence, signatures.get(signature)))
            signatures[signature] = sequence

    def test_restoration_and_veil_do_not_paint_the_opponent(self):
        for sequence, family in (('nirvana_lotus', 'heal'), ('sovereign_veil', 'guard'),
                                 ('clock_rewind', 'rewind')):
            for t in (.3, .52, .72):
                with self.subTest(sequence=sequence, t=t):
                    image = self.layer(sequence, t, family=family)
                    self.assertIsNone(image.crop((340, 0, 640, 248)).getbbox())
        # The same divine names are damaging ultimates in the canonical roster.
        for sequence in ('nirvana_lotus', 'sovereign_veil'):
            self.assertIsNotNone(self.layer(sequence, .52).crop((410, 50, 555, 240)).getbbox())

    def test_concurrent_raid_scale_stays_local_and_reproducible(self):
        for sequence in divine_effects.SEQUENCES:
            with self.subTest(sequence=sequence):
                first = self.layer(sequence, .54, scale=.55)
                second = self.layer(sequence, .54, scale=.55)
                self.assertEqual(first.tobytes(), second.tobytes())
                alpha = first.getchannel('A')
                # No cinematic may wash the entire raid field with a solid tint.
                self.assertLess(sum(value > 0 for value in alpha.tobytes()), 640*248*.3)

    def test_duel_background_holds_still_for_gif_delta_compression(self):
        # A continuously changing arena tint made the painted Hollowlight GIF
        # exceed Discord's attachment budget despite bounded local geometry.
        # Static pixels outside the action must be reusable between frames.
        frames = [self.layer('phantom_court', t) for t in (.25, .4, .6, .75)]
        corners = [frame.crop((0, 0, 16, 16)).tobytes() for frame in frames]
        self.assertTrue(all(corner == corners[0] for corner in corners))
        self.assertNotEqual(frames[0].tobytes(), frames[2].tobytes())

    def test_end_of_time_and_last_twilight_are_not_palette_swaps(self):
        actor, target = fx.Pose(162, 218, height=130), fx.Pose(478, 218, height=130)
        strips = []
        for species, name in (('chronos', 'End of Time'), ('terminus', 'Last Twilight')):
            # Keep the real profile's family but remove color and accent as
            # possible explanations for a difference in the two ultimates.
            animation = replace(fx.profile(species, name), palette='arcane', accent=731)
            strip = Image.new('RGBA', (640, 248*4))
            for i, t in enumerate((.24, .43, .54, .7)):
                layer = fx.Effects((640, 248))
                for back in (True, False):
                    divine_effects.paint(layer, animation, actor, target, t,
                                         colors=fx.PALETTES['arcane'], back=back)
                strip.paste(layer.image, (0, i*248))
            strips.append(strip.tobytes())
        self.assertNotEqual(*strips)

    def test_big_bang_containment_is_visible_in_front_of_a_wide_target(self):
        actor, target = fx.Pose(162, 218, height=130), fx.Pose(478, 218, height=130)
        animation = fx.profile('singularity', 'Big Bang')
        for t in (.24, .43):
            with self.subTest(t=t):
                layer = fx.Effects((640, 248))
                divine_effects.paint(layer, animation, actor, target, t,
                                     colors=fx.PALETTES[animation.palette], back=False)
                # The wind-up must extend well beyond the tiny central star,
                # and cannot rely on a background layer hidden by the beast.
                self.assertIsNotNone(layer.image.crop((350, 65, 415, 225)).getbbox())
                self.assertIsNotNone(layer.image.crop((540, 65, 610, 225)).getbbox())

