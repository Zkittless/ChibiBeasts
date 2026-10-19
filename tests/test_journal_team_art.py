"""Full-body team composition, owned armor, and bounded renderer recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from utils import armor_art, journal_team_art as art
from utils.catalog import load_catalog


def row(species='twine', armor_id='', hp=10):
    return {'beast_id': species, 'armor_id': armor_id, 'hp': hp}


def opened(artifact):
    return Image.open(io.BytesIO(artifact.data)).convert('RGB')


class TeamStage(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        art.clear_cache()

    async def test_three_actual_appearances_stand_with_larger_lead_in_front(self):
        colors = {'lead': '#ee2211', 'left': '#22ee11', 'right': '#1122ee'}
        originals = {species: Image.new('RGBA', (300, 300), color) for species, color in colors.items()}
        before = {species: image.tobytes() for species, image in originals.items()}

        def sprite(species, armor_id):
            return originals[species].copy()

        with patch.object(armor_art, 'portrait', side_effect=sprite) as fitted:
            image = opened(await art.render_team([row('lead', 'lead_armor'), row('left', 'left_armor'), row('right', 'right_armor')]))
        self.assertEqual(image.size, (640, 300))
        # Both reserves remain visible on their own side.  The overlap belongs
        # to the lead, whose feet stand on the lower foreground plane.
        self.assertEqual(image.getpixel((100, 180)), (34, 238, 17))
        self.assertEqual(image.getpixel((540, 180)), (17, 34, 238))
        self.assertEqual(image.getpixel((220, 200)), (238, 34, 17))
        self.assertEqual(image.getpixel((420, 200)), (238, 34, 17))
        self.assertEqual(image.getpixel((320, 265)), (238, 34, 17))
        self.assertNotEqual(image.getpixel((147, 265)), (34, 238, 17))
        self.assertEqual(set(fitted.call_args_list[i].args for i in range(3)),
                         {('lead', 'lead_armor'), ('left', 'left_armor'), ('right', 'right_armor')})
        for species, original in originals.items():
            self.assertEqual(original.size, (300, 300))
            self.assertEqual(original.tobytes(), before[species])

    async def test_every_equipment_set_renders_on_starter_legendary_and_evolved_species(self):
        baseline = {}
        species = ('twine', 'leviathan', 'rootwarden')
        for beast in species:
            baseline[beast] = await art.render_team([row(beast)])
        names, pixels = set(), set()
        for beast in species:
            for equipment in load_catalog('equipment.json')['equipment']:
                with self.subTest(beast=beast, equipment=equipment):
                    result = await art.render_team([row(beast, equipment)])
                    self.assertIsNotNone(result)
                    self.assertNotEqual(result.name, baseline[beast].name)
                    self.assertNotEqual(result.data, baseline[beast].data)
                    self.assertNotIn(result.name, names)
                    self.assertNotIn(result.data, pixels)
                    self.assertLess(len(result.data), art.MAX_BYTES)
                    names.add(result.name)
                    pixels.add(result.data)
        self.assertEqual(len(names), 48)
        self.assertLessEqual(art._render.cache_info().currsize, art.CACHE_SIZE)

    async def test_sparse_reserved_slots_preserve_holes_without_inventing_characters(self):
        with patch.object(armor_art, 'portrait', wraps=armor_art.portrait) as fitted:
            result = await art.render_team([row('twine'), None, row('prismite')])
        self.assertIsNotNone(result)
        self.assertEqual({call.args[0] for call in fitted.call_args_list}, {'twine', 'prismite'})
        one = await art.render_team([row('twine')])
        self.assertNotEqual(result.data, one.data)
        with patch.object(armor_art, 'portrait') as fitted:
            self.assertIsNotNone(await art.render_team([None, None, None]))
            fitted.assert_not_called()

    async def test_cache_name_tracks_visual_appearance_not_nickname_or_health_points(self):
        rows = [row('twine', 'gnome_iron_plating'), row('prismite')]
        first = await art.render_team(rows, 'woods')
        rows[0].update(nickname='New name', id=777, hp=5, level=40)
        same = await art.render_team(rows, 'woods')
        self.assertEqual(first.name, same.name)
        self.assertEqual(first.data, same.data)
        rows[0]['hp'] = 0
        recovering = await art.render_team(rows, 'woods')
        self.assertNotEqual(first.name, recovering.name)
        self.assertNotEqual(first.data, recovering.data)
        changed_realm = await art.render_team(rows, 'ember')
        self.assertNotEqual(recovering.name, changed_realm.name)
        self.assertNotEqual(recovering.data, changed_realm.data)
        rows.reverse()
        self.assertNotEqual((await art.render_team(rows, 'ember')).name, changed_realm.name)

    async def test_asset_revision_refreshes_discord_attachment_name(self):
        rows = [row('twine', 'gnome_iron_plating')]
        first = await art.render_team(rows)
        manifest = deepcopy(armor_art.manifest())
        variant = manifest['sets']['gnome_iron_plating']['variants']['twine']
        manifest['sha256'][variant] = 'changed-content-revision'
        with patch.object(armor_art, 'manifest', return_value=manifest):
            changed = await art.render_team(rows)
        self.assertNotEqual(first.name, changed.name)

    async def test_missing_species_returns_native_controls_fallback(self):
        with self.assertLogs('chibibeasts.journal.team_art', level='WARNING'):
            self.assertIsNone(await art.render_team([row('../../outside')]))

    async def test_five_realms_return_valid_complete_pngs(self):
        for realm in ('woods', 'ember', 'glacial', 'trenches', 'loom'):
            with self.subTest(realm=realm):
                result = await art.render_team([row('leviathan', 'deepwater_hide'), row('rootwarden'), row('prismite')], realm)
                with Image.open(io.BytesIO(result.data)) as image:
                    self.assertEqual(image.format, 'PNG')
                    self.assertEqual(image.size, (640, 300))
                    image.verify()

    async def test_timeout_does_not_release_worker_budget_or_queue_another_render(self):
        entered, release = threading.Event(), threading.Event()
        slots = threading.BoundedSemaphore(1)
        workers = ThreadPoolExecutor(max_workers=1)
        expected = art.TeamArtwork('test.png', b'png')

        def slow(*args):
            entered.set()
            release.wait(2)
            return expected

        try:
            with patch.object(art, '_workers', workers), patch.object(art, '_slots', slots), \
                    patch.object(art, '_render', side_effect=slow) as render, patch.object(art, 'RENDER_TIMEOUT', .02):
                with self.assertLogs('chibibeasts.journal.team_art', level='WARNING'):
                    self.assertIsNone(await art.render_team([row()]))
                self.assertTrue(entered.is_set())
                self.assertIsNone(await art.render_team([row()]))
                self.assertEqual(render.call_count, 1)
                release.set()
                await asyncio.wrap_future(workers.submit(lambda: None))
                self.assertEqual(await art.render_team([row()]), expected)
        finally:
            release.set()
            workers.shutdown(wait=True)

    async def test_cancelled_callback_keeps_budget_and_immutable_appearance_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        slots = threading.BoundedSemaphore(1)
        workers = ThreadPoolExecutor(max_workers=1)
        captured = []
        rows = [row('twine', 'gnome_iron_plating')]

        def slow(members, realm, revision):
            entered.set()
            release.wait(2)
            captured.append(members)
            return art.TeamArtwork('test.png', b'png')

        try:
            with patch.object(art, '_workers', workers), patch.object(art, '_slots', slots), patch.object(art, '_render', side_effect=slow):
                task = asyncio.create_task(art.render_team(rows))
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                rows[0].update(beast_id='rootwarden', armor_id='deepwater_hide')
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIsNone(await art.render_team(rows))
                release.set()
                await asyncio.wrap_future(workers.submit(lambda: None))
                self.assertEqual(captured, [(art.Member('twine', 'gnome_iron_plating', False), None, None)])
                self.assertTrue(slots.acquire(blocking=False))
                slots.release()
        finally:
            release.set()
            workers.shutdown(wait=True)
