"""Private, target-first companion care with an explicit one-item preview."""
import asyncio
import io
import logging
from uuid import uuid4

import discord

from utils import care, db as storage, gear_art, owned_roster
from utils.items import use_item
from utils.theme import COLORS

logger = logging.getLogger('chibibeasts.care')
PAGE_SIZE = 25


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def text_parts(text, limit=1000):
    """Keep every effect and affected member within Discord's field limit."""
    parts, current = [], ''
    for line in text.splitlines():
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ''
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = ''
        current += ('\n' if current else '') + line
    if current:
        parts.append(current)
    return parts


class CareSearch(discord.ui.Modal, title='Find a companion'):
    def __init__(self, view, generation):
        super().__init__(timeout=180)
        self.care_view, self.generation = view, generation
        self.query = discord.ui.TextInput(label='Name, nickname or collection number', required=False,
                                         max_length=100, default=view.picker.query)
        self.add_item(self.query)

    async def on_submit(self, interaction):
        await self.care_view.handle(interaction, 'search', generation=self.generation, value=str(self.query))


class CareName(discord.ui.Modal, title='Name your companion'):
    def __init__(self, view, generation):
        super().__init__(timeout=180)
        self.care_view, self.generation = view, generation
        self.name = discord.ui.TextInput(label='New name', min_length=1, max_length=40,
                                        default=view.choice or '')
        self.add_item(self.name)

    async def on_submit(self, interaction):
        await self.care_view.handle(interaction, 'name', generation=self.generation, value=str(self.name))


