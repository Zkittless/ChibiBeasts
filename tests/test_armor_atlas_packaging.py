"""Generated wardrobe inputs must be reviewable before production registration."""
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from scripts import stage_armor_atlas as package


ORDER = [f'armor_{index}' for index in range(16)]
ORDER[1] = 'gnome_iron_plating'


def atlas():
    image = Image.new('RGBA', (256, 256))
    draw = ImageDraw.Draw(image)
    for index in range(16):
        x, y = index % 4 * 64, index // 4 * 64
        draw.rectangle((x + 12, y + 10, x + 50, y + 54), fill=(index * 12, 80, 160, 255))
        image.putpixel((x + 12, y + 10), (index * 12, 80, 160, 79))
    return image


class AtlasExtraction(unittest.TestCase):
    def test_row_major_mapping_preserves_alpha_and_only_scales_down(self):
        image = atlas()
        before = image.tobytes()
        sprites, report = package.extract_atlas(image, ORDER)
        self.assertEqual(image.tobytes(), before)
        self.assertEqual(list(sprites), ORDER)
        for index, armor in enumerate(ORDER):
            self.assertEqual(sprites[armor].size, (39, 45))
            self.assertEqual(sprites[armor].getpixel((0, 0)), (index * 12, 80, 160, 79))
            self.assertEqual(report[armor]['row'], index // 4)
            self.assertEqual(report[armor]['column'], index % 4)
        small, _ = package.extract_atlas(image, ORDER, max_size=(20, 20))
        self.assertTrue(all(max(sprite.size) <= 20 for sprite in small.values()))

    def test_rejects_empty_clipped_opaque_or_composite_cells(self):
        bad = atlas()
        bad.paste((0, 0, 0, 0), (0, 0, 64, 64))
        with self.assertRaisesRegex(ValueError, 'empty'):
            package.extract_atlas(bad, ORDER)
        bad = atlas()
        bad.putpixel((0, 32), (255, 0, 0, 255))
        with self.assertRaisesRegex(ValueError, 'edge'):
            package.extract_atlas(bad, ORDER)
        with self.assertRaisesRegex(ValueError, 'transparent'):
            package.extract_atlas(Image.new('RGBA', (256, 256), 'white'), ORDER)
        bad = atlas()
        bad.paste((0, 0, 0, 0), (0, 0, 64, 64))
        draw = ImageDraw.Draw(bad)
        draw.rectangle((5, 10, 25, 50), fill='red')
        draw.rectangle((35, 10, 55, 50), fill='blue')
        with self.assertRaisesRegex(ValueError, 'multiple substantial'):
            package.extract_atlas(bad, ORDER)

    def test_proportional_grid_preserves_odd_canvas_dimensions_without_guessing_order(self):
        image = Image.new('RGBA', (258, 258))
        draw = ImageDraw.Draw(image)
        for index in range(16):
            x, y = index % 4 * 258 // 4, index // 4 * 258 // 4
            draw.rectangle((x + 12, y + 10, x + 50, y + 54), fill=(index * 12, 80, 160, 255))
        _, report = package.extract_atlas(image, ORDER)
        self.assertEqual(report[ORDER[-1]]['cell_bounds'], [193, 193, 258, 258])
        self.assertEqual(report[ORDER[0]]['cell_bounds'], [0, 0, 64, 64])
        with self.assertRaisesRegex(ValueError, '16 distinct'):
            package.extract_atlas(atlas(), ORDER[:-1])
        with self.assertRaisesRegex(ValueError, 'RGBA'):
            package.extract_atlas(atlas().convert('RGB'), ORDER)


class ReviewBeforeRegistration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'game'
        self.assets = self.root / 'data' / 'armor'
        self.assets.mkdir(parents=True)
        (self.root / 'data' / 'equipment.json').write_text(json.dumps({'equipment': dict.fromkeys(ORDER, {})}))
        (self.root / 'data' / 'beasts.json').write_text(json.dumps({'beasts': {'twine': {}, 'prismite': {}}}))
        original = self.assets / 'variants' / 'gnome_iron_plating' / 'twine.png'
        original.parent.mkdir(parents=True)
        Image.new('RGBA', (4, 4), (200, 10, 10, 255)).save(original)
        self.original = original
        relative = 'variants/gnome_iron_plating/twine.png'
        self.manifest = {'version': 1, 'sets': {key: {'components': {'chest': 'legacy.png'}} for key in ORDER},
                         'sha256': {relative: package.sha256(original)}}
        self.manifest['sets']['gnome_iron_plating']['variants'] = {'twine': relative}
        (self.assets / 'manifest.json').write_text(json.dumps(self.manifest))
        self.source = Path(self.temporary.name) / 'twine-atlas.png'
        atlas().save(self.source)
        self.jobs = Path(self.temporary.name) / 'jobs.json'
        self.document = {'armor_order': ORDER, 'jobs': {'twine': {
            'source': self.source.name, 'prompt': 'Test fixture, no generated art',
            'status': 'generated', 'approved': False,
        }}}
        self.jobs.write_text(json.dumps(self.document))
        self.stage = Path(self.temporary.name) / 'review'

    def test_staging_preserves_sources_and_approved_gnome_reports_actual_coverage(self):
        source_hash = package.sha256(self.source)
        original_hash = package.sha256(self.original)
        production_manifest = (self.assets / 'manifest.json').read_bytes()
        result = package.stage_jobs(self.jobs, stage=self.stage, root=self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['coverage']['dedicated_pairs'], 16)
        self.assertEqual(result['coverage']['expected_pairs'], 32)
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(len(result['coverage']['species']['prismite']['legacy_component_only']), 16)
        self.assertEqual(len(result['jobs']['twine']['variants']), 15)
        self.assertEqual(package.sha256(self.source), source_hash)
        self.assertEqual(package.sha256(self.original), original_hash)
        self.assertEqual((self.assets / 'manifest.json').read_bytes(), production_manifest)
        self.assertEqual(package.install_approved(result, stage=self.stage, root=self.root), 0)

    def test_explicit_approval_registers_validated_files_only_and_checks_hashes(self):
        self.document['jobs']['twine']['approved'] = True
        self.jobs.write_text(json.dumps(self.document))
        result = package.stage_jobs(self.jobs, stage=self.stage, root=self.root)
        first = next(iter(result['jobs']['twine']['variants'].values()))
        staged_file = self.stage / 'data' / 'armor' / first
        correct_bytes = staged_file.read_bytes()
        staged_file.write_bytes(b'changed after review')
        with self.assertRaisesRegex(ValueError, 'changed after validation'):
            package.install_approved(result, stage=self.stage, root=self.root)
        self.assertEqual(json.loads((self.assets / 'manifest.json').read_text()), self.manifest)
        staged_file.write_bytes(correct_bytes)
        self.assertEqual(package.install_approved(result, stage=self.stage, root=self.root), 15)
        final = json.loads((self.assets / 'manifest.json').read_text())
        self.assertEqual(len([record for record in final['sets'].values() if record.get('variants', {}).get('twine')]), 16)
        self.assertEqual(package.sha256(self.original), self.manifest['sha256']['variants/gnome_iron_plating/twine.png'])

    def test_catalog_order_is_required_and_rejected_atlas_does_not_register(self):
        bad_order = dict(self.document, armor_order=list(reversed(ORDER)))
        self.jobs.write_text(json.dumps(bad_order))
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            package.stage_jobs(self.jobs, stage=self.stage, root=self.root)
        self.jobs.write_text(json.dumps(self.document))
        Image.new('RGBA', (256, 256), 'white').save(self.source)
        result = package.stage_jobs(self.jobs, stage=self.stage, root=self.root)
        self.assertEqual(result['jobs']['twine']['status'], 'rejected')
        self.assertEqual(result['coverage']['dedicated_pairs'], 1)
        self.assertEqual(package.install_approved(result, stage=self.stage, root=self.root), 0)


if __name__ == '__main__':
    unittest.main()
