"""Dedicated armor sprites, truthful coverage, safe fallbacks, isolated caches."""
import hashlib
import unittest
from unittest.mock import patch

from PIL import Image

from utils import armor_art, battle_art
from utils.catalog import load_catalog


class ArmorAssets(unittest.TestCase):
    def tearDown(self):
        armor_art.clear_cache()
        battle_art.sprite.cache_clear()

    def test_release_has_a_dedicated_sprite_for_every_species_and_armor(self):
        actual = armor_art.coverage()
        self.assertEqual(actual['expected_pairs'], 86 * 16)
        self.assertTrue(actual['complete'], f"Missing armor sprites: {actual['missing'][:12]}")
        self.assertEqual(actual['dedicated_pairs'], actual['expected_pairs'])
        self.assertEqual(actual['missing'], [])

    def test_registered_dedicated_assets_have_valid_hashes_and_full_cutouts(self):
        catalog = load_catalog('equipment.json')['equipment']
        roster = load_catalog('beasts.json')['beasts']
        manifest = armor_art.manifest()
        self.assertEqual(set(manifest['sets']), set(catalog))
        self.assertEqual(len(catalog), 16)
        registered = set()
        for armor_id, record in manifest['sets'].items():
            for species, relative in record.get('variants', {}).items():
                with self.subTest(species=species, armor=armor_id):
                    self.assertIn(species, roster)
                    path = armor_art.ASSETS / relative
                    self.assertTrue(path.is_file(), relative)
                    self.assertTrue(path.resolve().is_relative_to(armor_art.ASSETS.resolve()))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest['sha256'][relative])
                    with Image.open(path) as source:
                        self.assertEqual(source.mode, 'RGBA')
                        low, high = source.getchannel('A').getextrema()
                        self.assertEqual(low, 0)
                        self.assertGreaterEqual(high, 240)
                        self.assertLessEqual(source.width, 360)
                        self.assertLessEqual(source.height, 352)
                    registered.add((species, armor_id))
        for species in ('twine', 'barkley', 'gloop'):
            self.assertIn((species, 'gnome_iron_plating'), registered)
        actual = armor_art.coverage()
        self.assertEqual(set(actual['dedicated']), registered)
        self.assertEqual(actual['dedicated_pairs'], len(registered))
        self.assertEqual(actual['expected_pairs'], len(roster) * len(catalog))
        self.assertEqual(actual['dedicated_pairs'] + actual['missing_pairs'], actual['expected_pairs'])
        self.assertEqual(actual['complete'], not actual['missing'])

    def test_each_registered_full_variant_is_visible_distinct_and_not_a_base_fallback(self):
        seen = {}
        for species, armor_id in armor_art.coverage()['dedicated']:
            with self.subTest(species=species, armor=armor_id):
                base = armor_art.portrait(species)
                image = armor_art.portrait(species, armor_id)
                self.assertEqual(image.mode, 'RGBA')
                self.assertLessEqual(image.width, 360)
                self.assertLessEqual(image.height, 352)
                self.assertIsNotNone(image.getchannel('A').getbbox())
                fingerprint = (image.size, hashlib.sha256(image.tobytes()).hexdigest())
                self.assertNotEqual(fingerprint, (base.size, hashlib.sha256(base.tobytes()).hexdigest()))
                self.assertNotIn(fingerprint, seen.setdefault(species, set()))
                seen[species].add(fingerprint)

    def test_legacy_components_never_render_as_a_claimed_fitted_appearance(self):
        bare = armor_art.portrait('twine')
        legacy = {'sets': {'kelpie_scale_mail': {'components': {
            'chest': 'components/kelpie_scale_mail/chest.png',
            'shoulder': 'components/kelpie_scale_mail/shoulder.png',
        }}}}
        with patch.object(armor_art, 'manifest', return_value=legacy), patch.object(armor_art, '_cutout') as read:
            image = armor_art.portrait('twine', 'kelpie_scale_mail')
            read.assert_not_called()
            self.assertEqual(image.size, bare.size)
            self.assertEqual(image.tobytes(), bare.tobytes())
            self.assertEqual(armor_art.coverage()['dedicated_pairs'], 0)

    def test_missing_or_escaping_registered_sprite_falls_back_to_canonical_art(self):
        bare = armor_art.portrait('twine')
        for relative in ('variants/nonexistent/twine.png', '../../outside.png'):
            armor_art._portrait.cache_clear()
            record = {'sets': {'gnome_iron_plating': {'variants': {'twine': relative}}}}
            with patch.object(armor_art, 'manifest', return_value=record), self.assertLogs('chibibeasts.armor.art', level='WARNING'):
                image = armor_art.portrait('twine', 'gnome_iron_plating')
            self.assertEqual(image.size, bare.size)
            self.assertEqual(image.tobytes(), bare.tobytes())

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
