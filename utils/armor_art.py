"""Species-specific, packaged full-sprite armor appearances.

Complete dressed sprites are illustrated for each species/armor combination.
Missing appearances retain the canonical portrait; shared component stretching
is deliberately unsupported. Rendering never reads or changes player equipment.
"""
from functools import lru_cache
import json
import logging

from PIL import Image

from utils.artwork import PROJECT_ROOT, card_artwork_path

logger = logging.getLogger('chibibeasts.armor.art')
ASSETS = PROJECT_ROOT / 'data' / 'armor'
SIZE = (360, 352)


@lru_cache(maxsize=1)
def manifest():
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))


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


def _variant_path(record, species):
    """Resolve a registered local full sprite without accepting path escapes."""
    if not isinstance(record, dict) or not isinstance(record.get('variants'), dict):
        return None
    relative = record['variants'].get(species)
    if not isinstance(relative, str) or not relative:
        return None
    path = (ASSETS / relative).resolve()
    if not path.is_relative_to(ASSETS.resolve()):
        raise ValueError('Armor appearance path is outside packaged assets')
    return path


@lru_cache(maxsize=64)
def _portrait(species, armor_id):
    original = _base(species)
    if original is None or not armor_id:
        return original
    try:
        records = manifest().get('sets', {})
        if not isinstance(records, dict):
            return original
        path = _variant_path(records.get(armor_id), species)
        if path is None:
            return original
        image = _cutout(path)
        if image is None:
            return original
        image.thumbnail(SIZE, Image.Resampling.NEAREST)
        return image
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        # A missing optional asset must never break an acknowledged game action.
        logger.warning('Unable to load armor %s on %s', armor_id, species, exc_info=True)
        return original


def coverage():
    """Report actual dedicated assets; base fallbacks never count as armor art.

    This diagnostic is intentionally separate from the cached rendering path.
    It inspects registration and file existence, while packaging/tests verify
    source hashes and image integrity before release.
    """
    from utils.catalog import load_catalog
    armor_ids = list(load_catalog('equipment.json')['equipment'])
    species_ids = list(load_catalog('beasts.json')['beasts'])
    records = manifest().get('sets', {})
    if not isinstance(records, dict):
        records = {}
    dedicated, missing = [], []
    for species in species_ids:
        for armor_id in armor_ids:
            try:
                path = _variant_path(records.get(armor_id), species)
                exists = path is not None and path.is_file()
            except (OSError, ValueError, TypeError):
                exists = False
            (dedicated if exists else missing).append((species, armor_id))
    return {'expected_pairs': len(species_ids) * len(armor_ids),
            'dedicated_pairs': len(dedicated), 'missing_pairs': len(missing),
            'complete': not missing, 'dedicated': dedicated, 'missing': missing}


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
    _base.cache_clear()
    manifest.cache_clear()
