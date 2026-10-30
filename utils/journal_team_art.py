"""A bounded, local stage for the trainer's actual exploration companions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import json
import logging
import threading

from PIL import Image, ImageDraw, ImageEnhance

from utils import armor_art, battle_art, db as storage, field_journal as ui
from utils.artwork import PROJECT_ROOT, card_artwork_manifest

WIDTH, HEIGHT = 640, 300
RENDER_TIMEOUT = 5
MAX_BYTES = 1_000_000
CACHE_SIZE = 48
LAYOUT_VERSION = 2
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='journal-team-art')
_slots = threading.BoundedSemaphore(2)
logger = logging.getLogger('chibibeasts.journal.team_art')

# Lead is painted last, on the lower foreground plane.  Every role has enough
# space for a broad full-body silhouette; no framed or cropped head portraits.
POSITIONS = ((320, 270, (230, 232)), (147, 239, (186, 190)), (493, 239, (186, 190)))


@dataclass(frozen=True)
class Member:
    species: str
    armor_id: str
    recovering: bool


@dataclass(frozen=True)
class TeamArtwork:
    name: str
    data: bytes


def snapshot(rows):
    """Copy appearance fields before a queued worker can outlive a UI change."""
    members = []
    for row in list(rows)[:3]:
        members.append(None if row is None else Member(
            str(row['beast_id']), str(row.get('appearance_id') or row.get('armor_id') or ''),
            int(row.get('hp', 1)) <= 0 or storage.is_knocked_out(row)))
    return tuple(members + [None] * (3 - len(members)))


@lru_cache(maxsize=1)
def _realm_revisions():
    return json.loads((PROJECT_ROOT / 'data' / 'battle-artwork.json').read_text(encoding='utf-8'))['realms']


def _revision(members, realm):
    """Discord's attachment name changes whenever a packaged appearance does."""
    cards = card_artwork_manifest()['beasts']
    armor = armor_art.manifest()
    sources = [_realm_revisions()[realm]['sha256']]
    for member in members:
        if member is None:
            sources.append(None)
            continue
        record = armor.get('sets', {}).get(member.armor_id, {})
        variant = record.get('variants', {}).get(member.species)
        from utils import cosmetics
        cosmetic_revision = cosmetics.revision(member.species, member.armor_id)
        sources.append((member.species, member.armor_id, member.recovering,
                        cards.get(member.species, {}).get('sha256'),
                        cosmetic_revision or armor.get('sha256', {}).get(variant)))
    return hashlib.sha256(json.dumps([LAYOUT_VERSION, realm, sources], sort_keys=True).encode()).hexdigest()[:24]


def _ground(image, role, occupied):
    x, feet, _ = POSITIONS[role]
    layer = Image.new('RGBA', image.size)
    draw = ImageDraw.Draw(layer)
    radius = 76 if role == 0 else 61
    if occupied:
        draw.ellipse((x-radius, feet-8, x+radius, feet+10), fill=(3, 17, 22, 145))
        if role == 0:
            draw.arc((x-radius-4, feet-8, x+radius+4, feet+13), 8, 172, fill=(213, 199, 148, 175), width=2)
    else:
        # An empty station, never a placeholder creature or an invented sprite.
        draw.arc((x-radius+10, feet-3, x+radius-10, feet+8), 8, 172, fill=(183, 190, 168, 105), width=2)
        draw.arc((x-radius+10, feet-3, x+radius-10, feet+8), 188, 352, fill=(183, 190, 168, 70), width=1)
    image.alpha_composite(layer)


@lru_cache(maxsize=CACHE_SIZE)
def _render(members, realm, revision):
    image = ui.backdrop(battle_art.background(realm), (WIDTH, HEIGHT))
    # Keep the realm recognizable while the equipped sprites remain brightest.
    image.alpha_composite(Image.new('RGBA', image.size, (6, 22, 24, 20)))
    for role in (1, 2, 0):
        member = members[role]
        _ground(image, role, member is not None)
        if member is None:
            continue
        sprite = armor_art.portrait(member.species, member.armor_id)
        if sprite is None:
            raise ValueError('Missing exploration companion artwork')
        x, feet, size = POSITIONS[role]
        sprite.thumbnail(size, Image.Resampling.NEAREST)
        if member.recovering:
            alpha = sprite.getchannel('A')
            sprite = ImageEnhance.Color(sprite).enhance(.25)
            sprite = ImageEnhance.Brightness(sprite).enhance(.66)
            sprite.putalpha(alpha)
        image.alpha_composite(sprite, (x - sprite.width // 2, feet - sprite.height))
    # Native Discord text below this image owns names, levels and exact status.
    # These small ground labels only explain the spatial formation.
    draw = ImageDraw.Draw(image)
    for role, label in ((1, 'RESERVE 1'), (2, 'RESERVE 2'), (0, 'LEAD')):
        x, feet, _ = POSITIONS[role]
        ui.text(draw, label, (x-66, feet+13, x+66, feet+27), size=11, minimum=10,
                fill='#e8dab5' if role == 0 else '#b7c6b8', align='center', outline=True)
    with io.BytesIO() as output:
        image.convert('RGB').save(output, 'PNG', optimize=True)
        data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise ValueError('Exploration team artwork exceeded its attachment budget')
    return TeamArtwork(f'exploration-team-{revision}.png', data)


async def render_team(rows, realm='woods'):
    """Return the equipped lineup or None, without blocking or queuing jobs."""
    members = snapshot(rows)
    realm = battle_art.realm_key(realm)
    if not _slots.acquire(blocking=False):
        return None

    def work():
        try:
            return _render(members, realm, _revision(members, realm))
        finally:
            _slots.release()

    try:
        future = _workers.submit(work)
    except BaseException:
        _slots.release()
        raise
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda result: None if result.cancelled() else result.exception())
    try:
        async with asyncio.timeout(RENDER_TIMEOUT):
            return await asyncio.shield(wrapped)
    except Exception:
        logger.warning('Exploration lineup unavailable; keeping native companion controls', exc_info=True)
        return None


def clear_cache():
    """Development hook; deployed asset catalogs refresh on bot restart."""
    _render.cache_clear()
    _realm_revisions.cache_clear()
