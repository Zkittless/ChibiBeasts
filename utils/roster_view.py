"""One searchable, read-only browser for owned companions and detailed cards."""
import asyncio
from contextlib import nullcontext
import io
import logging

import discord

from utils import db as storage, gear_art, owned_roster
from utils.db import (get_beast_data, get_beast_exp_for_level, is_knocked_out,
                      ko_time_remaining)
from utils.dispositions import disposition_display
from utils.progression import awakening_requirements, evolution_blockers
from utils.theme import COLORS, RARITY_EMOJI, TYPE_EMOJI, exp_bar, fmt_stats

PAGE_SIZE = 10
logger = logging.getLogger('chibibeasts.roster')


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def detail_embed(row: dict, player, inventory, rows) -> discord.Embed:
    beast_data = get_beast_data(row["beast_id"])
    if not beast_data:
        return discord.Embed(description="✦ Beast data not found!", color=COLORS["error"])
    name = safe(row["nickname"] or beast_data["name"])
    rarity = row["rarity"]
    exp_needed = get_beast_exp_for_level(dict(row), row["level"])
    num = owned_roster.number(row)
    active_tag = " ⚔️" if row["is_active"] else ""
    embed = discord.Embed(
        title=f"{RARITY_EMOJI.get(rarity,'⚪')} {name}{active_tag}",
        description=f"*{beast_data['title']}*\n{beast_data['description']}",
        color=COLORS.get(rarity, COLORS["info"])
    )
    embed.add_field(name="📊 Stats", value=fmt_stats(row), inline=True)
    ult_charges = player.get("ultimate_charges", 0) or 0
    ult_line = ""
    if row.get("is_active"):
        ult_bar = "⚡" * ult_charges + "◇" * (3 - ult_charges)
        ult_label = "**READY**" if ult_charges >= 3 else f"{ult_charges}/3"
        ult_line = f"\n⚡ Ultimate: {ult_bar} {ult_label}"
    embed.add_field(
        name="📈 Progress",
        value=(
            f"⭐ Level: `{row['level']}`\n"
            f"✨ EXP: {exp_bar(row['exp'], exp_needed)}\n"
            f"😊 Happiness: `{row['happiness']}/100`"
            + ult_line
        ),
        inline=True
    )
    embed.add_field(
        name="⚡ Moves",
        value="\n".join(f"• {m}" for m in beast_data["moves"]) + f"\n🌟 **Ultimate:** {beast_data['ultimate']}",
        inline=False
    )
    embed.add_field(name="🎭 Disposition", value=disposition_display(row.get("disposition")), inline=False)
    # KO recovery status
    if is_knocked_out(row):
        timer = ko_time_remaining(row)
        embed.add_field(
            name="💀 Knocked Out",
            value=f"Recovering — ready in `{timer}`\n*Use a **Phoenix Elixir** to revive instantly.*",
            inline=False
        )
    # Evolution hint
    evo = beast_data.get("evolution")
    if evo and evo.get("evolves_to"):
        from utils.db import load_items as _li
        _items = _li()
        method_id = evo.get("method", "")
        method_item = _items.get(method_id, {})
        method_name = method_item.get("name", method_id.replace("_"," ").title())
        tgt_id = evo["evolves_to"]
        tgt_bd = get_beast_data(tgt_id) or {}
        tgt_name = tgt_bd.get("name", tgt_id)
        tgt_r = RARITY_EMOJI.get(tgt_bd.get("rarity",""), "⚪")
        form = evo.get("form","")
        form_label = "✨ Ascended" if form == "ascended" else "🌟 Radiant" if form == "radiant" else "🔀 Evolves"
        lvl_req = evo.get("level_required", 1)
        recipe = method_item.get("recipe")
        recipe_str = ""
        if recipe:
            from utils.economy_balance import recipe_level
            recipe_parts = ", ".join(f"{q}× {m.replace('_',' ').title()}" for m, q in recipe.items())
            recipe_str = f"\n*Craft at trainer level {recipe_level(method_item)}: {recipe_parts}*"
        elif method_id == "abyssal_scale":
            recipe_str = "\n*Drop: Corrupted Leviathan raid*"
        blockers = evolution_blockers(row, player['level'], evo, inventory=inventory,
                                       knocked_out=is_knocked_out(row))
        can_evolve = not blockers
        if form == 'bond':
            required = awakening_requirements(evo)
            method_name = 'No item needed — a bond awakening'
            recipe_str = (f"\nTrainer: {player['level']}/{required['trainer_level']} · "
                          f"Bond: {row.get('bond', 0)}/{required['bond']} · "
                          f"Happiness: {row['happiness']}/{required['happiness']} · "
                          "`/journal` → Companions shows your progress.")
        else:
            recipe_str += f"\nOwned: {inventory.get(method_id, 0)}"
        lvl_note = blockers[0] if not can_evolve else f"✅ Lv.{lvl_req} — **ready to evolve!**"
        embed.add_field(
            name=f"{form_label} → {tgt_r} {tgt_name}",
            value=f"**Item:** {method_name} · {lvl_note}{recipe_str}\n*Use `/evolve #{num}` when ready.*",
            inline=False
        )
    if beast_data.get("divine_passive"):
        dp = beast_data["divine_passive"]
        passive_label = {
            "divine": "✨ Divine Passive", "altered_divine": "⚠️ Altered Passive",
            "corrupted": "🖤 Corrupted Passive", "ancient": "🏛️ Ancient Passive",
            "dev": "👑 Developer Passive",
        }.get(rarity, "✨ Special Passive")
        embed.add_field(name=f"{passive_label}: **{dp['passive_name']}**", value=f"*{dp['passive_desc']}*", inline=False)
    if beast_data.get("starter"):
        embed.add_field(
            name="🏛️ Origin",
            value=f"*{beast_data.get('starter_house', 'Unknown House')} — {beast_data.get('starter_flavor', '')}*",
            inline=False
        )
    embed.set_footer(text=f"Beast #{num} · {len(rows)} companions | Caught via: {row['caught_from']}")
    return embed


