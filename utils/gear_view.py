"""Browse an owned companion's current equipment without remembering its number."""
import asyncio
import io
import logging

import aiosqlite
import discord

from utils import db as storage, gear_art, owned_roster
from utils.equipment import definitions, dormant_label, effect_summary
from utils.theme import COLORS, RARITY_EMOJI

logger = logging.getLogger('chibibeasts.gear.art')
PAGE_SIZE = 25


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


class GearSearch(discord.ui.Modal, title='Find a beast'):
    def __init__(self, view):
        super().__init__(timeout=180)
        self.gear_view = view
        self.query = discord.ui.TextInput(label='Name, nickname or collection number', required=False,
                                         max_length=100, default=view.picker.query)
        self.add_item(self.query)

    async def on_submit(self, interaction):
        await self.gear_view.handle(interaction, 'search', str(self.query))


class GearView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.picker = owned_roster.PickerState()
        self.rows = []
        self.selected_id = None
        self.bound = []
        self.available = {}
        self.trainer_level = 1
        self.message = None
        self.notice = ''
        self.closed = False
        self.lock = asyncio.Lock()
        self.select = discord.ui.Select(placeholder='Choose a beast', row=0)
        self.select.callback = lambda interaction: self.handle(interaction, 'select')
        self.add_item(self.select)
        self.previous = self.button('Previous', 'previous')
        self.next = self.button('Next', 'next')
        self.search = self.button('Search', 'open_search')
        self.clear = self.button('Clear search', 'clear')
        self.refresh = self.button('Refresh', 'refresh')
        self.care = discord.ui.Button(label='Care', style=discord.ButtonStyle.primary, row=2)
        self.add_item(self.care)

    def button(self, label, action):
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=1)
        button.callback = lambda interaction: self.handle(interaction, action)
        self.add_item(button)
        return button

    def page_rows(self):
        return owned_roster.visible(self.rows, self.picker, page_size=PAGE_SIZE)

    def selected(self):
        return next((row for row in self.rows if row['id'] == self.selected_id), None)

    async def reload(self):
        # Recovery is the same read preparation used by /collection. All fields
        # subsequently shown belong to one fresh, read-only database snapshot.
        await storage.get_player_beasts(self.user_id)
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN')
            async with db.execute('SELECT * FROM player_beasts WHERE user_id=? ORDER BY COALESCE(player_number,id)',
                                  (self.user_id,)) as cursor:
                self.rows = [dict(row) for row in await cursor.fetchall()]
            async with db.execute('SELECT level FROM players WHERE user_id=?', (self.user_id,)) as cursor:
                player = await cursor.fetchone()
                self.trainer_level = player['level'] if player else 1
            async with db.execute('SELECT beast_row_id,equipment_id FROM player_equipment WHERE user_id=? ORDER BY id',
                                  (self.user_id,)) as cursor:
                self.bound = [dict(row) for row in await cursor.fetchall()]
            async with db.execute('SELECT item_id,SUM(quantity) AS quantity FROM player_inventory WHERE user_id=? AND quantity>0 GROUP BY item_id',
                                  (self.user_id,)) as cursor:
                self.available = {row['item_id']: row['quantity'] for row in await cursor.fetchall()}

    def armor_and_rune(self):
        row = self.selected()
        if not row:
            return '', None
        catalog = definitions()
        equipped = [entry['equipment_id'] for entry in self.bound if entry['beast_row_id'] == row['id']]
        armor = next((key for key in equipped if catalog.get(key, {}).get('slot') == 'armor'), '')
        rune = row.get('rune_id') or next((key for key in equipped if catalog.get(key, {}).get('slot') == 'rune'), None)
        return armor, rune

    def inventory_text(self, slot):
        catalog = definitions()
        counts = {}
        for entry in self.bound:
            key = entry['equipment_id']
            if entry['beast_row_id'] is None and catalog.get(key, {}).get('slot') == slot:
                counts[key] = counts.get(key, 0) + 1
        if slot == 'rune':
            for key, amount in self.available.items():
                if catalog.get(key, {}).get('slot') == slot:
                    counts[key] = counts.get(key, 0) + amount
        lines = []
        for key, amount in counts.items():
            gear = catalog[key]
            line = f"{RARITY_EMOJI.get(gear.get('rarity'), '⚪')} {gear['name']}"
            if amount > 1:
                line += f' ×{amount}'
            dormant = dormant_label(gear, self.trainer_level)
            if dormant:
                line += ' · ' + dormant
            if len('\n'.join(lines + [line])) > 920:
                lines.append('More gear available — open /craft to browse it.')
                break
            lines.append(line)
        return '\n'.join(lines)

    def render(self):
        visible, pages = self.page_rows()
        self.select.options = owned_roster.options(visible, self.selected_id) or [
            discord.SelectOption(label='No matching beasts', value='none')]
        self.select.placeholder = f'Choose a beast · Page {self.picker.page + 1}/{pages}'
        self.select.disabled = self.closed or not visible
        self.previous.disabled = self.closed or self.picker.page <= 0
        self.next.disabled = self.closed or self.picker.page >= pages - 1
        self.search.disabled = self.closed or not self.rows
        self.clear.disabled = self.closed or not self.picker.query
        self.refresh.disabled = self.closed
        row = self.selected()
        self.care.disabled = self.closed or row is None
        selected_id = row['id'] if row else None
        self.care.callback = lambda interaction: self.open_care(interaction, selected_id)
        if not row:
            if not self.rows:
                description = 'Your collection is empty. Choose a starter or hatch a beast first.'
            elif self.picker.query:
                description = 'No beasts match your search. Clear it or try another name or number.'
            else:
                description = self.notice or 'Choose a beast to view its equipment.'
            return discord.Embed(title='🛡️ Gear', description=description, color=COLORS['info'])
        number = owned_roster.number(row)
        description = f"Lv.{row['level']} · `{row['hp']}/{row['max_hp']}HP`"
        if self.notice:
            description += '\n' + self.notice
        if self.picker.query:
            description += '\nSearch: ' + safe(self.picker.query)
        embed = discord.Embed(title=f"🛡️ Gear — {RARITY_EMOJI.get(row['rarity'], '⚪')} {safe(owned_roster.name(row))} #{number}",
                              description=description, color=COLORS.get(row['rarity'], COLORS['info']))
        catalog = definitions()
        armor, rune = self.armor_and_rune()
        for key, slot_label in ((armor, '🔰 Armor'), (rune, '💎 Rune')):
            if key and key in catalog:
                gear = catalog[key]
                dormant = dormant_label(gear, self.trainer_level)
                embed.add_field(name=f"{slot_label.split()[0]} {RARITY_EMOJI.get(gear.get('rarity'), '⚪')} {gear['name']}",
                                value=dormant + '\nEffects are inactive.' if dormant else effect_summary(gear) or 'Special', inline=False)
            else:
                embed.add_field(name=slot_label, value=f'*None equipped — use `/equip <{slot_label.split()[1].lower()}> {number}`*', inline=False)
        available_armor, available_runes = self.inventory_text('armor'), self.inventory_text('rune')
        if available_armor:
            embed.add_field(name='📦 Armor in Inventory', value=available_armor, inline=False)
        if available_runes:
            embed.add_field(name='📦 Runes in Inventory', value=available_runes, inline=False)
        if not any((available_armor, available_runes, armor, rune)):
            embed.add_field(name='🔍 No Gear Available', value='Open `/craft` to browse recipes, check materials and craft gear.', inline=False)
        embed.set_footer(text=f'/equip <item> {number} to equip · /unequip {number} to remove · Viewing gear never changes your active beast')
        return embed

    async def publish(self, interaction, *, opening=False):
        embed = self.render()
        row = self.selected()
        armor, _ = self.armor_and_rune()
        portrait = await gear_art.render_portrait(row['beast_id'], armor) if row else None
        file = None
        buffer = None
        if portrait:
            buffer = io.BytesIO(portrait.data)
            file = discord.File(buffer, filename=portrait.filename,
                                description=f'{owned_roster.name(row)} equipment appearance.')
            embed.set_image(url='attachment://' + portrait.filename)
        async def send(*, artwork):
            kwargs = {'embed': embed, 'view': self}
            if opening:
                if artwork:
                    kwargs['file'] = file
                return await interaction.followup.send(**kwargs, wait=True)
            kwargs['attachments'] = [file] if artwork else []
            return await interaction.edit_original_response(**kwargs)
        try:
            async with asyncio.timeout(15):
                message = await send(artwork=file is not None)
        except discord.HTTPException as error:
            if file is None or isinstance(error, discord.NotFound):
                raise
            logger.warning('Equipment portrait rejected (%s); showing gear text', error.status)
            embed.set_image(url=None)
            async with asyncio.timeout(15):
                message = await send(artwork=False)
        finally:
            if file:
                file.close()
                buffer.close()
        if message is not None:
            self.message = message

    async def handle(self, interaction, action, value=None):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('Open /gear to view your own beasts.', ephemeral=True)
        if self.closed:
            return await interaction.response.send_message('This gear panel has closed. Open /gear again.', ephemeral=True)
        if action == 'open_search':
            return await interaction.response.send_modal(GearSearch(self))
        await interaction.response.defer()
        async with self.lock:
            if self.closed:
                return
            previously_visible = {row['id'] for row in self.page_rows()[0]}
            await self.reload()
            self.notice = ''
            if action == 'select':
                try:
                    requested = int(interaction.data['values'][0])
                except (KeyError, IndexError, TypeError, ValueError):
                    requested = None
                if requested in previously_visible and any(row['id'] == requested for row in self.rows):
                    self.selected_id = requested
                else:
                    self.selected_id = None
                    self.notice = 'That selection changed. Choose a beast from the updated list.'
            elif action in {'previous', 'next'}:
                self.picker.page += -1 if action == 'previous' else 1
                visible, _ = self.page_rows()
                self.selected_id = visible[0]['id'] if visible else None
            elif action in {'search', 'clear'}:
                self.picker.query = str(value or '').strip()[:100] if action == 'search' else ''
                self.picker.page = 0
                visible, _ = self.page_rows()
                self.selected_id = visible[0]['id'] if visible else None
            if self.selected_id is not None and self.selected() is None:
                self.selected_id = None
                self.notice = 'That companion is no longer in your collection. Choose another beast.'
            await self.publish(interaction)

    async def open_care(self, interaction, row_id):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('Open /gear to view your own beasts.', ephemeral=True)
        if self.closed:
            return await interaction.response.send_message('This gear panel has closed. Open /gear again.', ephemeral=True)
        if row_id is None:
            return await interaction.response.send_message('Choose a beast before opening its Care panel.', ephemeral=True)
        # Care revalidates the captured identity before showing its private
        # preview; this never redirects a click to a renumbered companion.
        from utils.care_view import open_care
        return await open_care(interaction, beast_row_id=row_id)

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


async def open_gear(interaction, beast_number=None):
    view = GearView(interaction.user.id)
    await view.reload()
    if beast_number is None:
        row = next((row for row in view.rows if row.get('is_active')), view.rows[0] if view.rows else None)
    else:
        row = next((row for row in view.rows if owned_roster.number(row) == beast_number), None)
        if row is None:
            view.notice = f'No beast found with number #{beast_number}. Choose one below.'
    if row:
        view.selected_id = row['id']
        matches = owned_roster.filtered(view.rows, view.picker)
        view.picker.page = next(index for index, item in enumerate(matches) if item['id'] == row['id']) // PAGE_SIZE
    try:
        await view.publish(interaction, opening=True)
    except BaseException:
        view.stop()
        raise
    return view
