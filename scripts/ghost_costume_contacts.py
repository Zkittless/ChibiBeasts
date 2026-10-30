"""Full-roster canonical/costume QA pages at 180px on light and dark backgrounds."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent / 'output/hollowlight-carnival/ghost-costumes'


def cutout(path):
    with Image.open(path) as source:
        image = source.convert('RGBA')
    box = image.getchannel('A').point(lambda alpha: 255 if alpha > 20 else 0).getbbox()
    return image.crop(box)


def build():
    roster = json.loads((ROOT / 'data/beasts.json').read_text(encoding='utf-8'))['beasts']
    cards = json.loads((ROOT / 'data/card-artwork.json').read_text(encoding='utf-8'))['beasts']
    target = WORK / 'full-roster-contacts'
    target.mkdir(parents=True, exist_ok=True)
    index = []
    names = list(roster)
    for page, start in enumerate(range(0, len(names), 9), 1):
        group = names[start:start + 9]
        for theme, color, ink in (('light', '#eef1f7', '#152237'), ('dark', '#202832', '#f8f9ff')):
            canvas = Image.new('RGBA', (1260, 750), color)
            draw = ImageDraw.Draw(canvas)
            for slot, name in enumerate(group):
                x, y = slot % 3 * 420, slot // 3 * 250
                draw.text((x + 10, y + 8), name, fill=ink)
                draw.text((x + 10, y + 28), 'Canonical (180px)', fill=ink)
                draw.text((x + 220, y + 28), 'Ghost costume (180px)', fill=ink)
                paths = (ROOT / 'data' / cards[name]['file'], ROOT / 'data/cosmetics/ghost_costume_2026' / (name + '.png'))
                for column, path in enumerate(paths):
                    image = cutout(path)
                    image.thumbnail((180, 176), Image.Resampling.NEAREST)
                    canvas.alpha_composite(image, (x + column * 210 + (210 - image.width) // 2,
                                                  y + 55 + (176 - image.height) // 2))
            destination = target / f'roster-{page:02d}-{theme}.png'
            canvas.convert('RGB').save(destination)
        index.append(dict(page=page, species=group, light=f'roster-{page:02d}-light.png', dark=f'roster-{page:02d}-dark.png'))
    (target / 'index.json').write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {len(index)} light/dark roster pages at {target}')


if __name__ == '__main__':
    build()
