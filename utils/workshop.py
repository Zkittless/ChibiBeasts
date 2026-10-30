"""The original workshop: recipes, materials and confirmed uses in one message."""
import asyncio
from contextlib import ExitStack
import io
import logging
from uuid import uuid4

import discord

from utils import crafting, db as storage, gear_art
from utils.artwork import PROJECT_ROOT
from utils.catalog import load_catalog
from utils.economy_balance import material_sources, recipe_level
from utils.equipment import dormant_label, effect_summary, equip
from utils.gameplay import evolve_beast
from utils.message_transport import durable_message
from utils.sanctuary import apply_craft_discount

logger = logging.getLogger('chibibeasts.workshop')
HEADER = PROJECT_ROOT / 'data' / 'workshop' / 'header.png'
PAGE_SIZE = 25


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


class WorkshopView(discord.ui.LayoutView):
    def __init__(self, user_id, state, *, selected=None, drawer='', notice=''):
        super().__init__(timeout=300)
        self.user_id, self.state = user_id, state
        self.catalog = crafting.recipes()
        self.selected = selected or crafting.recipe_ids('armor')[0]
        self.section = self.item['section']
        self.drawer, self.notice = drawer, notice
        self.using = False
        self.target_id = None
        self.page = 0
        self.confirmation = None
        self.result_species = None
        self.result_row_id = None
        self.message = None
        self.lock = asyncio.Lock()
        self.mutating = False
        self.closed = False
        self.art_enabled = True
        self.last_input = 0
        self.prefix = 'workshop:' + uuid4().hex
        self.container = discord.ui.Container(accent_colour=0xc9b88c)
        self.add_item(self.container)
        self.tabs = discord.ui.ActionRow()
        for key, label in zip(crafting.SECTIONS, ('Armor', 'Runes', 'Evolution')):
            self.tabs.add_item(self.button(label, 'section:' + key))
        self.recipe_select = discord.ui.Select(custom_id=self.prefix + ':recipe', placeholder='Choose a recipe')
        self.recipe_select.callback = lambda interaction: self.handle(interaction, 'recipe')
        self.recipe_row = discord.ui.ActionRow(self.recipe_select)
        self.craft_button = self.button('Craft 1', 'craft', discord.ButtonStyle.success)
        self.use_button = self.button('Equip', 'use', discord.ButtonStyle.primary)
        self.actions = discord.ui.ActionRow(self.craft_button, self.use_button,
            self.button('Materials', 'materials'), self.button('Sources', 'sources'), self.button('Refresh', 'refresh'))
        self.target_select = discord.ui.Select(custom_id=self.prefix + ':target', placeholder='Choose a companion')
        self.target_select.callback = lambda interaction: self.handle(interaction, 'target')
        self.target_row = discord.ui.ActionRow(self.target_select)
        self.confirm_button = self.button('Equip', 'confirm', discord.ButtonStyle.success)
        self.back_button = self.button('Back', 'back')
        self.prev_button = self.button('Previous', 'previous')
        self.page_button = discord.ui.Button(label='1/1', disabled=True, custom_id=self.prefix + ':page')
        self.next_button = self.button('Next', 'next')
        self.confirm_row = discord.ui.ActionRow()
        self.journal_row = discord.ui.ActionRow(self.button('Pin recipe', 'pin_recipe'), self.button('Field Journal', 'journal'))
        self.render()

    @property
    def item(self):
        return self.catalog[self.selected]

    @property
    def costs(self):
        return apply_craft_discount(self.item['recipe'], self.state.sanctuary)

    @property
    def available(self):
        return min((self.state.materials.get(key, 0) // amount for key, amount in self.costs.items()), default=0)

    @property
    def owned(self):
        return self.state.owned.get(self.selected, 0)

    def candidates(self):
        if self.section != 'evolution':
            return self.state.companions
        beasts = storage.load_beasts()
        return [row for row in self.state.companions
                if (beasts[row['beast_id']].get('evolution') or {}).get('method') == self.selected]

    def target(self):
        return next((row for row in self.candidates() if row['id'] == self.target_id), None)

    def appearance(self):
        """Confirmation shows the proposed armor; a result shows saved gear."""
        row = self.target() if self.using else next(
            (row for row in self.state.companions if row['id'] == self.result_row_id), None)
        species = row['beast_id'] if row else self.result_species if not self.using else None
        if species is None:
            return None
        armor_id = self.selected if self.using and self.section == 'armor' else (row or {}).get('appearance_id') or (row or {}).get('armor_id') or ''
        return species, armor_id

    def portrait_url(self):
        return 'attachment://' + gear_art.portrait_filename(*self.appearance())

    def button(self, label, action, style=discord.ButtonStyle.secondary):
        button = discord.ui.Button(label=label, style=style, custom_id=self.prefix + ':' + action)
        button.callback = lambda interaction: self.handle(interaction, action)
        return button

    def remember_target(self):
        row = self.target()
        self.confirmation = None if row is None else (
            self.selected, row['id'], row['beast_id'], row['player_number'],
            row['rune_id'] if self.section == 'runes' else row['armor_id'])

    def target_ready(self, row):
        if not row or row['listed'] or not self.owned:
            return False
        if self.section == 'evolution':
            evo = storage.get_beast_data(row['beast_id'])['evolution']
            return row['level'] >= evo['level_required'] and not storage.is_knocked_out(row)
        if self.state.level < recipe_level(self.item):
            return False
        return (row['rune_id'] if self.section == 'runes' else row['armor_id']) != self.selected

    def target_text(self, row):
        beast = storage.get_beast_data(row['beast_id'])
        label = safe((row['nickname'] or beast['name'])[:80])
        text = f"### #{row['player_number']} · {label} · Lv.{row['level']}\n"
        if self.section == 'evolution':
            evo = beast['evolution']
            after = storage.get_beast_data(evo['evolves_to'])
            text += f"**Next form: {after['name']}**\nRequires companion Lv.{evo['level_required']}. Uses 1 {self.item['name']}.\nEarned stats, bond and equipped gear carry forward."
            if storage.is_knocked_out(row):
                text += '\nLet this companion recover before evolving.'
        else:
            key = row['rune_id'] if self.section == 'runes' else row['armor_id']
            old = self.catalog.get(key)
            text += f"**Current {'rune' if self.section == 'runes' else 'armor'}:** {old['name'] if old else 'empty'}"
            if old:
                status = dormant_label(old, self.state.level)
                text += f"\n{status or effect_summary(old)}\nThe replaced piece returns to your bag."
            text += f"\n**New:** {self.item['name']} · {effect_summary(self.item)}"
            if dormant_label(self.item, self.state.level):
                text += '\n' + dormant_label(self.item, self.state.level) + '; reach that level before equipping.'
        if row['listed']:
            text += '\nDelist this companion before changing it.'
        return text

    def text_parts(self):
        item = self.item
        required = recipe_level(item)
        status = (dormant_label(item, self.state.level) if self.section != 'evolution' and self.owned else f'Unlocks at trainer Lv.{required}') if self.state.level < required else 'Ready to craft' if self.available else 'Missing materials'
        effect = 'Radiant evolution catalyst' if self.section == 'evolution' else effect_summary(item)
        stock = f'{self.owned} unequipped' if self.section == 'armor' else f'{self.owned} in bag'
        details = f"### {item['name']}\n{item['rarity'].replace('_', ' ').title()} · Trainer Lv.{required} · {stock}\n**{effect}**\n{item['description']}\n**{status}**"
        lines = ['**Materials for one** · ' + ('Gnome Forge cost applied' if self.state.sanctuary.get('gnome_forge') else 'Standard recipe')]
        mats = load_catalog('materials.json')['materials']
        missing = []
        for mid, amount in self.costs.items():
            have = self.state.materials.get(mid, 0)
            lines.append(f"{mats[mid]['name']} — **{have:,} owned / {amount:,} needed**")
            if have < amount:
                missing.append(f"{amount - have:,} {mats[mid]['name']}")
        if self.state.level < required:
            lines.append(f'Reach trainer Lv.{required} to craft this recipe.')
        elif missing:
            lines.append('Still need ' + ', '.join(missing) + '.')
        else:
            lines.append(f'Enough materials for {self.available:,}.')
        return details, '\n'.join(lines)

    def render(self):
        self.container.clear_items()
        if self.art_enabled:
            self.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem('attachment://header.png', description='The Workshop in the Whispering Woods, with Twine.')))
        self.container.add_item(discord.ui.TextDisplay(f"Trainer Lv.{self.state.level} · {len(self.catalog)} recipes"))
        for button, section in zip(self.tabs.children, crafting.SECTIONS):
            button.style = discord.ButtonStyle.primary if section == self.section else discord.ButtonStyle.secondary
        self.container.add_item(self.tabs)
        options = []
        for key in crafting.recipe_ids(self.section):
            item = self.catalog[key]
            costs = apply_craft_discount(item['recipe'], self.state.sanctuary)
            unlocked = self.state.level >= recipe_level(item)
            ready = unlocked and all(self.state.materials.get(mid, 0) >= n for mid, n in costs.items())
            prefix = 'Locked · ' if not unlocked else 'Ready · ' if ready else ''
            options.append(discord.SelectOption(label=(prefix + item['name'])[:100], value=key,
                description=f"Trainer Lv.{recipe_level(item)} · {item['rarity'].replace('_', ' ').title()}"[:100], default=key == self.selected))
        self.recipe_select.options = options
        self.container.add_item(self.recipe_row)
        details, materials = self.text_parts()
        self.container.add_item(discord.ui.TextDisplay(details))
        self.container.add_item(discord.ui.TextDisplay(materials))
        self.craft_button.disabled = not self.available or self.state.level < recipe_level(self.item) or self.closed
        self.use_button.label = 'Evolve' if self.section == 'evolution' else 'Equip'
        self.use_button.disabled = not self.owned or self.closed or (self.section != 'evolution' and self.state.level < recipe_level(self.item))
        self.container.add_item(self.actions)
        if self.notice:
            self.container.add_item(discord.ui.TextDisplay(self.notice[:650]))
        if self.drawer:
            mats = load_catalog('materials.json')['materials']
            if self.drawer == 'materials':
                rows = sorted(self.state.materials.items(), key=lambda pair: mats.get(pair[0], {}).get('name', pair[0]))
                body = '\n'.join(f"**{mats.get(mid, {}).get('name', mid)}** · {amount:,}" for mid, amount in rows if amount > 0)
                text = '**Your materials**\n' + (body or 'Your material bag is empty. Exploring supplies crafting materials.')
            else:
                text = '**Where to gather**\n' + '\n'.join(f"**{mats[mid]['name']}**\n{material_sources(mid)}" for mid in self.costs)
            self.container.add_item(discord.ui.TextDisplay(text[:1800]))
        if self.using:
            candidates = self.candidates()
            self.page = max(0, min(self.page, (len(candidates) - 1) // PAGE_SIZE)) if candidates else 0
            visible = candidates[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
            if self.target_id not in {row['id'] for row in visible}:
                self.target_id = visible[0]['id'] if visible else None
                self.remember_target()
            row = self.target()
            if len(candidates) > 1:
                self.target_select.options = [discord.SelectOption(label=f"#{r['player_number']} · {r['nickname'] or storage.get_beast_data(r['beast_id'])['name']} · Lv.{r['level']}"[:100], value=str(r['id']), default=r['id'] == self.target_id) for r in visible]
                self.container.add_item(self.target_row)
            if row:
                text = self.target_text(row)
                if self.art_enabled:
                    self.container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(self.portrait_url(), description=storage.get_beast_data(row['beast_id'])['name'])))
                else:
                    self.container.add_item(discord.ui.TextDisplay(text))
            else:
                self.container.add_item(discord.ui.TextDisplay('No compatible companion in your collection. Your item stays in the bag.'))
            if self.section == 'evolution':
                self.confirm_button.label = 'Confirm evolution'
            elif row and (row['rune_id'] if self.section == 'runes' else row['armor_id']):
                self.confirm_button.label = 'Replace & return old'
            elif row:
                self.confirm_button.label = ('Equip to ' + (row['nickname'] or storage.get_beast_data(row['beast_id'])['name']))[:80]
            else:
                self.confirm_button.label = 'Equip companion'
            self.confirm_button.disabled = not self.target_ready(row) or self.closed
            self.confirm_row.clear_items()
            self.confirm_row.add_item(self.confirm_button).add_item(self.back_button)
            if len(candidates) > PAGE_SIZE:
                pages = (len(candidates) + PAGE_SIZE - 1) // PAGE_SIZE
                self.prev_button.disabled, self.next_button.disabled = self.page == 0, self.page + 1 >= pages
                self.page_button.label = f'{self.page + 1}/{pages}'
                self.confirm_row.add_item(self.prev_button).add_item(self.page_button).add_item(self.next_button)
            self.container.add_item(self.confirm_row)
        elif self.result_species:
            beast = storage.get_beast_data(self.result_species)
            text = f"### {beast['name']}\n{beast['description']}"
            if self.art_enabled:
                self.container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(self.portrait_url(), description=beast['name'])))
            else:
                self.container.add_item(discord.ui.TextDisplay(text))
        if self.closed:
            for child in self.walk_children():
                if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                    child.disabled = True
        self.container.add_item(self.journal_row)
        if self.closed:
            for child in self.journal_row.children:
                child.disabled = True
        if self.content_length() > 4000:
            raise ValueError('Workshop text exceeds Discord’s message limit.')

    async def publish(self, interaction, *, opening=False):
        portrait = None
        appearance = self.appearance()
        previous = {a.filename: a for a in getattr(self.message, 'attachments', [])}
        if self.art_enabled and appearance and gear_art.portrait_filename(*appearance) not in previous:
            portrait = await gear_art.render_portrait(*appearance)
            if portrait is None:
                self.art_enabled = False
                self.render()
        with ExitStack() as stack:
            files, attachments = [], []
            if self.art_enabled:
                paths = [HEADER]
                for path in paths:
                    if path.name in previous:
                        attachments.append(previous[path.name])
                    else:
                        file = discord.File(path)
                        stack.callback(file.close)
                        files.append(file)
                if appearance:
                    filename = gear_art.portrait_filename(*appearance)
                    if filename in previous:
                        attachments.append(previous[filename])
                    else:
                        buffer = io.BytesIO(portrait.data)
                        stack.callback(buffer.close)
                        file = discord.File(buffer, filename=portrait.filename,
                                            description=f'{storage.get_beast_data(appearance[0])["name"]} equipment appearance.')
                        stack.callback(file.close)
                        files.append(file)
            async def deliver(with_art):
                async with asyncio.timeout(15):
                    if opening:
                        return await interaction.followup.send(view=self, files=files if with_art else [],
                            allowed_mentions=discord.AllowedMentions.none(), wait=True)
                    return await interaction.edit_original_response(view=self, attachments=attachments + files if with_art else [],
                        allowed_mentions=discord.AllowedMentions.none())
            try:
                self.message = await deliver(self.art_enabled)
            except discord.HTTPException as error:
                if isinstance(error, discord.NotFound) or not files:
                    raise
                logger.warning('Workshop artwork rejected (%s); using native text controls', error.status)
                self.art_enabled = False
                self.render()
                self.message = await deliver(False)

    async def handle(self, interaction, action):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('Open /craft for your own workshop.', ephemeral=True)
        # Acknowledge before lock waits, database reads, image uploads or events.
        mutation = action in {'craft', 'confirm'}
        duplicate = mutation and (self.mutating or self.lock.locked())
        if mutation and not duplicate:
            self.mutating = True
        try:
            await interaction.response.defer()
            if duplicate:
                return await interaction.followup.send('The workshop is updating. Check the refreshed panel before crafting again.', ephemeral=True)
            async with self.lock:
                if self.closed or self.is_finished():
                    return await interaction.followup.send('This workshop has closed. Open /craft again.', ephemeral=True)
                # Snowflakes preserve input order even if callbacks arrive late.
                input_id = getattr(interaction, 'id', 0)
                if input_id and input_id < self.last_input:
                    return await interaction.followup.send('That input came from an older panel. Use the current controls.', ephemeral=True)
                self.last_input = max(self.last_input, input_id)
                if action == 'journal':
                    from utils.journal import open_journal
                    result = await open_journal(interaction, already_deferred=True, replace_message=True)
                    if result:
                        self.closed = True
                        self.stop()
                    return
                expected = self.confirmation
                self.state = await crafting.workshop_state(self.user_id)
                self.notice = ''
                self.result_species = None
                self.result_row_id = None
                try:
                    result = None
                    if action.startswith('section:'):
                        section = action.split(':')[1]
                        self.selected = crafting.recipe_ids(section)[0]
                        self.section = section
                        self.using, self.drawer = False, ''
                    elif action == 'recipe':
                        key = interaction.data['values'][0]
                        if key not in crafting.recipe_ids(self.section):
                            raise ValueError('Choose a recipe from the current category.')
                        self.selected, self.using, self.drawer = key, False, ''
                    elif action == 'craft':
                        result = await crafting.craft_item(self.user_id, self.selected)
                        self.notice = f"Crafted 1 **{result.item['name']}**. Ready to {'evolve' if self.section == 'evolution' else 'equip'}."
                        self.using = False
                    elif action in {'materials', 'sources'}:
                        self.drawer = '' if self.drawer == action else action
                        self.using = False
                    elif action == 'use':
                        if not self.owned:
                            raise ValueError('Craft or own one copy before using it.')
                        self.using, self.drawer, self.page = True, '', 0
                        self.target_id = None
                    elif action == 'back':
                        self.using = False
                    elif action in {'previous', 'next'}:
                        self.page += 1 if action == 'next' else -1
                        self.target_id = None
                    elif action == 'target':
                        target_id = int(interaction.data['values'][0])
                        if target_id not in {row['id'] for row in self.candidates()[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]}:
                            raise ValueError('Choose a companion from the current page.')
                        self.target_id = target_id
                        self.remember_target()
                    elif action == 'confirm':
                        if not self.using or not expected or expected[0] != self.selected:
                            raise ValueError('Choose a companion and review it before confirming.')
                        _, row_id, species, number, previous = expected
                        if self.section == 'evolution':
                            before, after, _ = await evolve_beast(self.user_id, number, expected_row_id=row_id,
                                expected_species=species, required_item=self.selected)
                            self.notice = f"**{before['name']} became {after['name']}!**"
                            self.result_species = after['id']
                            self.result_row_id = row_id
                            if interaction.guild:
                                from utils.progress import record_bestiary_sighting
                                await record_bestiary_sighting(interaction.guild.id, after['id'], self.user_id)
                            from cogs.hatch import check_collection_completion
                            await check_collection_completion(interaction, after, storage.load_beasts())
                        else:
                            companion, gear = await equip(self.user_id, number, self.selected, replace=True,
                                expected_row_id=row_id, expected_species=species, expected_equipment=previous)
                            self.notice = f"**{gear['name']}** equipped to {safe(companion['nickname'] or storage.get_beast_data(species)['name'])}."
                            self.result_species = species
                            self.result_row_id = companion['id']
                            if previous:
                                self.notice += ' Previous gear returned to your bag.'
                        self.using = False
                    elif action == 'refresh':
                        self.notice = 'Workshop refreshed.'
                    elif action == 'pin_recipe':
                        from utils.journal_state import pin_goal
                        await pin_goal(self.user_id, 'recipe', self.selected)
                        self.notice = f"**{self.item['name']}** pinned in your Field Journal. Its material progress will follow you between visits."
                    self.state = await crafting.workshop_state(self.user_id)
                    self.remember_target()
                except ValueError as error:
                    self.notice = safe(error)
                    self.state = await crafting.workshop_state(self.user_id)
                    self.remember_target()
                self.render()
                await self.publish(interaction)
                if result and result.quests and interaction.channel:
                    from utils.progress import notify_quest_completions
                    await notify_quest_completions(interaction.channel, result.quests)
        except discord.NotFound:
            self.closed = True
            self.stop()
            await interaction.followup.send('This workshop message was removed. Open /craft again.', ephemeral=True)
        finally:
            if mutation and not duplicate:
                self.mutating = False

    async def on_timeout(self):
        self.closed = True
        async with self.lock:
            self.render()
            if self.message:
                try:
                    async with asyncio.timeout(15):
                        await durable_message(self.message).edit(view=self)
                except (discord.HTTPException, TimeoutError):
                    pass

    async def on_error(self, interaction, error, item):
        logger.error('Workshop input failed', exc_info=error)
        text = 'The workshop could not refresh. Check /craft before trying again; completed crafts remain in your bag.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass


