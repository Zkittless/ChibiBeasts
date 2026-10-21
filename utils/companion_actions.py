"""Identity-bound companion actions launched from the shared roster."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import discord

from utils import db as storage, owned_roster
from utils.theme import COLORS


async def current(user_id, target):
    row = next((row for row in await storage.get_player_beasts(user_id) if row['id'] == target['id']), None)
    if not row or row['beast_id'] != target['beast_id']:
        raise ValueError('That companion changed or left your collection. Open its current card again.')
    return row


class RenameModal(discord.ui.Modal, title='Rename your companion'):
    def __init__(self, owner, target, *, navigator=None, source=None):
        super().__init__()
        self.owner, self.target = owner, dict(target)
        self.navigator, self.source = navigator, source
        self.name_input = discord.ui.TextInput(label='Nickname', max_length=20, default=target.get('nickname') or '', required=True)
        self.add_item(self.name_input)

    async def on_submit(self, click):
        if click.user.id != self.owner:
            return await click.response.send_message('Open your own companion card.', ephemeral=True)
        await click.response.defer(ephemeral=not bool(self.navigator))
        name = str(self.name_input).strip()
        async with (self.navigator.lock if self.navigator else nullcontext()):
            if self.navigator:
                if not self.navigator.current(self.source) or self.source.selected_id != self.target['id']:
                    return await click.followup.send('Use the current beast card before renaming.', ephemeral=True)
                self.navigator.remember(self.source)
            try:
                if not 1 <= len(name) <= 20:
                    raise ValueError('Use a nickname from 1 to 20 characters.')
                async with storage.connect() as db:
                    cursor = await db.execute('UPDATE player_beasts SET nickname=? WHERE id=? AND user_id=? AND beast_id=?',
                        (name, self.target['id'], self.owner, self.target['beast_id']))
                    if not cursor.rowcount:
                        raise ValueError('That companion changed or left your collection.')
                    await db.commit()
                result = 'Nickname saved: ' + discord.utils.escape_mentions(discord.utils.escape_markdown(name))
            except ValueError as error:
                result = str(error)
            if self.navigator:
                return await self.navigator.restore(click, notice=result)
            await click.followup.send(result, ephemeral=True)


class GrowthView(discord.ui.View):
    def __init__(self, user_id, target, operation, *, navigator=None):
        super().__init__(timeout=300 if navigator else 180)
        self.user_id, self.target, self.operation = user_id, dict(target), operation
        self.navigator, self.workspace_route, self.message = navigator, operation, None
        self.stat, self.cost, self.done = 'attack', None, False
        self.lock = navigator.lock if navigator else asyncio.Lock()
        self.confirm = discord.ui.Button(label='Confirm', style=discord.ButtonStyle.success, row=2 if navigator else 1)
        self.confirm.callback = self.apply
        if operation == 'train':
            self.stat_picker = discord.ui.Select(placeholder='Choose a stat', row=1 if navigator else 0, options=[
                discord.SelectOption(label=stat.title(), value=stat, default=stat == self.stat) for stat in ('attack', 'defense', 'speed', 'hp')])
            self.stat_picker.callback = self.choose
            self.add_item(self.stat_picker)
        self.add_item(self.confirm)

    async def preview(self):
        row = await current(self.user_id, self.target)
        beast = storage.get_beast_data(row['beast_id'])
        embed = discord.Embed(title=f"{self.operation.title()} · {owned_roster.name(row)}", color=COLORS['info'])
        if self.operation == 'train':
            from cogs.economy import TRAIN_COST, TRAIN_GAIN, TRAIN_HP_MULTIPLIER, TRAIN_DAILY_LIMIT
            from utils.sanctuary import get_user_sanctuary, apply_training_discount
            self.cost = apply_training_discount(TRAIN_COST.get(row['rarity'], 50), await get_user_sanctuary(self.user_id))
            gain = TRAIN_GAIN.get(row['rarity'], 1) * (TRAIN_HP_MULTIPLIER if self.stat == 'hp' else 1)
            self.confirm.label = f'Train {self.stat} · {self.cost:,} gold'
            embed.description = f"**+{gain} {self.stat.upper()} · {self.cost:,} gold**\nExisting stat caps and the {TRAIN_DAILY_LIMIT}-session daily limit apply."
        else:
            from utils.progression import evolution_blockers
            evo = beast.get('evolution') or {}
            if not evo.get('evolves_to'):
                raise ValueError('This companion has no further evolution.')
            inventory = {}
            for entry in await storage.get_inventory(self.user_id):
                inventory[entry['item_id']] = inventory.get(entry['item_id'], 0) + entry['quantity']
            player = await storage.get_player(self.user_id)
            blockers = evolution_blockers(row, player['level'], evo, inventory=inventory, knocked_out=storage.is_knocked_out(row))
            name = storage.get_beast_data(evo['evolves_to'])['name']
            self.confirm.label = 'Evolve into ' + name
            self.confirm.disabled = bool(blockers)
            method = evo.get('method')
            item = storage.load_items().get(method, {})
            embed.description = f"**{beast['name']} → {name}**\n" + ('\n'.join(blockers) if blockers else 'Ready to evolve.')
            cost = 'Consumes 1 ' + item.get('name', str(method))
            if evo.get('form') == 'radiant' and method != 'sunforge_core':
                cost += ', or 1 Sunforge Core if that catalyst is unavailable.'
            embed.add_field(name='Cost', value='Bond awakening · no item required' if evo.get('form') == 'bond' else cost, inline=False)
        quote = (self.stat, self.cost)
        self.confirm.callback = lambda click: self.apply(click, quote)
        if self.navigator:
            self.navigator.decorate(self)
        return embed

    async def choose(self, click):
        if click.user.id != self.user_id or self.is_finished() or (self.navigator and not self.navigator.current(self)):
            return await click.response.send_message('Open your own current companion card.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if self.done or (self.navigator and not self.navigator.current(self)):
                return
            stat = click.data['values'][0]
            if stat not in {'attack', 'defense', 'speed', 'hp'}:
                return
            self.stat = stat
            for option in self.stat_picker.options:
                option.default = option.value == stat
            try:
                await click.edit_original_response(embed=await self.preview(), view=self)
            except ValueError as error:
                if self.navigator:
                    return await self.navigator.show_result(click, str(error))
                await click.edit_original_response(content=str(error), embed=None, view=None)

    async def apply(self, click, quote=None):
        if click.user.id != self.user_id or self.is_finished() or (self.navigator and not self.navigator.current(self)):
            return await click.response.send_message('Open your own current companion card.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if self.done or (self.navigator and not self.navigator.current(self)):
                return await click.followup.send('This action has already finished.', ephemeral=True)
            if quote is not None and quote != (self.stat, self.cost):
                return await click.followup.send('The training selection changed. Review the current price and stat.', ephemeral=True)
            self.done = True
            try:
                row = await current(self.user_id, self.target)
                if self.operation == 'train':
                    from cogs.economy import Economy
                    from utils.duels import reserve_fighters
                    async def reply(*args, **kwargs):
                        if self.navigator:
                            return await self.navigator.show_result(click, args[0] if args else kwargs.get('content', ''),
                                                                    embed=kwargs.get('embed'), repeat='train')
                        return await click.edit_original_response(*args, **kwargs, view=None)
                    panel_reply = SimpleNamespace(user=click.user, channel=click.channel, followup=SimpleNamespace(send=reply))
                    with reserve_fighters([self.user_id]):
                        await Economy(None)._train(panel_reply, owned_roster.number(row), self.stat,
                            expected_row_id=row['id'], expected_species=self.target['beast_id'], expected_cost=self.cost)
                    self.stop()
                    return
                else:
                    from utils.gameplay import evolve_beast
                    before, after, _ = await evolve_beast(self.user_id, owned_roster.number(row),
                        expected_row_id=row['id'], expected_species=self.target['beast_id'])
                    result = f"{before['name']} became {after['name']}!"
                    if click.guild:
                        from utils.progress import record_bestiary_sighting
                        await record_bestiary_sighting(click.guild.id, after['id'], self.user_id)
                    from cogs.hatch import check_collection_completion
                    await check_collection_completion(click, after, storage.load_beasts())
            except ValueError as error:
                result = str(error)
            if self.navigator:
                return await self.navigator.show_result(click, result)
            await click.edit_original_response(content=result, embed=None, view=None)
            self.stop()


async def open_growth(click, target, operation):
    await click.response.defer(ephemeral=True)
    view = GrowthView(click.user.id, target, operation)
    try:
        embed = await view.preview()
    except ValueError as error:
        view.stop()
        return await click.followup.send(str(error), ephemeral=True)
    await click.followup.send(embed=embed, view=view, ephemeral=True)
    return view
