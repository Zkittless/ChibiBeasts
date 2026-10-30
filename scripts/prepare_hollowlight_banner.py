"""Build the seasonal Journal banner from its approved packaged biome art."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import field_journal as ui
from utils.artwork import card_artwork_path


def build():
    with Image.open(ROOT / 'data/battle_realms/hollowlight.png') as source:
        image = ui.backdrop(source, (640, 126))
        scene = ui.backdrop(source, (624, 110))
    ui.panel(image, (5, 5, 635, 121), corners=False)
    image.alpha_composite(scene, (8, 8))
    draw = ImageDraw.Draw(image)
    ui.text(draw, 'HOLLOWLIGHT CARNIVAL', (22, 17, 470, 33), size=11, fill=ui.GOLD)
    ui.text(draw, 'Field Journal', (20, 39, 487, 88), size=35, display=True)
    ui.text(draw, 'Follow the lanterns. Find your way home.', (23, 94, 495, 111), size=12)
    with Image.open(card_artwork_path('patchkin')) as source:
        portrait = source.convert('RGBA')
    portrait = portrait.crop(portrait.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox())
    portrait.thumbnail((98, 98), Image.Resampling.NEAREST)
    image.alpha_composite(portrait, (524, 119 - portrait.height))
    target = ROOT / 'data/journal/journal-hollowlight.png'
    image.convert('RGB').save(target, optimize=True)
    print(target)


if __name__ == '__main__':
    build()
