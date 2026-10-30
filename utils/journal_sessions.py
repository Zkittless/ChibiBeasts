"""Keep one current Field Journal per trainer and channel.

The replacement is sent before the former message is retired.  Tracking the
message ID in SQLite also lets the next journal clean up after a bot restart.
"""
import asyncio
from dataclasses import dataclass
import logging
import weakref

import discord

from utils import db as storage
from utils.message_transport import durable_message

logger = logging.getLogger('chibibeasts.journal_sessions')
_locks = {}
_panels = {}


@dataclass
class _Panel:
    message: object
    view_ref: object = None

    @property
    def view(self):
        return self.view_ref() if self.view_ref else None


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _identity(view, interaction):
    user_id = view.user_id
    channel = getattr(interaction, 'channel', None)
    channel_id = getattr(interaction, 'channel_id', None)
    if not _integer(channel_id):
        channel_id = getattr(channel, 'id', None)
    persistent = _integer(user_id) and _integer(channel_id)
    if not _integer(channel_id):
        channel_id = id(channel if channel is not None else interaction)
    # Tests and maintenance tools can switch databases in the same process.
    return (str(storage.DB_PATH), user_id, channel_id), persistent


def _token(message):
    message_id = getattr(message, 'id', None)
    return message_id if _integer(message_id) else ('object', id(message))


def _panel(view, message):
    try:
        reference = weakref.ref(view)
    except TypeError:
        reference = None
    return _Panel(message, reference)


def _close(view):
    if view is not None:
        view.closed = True
        render = getattr(view, 'render', None)
        if callable(render):
            try:
                render()
            except Exception:
                logger.warning('Could not render retired Field Journal', exc_info=True)
        view.stop()


async def _stored(key, persistent):
    if not persistent:
        return None
    async with storage.connect() as db:
        async with db.execute('SELECT message_id FROM journal_panels WHERE user_id=? AND channel_id=?', key[1:]) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def _remember(key, persistent, message):
    message_id = getattr(message, 'id', None)
    if persistent and _integer(message_id):
        async with storage.connect() as db:
            await db.execute('INSERT INTO journal_panels(user_id,channel_id,message_id) VALUES(?,?,?) '
                             'ON CONFLICT(user_id,channel_id) DO UPDATE SET message_id=excluded.message_id',
                             (*key[1:], message_id))
            await db.commit()


def _prior_message(interaction, message_id):
    channel = getattr(interaction, 'channel', None)
    partial = getattr(channel, 'get_partial_message', None)
    return partial(message_id) if callable(partial) else None


async def _retire(panel, *, protected=None, replacement=None, channel=None):
    """Close dispatch first, then remove the old message or its controls."""
    old_view, message = panel.view, panel.message
    if old_view is not replacement:
        _close(old_view)
    if message is None or _token(message) == protected:
        return
    token = _token(message)
    if old_view is not None and getattr(old_view, '_journal_retired_token', None) == token:
        return
    # Interaction message objects keep the opening click's webhook token.
    # Public messages outlive that token; address them by channel/message ID
    # using the bot connection for both deletion and the controls fallback.
    message = durable_message(message, channel=channel)
    delete = getattr(message, 'delete', None)
    if callable(delete):
        try:
            async with asyncio.timeout(10):
                await delete()
            if old_view is not None:
                old_view._journal_retired_token = token
            return
        except discord.NotFound:
            if old_view is not None:
                old_view._journal_retired_token = token
            return
        except Exception:
            logger.warning('Could not delete former Field Journal; removing its controls', exc_info=True)
    edit = getattr(message, 'edit', None)
    if callable(edit):
        # Components V2 messages cannot gain content/embeds.  A static display
        # removes all controls and attachment references without changing flags.
        retired = discord.ui.LayoutView(timeout=None)
        retired.add_item(discord.ui.TextDisplay('This Field Journal has moved to the newest journal below.'))
        try:
            async with asyncio.timeout(10):
                await edit(view=retired, attachments=[], allowed_mentions=discord.AllowedMentions.none())
            if old_view is not None:
                old_view._journal_retired_token = token
        except discord.NotFound:
            if old_view is not None:
                old_view._journal_retired_token = token
        except Exception:
            logger.warning('Could not clear former Field Journal message', exc_info=True)
        finally:
            retired.stop()


async def publish(view, interaction, sender, *, replace=False, source=None):
    """Send/register a journal, then remove the previous journal in this channel.

    ``sender`` performs the actual Discord send or edit and returns its message.
    A failed send leaves the current journal and its controls untouched.  The
    source argument can retire a pre-tracking journal used to start a battle.
    ``replace`` means the sender is editing an existing handoff message; that
    message is protected while any other registered journal is retired.
    """
    key, persistent = _identity(view, interaction)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        old_id = await _stored(key, persistent)
        previous = _panels.get(key)
        message = await sender()
        if message is None:
            raise RuntimeError('A Field Journal send did not return its message')
        # Persist before retiring.  If recording fails, remove the new message
        # and keep the former journal usable rather than losing its location.
        try:
            await _remember(key, persistent, message)
        except BaseException:
            if replace:
                # A handoff edits an existing Workshop/other panel.  Its
                # message predates this operation and must survive a registry
                # failure even when no former journal is currently tracked.
                _close(view)
            elif _token(message) != old_id and (not previous or _token(message) != _token(previous.message)):
                await _retire(_panel(view, message), channel=getattr(interaction, 'channel', None))
            raise
        view.message = message
        _panels[key] = _panel(view, message)
        candidates = []
        if previous:
            candidates.append(previous)
        if old_id is not None and (not previous or _token(previous.message) != old_id):
            previous_message = _prior_message(interaction, old_id)
            if previous_message is not None:
                candidates.append(_Panel(previous_message))
        if (source is not None and source is not view and getattr(source, 'user_id', None) == view.user_id
                and str(getattr(source, 'prefix', '')).startswith('journal:')
                and not getattr(source, 'message_repurposed', False)
                and getattr(source, 'message', None) is not None):
            candidates.append(_panel(source, source.message))
        protected = _token(message)
        retired_tokens = set()
        for panel in candidates:
            token = _token(panel.message)
            if token in retired_tokens:
                if panel.view is not view:
                    _close(panel.view)
                    if token != protected and panel.view is not None:
                        panel.view._journal_retired_token = token
                continue
            retired_tokens.add(token)
            await _retire(panel, protected=protected, replacement=view,
                          channel=getattr(interaction, 'channel', None))
        return message


async def _forget(view, key, persistent):
    message = getattr(view, 'message', None)
    current = _panels.get(key)
    if current and (current.view is view or _token(current.message) == _token(message)):
        _panels.pop(key, None)
    message_id = getattr(message, 'id', None)
    if persistent and _integer(message_id):
        async with storage.connect() as db:
            await db.execute('DELETE FROM journal_panels WHERE user_id=? AND channel_id=? AND message_id=?',
                             (*key[1:], message_id))
            await db.commit()


async def forget(view, interaction):
    """Unregister only this journal when its message becomes another panel."""
    key, persistent = _identity(view, interaction)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        await _forget(view, key, persistent)


async def discard(view, interaction):
    """Remove a former journal after opening a separate Nursery panel."""
    if getattr(view, 'message_repurposed', False):
        return
    key, persistent = _identity(view, interaction)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        await _forget(view, key, persistent)
        await _retire(_panel(view, getattr(view, 'message', None)),
                      channel=getattr(interaction, 'channel', None))
