"""Compact, illustrated Discord cards shared by the codex and starter browser."""
import asyncio
import logging

import discord

from utils.artwork import card_artwork_url
from utils.theme import COLORS, RARITY_LABEL, TYPE_EMOJI
from utils.type_chart import TYPE_LORE

STARTER_ORDER = ('prismite', 'twine', 'gloop', 'barkley')
STARTER_ROLES = {
    'prismite': ('Steady and balanced', 'A balanced companion with sturdy defense and a generous mana pool.'),
    'twine': ('Quick and tactical', 'Act quickly, weaken an opponent and guard to prepare a stronger strike.'),
    'gloop': ('Gentle and resilient', 'A large health pool and a healing skill help you stay in the fight.'),
    'barkley': ('Patient and protective', 'The strongest starting defense; guard, endure and answer with a focused attack.'),
}


def stats_text(beast):
    stats = beast['base_stats']
    return f"HP `{stats['hp']}` · ATK `{stats['attack']}` · DEF `{stats['defense']}`\nSPD `{stats['speed']}` · MANA `{stats['mana']}`"


def add_moves(embed, beast):
    for name in beast['moves']:
        spec = beast.get('move_effects', {}).get(name, {})
        cost = spec.get('mana_cost', 0)
        embed.add_field(name=f'{name} · {cost} MP', value=spec.get('description', 'A reliable attack.'), inline=False)
    embed.add_field(name=f"⚡ {beast['ultimate']}", value='Ultimate · 3 charges · 50 MP', inline=False)


def identity(embed, beast):
    element = beast.get('type', '')
    embed.add_field(name='Type', value=f"{TYPE_EMOJI.get(element, '✦')} {element.title()}", inline=True)
    rarity = beast['rarity']
    embed.add_field(name='Rarity', value=RARITY_LABEL.get(rarity, rarity.title()), inline=True)
    if beast.get('collection'):
        embed.add_field(name='Collection', value=beast['collection'], inline=True)


def codex_embed(beast, tab='overview'):
    embed = discord.Embed(title=beast['name'], description=f"*{beast['title']}*", color=COLORS.get(beast['rarity'], COLORS['info']))
    if tab == 'overview':
        embed.description += f"\n\n{beast['description']}"
        identity(embed, beast)
        embed.set_image(url=card_artwork_url(beast['id']))
    elif tab == 'battle':
        embed.set_thumbnail(url=card_artwork_url(beast['id']))
        embed.add_field(name='Base stats · Lv.1', value=stats_text(beast), inline=False)
        add_moves(embed, beast)
        passive = beast.get('divine_passive')
        if passive:
            embed.add_field(name=f"✨ {passive['passive_name']}", value=passive['passive_desc'], inline=False)
    else:
        embed.set_thumbnail(url=card_artwork_url(beast['id']))
        element = beast.get('type', '')
        if element in TYPE_LORE:
            embed.add_field(name=f'{element.title()} lore', value=TYPE_LORE[element], inline=False)
        if beast.get('collection'):
            embed.add_field(name='Collection', value=beast['collection'], inline=False)
        if beast.get('starter'):
            embed.add_field(name=f"🏛️ {beast.get('starter_house', 'Starter companion')}", value=beast.get('starter_flavor') or beast['description'], inline=False)
        evolution = beast.get('evolution')
        if evolution:
            from utils.db import get_beast_data
            form = get_beast_data(evolution['evolves_to'])
            embed.add_field(name=f"Next form · {form['name'] if form else evolution['evolves_to'].replace('_', ' ').title()}", value=evolution.get('description', 'See /beastinfo for evolution requirements.'), inline=False)
        if not embed.fields:
            embed.description += f"\n\n{beast['description']}"
    embed.set_footer(text=f"ChibiBeasts · {'Moves & stats' if tab == 'battle' else tab.title()} · /bestiary for server discoveries")
    return embed


def starter_overview_embed():
    embed = discord.Embed(
        title='Your first thread',
        description='Before the world had a floor to stand on, the Loom wove four threads—and the Architects awoke. Each sent a small companion to discover what the world could become.\n\n**Meet all four. Find your companion.**\nSelect a name below to see their portrait, play style and moves. Your choice is confirmed with **Choose**.',
        color=COLORS['divine'],
    )
    embed.set_image(url=card_artwork_url())
    embed.set_footer(text='ChibiBeasts · Meet a starter before beginning your journey')
    return embed


def starter_preview_embed(beast, *, details=False):
    role, description = STARTER_ROLES[beast['id']]
    embed = discord.Embed(title=beast['name'], description=f"*{beast['title']}*\n\n{beast.get('starter_flavor', beast['description'])}", color=COLORS.get(beast['rarity'], COLORS['info']))
    if details:
        embed.set_thumbnail(url=card_artwork_url(beast['id']))
        embed.add_field(name='Base stats · Lv.1', value=stats_text(beast), inline=False)
        add_moves(embed, beast)
    else:
        element = beast['type']
        embed.add_field(name='Type', value=f"{TYPE_EMOJI.get(element, '✦')} {element.title()}", inline=True)
        embed.add_field(name='Play style', value=role, inline=True)
        embed.add_field(name='At your side', value=description, inline=False)
        embed.set_image(url=card_artwork_url(beast['id']))
    embed.set_footer(text='ChibiBeasts · Previewing a companion · Confirm with Choose')
    return embed


class CodexView(discord.ui.View):
    def __init__(self, trainer_id, beast):
        super().__init__(timeout=180)
        self.trainer_id, self.beast, self.tab = trainer_id, beast, 'overview'
        self.message = None
        self.lock = asyncio.Lock()
        self._update_buttons()

    def _update_buttons(self):
        for button, tab in zip(self.children, ('overview', 'battle', 'lore')):
            button.style = discord.ButtonStyle.primary if tab == self.tab else discord.ButtonStyle.secondary

    async def show(self, interaction, tab):
        if interaction.user.id != self.trainer_id:
            return await interaction.response.send_message('Open /codex to browse your own copy.', ephemeral=True)
        await interaction.response.defer()
        async with self.lock:
            if self.is_finished():
                return await interaction.followup.send('This codex has closed. Use /codex to open it again.', ephemeral=True)
            self.tab = tab
            self._update_buttons()
            async with asyncio.timeout(15):
                await interaction.edit_original_response(embed=codex_embed(self.beast, tab), view=self)

    @discord.ui.button(label='Overview', style=discord.ButtonStyle.primary)
    async def overview(self, interaction, button):
        await self.show(interaction, 'overview')

    @discord.ui.button(label='Moves & stats', style=discord.ButtonStyle.secondary)
    async def battle(self, interaction, button):
        await self.show(interaction, 'battle')

    @discord.ui.button(label='Lore', style=discord.ButtonStyle.secondary)
    async def lore(self, interaction, button):
        await self.show(interaction, 'lore')

    async def on_timeout(self):
        async with self.lock:
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

    async def on_error(self, interaction, error, item):
        logging.getLogger('chibibeasts.codex').error('Codex input failed for %s', self.beast['id'], exc_info=error)
        message = 'The codex could not turn that page. Use /codex to open it again.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass
