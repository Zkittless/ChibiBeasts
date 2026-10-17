"""Build Discord-sized cards from existing sprites; Pillow is a dev-only tool."""
import hashlib
import json
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'card_art'
STARTERS = ('prismite', 'twine', 'gloop', 'barkley')
SIZE = (768, 448)


def sprite(path, bounds):
    original = Image.open(path).convert('RGBA')
    box = original.getchannel('A').point(lambda alpha: 255 if alpha > 16 else 0).getbbox()
    cropped = original.crop(box) if box else original
    factor = min(bounds[0] / cropped.width, bounds[1] / cropped.height)
    return cropped.resize((round(cropped.width * factor), round(cropped.height * factor)), Image.Resampling.NEAREST)


def save(image, name):
    path = OUTPUT / name
    image.save(path, optimize=True, compress_level=9)
    return {'file': f'card_art/{name}', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lineup-only', action='store_true')
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    beasts = json.loads((ROOT / 'data' / 'beasts.json').read_text(encoding='utf-8'))['beasts']
    manifest_path = ROOT / 'data' / 'card-artwork.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if args.lineup_only else {'version': 1, 'portrait_size': list(SIZE), 'beasts': {}}
    for key in (() if args.lineup_only else sorted(beasts)):
        source = ROOT / 'data' / 'sprites' / f'{key}.png'
        portrait = Image.new('RGBA', SIZE)
        beast = sprite(source, (SIZE[0] - 128, SIZE[1] - 48))
        portrait.alpha_composite(beast, ((SIZE[0] - beast.width) // 2, (SIZE[1] - beast.height) // 2))
        entry = save(portrait, f'{key}.png')
        entry['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest['beasts'][key] = entry

    lineup = Image.new('RGBA', (768, 640))
    draw = ImageDraw.Draw(lineup)
    # The bundled font makes the labels reproducible on Windows and Railway/Linux.
    font = ImageFont.load_default(size=25)
    small = ImageFont.load_default(size=18)
    for i, key in enumerate(STARTERS):
        x, y = (i % 2) * 384, (i // 2) * 320
        beast = sprite(ROOT / 'data' / 'sprites' / f'{key}.png', (280, 230))
        lineup.alpha_composite(beast, (x + (384 - beast.width) // 2, y + 8 + (230 - beast.height) // 2))
        draw.rounded_rectangle((x + 78, y + 242, x + 306, y + 311), radius=10, fill='#232428')
        draw.text((x + 192, y + 251), beasts[key]['name'], font=font, fill='#e8e9ef', anchor='mt')
        draw.text((x + 192, y + 283), beasts[key]['type'].title(), font=small, fill='#aeb2c1', anchor='mt')
    manifest['starter_lineup'] = save(lineup, 'starter-lineup.png')
    (ROOT / 'data' / 'card-artwork.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Built {len(beasts)} portraits and the starter lineup ({sum(p.stat().st_size for p in OUTPUT.glob("*.png")) / 1024 / 1024:.2f} MB).')


if __name__ == '__main__':
    main()