class CareView(discord.ui.View):
    def __init__(self, user_id, state):
        super().__init__(timeout=180)
        self.user_id, self.state = user_id, state
        self.picker = owned_roster.PickerState()
        self.item_id = None
        self.choice = None
        self.current_preview = None
        self.confirming = False
        self.generation = 0
        self.prefix = 'care:' + uuid4().hex
        self.notice = ''
        self.message = None
        self.closed = False
        self.lock = asyncio.Lock()
        self.render()

    def page_rows(self):
        return owned_roster.visible(self.state.rows, self.picker, page_size=PAGE_SIZE)

    def button(self, label, action, *, row=2, disabled=False, style=discord.ButtonStyle.secondary):
        generation = self.generation
        button = discord.ui.Button(label=label, style=style, row=row, disabled=disabled or self.closed,
                                   custom_id=f'{self.prefix}:{generation}:{action}')
        button.callback = lambda interaction: self.handle(interaction, action, generation=generation)
        self.add_item(button)
        return button

    def render(self):
        # New item objects retain the revision they actually displayed, even if
        # an already-dispatched old callback runs after a later edit completes.
        self.clear_items()
        generation = self.generation
        visible, pages = self.page_rows()
        self.companion_select = discord.ui.Select(row=0, placeholder=f'Choose a companion · Page {self.picker.page+1}/{pages}',
            custom_id=f'{self.prefix}:{generation}:companion', disabled=self.closed or not visible,
            options=owned_roster.options(visible, self.state.selected_id) or [discord.SelectOption(label='No matching companions', value='none')])
        self.companion_select.callback = lambda interaction: self.handle(interaction, 'companion', generation=generation)
        self.add_item(self.companion_select)
        items = care.supported_items(self.state)
        if self.item_id and self.item_id not in items:
            items.insert(0, self.item_id)
        catalog = storage.load_items()
        options = []
        for key in items[:25]:
            preview = care.preview(self.state, key, self.choice if key == self.item_id else None)
            description = preview.reason or preview.text
            options.append(discord.SelectOption(label=f"{catalog.get(key, {}).get('name', key)} ×{preview.quantity}"[:100],
                value=key, default=key == self.item_id,
                description=description.replace('\n', ' ')[:100] or 'Review this item before using it.'))
        self.item_select = discord.ui.Select(row=1, placeholder='Choose an owned care item',
            custom_id=f'{self.prefix}:{generation}:item', options=options or [discord.SelectOption(label='No care items owned', value='none')],
            disabled=self.closed or not items)
        self.item_select.callback = lambda interaction: self.handle(interaction, 'item', generation=generation)
        self.add_item(self.item_select)
        preview = care.preview(self.state, self.item_id, self.choice) if self.item_id else None
        self.current_preview = preview
        use_label = 'Review' if preview and preview.requires_confirmation else 'Use 1'
        self.use_button = self.button(use_label, 'use', disabled=not preview or not preview.usable or self.confirming,
                                      style=discord.ButtonStyle.primary)
        self.previous_button = self.button('Previous', 'previous', disabled=self.picker.page <= 0)
        self.next_button = self.button('Next', 'next', disabled=self.picker.page >= pages-1)
        self.search_button = self.button('Search', 'open_search', disabled=not self.state.rows)
        self.refresh_button = self.button('Refresh', 'refresh')
        self.clear_button = self.button('Clear search', 'clear', row=3, disabled=not self.picker.query)
        self.name_button = None
        if preview and catalog.get(self.item_id, {}).get('effect', {}).get('rename'):
            self.name_button = self.button('Choose name', 'open_name', row=3,
                                           disabled=not self.state.beast or preview.quantity <= 0)
        self.confirm_button = None
        if self.confirming:
            self.confirm_button = self.button('Confirm · Use 1', 'confirm', row=3,
                disabled=not preview or not preview.usable, style=discord.ButtonStyle.success)
            self.button('Cancel', 'cancel', row=3)
        beast, target = self.state.beast, self.state.target
        if beast and target:
            title = f'🌿 Care — {safe(owned_roster.name(beast))} #{owned_roster.number(beast)}'
            description = f"Lv.{beast['level']} · **{target['hp']}/{target['max_hp']} HP**"
            if storage.is_knocked_out(beast) or target['hp'] <= 0:
                description += ' · Recovering'
            if target.get('status'):
                description += ' · ' + safe(target['status']).title()
        else:
            title = '🌿 Companion Care'
            description = ('This companion is no longer in your collection. Choose another companion.'
                           if self.state.rows else 'Your collection is empty. Choose a starter or hatch a beast first.')
        if self.notice:
            description += '\n\n' + safe(self.notice)
        if self.picker.query:
            description += '\nSearch: ' + safe(self.picker.query)
            if not visible:
                description += '\nNo matching companions. Clear the search to see your roster.'
        embed = discord.Embed(title=title, description=description, color=COLORS['info'])
        if preview:
            embed.add_field(name=f'{preview.name} ×{preview.quantity}', value='Consume **1** on the selected companion.'
                if not catalog.get(self.item_id, {}).get('effect', {}).get('revive_party') else 'Consume **1** for all companions listed below.', inline=False)
            for index, part in enumerate(text_parts(safe(preview.text))):
                embed.add_field(name='Effect preview' if index == 0 else 'Affected companions (continued)', value=part, inline=False)
            if preview.reason:
                embed.add_field(name='Before use', value=safe(preview.reason)[:1024], inline=False)
            if self.confirming:
                embed.add_field(name='Confirm this item', value='Review the effects and affected companions above, then confirm to use one item.', inline=False)
        else:
            embed.add_field(name='Choose an item', value='Select an owned item to see its exact effect before using it. Your active beast stays the same.', inline=False)
        embed.set_footer(text='Private care panel · One item per confirmed click · No automatic item selection or spending')
        return embed

    async def publish(self, interaction, *, opening=False):
        embed = self.render()
        beast = self.state.beast
        artwork, attachment, file, buffer = None, None, None, None
        if beast:
            armor = (await gear_art.equipped_armor_ids(self.user_id)).get(beast['id'], '')
            filename = gear_art.portrait_filename(beast['beast_id'], armor)
            attachment = next((item for item in getattr(self.message, 'attachments', []) if item.filename == filename), None) if self.message else None
            if attachment is None:
                artwork = await gear_art.render_portrait(beast['beast_id'], armor)
                if artwork:
                    buffer = io.BytesIO(artwork.data)
                    file = discord.File(buffer, filename=artwork.filename, description='Your companion and its equipped armor.')
                    attachment = file
            if attachment:
                embed.set_image(url='attachment://' + attachment.filename)

        async def send(with_art):
            kwargs = {'embed': embed, 'view': self, 'allowed_mentions': discord.AllowedMentions.none()}
            if opening:
                if with_art and file:
                    kwargs['file'] = file
                return await interaction.followup.send(**kwargs, ephemeral=True, wait=True)
            kwargs['attachments'] = [attachment] if with_art and attachment else []
            return await interaction.edit_original_response(**kwargs)

        try:
            async with asyncio.timeout(15):
                message = await send(attachment is not None)
        except discord.HTTPException as error:
            if attachment is None or isinstance(error, discord.NotFound):
                raise
            logger.warning('Care portrait rejected (%s); showing care text', error.status)
            embed.set_image(url=None)
            async with asyncio.timeout(15):
                message = await send(False)
        finally:
            if file:
                file.close()
                buffer.close()
        if message is not None:
            self.message = message

    async def reject(self, interaction, text):
        if interaction.response.is_done():
            return await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        return await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error, item):
        logger.warning('Care panel update failed; item operations are never retried',
                       exc_info=(type(error), error, error.__traceback__))
        try:
            await self.reject(interaction, 'The care panel could not be updated. Open /care to check HP and inventory before using another item.')
        except (discord.HTTPException, TimeoutError):
            pass

    async def handle(self, interaction, action, *, generation=None, value=None):
        if interaction.user.id != self.user_id:
            return await self.reject(interaction, 'Open /care to use items on your own companions.')
        if self.closed:
            return await self.reject(interaction, 'This care panel has closed. Open /care again.')
        generation = self.generation if generation is None else generation
        if generation != self.generation:
            return await self.reject(interaction, 'This preview has changed. Use the latest care panel or reopen /care.')
        if action in {'open_search', 'open_name'}:
            modal = CareSearch(self, generation) if action == 'open_search' else CareName(self, generation)
            return await interaction.response.send_modal(modal)
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            if self.closed or generation != self.generation:
                return await self.reject(interaction, 'This preview has changed. Use the latest care panel or reopen /care.')
            previous = self.current_preview
            visible_ids = {row['id'] for row in self.page_rows()[0]}
            selected_id = self.state.selected_id
            self.state = await care.snapshot(self.user_id, selected_id)
            self.notice = ''
            self.generation += 1
            if action == 'companion':
                try:
                    target_id = int(interaction.data['values'][0])
                except (KeyError, IndexError, ValueError, TypeError):
                    target_id = None
                if target_id in visible_ids and any(row['id'] == target_id for row in self.state.rows):
                    self.state = await care.snapshot(self.user_id, target_id)
                else:
                    self.state = await care.snapshot(self.user_id, -1)
                    self.notice = 'That companion changed. Choose one from the refreshed list.'
                self.confirming, self.choice = False, None
            elif action == 'item':
                requested = interaction.data.get('values', [None])[0]
                if requested in care.supported_items(self.state) or requested == self.item_id:
                    self.item_id = requested
                    self.choice = None
                else:
                    self.notice = 'That item is no longer available. Choose another owned item.'
                self.confirming = False
            elif action in {'previous', 'next', 'search', 'clear'}:
                if action in {'previous', 'next'}:
                    self.picker.page += -1 if action == 'previous' else 1
                else:
                    self.picker.query = str(value or '').strip()[:100] if action == 'search' else ''
                    self.picker.page = 0
                visible, _ = self.page_rows()
                if visible:
                    self.state = await care.snapshot(self.user_id, visible[0]['id'])
                else:
                    self.state = await care.snapshot(self.user_id, -1)
                self.confirming, self.choice = False, None
            elif action == 'name':
                self.choice = str(value or '').strip()
                self.confirming = False
            elif action == 'cancel':
                self.confirming = False
            elif action in {'use', 'confirm'}:
                fresh = care.preview(self.state, self.item_id, self.choice) if self.item_id else None
                if not previous or not fresh or previous.item_id != fresh.item_id or previous.fingerprint != fresh.fingerprint:
                    self.notice = 'Your companion or item effects changed. Review the refreshed preview before using an item.'
                    self.confirming = False
                elif not fresh.usable:
                    self.notice = fresh.reason or 'This item cannot be used right now.'
                    self.confirming = False
                elif fresh.requires_confirmation and action == 'use':
                    self.confirming = True
                elif action == 'confirm' and (not self.confirming or not fresh.requires_confirmation):
                    self.notice = 'Review this item before confirming it.'
                    self.confirming = False
                else:
                    # This revision is spent before awaiting the transaction.
                    # A network failure can never retry the item operation.
                    self.confirming = False
                    beast = self.state.beast
                    try:
                        self.notice = await use_item(self.user_id, self.item_id, choice=self.choice,
                            beast_number=owned_roster.number(beast), expected_row_id=beast['id'],
                            expected_species=beast['beast_id'], expected_care_state=previous.fingerprint)
                    except ValueError as error:
                        self.notice = str(error)
                    except Exception:
                        logger.exception('Care item outcome could not be confirmed')
                        self.notice = 'Item use could not be confirmed. Check the refreshed state before using another item.'
                    self.state = await care.snapshot(self.user_id, selected_id)
            else:
                self.confirming = False
            await self.publish(interaction)

    async def on_timeout(self):
        async with self.lock:
            self.closed = True
            self.clear_items()
            if self.message is not None:
                try:
                    async with asyncio.timeout(15):
                        await self.message.edit(view=None)
                except (discord.HTTPException, TimeoutError):
                    pass
            self.stop()


async def open_care(interaction, *, beast_row_id=None, beast_number=None, already_deferred=False):
    if not already_deferred:
        await interaction.response.defer(ephemeral=True)
    if beast_row_id is None and beast_number is not None:
        rows = await storage.get_player_beasts(interaction.user.id)
        beast_row_id = next((row['id'] for row in rows if owned_roster.number(row) == beast_number), -1)
    state = await care.snapshot(interaction.user.id, beast_row_id)
    view = CareView(interaction.user.id, state)
    if state.beast:
        matches = owned_roster.filtered(state.rows, view.picker)
        view.picker.page = next(index for index, row in enumerate(matches) if row['id'] == state.selected_id) // PAGE_SIZE
    try:
        await view.publish(interaction, opening=True)
    except BaseException:
        view.stop()
        raise
    return view
