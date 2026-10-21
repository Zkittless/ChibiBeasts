"""One message and one navigation lifetime for an owned beast's actions."""
import asyncio
import copy
import io

import discord

from utils import adventure_party, db as storage, journal_state, journal_team, journal_team_art, owned_roster
from utils.theme import COLORS

ROUTES = {'beast': 'Beast info', 'care': 'Care & items', 'gear': 'Gear',
          'train': 'Train', 'evolve': 'Evolve / awaken', 'team': 'Team'}


class BeastWorkspace:
    def __init__(self, root):
        self.user_id, self.display_name = root.user_id, root.display_name
        self.lock = asyncio.Lock()
        self.active, self.message, self.context = root, None, None
        self.art_enabled = root.art_enabled

    def current(self, view):
        return self.active is view and not view.is_finished()

    def remember(self, root):
        self.context = dict(picker=copy.copy(root.picker), selected_id=root.selected_id,
                            mode=root.mode, expanded=root.expanded)
        self.art_enabled = root.art_enabled
        self.message = root.message

    async def target(self):
        row_id = self.context['selected_id']
        row = next((row for row in await storage.get_player_beasts(self.user_id) if row['id'] == row_id), None)
        if not row:
            raise ValueError('That companion has left your collection. Back to beast will return to your roster.')
        return row

    def decorate(self, view, *, back_row=4):
        for control in getattr(view, '_workspace_controls', []):
            view.remove_item(control)
        route = getattr(view, 'workspace_route', '')
        picker = discord.ui.Select(placeholder='Beast workspace', row=0, options=[
            discord.SelectOption(label=label, value=key, default=key == route) for key, label in ROUTES.items()])
        picker.callback = lambda click: self.navigate(click, view, click.data['values'][0])
        back = discord.ui.Button(label='Back to beast', row=back_row, style=discord.ButtonStyle.primary)
        back.callback = lambda click: self.navigate(click, view, 'beast')
        view._workspace_controls = [picker, back]
        view.add_item(picker)
        view.add_item(back)

    def activate(self, view):
        previous = self.active
        self.active = view
        self.message = view.message or self.message
        if previous is not view:
            if hasattr(previous, 'closed'):
                previous.closed = True
            previous.stop()

    async def navigate(self, click, source, route, target=None):
        if click.user.id != self.user_id:
            return await click.response.send_message('Open your own beast workspace.', ephemeral=True)
        if not self.current(source):
            return await click.response.send_message('Use the current beast workspace controls.', ephemeral=True)
        if route not in ROUTES:
            return await click.response.send_message('Choose an action from this beast workspace.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if not self.current(source):
                return await click.followup.send('Use the current beast workspace controls.', ephemeral=True)
            if getattr(source, 'workspace_route', '') == 'beast':
                if target is not None and source.selected_id != target['id']:
                    return await click.followup.send('The beast selection changed. Use its current card.', ephemeral=True)
                self.remember(source)
            if route == 'beast':
                return await self.restore(click)
            child = None
            try:
                row = await self.target()
                if route == 'care':
                    from utils import care
                    from utils.care_view import CareView
                    child = CareView(self.user_id, await care.snapshot(self.user_id, row['id']), navigator=self)
                elif route == 'gear':
                    from utils.gear_view import GearView
                    child = GearView(self.user_id, navigator=self)
                    await child.reload()
                    child.selected_id = row['id']
                elif route in {'train', 'evolve'}:
                    from utils.companion_actions import GrowthView
                    child = GrowthView(self.user_id, row, route, navigator=self)
                else:
                    child = BeastTeamView(self, row)
                    await child.load()
                child.message = self.message
                if route in {'care', 'gear', 'team'}:
                    await child.publish(click)
                else:
                    await self.publish_embed(click, child, await child.preview())
                self.activate(child)
                return child
            except ValueError as error:
                if child:
                    child.stop()
                return await self.show_result(click, str(error))
            except BaseException:
                if child:
                    child.stop()
                raise

    async def restore(self, click, *, notice=''):
        from utils.roster_view import OwnedRosterView
        root = OwnedRosterView(self.user_id, self.display_name, navigator=self)
        root.picker = copy.copy(self.context['picker'])
        root.selected_id, root.mode = self.context['selected_id'], self.context['mode']
        root.expanded, root.art_enabled = self.context['expanded'], self.art_enabled
        root.message = self.message
        try:
            await root.show(click, target_id=root.selected_id, expanded=root.expanded,
                            already_deferred=True, _lock_held=True, restore_target=True, notice=notice)
        except BaseException:
            root.stop()
            raise
        self.activate(root)
        return root

    async def publish_embed(self, click, view, embed, *, file=None):
        attachments = [file] if file else []
        try:
            async with asyncio.timeout(15):
                message = await click.edit_original_response(content=None, embed=embed, view=view, attachments=attachments,
                                                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as error:
            if not file or isinstance(error, discord.NotFound):
                raise
            embed.set_image(url=None)
            async with asyncio.timeout(15):
                message = await click.edit_original_response(content=None, embed=embed, view=view, attachments=[],
                                                              allowed_mentions=discord.AllowedMentions.none())
        view.message = message or self.message
        self.message = view.message

    async def show_result(self, click, text='', *, embed=None, repeat=None):
        view = BeastResultView(self, repeat=repeat)
        try:
            await self.publish_embed(click, view, embed or discord.Embed(title='Companion update', description=text, color=COLORS['info']))
        except BaseException:
            view.stop()
            raise
        self.activate(view)
        return view


class BeastResultView(discord.ui.View):
    def __init__(self, navigator, *, repeat=None):
        super().__init__(timeout=300)
        self.navigator, self.user_id, self.message = navigator, navigator.user_id, navigator.message
        self.workspace_route = 'result'
        if repeat:
            button = discord.ui.Button(label='Train again', row=2)
            button.callback = lambda click: navigator.navigate(click, self, repeat)
            self.add_item(button)
        navigator.decorate(self)


class BeastTeamView(discord.ui.View):
    """Assign the selected individual without leaving the beast workspace."""
    def __init__(self, navigator, target):
        super().__init__(timeout=300)
        self.navigator, self.user_id, self.target = navigator, navigator.user_id, dict(target)
        self.lock, self.message = navigator.lock, navigator.message
        self.workspace_route, self.team_mode = 'team', 'exploration'
        self.notice, self.generation = '', 0

    async def load(self):
        self.state = await journal_state.snapshot(self.user_id)

    def render(self):
        self.clear_items()
        self.generation += 1
        generation = self.generation
        raid = journal_team.raid_mode(self)
        assigned = journal_team.roster(self)
        target = next((row for row in journal_team.companions(self) if row['id'] == self.target['id']), None)
        embed = discord.Embed(title='Raid team' if raid else 'Exploration team', color=COLORS['info'],
            description='Choose a slot for **' + discord.utils.escape_mentions(discord.utils.escape_markdown(owned_roster.name(target or self.target))) + '**.'
                        + ('\n' + self.notice if self.notice else ''))
        for role, row in assigned:
            value = f"{owned_roster.name(row)} · Lv.{row['level']}\nHP {row['hp']}/{row['max_hp']} · {journal_team.status(row)}" if row else 'Empty'
            embed.add_field(name=journal_team.ROLE_NAMES[role], value=discord.utils.escape_mentions(discord.utils.escape_markdown(value)), inline=False)
        for mode, label in (('exploration', 'Exploration'), ('raid', 'Raids')):
            self.control(label, 'mode:' + mode, generation, row=1, style=discord.ButtonStyle.primary if mode == self.team_mode else discord.ButtonStyle.secondary)
        frozen = journal_team.frozen(self)
        for role, row in assigned:
            self.control('Assign ' + journal_team.ROLE_NAMES[role].lower(), 'assign:' + role, generation, row=2,
                disabled=not target or frozen or not journal_team._eligible(target, role) or bool(row and row['id'] == target['id']))
        membership = next((role for role, row in assigned if row and row['id'] == self.target['id']), None)
        if membership and membership != 'lead':
            self.control('Remove from slot', 'clear:' + membership, generation, row=3, disabled=frozen)
        self.control('Refresh', 'refresh', generation, row=3)
        if frozen:
            embed.set_footer(text='Return home before editing the team saved for your expedition.')
        else:
            embed.set_footer(text='Raid and exploration assignments stay separate.')
        self.navigator.decorate(self)
        return embed

    def control(self, label, action, generation, *, row, disabled=False, style=discord.ButtonStyle.secondary):
        button = discord.ui.Button(label=label, row=row, disabled=disabled, style=style)
        button.callback = lambda click: self.handle(click, action, generation)
        self.add_item(button)

    async def publish(self, click):
        embed = self.render()
        art = await journal_team_art.render_team([row for _, row in journal_team.roster(self)], 'woods') if self.navigator.art_enabled else None
        if not art:
            return await self.navigator.publish_embed(click, self, embed)
        with io.BytesIO(art.data) as buffer:
            file = discord.File(buffer, filename=art.name)
            try:
                embed.set_image(url='attachment://' + art.name)
                await self.navigator.publish_embed(click, self, embed, file=file)
            finally:
                file.close()

    async def handle(self, click, action, generation):
        if click.user.id != self.user_id or not self.navigator.current(self):
            return await click.response.send_message('Use your current beast workspace.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if not self.navigator.current(self) or generation != self.generation:
                return await click.followup.send('Use the refreshed team controls.', ephemeral=True)
            await self.load()
            self.notice = ''
            try:
                if action.startswith('mode:'):
                    mode = action.split(':')[1]
                    if mode not in {'exploration', 'raid'}:
                        raise ValueError('Choose a team mode.')
                    self.team_mode = mode
                elif action.startswith(('assign:', 'clear:')):
                    role = action.split(':')[1]
                    journal_team.select_role(self, role)
                    self.team_candidate_id = self.target['id']
                    if action.startswith('assign:'):
                        role, row_id = journal_team.assignment(self, f"{role}:{self.target['id']}")
                        if role in journal_team.RAID_ROLES:
                            await storage.set_raid_slot(self.user_id, row_id, int(role[-1]))
                        elif role == 'lead':
                            target = next(row for row in journal_team.companions(self) if row['id'] == row_id)
                            await storage.set_active_companion(self.user_id, owned_roster.number(target), expected_row_id=row_id)
                        else:
                            await adventure_party.set_slot(self.user_id, row_id, int(role))
                        self.notice = 'Team assignment saved.'
                    else:
                        slot = journal_team.removal(self, f"{role}:{self.target['id']}")
                        if isinstance(slot, str):
                            await storage.clear_raid_slot(self.user_id, int(slot[-1]))
                        else:
                            await adventure_party.clear_slot(self.user_id, slot)
                        self.notice = 'Slot cleared.'
            except ValueError as error:
                self.notice = str(error)
            await self.load()
            await self.publish(click)
