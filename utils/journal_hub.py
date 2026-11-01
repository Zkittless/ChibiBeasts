"""Keep existing game panels inside one durable Components V2 journal.

The adapters change presentation and transport only. Inventory transactions,
revision guards, modal validation and combat services remain with their views.
"""
import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import discord
from discord.components import _component_factory

from utils.message_transport import durable_message, restore_view_tracking

log = logging.getLogger('chibibeasts.journal.hub')
SECTIONS = {'adventure': 'Adventure', 'beasts': 'Beasts', 'camp': 'Camp'}


def section_for(page):
    if page in {'beasts', 'roster', 'companions', 'care', 'gear'}:
        return 'beasts'
    if page in {'camp', 'activities', 'nursery', 'workshop', 'story', 'goals', 'daily_objectives', 'supplies', 'trainer', 'community'}:
        return 'camp'
    return 'adventure'


def embed_text(embed):
    """Preserve full native text when an older embed joins a V2 message."""
    data = embed.to_dict()
    parts = []
    if data.get('title'):
        parts.append('### ' + data['title'])
    if data.get('description'):
        parts.append(data['description'])
    parts.extend('**' + field['name'] + '**\n' + field['value'] for field in data.get('fields', []))
    if data.get('footer', {}).get('text'):
        parts.append(data['footer']['text'])
    return parts


def text_pages(parts, limit=2800):
    pages, current = [], ''
    for part in parts:
        # Long individual fields are split at a line/word boundary, never cut.
        while part:
            room = limit - len(current) - (2 if current else 0)
            if len(part) <= room:
                current += ('\n\n' if current else '') + part
                break
            if current:
                pages.append(current)
                current = ''
                continue
            cut = max(part.rfind('\n', 0, limit), part.rfind(' ', 0, limit))
            cut = cut if cut > 0 else limit
            pages.append(part[:cut])
            part = part[cut:].lstrip()
    if current:
        pages.append(current)
    return pages or ['Your Field Journal is ready.']