async def open_workshop(interaction, *, item_name=None, drawer='', recipe_id=None, already_deferred=False, replace_message=False):
    """/craft opens the panel; the existing named quick-craft stays compatible."""
    if not already_deferred:
        await interaction.response.defer()
    selected, notice, result = recipe_id if recipe_id in crafting.recipes() else None, '', None
    if item_name:
        selected = crafting.resolve_recipe(item_name)
        if selected is None:
            notice = f'{safe(item_name)} is not a craftable item. Choose a recipe below.'
        else:
            try:
                result = await crafting.craft_item(interaction.user.id, selected)
                notice = f"Crafted 1 **{result.item['name']}**. Ready to {'evolve' if result.item['section'] == 'evolution' else 'equip'}."
            except ValueError as error:
                notice = safe(error)
    try:
        state = await crafting.workshop_state(interaction.user.id)
    except ValueError as error:
        return await interaction.followup.send(str(error), ephemeral=True)
    view = WorkshopView(interaction.user.id, state, selected=selected, drawer=drawer, notice=notice)
    if replace_message:
        view.message = interaction.message
    try:
        await view.publish(interaction, opening=not replace_message)
    except BaseException:
        view.stop()
        raise
    if result and result.quests and interaction.channel:
        from utils.progress import notify_quest_completions
        await notify_quest_completions(interaction.channel, result.quests)
    return view
