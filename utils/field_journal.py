"""Shared Field Journal drawing primitives. No combat, I/O or client state.

Every label is placed by its actual ink bounds. Resource values own an inset
inside their track; ULT's label, three orbs and count occupy separate boxes.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import threading

from PIL import Image, ImageDraw, ImageFont, ImageOps

INK = '#f2e8cd'
MUTED = '#bac5b4'
GOLD = '#b7ad87'
HEALTH = '#8edbb7'
MANA = '#96bfe9'
SURFACE = '#102b2c'
_fonts = threading.local()
DISPLAY_FONT = Path(__file__).resolve().parents[1] / 'data' / 'fonts' / 'Cinzel.ttf'


@dataclass(frozen=True)
class Text:
    value: str
    size: int
    ink: tuple[float, float, float, float]


def font(size, display=False):
    if not hasattr(_fonts, 'faces'):
        _fonts.faces = {}
    key = (size, display)
    if key not in _fonts.faces:
        try:
            _fonts.faces[key] = ImageFont.truetype(str(DISPLAY_FONT), size) if display else ImageFont.load_default(size=size)
        except OSError:
            _fonts.faces[key] = ImageFont.load_default(size=size)
    return _fonts.faces[key]


def clean(value):
    return ' '.join(''.join(c for c in str(value) if c.isprintable()).replace('**', '').replace('`', '').split())


def text(draw, value, box, *, size=18, minimum=10, fill=INK, display=False,
         align='left', ellipsis=True, outline=False):
    """Fit both axes and compensate for glyph bearings, including descenders."""
    value = clean(value)
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    stroke = 1 if outline else 0
    if width <= 0 or height <= 0:
        raise ValueError('Text needs a positive content rectangle')
    for chosen in range(size, minimum - 1, -1):
        face = font(chosen, display)
        bounds = draw.textbbox((0, 0), value, font=face, stroke_width=stroke)
        if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
            break
    else:
        chosen, face = minimum, font(minimum, display)
        if not ellipsis:
            raise ValueError('Numeric label exceeds its content rectangle')
        while value:
            bounds = draw.textbbox((0, 0), value, font=face, stroke_width=stroke)
            if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
                break
            value = value[:-2] + '~' if len(value) > 1 else ''
    bounds = draw.textbbox((0, 0), value, font=face, stroke_width=stroke)
    ink_width, ink_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x = left + (width - ink_width) / 2 if align == 'center' else right - ink_width if align == 'right' else left
    y = top + (height - ink_height) / 2
    # The origin is not the visible top-left for TrueType fonts.
    origin = (round(x - bounds[0]), round(y - bounds[1]))
    draw.text(origin, value, font=face, fill=fill, stroke_width=stroke, stroke_fill='#071c24')
    actual = draw.textbbox(origin, value, font=face, stroke_width=stroke)
    return Text(value, chosen, actual)


def panel(image, box, *, corners=True):
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=SURFACE, outline='#071a1d', width=3)
    draw.rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), outline='#777f6a', width=1)
    draw.line((x0 + 4, y1 - 6, x0 + 4, y0 + 4, x1 - 6, y0 + 4), fill='#d0c59d', width=1)
    draw.line((x0 + 6, y1 - 4, x1 - 4, y1 - 4, x1 - 4, y0 + 6), fill='#344b45', width=1)
    if corners:
        for x, y in ((x0 + 2, y0 + 2), (x1 - 2, y0 + 2), (x0 + 2, y1 - 2), (x1 - 2, y1 - 2)):
            draw.rectangle((x - 1, y - 1, x + 1, y + 1), fill='#dfd4ac')


def backdrop(source, size):
    image = ImageOps.fit(source, size, Image.Resampling.NEAREST).convert('RGBA')
    shade = Image.new('RGBA', size)
    draw = ImageDraw.Draw(shade)
    for y in range(size[1]):
        alpha = round(135 * max(0, 1 - y / 230) + 30 * max(0, (y - size[1] * .6) / (size[1] * .4)))
        draw.line((0, y, size[0], y), fill=(3, 19, 22, alpha))
    image.alpha_composite(shade)
    return image


def heading(image, title, realm):
    panel(image, (164, 8, 476, 45))
    draw = ImageDraw.Draw(image)
    text(draw, title, (174, 15, 466, 38), size=24, display=True, align='center')
    text(draw, realm.removeprefix('The '), (12, 49, 628, 65), size=13, fill='#e2d0a5', align='center')


def portrait(image, source, box):
    panel(image, box, corners=False)
    x0, y0, x1, y1 = box
    if source is not None:
        copy = source.copy()
        copy.thumbnail((x1 - x0 - 12, y1 - y0 - 12), Image.Resampling.NEAREST)
        image.alpha_composite(copy, (x0 + (x1 - x0 - copy.width) // 2, y1 - 6 - copy.height))
    else:
        text(ImageDraw.Draw(image), '?', (x0 + 8, y0 + 8, x1 - 8, y1 - 8), size=28, align='center')


def _compact(number):
    number = max(0, int(number))
    for threshold, suffix in ((10**18, 'E'), (10**15, 'P'), (10**12, 'T'), (10**9, 'B'), (10**6, 'M'), (10**3, 'K')):
        if number >= threshold:
            return f'{number / threshold:.3g}{suffix}'
    return str(number)


def resource_bar(image, box, value, maximum, label, color, *, markers=()):
    """Return real text/track geometry so overflow checks inspect painted ink."""
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(image)
    text(draw, label, (x0, y0 + 2, x0 + 28, y1 - 2), size=16, fill=MUTED)
    track = (x0 + 31, y0, x1, y1)
    draw.rectangle(track, fill='#758675')
    draw.rectangle((track[0] + 1, y0 + 1, x1 - 1, y1 - 1), fill='#071c24')
    interior = (track[0] + 3, y0 + 3, x1 - 3, y1 - 3)
    ratio = min(1, max(0, value / max(1, maximum)))
    width = round((interior[2] - interior[0]) * ratio)
    if width:
        draw.rectangle((interior[0], interior[1], interior[0] + width, interior[3]), fill=color)
        draw.line((interior[0], interior[1], interior[0] + width, interior[1]), fill='#c5e5d4')
        draw.line((interior[0], interior[3], interior[0] + width, interior[3]), fill='#5b7b78')
    for fraction in markers:
        x = track[0] + round((x1 - track[0]) * fraction)
        draw.line((x, y1 - 2, x, y1 - 1), fill='#071c24', width=2)
    content = (track[0] + 6, y0 + 4, x1 - 6, y1 - 4)
    exact = f'{max(0, int(value)):,} / {max(1, int(maximum)):,}'
    # Full values are preferred; only numbers larger than a normal encounter's
    # totals use compact units. Exact totals always remain in native Discord text.
    if draw.textlength(exact, font=font(11)) > content[2] - content[0]:
        exact = f'{_compact(value)} / {_compact(maximum)}'
    fitted = text(draw, exact, content, size=24, minimum=8, align='center', ellipsis=False, outline=True)
    return {'track': track, 'content': content, 'text': fitted}


def ultimate_boxes(x, y):
    return {'label': (x, y, x + 35, y + 20),
            'orbs': tuple((x + 48 + i * 28, y + 1, x + 66 + i * 28, y + 19) for i in range(3)),
            'count': (x + 143, y, x + 185, y + 20)}


@lru_cache(maxsize=2)
def orb(active):
    image = Image.new('RGBA', (19, 19))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, 18, 18), fill='#0a2025', outline=GOLD if active else '#748178', width=2)
    if active:
        draw.ellipse((3, 3, 15, 15), fill='#2e5a80')
        draw.ellipse((4, 3, 14, 13), fill='#71b3d8')
        draw.ellipse((5, 4, 11, 10), fill='#ceeff3')
        draw.rectangle((7, 4, 9, 6), fill='#f6ffff')
    else:
        draw.ellipse((3, 3, 15, 15), fill='#263f40')
    return image


def ultimate(image, x, y, charges):
    boxes = ultimate_boxes(x, y)
    draw = ImageDraw.Draw(image)
    label = text(draw, 'ULT', boxes['label'], size=15, fill=MUTED)
    charges = max(0, min(3, int(charges)))
    for i, box in enumerate(boxes['orbs']):
        image.alpha_composite(orb(i < charges), (box[0], box[1]))
    count = text(draw, f'{charges}/3', boxes['count'], size=16, fill=MUTED, align='center')
    return {**boxes, 'label_text': label, 'count_text': count}


def fighter_panel(image, fighter, source, x, hp, mp, visible):
    panel(image, (x, 72, x + 300, 204))
    portrait(image, source, (x + 9, 84, x + 67, 142))
    draw = ImageDraw.Draw(image)
    text(draw, f'{fighter.name}  Lv.{fighter.level}', (x + 78, 81, x + 290, 104), size=20, display=True)
    health = resource_bar(image, (x + 78, 108, x + 290, 138), hp, fighter.max_hp, 'HP', HEALTH if hp / fighter.max_hp > .3 else '#e6ad8b')
    mana = resource_bar(image, (x + 78, 142, x + 290, 172), mp, fighter.max_mana, 'MP', MANA)
    ult = ultimate(image, x + 78, 178, visible.charges)
    state = 'KO' if visible.hp <= 0 else visible.status or ('GUARD' if visible.guard else 'SHIELD' if visible.shield else fighter.element)
    text(ImageDraw.Draw(image), state.upper(), (x + 9, 148, x + 67, 180), size=11, fill=MUTED, align='center')
    return health, mana, ult


def journal(image, top, title, lines=()):
    """A title and up to two clipped-safe lines below the fighting silhouettes."""
    panel(image, (10, top + 4, 630, top + 66))
    draw = ImageDraw.Draw(image)
    text(draw, 'BATTLE JOURNAL', (20, top + 11, 190, top + 24), size=11, fill=GOLD)
    content = [clean(line) for line in lines if clean(line)]
    if not content:
        content = [title]
    if len(content) == 1:
        text(draw, content[0], (20, top + 31, 620, top + 55), size=17)
    else:
        text(draw, content[-2], (20, top + 28, 620, top + 43), size=14)
        text(draw, content[-1], (20, top + 46, 620, top + 60), size=13, fill=MUTED)