class HubFrame(discord.ui.LayoutView):
    def __init__(self, hub, source, *, content=None, embeds=(), page=0):
        super().__init__(timeout=300)
        self.hub, self.source = hub, source
        self.content, self.embeds, self.page = content, embeds, page
        self.prefix = 'journal:' + uuid4().hex
        callbacks = {item.custom_id: item for item in source.walk_children()
                     if getattr(item, 'custom_id', None)} if source else {}
        if source is not hub.root:
            self.add_item(self.navigation())
            back = next((item for item in callbacks.values() if getattr(item, 'label', '') in {'Back to beast', 'Back to roster'}), None)
            if back:
                self.add_item(discord.ui.ActionRow(self.control('Back to beast' if back.label == 'Back to beast' else 'Back to beasts', callback=back.callback)))
            elif hub.route not in {'beasts', 'roster'}:
                self.add_item(discord.ui.ActionRow(self.control('Back to ' + SECTIONS[hub.section], route=hub.section)))
        if source is None or not source.has_components_v2():
            parts = ([content] if content else []) + [part for embed in embeds for part in embed_text(embed)]
            pages = text_pages(parts)
            self.page = max(0, min(page, len(pages) - 1))
            container = discord.ui.Container(accent_colour=0xb7ad87)
            portraits = getattr(source, 'overview_images', [])
            if portraits and len(embeds) == 1 and len(pages) == 1:
                data = embeds[0].to_dict()
                container.add_item(discord.ui.TextDisplay('### ' + data.get('title', 'Beasts') + '\n' + data.get('description', '')))
                for index, field in enumerate(data.get('fields', [])):
                    text = discord.ui.TextDisplay('**' + field['name'] + '**\n' + field['value'])
                    url = portraits[index] if index < len(portraits) else None
                    container.add_item(discord.ui.Section(text, accessory=discord.ui.Thumbnail(url)) if url else text)
                if data.get('footer', {}).get('text'):
                    container.add_item(discord.ui.TextDisplay('-# ' + data['footer']['text']))
            else:
                container.add_item(discord.ui.TextDisplay(pages[self.page]))
            for embed in embeds:
                url = embed.image.url or embed.thumbnail.url
                if url:
                    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(url)))
            self.add_item(container)
            if len(pages) > 1:
                self.add_item(discord.ui.ActionRow(
                    self.control('Previous page', page=self.page - 1, disabled=self.page == 0),
                    discord.ui.Button(label=f'{self.page + 1}/{len(pages)}', disabled=True),
                    self.control('Next page', page=self.page + 1, disabled=self.page == len(pages) - 1)))
        if source:
            # discord.py's public from_message converter gives us independent
            # items; never reparent the live child's selectors or callbacks.
            copied = discord.ui.LayoutView.from_message(SimpleNamespace(
                components=[_component_factory(payload) for payload in source.to_components()]), timeout=None)
            for serial, item in enumerate(tuple(copied.walk_children())):
                key = getattr(item, 'custom_id', None)
                if not key or key not in callbacks:
                    continue
                original = callbacks[key]
                if source is not hub.root and (getattr(original, 'label', '') in {'Field Journal', 'Back to beast', 'Back to roster'}
                        or getattr(original, 'placeholder', '') == 'Beast workspace'):
                    (item.parent or copied).remove_item(item)
                    continue
                # ViewStore removes handlers by (type, custom_id) when the old
                # frame stops. Distinct IDs prevent it removing the new frame.
                item.custom_id = f'{self.prefix}:panel:{serial}'
                async def invoke(click, source_item=original, owner=source):
                    if owner.is_finished():
                        return await hub.reject(click, 'This page has closed. Use the journal section buttons.')
                    adapted = HubInteraction(click, hub, data={**click.data, 'custom_id': source_item.custom_id})
                    if not await owner.interaction_check(adapted):
                        return
                    source_item._refresh_state(adapted, adapted.data)
                    if not await source_item.interaction_check(adapted):
                        return
                    return await source_item.callback(adapted)
                item.callback = self.callback(invoke)
            for child in tuple(copied.walk_children()):
                if isinstance(child, discord.ui.ActionRow) and not child.children and child.parent:
                    child.parent.remove_item(child)
            for child in tuple(copied.children):
                if isinstance(child, discord.ui.ActionRow) and not child.children:
                    continue
                copied.remove_item(child)
                self.add_item(child)
            copied.stop()
        if self.content_length() > 4000 or self.total_children_count > 40:
            raise ValueError('This journal page exceeds Discord’s display limits.')

    def callback(self, callback):
        async def invoke(click):
            await self.hub.dispatch(self, click, callback)
        return invoke

    def control(self, label, *, route=None, callback=None, page=None, disabled=False, style=discord.ButtonStyle.secondary):
        item = discord.ui.Button(label=label, custom_id=f'{self.prefix}:{uuid4().hex[:8]}', disabled=disabled, style=style)
        if callback is None:
            async def callback(click):
                if not click.response.is_done():
                    await click.response.defer()
                if page is not None:
                    return await self.hub.present(click, view=self.source, content=self.content, embeds=self.embeds,
                                                  page=page, attachments=list(getattr(self.hub.message, 'attachments', [])))
                return await self.hub.navigate(click, route)
        item.callback = self.callback(callback)
        return item

    def navigation(self):
        return discord.ui.ActionRow(*(self.control(label, route=key,
            style=discord.ButtonStyle.primary if self.hub.section == key else discord.ButtonStyle.secondary)
            for key, label in SECTIONS.items()))

    async def on_timeout(self):
        if self.hub.frame is not self:
            return
        self.hub.closed = True
        for item in self.walk_children():
            if hasattr(item, 'disabled'):
                item.disabled = True
        if self.hub.message:
            try:
                async with asyncio.timeout(15):
                    await durable_message(self.hub.message).edit(view=self)
            except (discord.HTTPException, TimeoutError):
                log.info('Could not expire journal controls', exc_info=True)
        self.hub.close()

    async def on_error(self, interaction, error, item):
        log.error('Journal panel failed', exc_info=error)
        await self.hub.reject(interaction, 'Your progress is saved. Reopen /journal to refresh this page.')


class ResponseAdapter:
    def __init__(self, click, hub):
        self.click, self.hub = click, hub

    def __getattr__(self, name):
        return getattr(self.click.response, name)

    async def send_message(self, content=None, **kwargs):
        if kwargs.get('ephemeral') and not kwargs.get('view') and not kwargs.get('embed') and not kwargs.get('embeds'):
            return await self.click.response.send_message(content, **kwargs)
        await self.click.response.defer()
        return await self.hub.present(self.click, content=content, **kwargs)

    async def edit_message(self, **kwargs):
        if not self.click.response.is_done():
            await self.click.response.defer()
        return await self.hub.edit(self.click, **kwargs)

    async def send_modal(self, modal):
        original = modal.on_submit
        frame = self.hub.frame
        async def submit(click):
            return await self.hub.dispatch(frame, click, original)
        modal.on_submit = submit
        return await self.click.response.send_modal(modal)


class FollowupAdapter:
    def __init__(self, click, hub):
        self.click, self.hub = click, hub

    def __getattr__(self, name):
        return getattr(self.click.followup, name)

    async def send(self, content=None, **kwargs):
        if kwargs.get('ephemeral') and not kwargs.get('view') and not kwargs.get('embed') and not kwargs.get('embeds'):
            return await self.click.followup.send(content, **kwargs)
        return await self.hub.present(self.click, content=content, **kwargs)


class HubInteraction:
    def __init__(self, click, hub, *, data=None):
        self.original = click.original if isinstance(click, HubInteraction) else click
        self.hub = hub
        self.data = data if data is not None else getattr(click, 'data', {})
        self.response = ResponseAdapter(self.original, hub)
        self.followup = FollowupAdapter(self.original, hub)

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def edit_original_response(self, **kwargs):
        return await self.hub.edit(self.original, **kwargs)


