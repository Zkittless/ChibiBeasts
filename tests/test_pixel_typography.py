"""The bundled pixel face must stay crisp, complete and safe across image routes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image, ImageColor, ImageDraw, ImageSequence

from cogs.battle import build_pve_beast_state
from utils import battle_art as art, db as storage, field_journal as ui

ROOT = Path(__file__).resolve().parents[1]


def rgba(color):
    return ImageColor.getrgb(color) + (255,)


class PixelTypography(unittest.TestCase):
    def test_body_headings_and_floating_labels_use_the_licensed_bundled_face(self):
        expected = ROOT / 'data/fonts/PixelifySans.ttf'
        self.assertTrue(expected.is_file())
        license_text = (expected.parent / 'OFL-PixelifySans.txt').read_text(encoding='utf-8')
        self.assertIn('SIL OPEN FONT LICENSE', license_text)
        self.assertIn('Version 1.1', license_text)
        for face in (ui.font(18), ui.font(28, display=True), art.font(18)):
            self.assertEqual(Path(face.path).resolve(), expected.resolve())
            self.assertIn('Pixelify', face.getname()[0])

    def test_new_font_instances_load_outside_the_project_working_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                # A fresh rendering thread cannot hide a relative-path bug
                # behind a face cached by an earlier test.
                with ThreadPoolExecutor(max_workers=1) as workers:
                    body, title = workers.submit(lambda: (ui.font(18), ui.font(28, True))).result(timeout=5)
                for face in (body, title):
                    self.assertEqual(Path(face.path).resolve(), (ROOT / 'data/fonts/PixelifySans.ttf').resolve())
            finally:
                os.chdir(previous)

    def test_render_threads_keep_separate_cached_variable_font_instances(self):
        barrier = threading.Barrier(2)
        main = ui.font(20, True)

        def capture():
            first = ui.font(20, True)
            repeated = ui.font(20, True)
            body = ui.font(20, False)
            barrier.wait(timeout=5)
            return first, repeated, body

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(capture) for _ in range(2)]
            first, second = [future.result(timeout=10) for future in futures]
        for face, repeated, body in (first, second):
            self.assertIs(face, repeated)
            self.assertIsNot(face, body)
            self.assertIsNot(face, main)
        self.assertIsNot(first[0], second[0])

    def test_pixel_labels_have_hard_edges_and_stay_inside_their_ink_rectangles(self):
        for value, display, color in (('IN BATTLE', True, ui.MANILLA),
                                      ('123,456 / 999,999', False, ui.INK)):
            with self.subTest(value=value):
                image = Image.new('RGBA', (360, 70))
                draw = ImageDraw.Draw(image)
                box = (12, 10, 348, 60)
                result = ui.text(draw, value, box, size=28, display=display, ellipsis=False)
                self.assertEqual(result.value, value)
                self.assertEqual(set(image.getchannel('A').tobytes()), {0, 255})
                colors = {color for _, color in image.getcolors(image.width * image.height)}
                self.assertIn(rgba(color), colors)
                self.assertIn(rgba(ui.TEXT_OUTLINE), colors)
                actual = image.getbbox()
                self.assertTrue(box[0] <= actual[0] <= actual[2] <= box[2])
                self.assertTrue(box[1] <= actual[1] <= actual[3] <= box[3])
                phone = image.resize((180, 35), Image.Resampling.NEAREST)
                self.assertEqual(set(phone.getchannel('A').tobytes()), {0, 255})

    def test_stat_text_uses_white_and_manilla_without_repainting_the_resource_color(self):
        image = Image.new('RGBA', (340, 80))
        meter = ui.resource_bar(image, (12, 18, 328, 58), 123, 500, 'HP', ui.HEALTH)
        self.assertEqual(meter['text'].value, '123 / 500')
        colors = {color for _, color in image.getcolors(image.width * image.height)}
        for color in (ui.HEALTH, ui.INK, ui.MANILLA, ui.TEXT_OUTLINE):
            self.assertIn(rgba(color), colors)

    def test_floating_damage_keeps_the_complete_number_and_crisp_width(self):
        image = Image.new('RGBA', (220, 80))
        draw = ImageDraw.Draw(image)
        value = '+9,999,999 CRIT'
        art.label(draw, (20, 12), value, size=28, width=150)
        box = image.getbbox()
        self.assertTrue(20 <= box[0] < box[2] <= 170, box)
        self.assertEqual(set(image.getchannel('A').tobytes()), {0, 255})
        # Compare the actual foreground with a full-string glyph reference.
        # This does not depend on how the label renderer draws its outline or
        # chooses a fitting size, and catches a clipped suffix as well as '~'.
        foreground = image.getchannel('G').point(lambda value: 255 if value > 240 else 0)
        actual = foreground.crop(foreground.getbbox())
        matches = []
        for size in range(8, 40):
            expected = Image.new('L', (600, 100))
            painter = ImageDraw.Draw(expected)
            painter.fontmode = '1'
            painter.text((4, 4), value, font=ui.font(size, True), fill=255)
            expected = expected.crop(expected.getbbox())
            matches.append(actual.size == expected.size and actual.tobytes() == expected.tobytes())
        self.assertTrue(any(matches), 'Floating label lost part of the complete number.')

    def test_long_name_battle_animation_stays_within_the_attachment_and_playback_budgets(self):
        states = [build_pve_beast_state(storage.get_beast_data(species), 50)
                  for species in ('corrupted_leviathan', 'hollow_king')]
        before = art.snapshot(states)
        after = (before[0], replace(before[1], hp=max(1, before[1].hp - 47)))
        action = art.Action(0, states[0]['ultimate'], damage=47, ultimate=True)
        clip = art.render(before, after, 'hollowlight', action)
        self.assertLess(len(clip.data), art.MAX_BYTES)
        with Image.open(io.BytesIO(clip.data)) as image:
            self.assertEqual(image.size, (640, art.TOTAL_HEIGHT))
            self.assertGreater(image.n_frames, 1)
            self.assertNotIn('loop', image.info)
            self.assertEqual(sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)),
                             round(clip.seconds * 1000))

    def test_raid_names_party_counts_levels_and_timers_never_lose_their_suffixes(self):
        from scripts.preview_raid_arena import capture, make_raid
        from cogs.guilds import RAID_BOSSES
        from cogs.ancient import ANCIENT_BOSSES
        from utils import raid_art

        paint = ui.text
        bosses = [boss for group in RAID_BOSSES.values() for boss in group] + list(ANCIENT_BOSSES)
        cases = [(boss, 10, 49.5) for boss in bosses]
        cases.extend((bosses[0], count, level) for count in range(1, 11) for level in (20.0, 33.333333333333336))
        for boss, count, level in cases:
            scene = capture(make_raid(boss, count))
            scene = replace(scene, boss=replace(scene.boss, level=level), remaining=3599)
            labels = []

            def record(draw, value, box, **kwargs):
                result = paint(draw, value, box, **kwargs)
                if value == scene.boss.name or 'trainers' in value:
                    labels.append((ui.clean(value), box, result))
                return result

            with self.subTest(boss=boss['id'], count=count, level=level), \
                    patch.object(ui, 'text', side_effect=record):
                raid_art.frame(scene, scene)
                self.assertEqual(len(labels), 2)
                for original, box, fitted in labels:
                    self.assertEqual(fitted.value, original, (original, fitted))
                    self.assertTrue(box[0] <= fitted.ink[0] <= fitted.ink[2] <= box[2])
                    self.assertTrue(box[1] <= fitted.ink[1] <= fitted.ink[3] <= box[3])
                name_box, metadata_box = labels[0][1], labels[1][1]
                self.assertLessEqual(name_box[2], metadata_box[0])


class TypographyOutage(unittest.IsolatedAsyncioTestCase):
    async def test_a_font_load_outage_preserves_the_complete_native_battle_panel(self):
        states = [build_pve_beast_state(storage.get_beast_data(species), 6)
                  for species in ('twine', 'wisp')]
        saved = art.snapshot(states)
        presentation = art.BattlePresentation('woods')
        embed = discord.Embed(title='Battle', description='Exact HP and MP remain available here.')
        embed.add_field(name='Last actions', value='Twine dealt 27 damage.')
        view = SimpleNamespace(_closed=False, message=None)
        message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock(return_value=message)))
        with patch.object(ui, 'font', side_effect=OSError('Bundled font unavailable')) as missing, \
                self.assertLogs('chibibeasts.battle.art', level='WARNING'):
            await presentation.publish(interaction, None, embed, view, saved, saved)
        self.assertGreater(missing.call_count, 0)
        self.assertFalse(presentation.enabled)
        sent = interaction.channel.send.call_args.kwargs
        self.assertNotIn('file', sent)
        self.assertEqual(sent['embed'].description, 'Exact HP and MP remain available here.')
        self.assertEqual([(field.name, field.value) for field in sent['embed'].fields],
                         [('Last actions', 'Twine dealt 27 damage.')])
        self.assertIs(view.message, message)
