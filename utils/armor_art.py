"""Packaged armor cutouts fitted to canonical creature portraits.

All fitting happens locally, before the battle renderer moves a sprite. Saved
combat snapshots choose an appearance; rendering never reads or changes gear.
"""
from functools import lru_cache
import json
import logging

from PIL import Image, ImageChops, ImageDraw, ImageOps

from utils.artwork import PROJECT_ROOT, card_artwork_path

logger = logging.getLogger('chibibeasts.armor.art')
ASSETS = PROJECT_ROOT / 'data' / 'armor'
SIZE = (360, 352)


@lru_cache(maxsize=1)
def manifest():
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def fits():
    return json.loads((PROJECT_ROOT / 'data' / 'armor_fits.json').read_text(encoding='utf-8'))['profiles']


def _cutout(path):
    with Image.open(path) as source:
        image = source.convert('RGBA')
    box = image.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
    return image.crop(box) if box else None


@lru_cache(maxsize=86)
def _base(species):
    try:
        image = _cutout(card_artwork_path(species))
    except (KeyError, OSError):
        return None
    if image:
        image.thumbnail(SIZE, Image.Resampling.NEAREST)
    return image


@lru_cache(maxsize=64)
def _component(armor_id, part):
    # Both keys must occur in our packaged manifest, never in a supplied path.
    record = manifest()['sets'][armor_id]
    return _cutout(ASSETS / record['components'][part])


def _box(bounds, size):
    x, y, width, height = bounds
    w, h = size
    left, top = round(x*w), round(y*h)
    return left, top, max(left+1, round((x+width)*w)), max(top+1, round((y+height)*h))


def _place(canvas, source, bounds, *, mirror=False):
    if source is None:
        return
    left, top, right, bottom = _box(bounds, canvas.size)
    component = ImageOps.mirror(source) if mirror else source
    component = component.resize((right-left, bottom-top), Image.Resampling.NEAREST)
    canvas.alpha_composite(component, (left, top))


@lru_cache(maxsize=64)
def _portrait(species, armor_id):
    original = _base(species)
    if original is None or not armor_id:
        return original
    try:
        record = manifest()['sets'].get(armor_id)
        fit = fits().get(species) if record else None
        if not record or not fit:
            return original
        override = record.get('variants', {}).get(species)
        if override:
            image = _cutout(ASSETS / override)
            if image is None:
                return original
            image.thumbnail(SIZE, Image.Resampling.NEAREST)
            return image

        rear = Image.new('RGBA', original.size)
        front = Image.new('RGBA', original.size)
        cape = _component(armor_id, 'back')
        _place(rear, cape, fit['back'])
        if record.get('front_drape'):
            _place(front, cape, fit['back'])
        _place(front, _component(armor_id, 'chest'), fit['chest'])
        for index, bounds in enumerate(fit['shoulders']):
            _place(front, _component(armor_id, 'shoulder'), bounds, mirror=bool(index % 2))
        x, y, w, h = fit['chest']
        if record.get('accessory') == 'shield':
            accessory = [max(0, x-w*.35), y+h*.34, w*.64, h*.72]
        else:
            accessory = [x+w*.31, y+h*.05, w*.38, h*.36]
        accessory[2] = min(accessory[2], 1-accessory[0])
        accessory[3] = min(accessory[3], 1-accessory[1])
        _place(front, _component(armor_id, 'accessory'), accessory)

        # No rectangular paste edges or floating breastplates beyond a body.
        body = original.getchannel('A').point(lambda a: 255 if a > 20 else 0)
        front.putalpha(ImageChops.multiply(front.getchannel('A'), body))
        result = Image.alpha_composite(rear, original)
        result = Image.alpha_composite(result, front)
        protected = Image.new('L', original.size)
        draw = ImageDraw.Draw(protected)
        for bounds in fit['protected']:
            left, top, right, bottom = _box(bounds, original.size)
            draw.rectangle((left, top, right-1, bottom-1), fill=255)
        return Image.composite(original, result, protected)
    except (OSError, KeyError, ValueError):
        # A missing optional asset must never break an acknowledged game action.
        logger.warning('Unable to fit armor %s on %s', armor_id, species, exc_info=True)
        return original


def portrait(species, armor_id=''):
    """Return an independent RGBA cutout, or None for an unknown species.

    A bounded cache keeps repeated move frames fast. Copies keep a caller's
    thumbnail/alpha operation from contaminating another trainer's appearance.
    """
    image = _portrait(species, armor_id or '')
    return image.copy() if image is not None else None


def clear_cache():
    """Development hook after replacing artwork; production reloads on restart."""
    _portrait.cache_clear()
    _component.cache_clear()
    _base.cache_clear()
    fits.cache_clear()
    manifest.cache_clear()
