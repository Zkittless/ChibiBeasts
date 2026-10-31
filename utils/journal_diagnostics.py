"""Trace journal clicks without recording tokens, names, or message contents."""
import asyncio
from functools import wraps
import logging
import time

import discord

logger = logging.getLogger('chibibeasts.journal.input')
_HANDLED = 'chibibeasts_journal_handled'
_ACTION = 'chibibeasts_journal_action'


def record(interaction, stage, *, action='', elapsed_ms=None, level=logging.INFO):
    # Only a fixed action family is useful here; choices and text are not logged.
    family = (action or getattr(interaction, 'extras', {}).get(_ACTION, 'open')).split(':', 1)[0]
    if not family.replace('_', '').isalnum():
        family = 'unknown'
    logger.log(level, 'Journal input stage=%s interaction=%s trainer=%s channel=%s message=%s action=%s elapsed_ms=%s',
               stage, getattr(interaction, 'id', None), getattr(getattr(interaction, 'user', None), 'id', None),
               getattr(interaction, 'channel_id', None), getattr(getattr(interaction, 'message', None), 'id', None),
               family[:32], elapsed_ms)


def trace(callback):
    @wraps(callback)
    async def dispatch(view, interaction, action):
        # Interaction.extras is public SDK storage shared with on_interaction.
        # Claim before the first await so a live callback never gets a second ACK.
        extras = getattr(interaction, 'extras', None)
        if extras is None:
            interaction.extras = extras = {}
        extras[_HANDLED] = True
        extras[_ACTION] = action
        start = time.monotonic()
        record(interaction, 'handling', action=action)
        result = 'returned'
        try:
            return await callback(view, interaction, action)
        except BaseException:
            result = 'failed'
            raise
        finally:
            record(interaction, result, action=action, elapsed_ms=round((time.monotonic() - start) * 1000),
                   level=logging.WARNING if result == 'failed' else logging.INFO)
    return dispatch


async def received(interaction):
    """Observe gateway delivery, including clicks with no registered callback."""
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get('custom_id', '')
    if not isinstance(custom_id, str) or not custom_id.startswith('journal:'):
        return
    action = custom_id.split(':', 2)[-1]
    record(interaction, 'received', action=action)
    # discord.py schedules the component callback before on_interaction. Allow
    # that task to claim the click; never race an active handler or its modal.
    await asyncio.sleep(0)
    if getattr(interaction, 'extras', {}).get(_HANDLED) or interaction.response.is_done():
        return
    record(interaction, 'unhandled', action=action, level=logging.WARNING)
    try:
        await interaction.response.send_message(
            'These journal controls are no longer active. Open /journal to continue your saved adventure.',
            ephemeral=True)
    except discord.HTTPException as error:
        logger.warning('Could not acknowledge inactive journal interaction=%s status=%s code=%s',
                       interaction.id, error.status, error.code)