def compact_embed(row, player, inventory):
    beast = get_beast_data(row['beast_id'])
    embed = discord.Embed(title=f"{safe(owned_roster.name(row))} · #{owned_roster.number(row)}",
        description=f"Lv.{row['level']} · Bond {row.get('bond', 0)}/100 · Happiness {row['happiness']}/100" + ('\n**Active lead**' if row['is_active'] else ''),
        color=COLORS.get(row['rarity'], COLORS['info']))
    embed.add_field(name='Stats', value=fmt_stats(row), inline=False)
    if is_knocked_out(row):
        embed.add_field(name='Recovering', value=ko_time_remaining(row), inline=False)
    evo = beast.get('evolution') or {}
    if evo.get('evolves_to'):
        blockers = evolution_blockers(row, player['level'], evo, inventory=inventory, knocked_out=is_knocked_out(row))
        readiness = blockers[0] if blockers else 'Your companion is ready to evolve.'
        if evo.get('form') == 'bond':
            required = awakening_requirements(evo)
            cost = (f"No item needed · Bond: {row.get('bond', 0)}/{required['bond']}\n"
                    f"Trainer: {player['level']}/{required['trainer_level']} · Happiness: {row['happiness']}/{required['happiness']}")
        else:
            item = storage.load_items().get(evo.get('method'), {})
            cost = f"{item.get('name', evo.get('method', 'Catalyst'))} · Owned: {inventory.get(evo.get('method'), 0)}"
            if item.get('recipe'):
                from utils.economy_balance import recipe_level
                cost += f"\nCraft at trainer level {recipe_level(item)}."
        embed.add_field(name='Evolution → ' + get_beast_data(evo['evolves_to'])['name'],
                        value=readiness + '\n' + cost, inline=False)
    embed.set_footer(text=f"Beast #{owned_roster.number(row)} · Companion actions for care, gear and growth. Details shows moves and lore.")
    return embed


class SearchModal(discord.ui.Modal, title='Find a companion'):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.query = discord.ui.TextInput(
            label='Nickname, species or collection number',
            placeholder='e.g. Twine, Ember or #27',
            default=view.picker.query, required=False, max_length=100,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction):
        await self.view.show(interaction, query=self.query.value.strip(), mode='overview')


