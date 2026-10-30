"""Owned, per-companion appearance overrides. Cosmetics never change stats."""
from functools import lru_cache
import json
import time

import aiosqlite
from PIL import Image

from utils import db as storage
from utils.artwork import PROJECT_ROOT
from utils.duels import exclusive_player

ASSETS = PROJECT_ROOT / 'data/cosmetics'
GHOST = 'ghost_costume_2026'
EVENT = 'hollowlight_2026'
PREFIX = 'cosmetic:'


@lru_cache(maxsize=1)
def manifest():
    try:
        return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'version': 1, 'sets': {}, 'sha256': {}}


def appearance_id(cosmetic_id):
    return PREFIX + cosmetic_id if cosmetic_id == GHOST else ''


def cosmetic_id(appearance):
    return GHOST if appearance == PREFIX + GHOST else ''


def variant_path(species, cosmetic):
    relative = manifest().get('sets', {}).get(cosmetic, {}).get('variants', {}).get(species)
    if not isinstance(relative, str):
        return None
    path = (ASSETS / relative).resolve()
    if not path.is_relative_to(ASSETS.resolve()):
        return None
    return path if path.is_file() else None


def revision(species, appearance):
    cosmetic = cosmetic_id(appearance)
    relative = manifest().get('sets', {}).get(cosmetic, {}).get('variants', {}).get(species)
    return manifest().get('sha256', {}).get(relative, '')


@lru_cache(maxsize=128)
def _portrait(species, cosmetic):
    path = variant_path(species, cosmetic)
    if path is None:
        return None
    with Image.open(path) as source:
        image = source.convert('RGBA')
    box = image.getchannel('A').point(lambda alpha: 255 if alpha > 20 else 0).getbbox()
    if not box:
        return None
    image = image.crop(box)
    image.thumbnail((360, 352), Image.Resampling.NEAREST)
    return image


def portrait(species, appearance):
    image = _portrait(species, cosmetic_id(appearance))
    return image.copy() if image is not None else None


def coverage():
    roster = storage.load_beasts()
    present = [species for species in roster if variant_path(species, GHOST)]
    return dict(expected=len(roster), illustrated=len(present), complete=len(present) == len(roster),
                present=present, missing=[species for species in roster if species not in present])


async def equipped(db, user_id):
    """A transferred companion cannot carry another trainer's unlock with it."""
    try:
        async with db.execute('SELECT c.beast_row_id,c.cosmetic_id FROM player_cosmetics c '
            'JOIN player_beasts b ON b.id=c.beast_row_id AND b.user_id=c.user_id '
            'JOIN hollowlight_unlocks u ON u.user_id=c.user_id AND u.unlock_id=c.cosmetic_id AND u.event_id=? '
            'WHERE c.user_id=? AND c.cosmetic_id=?', (EVENT, user_id, GHOST)) as cursor:
            return {row[0]: row[1] for row in await cursor.fetchall()}
    except aiosqlite.OperationalError as error:
        if 'no such table' not in str(error):
            raise
        return {}  # Older read-only fixtures/deployments have no cosmetics yet.


@exclusive_player
async def set_equipped(user_id, beast_row_id, selected, *, expected_species=None, expected_current=...):
    from utils.hollowlight import owns_unlock
    if selected not in (None, GHOST):
        raise ValueError('Choose an owned cosmetic appearance.')
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND id=?', (user_id, beast_row_id)) as cursor:
            row = await cursor.fetchone()
        if row is None or expected_species is not None and row['beast_id'] != expected_species:
            raise ValueError('That companion changed. Review its current appearance first.')
        current = (await equipped(db, user_id)).get(beast_row_id)
        if expected_current is not ... and current != expected_current:
            raise ValueError('The costume changed. Review its current appearance first.')
        async with db.execute('SELECT 1 FROM beast_market WHERE seller_id=? AND beast_row_id=?', (user_id, beast_row_id)) as cursor:
            if await cursor.fetchone():
                raise ValueError('Delist this companion before changing its costume.')
        if selected:
            if not await owns_unlock(db, user_id, selected):
                raise ValueError('Unlock the Little Ghost Costume at Hollowlight Carnival first.')
            if variant_path(row['beast_id'], selected) is None:
                raise ValueError('This companion does not have a reviewed costume appearance installed yet.')
            await db.execute('INSERT INTO player_cosmetics(user_id,beast_row_id,cosmetic_id,equipped_at) VALUES(?,?,?,?) '
                'ON CONFLICT(user_id,beast_row_id) DO UPDATE SET cosmetic_id=excluded.cosmetic_id,equipped_at=excluded.equipped_at',
                (user_id, beast_row_id, selected, time.time()))
        else:
            await db.execute('DELETE FROM player_cosmetics WHERE user_id=? AND beast_row_id=?', (user_id, beast_row_id))
        await db.commit()
    return selected


def clear_cache():
    manifest.cache_clear()
    _portrait.cache_clear()
