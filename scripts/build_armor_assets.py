"""Package generated, transparent four-part armor kits without painting pixels.

    python scripts/build_armor_assets.py ../output/armor-implementation/generation-prompts.json

The source JSON records built-in image generation prompts and immutable source
paths. Only quadrant extraction, alpha cropping and nearest-neighbor scaling are
performed here. No API calls or hand-drawn replacement artwork are used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PARTS = ('chest', 'shoulder', 'back', 'accessory')
DRAPES = {'sprite_silk_attire', 'unicorn_healer_vestments', 'phoenix_born_shroud',
          'abyssal_shroud', 'thornweave_cloak', 'deepwater_hide', 'blight_weaver_regalia'}


def extract(image):
    width, height = image.size
    for part, box in zip(PARTS, ((0, 0, width//2, height//2), (width//2, 0, width, height//2),
                                (0, height//2, width//2, height), (width//2, height//2, width, height))):
        cutout = image.crop(box)
        bounds = cutout.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
        if not bounds:
            raise ValueError(f'Empty {part}')
        cutout = cutout.crop(bounds)
        cutout.thumbnail((96, 96), Image.Resampling.NEAREST)
        yield part, cutout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prompts', type=Path)
    args = parser.parse_args()
    prompts = json.loads(args.prompts.read_text(encoding='utf-8'))
    assets = ROOT / 'data' / 'armor'
    catalog = json.loads((ROOT / 'data' / 'equipment.json').read_text(encoding='utf-8'))['equipment']
    manifest = {'version': 1, 'sets': {}, 'sha256': {}}
    report = {}
    for armor_id, record in prompts['sets'].items():
        if armor_id not in catalog:
            raise ValueError(f'Unknown armor: {armor_id}')
        source = Path(record['source'])
        image = Image.open(source).convert('RGBA')
        alpha_min, alpha_max = image.getchannel('A').getextrema()
        if alpha_min != 0 or alpha_max < 240:
            raise ValueError(f'{armor_id} is not an actual transparent cutout')
        copied = args.prompts.parent / record['kit']
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        components = {}
        for part, cutout in extract(image):
            relative = f'components/{armor_id}/{part}.png'
            destination = assets / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            cutout.save(destination, optimize=True)
            components[part] = relative
            manifest['sha256'][relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest['sets'][armor_id] = {'name': record['name'], 'components': components,
                                     'front_drape': armor_id in DRAPES,
                                     'accessory': 'shield' if armor_id == 'gnome_iron_plating' else 'clasp'}
        report[armor_id] = {'source': str(source), 'size': image.size,
                            'alpha': image.getchannel('A').getextrema(),
                            'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    if 'gnome_iron_plating' in manifest['sets']:
        variants = {}
        for species in ('twine', 'barkley', 'gloop'):
            original = args.prompts.parent.parent / 'chibibeasts-armor-preview' / 'armor' / f'{species}.png'
            with Image.open(original) as source:
                image = source.convert('RGBA')
            bounds = image.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
            image = image.crop(bounds)
            image.thumbnail((360, 352), Image.Resampling.NEAREST)
            relative = f'variants/gnome_iron_plating/{species}.png'
            destination = assets / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, optimize=True)
            variants[species] = relative
            manifest['sha256'][relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest['sets']['gnome_iron_plating']['variants'] = variants
    assets.mkdir(parents=True, exist_ok=True)
    (assets / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    (args.prompts.parent / 'source-assets.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    # Public provenance keeps exact prompts and source identities without
    # publishing the generating machine's username or absolute local paths.
    provenance = {'mode': prompts['mode'], 'purpose': prompts['purpose'], 'sets': {}}
    for armor_id, record in prompts['sets'].items():
        provenance['sets'][armor_id] = {
            'name': record['name'], 'prompt': record['prompt'],
            'source_filename': Path(record['source']).name,
            'source_sha256': report[armor_id]['sha256'],
            'packaged_components': manifest['sets'][armor_id]['components'],
        }
    (ROOT / 'docs' / 'armor-generation.json').write_text(json.dumps(provenance, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'sets': len(manifest['sets']), 'files': len(manifest['sha256']), 'sources': report}, indent=2))


if __name__ == '__main__':
    main()
