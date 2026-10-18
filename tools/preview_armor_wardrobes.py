"""Export an offline beast picker showing all sixteen actual armor sprites.

    python tools/preview_armor_wardrobes.py --require-complete

Optionally read a validated review --stage before production installation.
Copies registered source PNGs without painting, re-encoding or resizing them.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'output/armor-refit/wardrobes')
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--species', nargs='+')
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    armor = read(ROOT / 'data/equipment.json')['equipment']
    roster = read(ROOT / 'data/beasts.json')['beasts']
    if args.species:
        unknown = set(args.species) - roster.keys()
        if unknown:
            raise ValueError(f'Unknown beasts: {sorted(unknown)}')
        roster = {key: value for key, value in roster.items() if key in args.species}
    assets = (args.stage.resolve() if args.stage else ROOT) / 'data/armor'
    manifest = read(assets / 'manifest.json')
    beasts, files, missing = [], [], []
    for species, item in roster.items():
        variants = {}
        for armor_id in armor:
            relative = manifest['sets'][armor_id].get('variants', {}).get(species)
            dedicated = relative is not None
            if dedicated:
                source = (assets / relative).resolve()
                if not source.is_relative_to(assets.resolve()):
                    raise ValueError(f'Unsafe registered path: {relative}')
                if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['sha256'][relative]:
                    raise ValueError(f'Hash mismatch: {relative}')
            else:
                missing.append((species, armor_id))
                source = ROOT / 'data/sprites' / f'{species}.png'
            destination = Path('portraits') / species / f'{armor_id}.png'
            files.append((source, destination))
            variants[armor_id] = {'src': destination.as_posix(), 'dedicated': dedicated}
        beasts.append({'id': species, 'name': item['name'], 'variants': variants,
                       'dedicated': sum(value['dedicated'] for value in variants.values())})
    if args.require_complete and missing:
        raise ValueError(f'Review requires complete wardrobes; {len(missing)} pairs are missing')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for source, relative in files:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    options = ''.join(f'<option value="{key}">{html.escape(value["name"])}</option>' for key, value in roster.items())
    figures = ''.join(
        f'<figure data-armor="{key}" data-name="{html.escape(item["name"], quote=True)}">'
        f'<img width="210" height="210" alt="{html.escape(item["name"], quote=True)}">'
        f'<figcaption>{html.escape(item["name"])}<span class="unavailable" hidden><br>Canonical fallback</span></figcaption></figure>'
        for key, item in armor.items())
    page = Path(__file__).with_name('armor_gallery.html').read_text(encoding='utf-8')
    for token, value in (('__OPTIONS__', options), ('__FIGURES__', figures),
                         ('__BEASTS__', json.dumps(beasts, separators=(',', ':')))):
        page = page.replace(token, value)
    (output / 'index.html').write_text(page, encoding='utf-8')
    report = {'species': len(beasts), 'armor_sets': len(armor), 'combinations': len(files),
              'dedicated': len(files) - len(missing), 'missing': missing,
              'page': str(output / 'index.html')}
    (output / 'gallery-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'missing'}, indent=2))


if __name__ == '__main__':
    main()
