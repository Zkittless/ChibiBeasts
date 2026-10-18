"""Packaged armor coverage, protected identity, and isolated appearance caches."""
import hashlib
import json
import unittest

from PIL import Image, ImageChops, ImageDraw

from utils import armor_art, battle_art
from utils.catalog import load_catalog
from utils.artwork import PROJECT_ROOT


class ArmorAssets(unittest.TestCase):
    def test_all_catalog_armor_and_species_have_packaged_fits(self):
        catalog = load_catalog('equipment.json')['equipment']
        roster = load_catalog('beasts.json')['beasts']
        manifest = armor_art.manifest()
        self.assertEqual(set(manifest['sets']), set(catalog))
        self.assertEqual(set(armor_art.fits()), set(roster))
        self.assertEqual(len(catalog), 16)
        for armor_id, record in manifest['sets'].items():
            self.assertEqual(set(record['components']), {'chest', 'shoulder', 'back', 'accessory'})
            for relative in [*record['components'].values(), *record.get('variants', {}).values()]:
                path = armor_art.ASSETS / relative
                self.assertTrue(path.is_file(), relative)
                self.assertTrue(path.resolve().is_relative_to(armor_art.ASSETS.resolve()))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest['sha256'][relative])
                with Image.open(path) as source:
                    self.assertEqual(source.mode, 'RGBA')
                    low, high = source.getchannel('A').getextrema()
                    self.assertEqual(low, 0)
                    self.assertGreaterEqual(high, 240)

    def test_every_set_changes_each_creature_and_sets_are_distinct(self):
        for species in armor_art.fits():
            base = armor_art.portrait(species)
            signatures = set()
            for armor_id in armor_art.manifest()['sets']:
                with self.subTest(species=species, armor=armor_id):
                    image = armor_art.portrait(species, armor_id)
                    self.assertEqual(image.mode, 'RGBA')
                    self.assertLessEqual(image.width, 360)
                    self.assertLessEqual(image.height, 352)
                    self.assertGreater(image.getchannel('A').getbbox()[2], 0)
                    signature = hashlib.sha256(image.tobytes()).hexdigest()
                    self.assertNotIn(signature, signatures)
                    signatures.add(signature)
                    if image.size == base.size:
                        self.assertIsNotNone(ImageChops.difference(image, base).convert('RGB').getbbox())

    def test_face_and_signature_regions_retain_exact_original_pixels(self):
        for species, fit in armor_art.fits().items():
            base = armor_art.portrait(species)
            protected = Image.new('L', base.size)
            draw = ImageDraw.Draw(protected)
            for bounds in fit['protected']:
                left, top, right, bottom = armor_art._box(bounds, base.size)
                draw.rectangle((left, top, right-1, bottom-1), fill=255)
            for armor_id, record in armor_art.manifest()['sets'].items():
                if species in record.get('variants', {}):
                    continue  # Approved whole-sprite edits have their own fitted silhouette.
                image = armor_art.portrait(species, armor_id)
                changed = ImageChops.difference(image, base)
                for channel in changed.split():
                    self.assertIsNone(ImageChops.multiply(channel, protected).getbbox(), (species, armor_id))

    def test_unknown_inputs_are_safe_and_callers_cannot_mutate_cached_portraits(self):
        self.assertIsNone(armor_art.portrait('../../outside'))
        bare = armor_art.portrait('twine')
        invalid = armor_art.portrait('twine', '../../outside')
        self.assertEqual(invalid.tobytes(), bare.tobytes())
        rune = armor_art.portrait('twine', 'pixie_ribbon')
        self.assertEqual(rune.tobytes(), bare.tobytes())
        geared = armor_art.portrait('twine', 'gnome_iron_plating')
        expected = geared.tobytes()
        geared.putalpha(0)
        self.assertEqual(armor_art.portrait('twine', 'gnome_iron_plating').tobytes(), expected)
        self.assertEqual(armor_art.portrait('twine').tobytes(), bare.tobytes())
        self.assertEqual(battle_art.sprite('twine', 'pixie_ribbon').tobytes(), battle_art.sprite('twine').tobytes())


if __name__ == '__main__':
    unittest.main()