class OwnedRosterView(discord.ui.View):
    """Search, roster pages and individual cards share one message and lock."""
    def __init__(self, user_id, display_name, *, details=False, navigator=None):
        super().__init__(timeout=300)
        self.user_id, self.display_name = user_id, display_name
        self.mode = 'detail' if details else 'overview'
        self.picker = owned_roster.PickerState()
        self.rows, self.player, self.inventory = [], {}, {}
        self.selected_id = None
        self.message = self.timeout_message = None
        self.art_enabled = True
        self.notice = ''
        self.expanded = False
        self.pinned_detail = False
        self.workspace_route = 'beast'
        if navigator is None:
            from utils.beast_workspace import BeastWorkspace
            navigator = BeastWorkspace(self)
        self.navigator, self.lock = navigator, navigator.lock

        self.rarity_select = discord.ui.Select(placeholder='Filter by rarity', row=0)
        self.rarity_select.callback = self.choose_rarity
        self.sort_select = discord.ui.Select(placeholder='Sort companions', row=1)
        self.sort_select.callback = self.choose_sort
        self.beast_select = discord.ui.Select(placeholder='Open a companion’s details', row=2)
        self.beast_select.callback = self.choose_beast

        self.prev_page = self.button('Previous page', row=3, callback=lambda click: self.show(click, page_delta=-1))
        self.next_page = self.button('Next page', row=3, callback=lambda click: self.show(click, page_delta=1))
        self.page_button = discord.ui.Button(label='1/1', row=3, disabled=True)
        # Keep these named controls and show() usable by existing detail callers.
        self.prev_btn = self.button('◀', row=3, callback=lambda click: self.show(click, delta=-1))
        self.next_btn = self.button('▶', row=3, callback=lambda click: self.show(click, delta=1))
        self.back_button = self.button('Back to roster', row=3,
                                      callback=lambda click: self.show(click, mode='overview'))
        self.search_button = self.button('Search', row=4, callback=self.search,
                                        style=discord.ButtonStyle.primary)
        self.clear_button = self.button('Clear filters', row=4,
                                       callback=lambda click: self.show(click, reset=True))
        self.refresh_button = self.button('Refresh', row=4, callback=lambda click: self.show(click))
        self.care_button = self.button('Care & items', row=4, callback=self.care,
                                      style=discord.ButtonStyle.success)
        self.details_button = self.button('Details', row=4, callback=lambda click: self.show(click, expanded=not self.expanded))
        self.journal_button = self.button('Field Journal', row=3, callback=self.return_journal)

    @staticmethod
    def button(label, *, row, callback, style=discord.ButtonStyle.secondary):
        button = discord.ui.Button(label=label, row=row, style=style)
        button.callback = callback
        return button

    async def permitted(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('Open /collection to browse your own companions.', ephemeral=True)
            return False
        return True

    async def search(self, interaction):
        if not await self.permitted(interaction):
            return
        if self.is_finished():
            return await interaction.response.send_message('This roster has closed. Open /collection again.', ephemeral=True)
        await interaction.response.send_modal(SearchModal(self))

    async def care(self, interaction):
        if not await self.permitted(interaction):
            return
        if self.is_finished() or self.mode != 'detail' or self.selected_id is None:
            return await interaction.response.send_message('Open a current companion card before choosing care.', ephemeral=True)
        return await self.navigator.navigate(interaction, self, 'care', self.selected())

    async def choose_rarity(self, interaction):
        await self.show(interaction, rarity=interaction.data['values'][0], mode='overview')

    async def choose_sort(self, interaction):
        await self.show(interaction, sort=interaction.data['values'][0], mode='overview')

    async def choose_beast(self, interaction):
        try:
            row_id = int(interaction.data['values'][0])
        except (KeyError, IndexError, ValueError, TypeError):
            if await self.permitted(interaction):
                await interaction.response.send_message('Choose a companion from the current roster.', ephemeral=True)
            return
        await self.show(interaction, target_id=row_id, mode='detail')

    def matches(self):
        return owned_roster.filtered(self.rows, self.picker)

    def selected(self):
        return next((row for row in self.rows if row['id'] == self.selected_id), None)

    def overview_embed(self, visible, pages):
        matches = self.matches()
        description = (f'**{len(matches)} of {len(self.rows)} companions** · Page {self.picker.page + 1}/{pages}\n'
                       'Select a companion below for their portrait, stats and evolution progress.')
        if self.picker.query:
            description += f'\nSearch: **{safe(self.picker.query)}**'
        if self.picker.rarity != 'all':
            description += f" · {self.picker.rarity.replace('_', ' ').title()}"
        if self.notice:
            description += '\n\n' + self.notice
        if not self.rows:
            description += '\n\nNo companions yet. Visit `/nursery` or `/journal` to meet one.'
        elif not matches:
            description += '\n\nNo companions match. Change the search or choose **Clear filters**.'
        embed = discord.Embed(title=f"🐾 {safe(self.display_name)}’s collection", description=description,
                              color=COLORS['divine'])
        for row in visible:
            beast = storage.get_beast_data(row['beast_id']) or {}
            tags = []
            if row['is_active']:
                tags.append('Lead')
            if row.get('is_favorite'):
                tags.append('Favourite')
            if row['hp'] <= 0 or storage.is_knocked_out(row):
                tags.append('Recovering')
            value = (f"{TYPE_EMOJI.get(beast.get('type'), '✦')} {beast.get('type', 'unknown').title()} · "
                     f"Lv.{row['level']} · HP {row['hp']}/{row['max_hp']}")
            if row.get('nickname'):
                value += '\n' + beast.get('name', row['beast_id'])
            if tags:
                value += '\n' + ' · '.join(tags)
            embed.add_field(name=f"{RARITY_EMOJI.get(row['rarity'], '✦')} #{owned_roster.number(row)} · {safe(owned_roster.name(row))}",
                            value=value, inline=False)
        embed.set_footer(text='Browse without changing your lead · /journal → Team to choose who travels')
        return embed

    def render(self):
        visible, pages = owned_roster.visible(self.rows, self.picker, page_size=PAGE_SIZE)
        matches = self.matches()
        self.clear_items()
        rarity_values = ('all',) + owned_roster.RARITIES + ('special',)
        self.rarity_select.options = [discord.SelectOption(
            label='All rarities' if rarity == 'all' else 'Special rarities' if rarity == 'special' else rarity.replace('_', ' ').title(),
            value=rarity, default=rarity == self.picker.rarity,
        ) for rarity in rarity_values]
        self.sort_select.options = [discord.SelectOption(label=label, value=value, default=value == self.picker.sort)
                                    for value, label in (('number', 'Collection number'), ('level', 'Highest level'), ('rarity', 'Highest rarity'))]
        row = self.selected() if self.mode == 'detail' else None
        if not row:
            self.add_item(self.rarity_select)
            self.add_item(self.sort_select)
        self.beast_select.options = owned_roster.options(visible, self.selected_id if self.mode == 'detail' else None)
        if self.beast_select.options:
            self.add_item(self.beast_select)

        if row:
            index = next((index for index, item in enumerate(matches) if item['id'] == row['id']), None)
            self.prev_btn.disabled = index is None or index == 0
            self.next_btn.disabled = index is None or index == len(matches) - 1
            self.prev_btn.label = f"◀ #{owned_roster.number(matches[index - 1])}" if index is not None and index > 0 else '◀'
            self.next_btn.label = f"#{owned_roster.number(matches[index + 1])} ▶" if index is not None and index + 1 < len(matches) else '▶'
            for item in (self.prev_btn, self.back_button, self.next_btn):
                self.add_item(item)
            embed = detail_embed(row, self.player, self.inventory, self.rows) if self.expanded else compact_embed(row, self.player, self.inventory)
            actions = discord.ui.Select(placeholder='Companion actions', row=1, options=[
                discord.SelectOption(label=label, value=key) for key, label in (
                    ('care', 'Care & items'), ('gear', 'Gear'), ('lead', 'Make active lead'),
                    ('train', 'Train'), ('rename', 'Rename'), ('evolve', 'Evolve / awaken'), ('team', 'Team panel'))])
            actions.callback = lambda click, target=dict(row): self.act(click, target)
            self.add_item(actions)
        else:
            self.mode = 'overview'
            self.prev_page.disabled = self.picker.page == 0
            self.next_page.disabled = self.picker.page + 1 >= pages
            self.page_button.label = f'{self.picker.page + 1}/{pages}'
            for item in (self.prev_page, self.page_button, self.next_page):
                self.add_item(item)
            embed = self.overview_embed(visible, pages)
        self.clear_button.disabled = not (self.picker.query or self.picker.rarity != 'all' or self.picker.sort != 'number')
        self.add_item(self.journal_button)
        for item in (self.search_button, self.refresh_button):
            self.add_item(item)
        if not self.clear_button.disabled:
            self.add_item(self.clear_button)
        if row:
            self.add_item(self.care_button)
            self.details_button.label = 'Hide details' if self.expanded else 'Details'
            self.add_item(self.details_button)
            if self.notice:
                embed.description += '\n\n' + self.notice
        return embed, row

    async def act(self, click, target):
        if not await self.permitted(click):
            return
        if self.is_finished():
            return await click.response.send_message('This roster has closed. Open /collection again.', ephemeral=True)
        from utils.companion_actions import current, RenameModal
        action = click.data['values'][0]
        if action == 'rename':
            return await click.response.send_modal(RenameModal(self.user_id, target, navigator=self.navigator, source=self))
        if action in {'care', 'gear', 'train', 'evolve', 'team'}:
            return await self.navigator.navigate(click, self, action, target)
        await click.response.defer()
        async with self.lock:
            if not self.navigator.current(self) or self.selected_id != target['id']:
                return await click.followup.send('Use the current beast card.', ephemeral=True)
            try:
                row = await current(self.user_id, target)
                if action == 'lead':
                    await storage.set_active_companion(self.user_id, owned_roster.number(row), expected_row_id=row['id'])
                    await self.show(click, target_id=row['id'], already_deferred=True, _lock_held=True,
                                    restore_target=True, notice='Active lead saved.')
            except ValueError as error:
                await self.show(click, already_deferred=True, _lock_held=True, notice=str(error))

    async def return_journal(self, click):
        if not await self.permitted(click):
            return
        if self.is_finished():
            return await click.response.send_message('Open /journal to resume.', ephemeral=True)
        from utils.journal import open_journal
        view = await open_journal(click)
        if view and self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass
            self.stop()
        return view

    async def show(self, click, *, delta=0, target_number=None, target_id=None, mode=None,
                   query=None, rarity=None, sort=None, page_delta=0, reset=False, opening=False,
                   expanded=None, already_deferred=False, _lock_held=False, restore_target=False, notice=''):
        if not await self.permitted(click):
            return
        if not opening and not already_deferred:
            await click.response.defer()
        async with (nullcontext() if _lock_held else self.lock):
            if self.is_finished():
                return await click.followup.send('This roster has closed. Open /collection again.', ephemeral=True)
            self.notice = notice
            if restore_target:
                self.pinned_detail = True
            if expanded is not None:
                self.expanded = expanded
            elif delta or target_id is not None or target_number is not None:
                self.expanded = False
            self.rows = await storage.get_player_beasts(self.user_id)
            self.player = await storage.get_player(self.user_id) or self.player
            self.inventory = {}
            for item in await storage.get_inventory(self.user_id):
                self.inventory[item['item_id']] = self.inventory.get(item['item_id'], 0) + item['quantity']
            if rarity is not None and rarity not in ('all', 'special') + owned_roster.RARITIES:
                return await click.followup.send('Choose a rarity from the current menu.', ephemeral=True)
            if sort is not None and sort not in {'number', 'level', 'rarity'}:
                return await click.followup.send('Choose a sort order from the current menu.', ephemeral=True)
            if reset:
                self.pinned_detail = False
                self.picker = owned_roster.PickerState()
                self.mode = 'overview'
                self.selected_id = None
            if query is not None or rarity is not None or sort is not None:
                self.pinned_detail = False
                if query is not None:
                    self.picker.query = str(query).strip()[:100]
                if rarity is not None:
                    self.picker.rarity = rarity
                if sort is not None:
                    self.picker.sort = sort
                self.picker.page = 0
                self.selected_id = None
                self.mode = 'overview'
            if mode is not None:
                self.mode = mode
            matches = self.matches()
            if target_number is not None:
                # The internal-ID fallback belongs only to legacy, unnumbered rows.
                target = next((row for row in self.rows if row.get('player_number') == target_number
                               or row.get('player_number') is None and row['id'] == target_number), None)
                target_id = target['id'] if target else -1
            if target_id is not None:
                self.pinned_detail = restore_target
                target = next((row for row in (self.rows if restore_target else matches) if row['id'] == target_id), None)
                if target:
                    self.selected_id, self.mode = target['id'], 'detail'
                else:
                    self.selected_id, self.mode = None, 'overview'
                    self.notice = (f'Companion #{target_number} was not found in your collection.' if target_number is not None
                                   else 'That companion is no longer on this roster. Choose another companion below.')
            elif opening and self.mode == 'detail' and matches:
                self.selected_id = next((row['id'] for row in matches if row['is_active']), matches[0]['id'])
            elif self.mode == 'detail' and self.selected_id not in {row['id'] for row in (self.rows if self.pinned_detail else matches)}:
                self.selected_id, self.mode = None, 'overview'
                self.notice = 'That companion is no longer on this roster. Your collection is up to date.'
            if self.mode == 'detail' and self.selected_id is not None:
                index = next((index for index, row in enumerate(matches) if row['id'] == self.selected_id), None)
                if index is not None and (delta or not restore_target):
                    index = max(0, min(len(matches) - 1, index + delta))
                    self.selected_id = matches[index]['id']
                    self.picker.page = index // PAGE_SIZE
            else:
                self.picker.page += page_delta
            embed, row = self.render()
            await self.deliver(click, embed, row, opening=opening)

    async def deliver(self, interaction, embed, row, *, opening):
        previous = {item.filename: item for item in getattr(self.message, 'attachments', [])}
        expected, retained, portrait = '', None, None
        if row and self.art_enabled:
            armor = (await gear_art.equipped_armor_ids(self.user_id)).get(row['id'], '')
            expected = gear_art.portrait_filename(row['beast_id'], armor)
            retained = previous.get(expected)
            if retained is None:
                portrait = await gear_art.render_portrait(row['beast_id'], armor)
        buffer = io.BytesIO(portrait.data) if portrait else None
        file = discord.File(buffer, filename=portrait.filename) if portrait else None
        attachments = [retained] if retained else [file] if file else []
        if attachments:
            embed.set_image(url=f'attachment://{expected}')

        async def send(with_art):
            async with asyncio.timeout(15):
                if opening:
                    return await interaction.followup.send(embed=embed, view=self,
                                                           files=[file] if with_art and file else [], wait=True)
                return await interaction.edit_original_response(content=None, embed=embed, view=self,
                                                                 attachments=attachments if with_art else [])
        try:
            try:
                result = await send(bool(attachments))
            except discord.HTTPException as error:
                if isinstance(error, discord.NotFound) or not attachments:
                    raise
                logger.warning('Roster artwork rejected (%s); showing text controls', error.status)
                self.art_enabled = False
                embed.set_image(url=None)
                result = await send(False)
            self.message = result or self.message
            self.navigator.message = self.message
            if self.message and isinstance(getattr(self.message, 'id', None), int) and interaction.channel:
                self.timeout_message = interaction.channel.get_partial_message(self.message.id)
            else:
                self.timeout_message = self.message
        finally:
            if file:
                file.close()
            if buffer:
                buffer.close()

    async def on_timeout(self):
        async with self.lock:
            if self.navigator.active is not self:
                return
            for child in self.children:
                child.disabled = True
            if self.timeout_message:
                try:
                    async with asyncio.timeout(15):
                        await self.timeout_message.edit(view=self)
                except (discord.HTTPException, TimeoutError):
                    pass

    async def on_error(self, interaction, error, item):
        logger.error('Roster input failed: trainer=%s control=%s', self.user_id,
                     getattr(item, 'custom_id', None), exc_info=error)
        message = 'The roster could not turn that page. Use /collection to open it again.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def open_roster(interaction, *, beast_number=None, details=False, already_deferred=False):
    if not already_deferred:
        await interaction.response.defer()
    await storage.get_or_create_player(interaction.user.id, str(interaction.user))
    view = OwnedRosterView(interaction.user.id, interaction.user.display_name, details=details)
    await view.show(interaction, target_number=beast_number, opening=True)
    return view
