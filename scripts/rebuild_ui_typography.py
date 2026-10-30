"""Refresh packaged UI lettering without regenerating creature or realm artwork.

Rebuilds 11 Journal banners, the Workshop banner, and the starter selection
labels. Individual creature cards, sprites, armor, costumes and biome paintings
are read-only. The shared field_journal text renderer supplies the approved font.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_card_artwork import STARTERS, save, starter_lineup
from scripts.prepare_hollowlight_banner import build as build_hollowlight
from scripts.preview_journal_hub import build_headers
from scripts.preview_workshop import build_header as build_workshop
from utils import field_journal as ui
from utils.habitats import BIOMES


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_assets():
    return [ROOT / 'data/journal' / f'journal-{key}.png' for key in (*BIOMES, 'hollowlight')] + [
        ROOT / 'data/workshop/header.png', ROOT / 'data/card_art/starter-lineup.png']


def sources():
    return [ROOT / 'data/battle_realms' / f'{key}.png' for key in (*BIOMES, 'hollowlight')] + [
        ROOT / 'data/sprites' / f'{key}.png' for key in STARTERS] + [
        ROOT / 'data/card_art' / f'{key}.png' for key in ('twine', 'patchkin')]


def contacts(paths, output):
    headers = paths[:-1]
    for page, start in enumerate(range(0, len(headers), 6), 1):
        selected = headers[start:start + 6]
        contact = Image.new('RGB', (1280, 34 + len(selected) * 156), '#15232a')
        draw = ImageDraw.Draw(contact)
        ui.text(draw, 'BEFORE', (12, 4, 620, 30), size=20, display=True)
        ui.text(draw, 'APPROVED MANILLA PIXEL TEXT', (652, 4, 1268, 30), size=20, display=True)
        for row, path in enumerate(selected):
            old = output / 'before' / path.relative_to(ROOT)
            for column, source in enumerate((old, path)):
                with Image.open(source) as image:
                    contact.paste(image.convert('RGB'), (column * 640, 34 + row * 156))
                ui.text(draw, path.name, (column * 640 + 12, 164 + row * 156,
                                         column * 640 + 628, 187 + row * 156), size=13)
        contact.save(output / f'headers-{page}.png', optimize=True)
    path = paths[-1]
    comparison = Image.new('RGBA', (1536, 682), '#15232a')
    draw = ImageDraw.Draw(comparison)
    ui.text(draw, 'BEFORE', (12, 4, 750, 34), size=22, display=True)
    ui.text(draw, 'APPROVED MANILLA PIXEL TEXT', (780, 4, 1524, 34), size=22, display=True)
    for column, source in enumerate((output / 'before' / path.relative_to(ROOT), path)):
        with Image.open(source) as image:
            comparison.alpha_composite(image.convert('RGBA'), (column * 768, 42))
    comparison.convert('RGB').save(output / 'starter-labels.png', optimize=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'output/typography/static-assets')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = text_assets()
    paint_hashes = {str(path.relative_to(ROOT)): digest(path) for path in sources()}
    manifest_path = ROOT / 'data/card-artwork.json'
    manifest = read(manifest_path)
    original_cards = dict(manifest['beasts'])
    for path in paths:
        backup = output / 'before' / path.relative_to(ROOT)
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
    build_headers()
    build_hollowlight()
    build_workshop()
    beasts = read(ROOT / 'data/beasts.json')['beasts']
    manifest['starter_lineup'] = save(starter_lineup(beasts), 'starter-lineup.png')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    assert read(manifest_path)['beasts'] == original_cards
    assert paint_hashes == {str(path.relative_to(ROOT)): digest(path) for path in sources()}
    contacts(paths, output)
    records = []
    for path in paths:
        with Image.open(path) as image:
            records.append({'file': path.relative_to(ROOT).as_posix(),
                            'size': list(image.size), 'sha256': digest(path)})
    report = {'status': 'passed', 'scope': '13 packaged runtime UI images; paintings and individual cards unchanged.',
              'font': 'Pixelify Sans', 'palette': 'Approved warm manilla headings / white body text',
              'assets': records, 'source_artwork_sha256': paint_hashes,
              'individual_card_manifest_unchanged': True}
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'passed', 'assets': len(records), 'contacts': str(output)}))


if __name__ == '__main__':
    main()
