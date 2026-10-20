"""A compact perk build editor using the existing slot and weekly limits."""
import asyncio
from datetime import datetime, timezone
import json

import aiosqlite
import discord

from utils import db as storage
from utils.gameplay import equip_perk, shorten_incubation
from utils.rewards import has_perk
from utils.theme import COLORS


async def unequip_perk(user_id, perk_id):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT equipped FROM player_perks WHERE user_id=? AND perk_id=?', (user_id, perk_id)) as c:
            row = await c.fetchone()
        if not row:
            raise ValueError('That perk is no longer in your collection.')
        await db.execute('UPDATE player_perks SET equipped=0 WHERE user_id=? AND perk_id=?', (user_id, perk_id))
        await db.commit()


async def activate_astral(user_id):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        if not await has_perk(db, user_id, 'astral_resonance'):
            raise ValueError('Equip Astral Resonance first.')
        async with db.execute('SELECT shard_shop_week FROM players WHERE user_id=?', (user_id,)) as c:
            row = await c.fetchone()
        try:
            data = json.loads(row[0] or '{}')
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        week = datetime.now(timezone.utc).strftime('%G-W%V')
        if data.get('week') != week:
            data = {'week': week}
        if data.get('astral_resonance_used'):
            raise ValueError('Already used this week. Resets Monday UTC.')
        result = await shorten_incubation(db, user_id, hours=6)
        data['astral_resonance_used'] = True
        await db.execute('UPDATE players SET shard_shop_week=? WHERE user_id=?', (json.dumps(data), user_id))
        await db.commit()
    return result


class PerksView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.page, self.frame = 0, 0
        self.selected = None
        self.notice = ''
        self.owned, self.player = {}, {}
        self.lock = asyncio.Lock()
        self.message = None

    async def load(self):
        self.catalog = storage.load_perks()['perks']
        self.player = await storage.get_player(self.user_id) or {}
        async with storage.connect() as db:
            async with db.execute('SELECT perk_id,equipped FROM player_perks WHERE user_id=? ORDER BY perk_id', (self.user_id,)) as c:
                self.owned = {row[0]: bool(row[1]) for row in await c.fetchall() if row[0] in self.catalog}

    def render(self):
        self.clear_items()
        self.frame += 1
        frame = self.frame
        used = sum(self.catalog[key]['slot_cost'] for key, active in self.owned.items() if active)
        equipped = ', '.join(self.catalog[key]['name'] for key, active in self.owned.items() if active) or 'None equipped'
        embed = discord.Embed(title='Your perks', color=COLORS['epic'],
            description=f"**Slots · {used}/{storage.get_perk_slots(self.player.get('level', 1))}**\n{equipped}" + ('\n\n' + self.notice if self.notice else ''))
        keys = list(self.owned)
        pages = max(1, (len(keys) + 24) // 25)
        self.page = max(0, min(self.page, pages - 1))
        visible = keys[self.page * 25:(self.page + 1) * 25]
        if self.selected not in visible:
            self.selected = visible[0] if visible else None
        if visible:
            picker = discord.ui.Select(placeholder=f'Choose a perk · {self.page + 1}/{pages}', row=0,
                options=[discord.SelectOption(label=self.catalog[key]['name'][:100], value=key, default=key == self.selected,
                    description=f"{self.catalog[key]['slot_cost']} slots · " + ('Equipped' if self.owned[key] else 'Available')) for key in visible])
            picker.callback = lambda click: self.handle(click, 'select', frame, tuple(visible))
            self.add_item(picker)
            perk = self.catalog[self.selected]
            embed.add_field(name=perk['name'], value=perk['description'][:1000], inline=False)
            if self.selected == 'astral_resonance':
                embed.add_field(name='Weekly ability', value='Bring your oldest incubating egg up to 6 hours closer. Resets Monday UTC.', inline=False)
            controls = [('Equip', 'equip', self.owned[self.selected]), ('Unequip', 'unequip', not self.owned[self.selected])]
            if self.selected == 'astral_resonance':
                controls.append(('Activate weekly · 6 hours', 'activate', not self.owned[self.selected]))
            for label, action, disabled in controls:
                self.add_button(label, action, frame, self.selected, disabled=disabled)
        else:
            embed.add_field(name='No perks yet', value='Your first perk unlocks at trainer Lv.3. Explore, catch and progress through the story.', inline=False)
        if pages > 1:
            self.add_button('Previous', 'prev', frame, disabled=self.page == 0, row=2)
            self.add_button('Next', 'next', frame, disabled=self.page + 1 >= pages, row=2)
        self.add_button('Refresh', 'refresh', frame, row=2)
        return embed

    def add_button(self, label, action, frame, value=None, *, disabled=False, row=1):
        button = discord.ui.Button(label=label, row=row, disabled=disabled)
        button.callback = lambda click: self.handle(click, action, frame, value)
        self.add_item(button)

    async def handle(self, click, action, frame, value=None):
        if click.user.id != self.user_id or self.is_finished():
            return await click.response.send_message('Open /perks for your own current build.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if frame != self.frame:
                return await click.followup.send('Use the refreshed perk controls.', ephemeral=True)
            self.notice = ''
            try:
                if action == 'select':
                    key = click.data['values'][0]
                    if key not in value:
                        raise ValueError('Choose a perk from this page.')
                    self.selected = key
                elif action in {'equip', 'unequip', 'activate'}:
                    if value != self.selected:
                        raise ValueError('Review the current perk first.')
                    if action == 'equip':
                        await equip_perk(self.user_id, value)
                        from utils.progress import unlock_simple_achievement
                        await unlock_simple_achievement(self.user_id, 'first_perk')
                        self.notice = 'Perk equipped.'
                    elif action == 'unequip':
                        await unequip_perk(self.user_id, value)
                        self.notice = 'Perk unequipped.'
                    elif value == 'astral_resonance':
                        self.notice = await activate_astral(self.user_id)
                elif action in {'prev', 'next'}:
                    self.page += -1 if action == 'prev' else 1
            except (ValueError, KeyError) as error:
                self.notice = str(error)
            await self.load()
            await click.edit_original_response(embed=self.render(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def open_perks(interaction, *, already_deferred=False, ephemeral=False):
    if not already_deferred:
        await interaction.response.defer(ephemeral=ephemeral)
    await storage.get_or_create_player(interaction.user.id, str(interaction.user))
    from utils.progress import check_achievements
    await check_achievements(interaction.user.id)
    view = PerksView(interaction.user.id)
    await view.load()
    view.message = await interaction.followup.send(embed=view.render(), view=view, wait=True)
    return view