class JournalHub:
    def __init__(self, root):
        self.root = root
        self.message = None
        self.frame = None
        self.closed = self.busy = False
        self._dispatch_owner = None
        self.route, self.section = root.page, section_for(root.page)

    def close(self):
        self.closed = True
        if self.frame:
            self.frame.stop()
            if self.frame.source and self.frame.source is not self.root:
                self.frame.source.stop()

    async def reject(self, click, text):
        original = click.original if isinstance(click, HubInteraction) else click
        send = original.followup.send if original.response.is_done() else original.response.send_message
        await send(text, ephemeral=True)

    async def dispatch(self, frame, click, callback):
        if click.user.id != self.root.user_id:
            return await self.reject(click, 'Open /journal for your own companions.')
        if self.closed or self.root.closed or self.frame is not frame:
            return await self.reject(click, 'Use the current journal controls or reopen /journal.')
        if self.busy:
            return await self.reject(click, 'Your journal is updating. Use the refreshed controls.')
        self.busy = True
        owner = self._dispatch_owner = object()
        try:
            adapted = HubInteraction(click, self)
            return await callback(adapted)
        finally:
            if self._dispatch_owner is owner:
                self.release_dispatch()

    def release_dispatch(self):
        """A separate multiplayer panel owns its own lifetime and controls."""
        self.busy = False
        self._dispatch_owner = None

    async def present(self, click, *, view=None, content=None, embed=None, embeds=None, file=None, files=None,
                      attachments=None, opening=False, channel_send=False, page=0, **ignored):
        original = click.original if isinstance(click, HubInteraction) else click
        if view is self.root:
            self.route, self.section = view.page, section_for(view.page)
        frame = HubFrame(self, view, content=content, embeds=embeds or ([embed] if embed else []), page=page)
        uploads = list(files or []) + ([file] if file else [])
        payload = dict(view=frame, allowed_mentions=discord.AllowedMentions.none())
        try:
            async with asyncio.timeout(15):
                if self.message is None:
                    sender = original.channel.send if channel_send else original.followup.send
                    message = await sender(**payload, files=uploads, **({} if channel_send else {'wait': True}))
                else:
                    payload.update(content=None, embeds=[], attachments=list(attachments or []) + uploads)
                    target = durable_message(self.message, channel=getattr(original, 'channel', None))
                    if target is not self.message or isinstance(target, (discord.Message, discord.PartialMessage)):
                        message = await target.edit(**payload)
                    else:
                        message = await original.edit_original_response(**payload)
        except BaseException:
            frame.stop()
            raise
        previous = self.frame
        self.frame, self.message = frame, message or self.message
        self.root.message = self.message
        if view is not None:
            view.message = self.message
        if previous:
            previous.stop()
            # discord.py also removes the message's synchronization entry when
            # stopping any view for that message. Restore the current frame's
            # tracking after retiring its predecessor, using the message state.
            restore_view_tracking(self.message, frame)
        return self.message

    async def edit(self, click, **kwargs):
        """Match Discord edit semantics: omitted fields keep their current value."""
        if self.frame:
            kwargs.setdefault('view', self.frame.source)
            kwargs.setdefault('content', self.frame.content)
            if 'embed' not in kwargs and 'embeds' not in kwargs:
                kwargs['embeds'] = self.frame.embeds
            kwargs.setdefault('attachments', list(getattr(self.message, 'attachments', [])))
        return await self.present(click, **kwargs)

    async def navigate(self, click, route, **options):
        from utils import journal_state, journal_adventure
        click = HubInteraction(click, self)
        if not click.response.is_done():
            await click.response.defer()
        self.route, self.section = route, section_for(route)
        if route in {'beasts', 'roster'}:
            from utils.roster_view import open_roster
            return await open_roster(click, already_deferred=True, **options)
        if route == 'care':
            from utils.care_view import open_care
            return await open_care(click, already_deferred=True, **options)
        if route == 'gear':
            from utils.gear_view import open_gear
            return await open_gear(click, **options)
        if route == 'nursery':
            from utils.nursery_view import open_nursery
            return await open_nursery(click, bot=self.root.bot, already_deferred=True, **options)
        if route == 'workshop':
            from utils.workshop import open_workshop
            return await open_workshop(click, already_deferred=True, **options)
        root = self.root
        root.page = 'camp' if route in {'home', 'activities'} else route
        root.notice = ''
        root.state = await journal_state.snapshot(root.user_id)
        root.trip = root.state.trip or root.trip
        if route == 'adventure' and (root.state.trip or root.state.trial):
            root.page = 'overview'
        await root.load_page()
        await journal_adventure.prepare_still(root)
        root.render()
        await root.publish(click)
        return root
