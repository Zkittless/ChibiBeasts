"""Release gate: a dedicated, reviewed ghost fit exists for every catalog species."""
import hashlib
import json
from pathlib import Path
import re
import unittest

from PIL import Image

from utils import armor_art, battle_art, cosmetics, db as storage

ROOT = Path(__file__).resolve().parents[1]


class CompleteGhostWardrobe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        armor_art.clear_cache()
        cls.manifest = cosmetics.manifest()
        cls.variants = cls.manifest['sets'][cosmetics.GHOST]['variants']
        cls.roster = set(storage.load_beasts())

    def test_every_species_has_a_unique_real_alpha_fit_with_matching_digest(self):
        self.assertEqual(len(self.roster), 90)
        self.assertEqual(set(self.variants), self.roster)
        self.assertEqual(set(self.manifest['sha256']), set(self.variants.values()))
        self.assertEqual(set(self.manifest['source_sha256']), self.roster)
        self.assertEqual({path.stem for path in (cosmetics.ASSETS / cosmetics.GHOST).glob('*.png')}, self.roster)
        self.assertEqual(self.manifest['sets'][cosmetics.GHOST]['stats'], {})
        self.assertEqual(self.manifest['sets'][cosmetics.GHOST]['unlock_id'], cosmetics.GHOST)
        digests = set()
        for species, relative in self.variants.items():
            with self.subTest(species=species):
                path = (cosmetics.ASSETS / relative).resolve()
                self.assertTrue(path.is_relative_to(cosmetics.ASSETS.resolve()))
                self.assertEqual(path.parent, (cosmetics.ASSETS / cosmetics.GHOST).resolve())
                self.assertEqual(path, cosmetics.variant_path(species, cosmetics.GHOST))
                self.assertEqual(path.name, species + '.png')
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, self.manifest['sha256'][relative])
                self.assertNotIn(digest, digests, 'A reused sprite is not an individually fitted costume')
                digests.add(digest)
                with Image.open(path) as source:
                    self.assertEqual(source.format, 'PNG')
                    self.assertEqual(source.mode, 'RGBA')
                    self.assertLessEqual(source.width, 360)
                    self.assertLessEqual(source.height, 352)
                    low, high = source.getchannel('A').getextrema()
                    self.assertEqual(low, 0)
                    self.assertGreaterEqual(high, 240)
                    self.assertIsNotNone(source.getbbox())
        self.assertTrue(cosmetics.coverage()['complete'])

    def test_every_fit_has_generation_prompt_reference_and_visual_review_provenance(self):
        records = json.loads((cosmetics.ASSETS / 'provenance.json').read_text(encoding='utf-8'))['assets']
        cards = json.loads((ROOT / 'data/card-artwork.json').read_text(encoding='utf-8'))['beasts']
        self.assertEqual(set(records), self.roster)
        artifacts = set()
        for species, record in records.items():
            with self.subTest(species=species):
                self.assertIn('image_gen__imagegen', record['method'])
                self.assertRegex(record['source_artifact'], r'^exec-[a-f0-9-]+\.png$')
                self.assertNotIn(record['source_artifact'], artifacts)
                artifacts.add(record['source_artifact'])
                for field in ('source_sha256', 'canonical_sha256', 'edit_reference_sha256', 'production_sha256'):
                    self.assertTrue(re.fullmatch('[0-9a-f]{64}', record[field]), field)
                self.assertEqual(record['source_sha256'], self.manifest['source_sha256'][species])
                self.assertEqual(record['production_sha256'], self.manifest['sha256'][self.variants[species]])
                self.assertEqual(record['canonical_file'], cards[species]['file'])
                canonical = ROOT / 'data' / record['canonical_file']
                self.assertEqual(record['canonical_sha256'], hashlib.sha256(canonical.read_bytes()).hexdigest())
                self.assertGreater(len(record['prompt']), 200)
                self.assertIn('ghost', record['prompt'].lower())
                self.assertGreater(len(record['review_notes']), 40)
                self.assertTrue(record['reviewer'])
                self.assertTrue(record['reviewed_at'])

    def test_full_roster_portraits_and_battle_sprites_use_the_installed_fit(self):
        appearance = cosmetics.appearance_id(cosmetics.GHOST)
        for species, relative in self.variants.items():
            with self.subTest(species=species):
                with Image.open(cosmetics.ASSETS / relative) as source:
                    expected = source.convert('RGBA')
                actual = armor_art.portrait(species, appearance)
                self.assertEqual(actual.size, expected.size)
                self.assertEqual(actual.tobytes(), expected.tobytes())
                normal = armor_art.portrait(species, '')
                self.assertNotEqual((actual.size, actual.tobytes()), (normal.size, normal.tobytes()))
                expected.thumbnail((180, 176), Image.Resampling.NEAREST)
                rendered = battle_art.sprite(species, appearance)
                self.assertEqual(rendered.size, expected.size)
                self.assertEqual(rendered.tobytes(), expected.tobytes())


if __name__ == '__main__':
    unittest.main()
