"""Compose full-sprite wardrobe contact sheets without changing source pixels.

    python tools/preview_armor_contacts.py --stage ../output/armor-refit --species prismite

Staged candidates require a successful atlas-validation.json entry. Without
--stage, only complete, registered production wardrobes can be previewed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contacts(species, assets, output, *, validation=None, sizes=(140, 180)):
    """Return four saved previews after validating all sixteen source hashes."""
    catalog = read_json(ROOT / 'data' / 'equipment.json')['equipment']
    roster = read_json(ROOT / 'data' / 'beasts.json')['beasts']
    if species not in roster:
        raise ValueError(f'Unknown species: {species}')
    if validation is not None:
        job = validation.get('jobs', {}).get(species, {})
        if job.get('status') != 'validated_candidate':
            raise ValueError(f'{species} does not have a validated staged wardrobe')
    manifest = read_json(assets / 'manifest.json')
    source_paths = []
    for armor_id in catalog:
        relative = manifest['sets'][armor_id].get('variants', {}).get(species)
        if not relative:
            raise ValueError(f'{species}/{armor_id} has no dedicated full sprite')
        path = (assets / relative).resolve()
        if not path.is_relative_to(assets.resolve()):
            raise ValueError('Preview asset path leaves the packaged directory')
        if digest(path) != manifest.get('sha256', {}).get(relative):
            raise ValueError(f'{species}/{armor_id} does not match the packaged hash')
        source_paths.append(path)
    before = {str(path): digest(path) for path in source_paths}
    output.mkdir(parents=True, exist_ok=True)
    label_font = ImageFont.load_default(size=15)
    title_font = ImageFont.load_default(size=21)
    written = []
    for size in sizes:
        if size < 1:
            raise ValueError('Contact preview size must be positive')
        width = max(220, size + 70)
        height = size + 74
        for theme, background, foreground, muted in (
                ('dark', '#181b1e', '#f7f3e9', '#b6bdc4'),
                ('light', '#f6f2e9', '#1c242b', '#525f69')):
            sheet = Image.new('RGBA', (width * 4, height * 4 + 64), background)
            draw = ImageDraw.Draw(sheet)
            draw.text((20, 13), f'{roster[species]["name"]} | fitted wardrobe | {size}px | {theme}', font=title_font, fill=foreground)
            for index, (armor_id, path) in enumerate(zip(catalog, source_paths)):
                with Image.open(path) as original:
                    sprite = original.convert('RGBA')
                sprite.thumbnail((size, round(size * 176 / 180)), Image.Resampling.NEAREST)
                x, y = index % 4 * width, index // 4 * height + 64
                sheet.alpha_composite(sprite, (x + (width - sprite.width) // 2, y + (size - sprite.height) // 2))
                text = '\n'.join(textwrap.wrap(catalog[armor_id]['name'], width=26))
                label = draw.multiline_textbbox((0, 0), text, font=label_font, spacing=3, align='center')
                draw.multiline_text((x + (width - label[2]) // 2, y + size + 9), text, font=label_font, fill=foreground, spacing=3, align='center')
                draw.text((x + 9, y + size + 47), f'{index + 1:02d}', font=label_font, fill=muted)
            target = output / f'{species}-{size}-{theme}.png'
            sheet.convert('RGB').save(target, optimize=True)
            written.append(str(target.resolve()))
    if before != {str(path): digest(path) for path in source_paths}:
        raise RuntimeError('A source image changed during preview composition')
    report = {'species': species, 'source_images_unchanged': True, 'source_sha256': before,
              'contacts': written, 'staged_candidate': validation is not None,
              'operations': ['RGBA asset read', 'nearest-neighbor thumbnail',
                             'alpha composition onto review sheet', 'UI text labels']}
    (output / f'{species}-contact-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--species', nargs='+', required=True)
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sizes', type=int, nargs='+', default=[140, 180])
    args = parser.parse_args()
    assets = args.stage / 'data' / 'armor' if args.stage else ROOT / 'data' / 'armor'
    validation = read_json(args.stage / 'atlas-validation.json') if args.stage else None
    output = args.output or (args.stage / 'contacts' if args.stage else ROOT.parent / 'output' / 'armor-contacts')
    for species in args.species:
        report = contacts(species, assets, output, validation=validation, sizes=args.sizes)
        print(json.dumps({'species': species, 'source_images_unchanged': report['source_images_unchanged'],
                          'contacts': report['contacts']}, indent=2))


if __name__ == '__main__':
    main()
