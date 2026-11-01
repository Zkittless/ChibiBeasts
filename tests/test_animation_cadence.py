"""Smooth playback preserves timing, resolved pixels, and bounded RGB memory."""
import bisect
from dataclasses import replace
import io
import math
import unittest
from unittest.mock import patch
import weakref

from PIL import Image, ImageDraw, ImageSequence

from cogs.guilds import RAID_BOSSES
from scripts.preview_hexpurr import fixture
from scripts.preview_move_animations import case
from scripts.preview_raid_arena import capture, clash, make_raid
from utils import battle_art as art, battle_scene, db, raid_art, raid_scene


def moving_scene(t):
    image = Image.new('RGB', (160, 100), '#142233')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 159, 16), fill='#fff1ce')
    x = round(120 * t)
    if .45 <= t <= .55:
        draw.rectangle((0, 17, 159, 99), fill='#772133')
    if t < .85:
        draw.rectangle((x, 35, x + 18, 70), fill='#efac56')
    return image


def clip_details(clip):
    with Image.open(io.BytesIO(clip.data)) as image:
        delays = [f.info['duration'] for f in ImageSequence.Iterator(image)]
        return image.n_frames, sum(delays), image.info.get('loop')


class AnimationCadence(unittest.TestCase):
    def test_delta_compression_preserves_every_pixel_hold_and_total_duration(self):
        for milliseconds, looping in ((1950, False), (2650, False), (4050, False), (2000, True)):
            with self.subTest(milliseconds=milliseconds, looping=looping):
                clip = art.render_animation(moving_scene, milliseconds, loop=looping)
                count = math.ceil(milliseconds * 30 / 1000)
                delays = art.frame_durations(milliseconds, count)
                ends = []
                for delay in delays:
                    ends.append((ends[-1] if ends else 0) + delay)
                elapsed = 0
                with Image.open(io.BytesIO(clip.data)) as image:
                    self.assertEqual(image.info.get('loop'), 0 if looping else None)
                    for frame in ImageSequence.Iterator(image):
                        index = bisect.bisect_right(ends, elapsed)
                        expected = moving_scene(index / (count if looping else count - 1))
                        self.assertEqual(frame.convert('RGB').tobytes(), expected.tobytes())
                        elapsed += frame.info['duration']
                self.assertEqual(elapsed, milliseconds)
                self.assertEqual(clip.seconds, 0 if looping else milliseconds / 1000)
                # A 30 FPS GIF distributes time in 30/40 ms ticks, without a
                # large final pause or sub-20 ms frames clamped by viewers.
                self.assertTrue(all(30 <= delay <= 40 for delay in delays))

    def test_rgb_sources_are_released_while_long_clips_are_encoded(self):
        references = []
        live_counts = []

        def source(t):
            image = moving_scene(t)
            references.append(weakref.ref(image))
            live_counts.append(sum(ref() is not None for ref in references))
            return image

        art.render_animation(source, 4050)
        self.assertGreater(len(references), 120)
        self.assertLessEqual(max(live_counts), 3)
        self.assertTrue(all(ref() is None for ref in references))

    def test_dense_real_moves_and_retreats_have_smooth_cadence_and_same_timing(self):
        species = 'genesis'
        move = db.get_beast_data(species)['ultimate']
        for knockout in (False, True):
            with self.subTest(knockout=knockout):
                before, after, action = case(species, move)
                if knockout:
                    after = (after[0], replace(after[1], hp=0))
                clip = art.render(before, after, 'loom', action)
                count, duration, loop = clip_details(clip)
                expected = battle_scene.duration(before, after, action)
                self.assertEqual(duration, expected)
                self.assertEqual(clip.seconds, expected / 1000)
                self.assertIsNone(loop)
                # Deliberately held poses may be merged without losing motion.
                self.assertGreaterEqual(count, math.floor(expected * .025))

    def test_both_battle_and_raid_auras_loop_at_30fps_without_blocking_controls(self):
        before, after, action = fixture()
        move = art.render(before, after, 'hollowlight', action)
        idle = art.render(after, after, 'hollowlight', palette=move.palette,
                          ambient_start=move.ambient_end)
        self.assertEqual(clip_details(idle), (60, 2000, 0))
        self.assertEqual(idle.seconds, 0)
        self.assertEqual(idle.palette, move.palette)
        raid = make_raid(RAID_BOSSES['corrupted'][2], 10)
        scene = capture(raid)
        member = replace(scene.members[0], fighter=replace(after[0], party=()))
        scene = replace(scene, members=(member, *scene.members[1:]))
        idle = raid_art.render(scene, scene)
        self.assertEqual(clip_details(idle), (60, 2000, 0))
        self.assertEqual(idle.seconds, 0)

    def test_raid_clips_include_30fps_boss_departure_and_hold_the_final_state(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2], 10)
        before, after, events = clash(raid)
        for defeated in (False, True):
            with self.subTest(defeated=defeated):
                resolved = replace(after, boss=replace(after.boss, hp=0), outcome='victory') if defeated else after
                clip = raid_art.render(before, resolved, events)
                count, duration, loop = clip_details(clip)
                expected = raid_scene.duration(before, resolved, events)
                self.assertEqual(duration, expected)
                self.assertEqual(clip.seconds, expected / 1000)
                self.assertIsNone(loop)
                # The boss exit deliberately holds after leaving the arena;
                # duplicate held frames collapse, while motion stays at 30 FPS.
                self.assertGreaterEqual(count, math.floor(expected * .02))
                palette = Image.new('P', (1, 1))
                palette.putpalette(clip.palette)
                final = raid_art.frame(before, resolved, events, 1, ambient_seconds=expected/1000)
                expected_pixels = final.quantize(palette=palette, dither=Image.Dither.NONE).convert('RGB')
                with Image.open(io.BytesIO(clip.data)) as image:
                    image.seek(image.n_frames - 1)
                    self.assertEqual(image.convert('RGB').tobytes(), expected_pixels.tobytes())

    def test_oversized_animation_still_respects_attachment_budget(self):
        with patch.object(art, 'MAX_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'attachment budget'):
                art.render_animation(moving_scene, 1950)


if __name__ == '__main__':
    unittest.main()
