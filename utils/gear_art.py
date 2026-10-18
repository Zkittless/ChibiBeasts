"""Bounded, local equipped portraits for workshop and equipment panels."""
import asyncio
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import logging
import threading

import discord

RENDER_TIMEOUT = 5
_slots = threading.BoundedSemaphore(2)
logger = logging.getLogger('chibibeasts.gear.art')


@dataclass(frozen=True)
class Portrait:
    filename: str
    data: bytes


def portrait_filename(species, armor_id=''):
    # Different appearances must never reuse Discord's previous attachment.
    digest = hashlib.sha256(f'{species}:{armor_id or ""}'.encode()).hexdigest()[:20]
    return f'equipped-{digest}.png'


@lru_cache(maxsize=192)
def _encode(species, armor_id):
    from utils.armor_art import portrait
    image = portrait(species, armor_id)
    if image is None:
        raise ValueError('No portrait available for this companion')
    with io.BytesIO() as stream:
        image.save(stream, 'PNG', optimize=True)
        return stream.getvalue()


async def render_portrait(species, armor_id=''):
    """Image work never blocks Discord's event loop or accumulates a job queue."""
    if not _slots.acquire(blocking=False):
        return None

    def work():
        try:
            return Portrait(portrait_filename(species, armor_id), _encode(species, armor_id))
        finally:
            _slots.release()

    job = asyncio.create_task(asyncio.to_thread(work))
    # A canceled HTTP callback must not cancel a queued worker before its
    # finally block can release the slot. Retrieve any late worker exception.
    job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    try:
        async with asyncio.timeout(RENDER_TIMEOUT):
            return await asyncio.shield(job)
    except Exception:
        logger.warning('Equipped portrait unavailable; keeping text controls', exc_info=True)
        return None


async def equipped_armor_ids(user_id):
    """Resolve owned armor consistently with battle preparation, ignoring runes."""
    from utils import db as storage
    from utils.catalog import load_catalog
    armor = load_catalog('equipment.json')['equipment']
    result = {}
    async with storage.connect() as db:
        async with db.execute(
            'SELECT e.beast_row_id,e.equipment_id FROM player_equipment e '
            'JOIN player_beasts b ON b.id=e.beast_row_id '
            'WHERE e.user_id=? AND b.user_id=? ORDER BY e.id', (user_id, user_id)
        ) as cursor:
            for row_id, item_id in await cursor.fetchall():
                if item_id in armor:
                    result.setdefault(row_id, item_id)
    return result


async def send_owned_portrait(interaction, embed, species, armor_id='', *, thumbnail=False):
    """Send an already acknowledged owned-companion card with safe text fallback."""
    setter = embed.set_thumbnail if thumbnail else embed.set_image
    setter(url=None)
    portrait = await render_portrait(species, armor_id)
    if portrait is None:
        async with asyncio.timeout(15):
            return await interaction.followup.send(embed=embed)
    with io.BytesIO(portrait.data) as buffer:
        file = discord.File(buffer, filename=portrait.filename, description='Your companion and its equipped armor.')
        setter(url=f'attachment://{portrait.filename}')
        try:
            async with asyncio.timeout(15):
                return await interaction.followup.send(embed=embed, file=file)
        except discord.HTTPException as error:
            if isinstance(error, discord.NotFound):
                raise
            logger.warning('Owned companion portrait rejected (%s); showing text', error.status)
            setter(url=None)
            async with asyncio.timeout(15):
                return await interaction.followup.send(embed=embed)
        finally:
            file.close()
