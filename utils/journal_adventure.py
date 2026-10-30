"""Authored scenes and animated combat inside the existing journal message."""
import asyncio
import hashlib
from types import SimpleNamespace

import discord

from utils import battle_art, combat, expeditions, trial_combat
from utils.team_combat import ASSIST_MANA, assist_effect

TEAM_MENU_READ_TIMEOUT = 1.5

def render_choices(view, trip, choices, *, dropdown=True):
    """Story decisions use a menu, with complete wrapping choice text above it."""
    view.path_select.options = [discord.SelectOption(label=choice['label'],
        description=(choice['description'] if len(choice['description']) <= 100 else 'Full effect shown above.'),
        value=f"{trip['id']}:{trip['version']}:{choice['value']}")
        for choice in choices]
    if dropdown:
        if choices:
            view.text('\n'.join(f"**{choice['label']}** · {choice['description']}" for choice in choices))
            view.row(view.path_select)
        return
    footer = []
    for index, choice in enumerate(choices):
        returning = choice['value'] in {'return', 'return_early'}
        style = (discord.ButtonStyle.success if choice['value'] == 'return' else
                 discord.ButtonStyle.secondary if returning else discord.ButtonStyle.primary)
        button = view.button(choice['label'], f"path:{trip['id']}:{trip['version']}:{index}", style)
        if returning:
            footer.append(button)
        else:
            view.container.add_item(discord.ui.Section(discord.ui.TextDisplay(choice['description']), accessory=button))
    if footer:
        view.row(*footer)


def render_combat_buttons(view, trip, state):
    """Keep attacks visible; reserve actions share one modal launcher."""
    active = state['party'][state['slot']]
    moves, ultimate, reserves = [], [], []
    for index, action in enumerate(trial_combat.legal_actions(state)):
        kind, label = action['kind'], action['label']
        if kind == 'move':
            cost = combat.move_spec(active, action['choice'][0]).get('mana_cost', 0)
            label += f' · {cost} MP'
        elif kind == 'ultimate':
            label = f"⚡ {label} · {active.get('ultimate_charges', 0)}/3 · 50 MP"
        elif kind in {'swap', 'assist'}:
            reserves.append(action)
            continue
        style = (discord.ButtonStyle.primary if kind == 'move' else
                 discord.ButtonStyle.danger if kind == 'ultimate' else discord.ButtonStyle.secondary)
        button = view.button(label, f"journey_act:{trip['id']}:{trip['version']}:{index}", style,
                             disabled=not action['enabled'])
        (moves if kind == 'move' else ultimate if kind == 'ultimate' else reserves).append(button)
    if moves:
        view.row(*moves)
    if ultimate:
        view.row(*ultimate)
    if len(state['party']) > 1:
        view.row(view.button('Swap / Assist', f"journey_team:{trip['id']}:{trip['version']}",
                             disabled=not any(action['enabled'] for action in reserves)))


class JourneyTeamMenu(discord.ui.Modal, title='Swap or assist'):
    def __init__(self, view, trip):
        super().__init__(timeout=120)
        self.journal_view, self.trip_id, self.version = view, trip['id'], trip['version']
        state = trip['payload']['combat']
        self.indices, options = set(), []
        for index, action in enumerate(trial_combat.legal_actions(state)):
            if action['kind'] not in {'swap', 'assist'} or not action['enabled']:
                continue
            member = next(member for member in state['party'] if member['id'] == action['choice'][1])
            label = (f"Swap in {member['name']}" if action['kind'] == 'swap' else
                     f"{assist_effect(member).title()} assist: {member['name']}")
            resources = f"HP {member['hp']}/{member['max_hp']} · MP {member['mana']}/{member['max_mana']}"
            cost = 'Uses your turn.' if action['kind'] == 'swap' else f'Uses your turn and {ASSIST_MANA} MP.'
            options.append(discord.SelectOption(label=label, description=resources + ' · ' + cost, value=str(index)))
            self.indices.add(str(index))
        self.action = discord.ui.Select(placeholder='Choose a teammate action', options=options)
        self.add_item(discord.ui.Label(text='Choose a teammate action', component=self.action,
            description=f"Swapping or assisting uses your turn. Assist meter {state['meter']}/3; assists cost {ASSIST_MANA} MP."))

    async def on_submit(self, interaction):
        if self.journal_view.page != 'adventure' or self.journal_view.message_repurposed:
            return await interaction.response.send_message('Return to the current adventure controls before choosing a team action.', ephemeral=True)
        values = self.action.values
        if len(values) != 1 or values[0] not in self.indices:
            return await interaction.response.send_message('Choose an available team action.', ephemeral=True)
        await self.journal_view.handle(interaction, f'journey_act:{self.trip_id}:{self.version}:{values[0]}')


async def open_team_menu(view, interaction, trip_id, version):
    """Opening or cancelling the modal never consumes a turn or changes RNG."""
    if (view.page != 'adventure' or view.closed or view.is_finished() or view.message_repurposed or view.journey_playing
            or view.lock.locked() or view.mutating):
        return await interaction.response.send_message('Use the refreshed adventure controls.', ephemeral=True)
    async with view.lock:
        try:
            # Modals must be the initial response, so they cannot be deferred.
            # Leave time to acknowledge a slow read with a usable retry notice.
            async with asyncio.timeout(TEAM_MENU_READ_TIMEOUT):
                trip = await expeditions.read(view.user_id, trip_id)
        except TimeoutError:
            return await interaction.response.send_message('Your adventure is syncing. Try this team menu again in a moment.', ephemeral=True)
        if (not trip or trip['version'] != version or trip['status'] != 'battle'
                or trip['payload']['combat']['status'] != 'fighting'):
            return await interaction.response.send_message('Use the refreshed adventure controls.', ephemeral=True)
        available = [action for action in trial_combat.legal_actions(trip['payload']['combat'])
                     if action['kind'] in {'swap', 'assist'} and action['enabled']]
        if not available:
            return await interaction.response.send_message('No reserve can act right now.', ephemeral=True)
        await interaction.response.send_modal(JourneyTeamMenu(view, trip))


def render(view, trip):
    p = trip['payload']
    scene = expeditions.presentation(trip)
    target = p.get('battle_target')
    finished = p.get('battles_completed', p.get('fights_won', 0))
    progress = f"Battles won {finished}/{target}" if target else f"{p['completed']}/{p['total']} stops"
    view.text(f"### {battle_art.REALMS[p.get('visual_realm') or trip['realm']]}\n{progress} · Progress saved")
    if view.journey_media and view.art_enabled:
        view.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
            'attachment://' + view.journey_media.name, description='Adventure encounter')))
    state = p.get('combat')
    fighting = trip['status'] == 'battle' or view.journey_playing
    if fighting and state:
        enemy = state.get('enemy') or {}
        count = state['plan']['total']
        recruitable = p.get('capture_fight', False) and state.get('wave_index', 0) == 0
        recruitment = ('Wild encounter · Can join your team after victory' if recruitable else
                       'Hostile encounter · Cannot be recruited')
        view.text(f"**{enemy.get('name', 'Wild encounter')}** · HP {max(0, enemy.get('hp', 0))}/{enemy.get('max_hp', 1)}\n"
                  + recruitment + '\n'
                  + (f"Pack {min(state['cleared'] + 1, count)}/{count} · " if count > 1 else '') + f"Assist {state['meter']}/3")
        view.text('\n'.join(f"**{member['name']}** · HP {max(0, member['hp'])}/{member['max_hp']} · MP {max(0, member['mana'])}/{member['max_mana']}"
                             for member in state['party']))
        if view.journey_playing:
            view.text('Playing action…')
            return
        if state['status'] == 'between':
            view.text('Another pack member approaches. Your team keeps its current resources.')
            view.row(view.button('Next opponent', f"journey_next:{trip['id']}:{trip['version']}", discord.ButtonStyle.primary))
        else:
            render_combat_buttons(view, trip, state)
        view.row(view.button('Retreat & return', f"journey_return:{trip['id']}:{trip['version']}"))
        return
    view.render_compact_team(trip)
    boons = p.get('boons', {})
    if any(boons.values()):
        view.text('**Until return** · ' + ' · '.join(f"+{value}% {stat}" for stat, value in boons.items() if value))
    # A resolved choice flows into the next encounter, without another click or
    # repeating the accumulated log on every page.
    previous = p['history'][-1] if p.get('history') else {}
    if scene['kind'] == 'battle' and (previous.get('root_id') or previous.get('phase') == 'material'):
        view.text(previous['outcome'])
    view.text('**' + scene['title'] + '**\n' + scene['body'])
    render_choices(view, trip, scene['choices'], dropdown=scene['kind'] not in {'battle', 'homeward'})


def artifact(art):
    name = 'adventure-' + hashlib.sha256(art.data).hexdigest()[:14] + '.' + art.extension
    return SimpleNamespace(name=name, data=art.data)


async def present(view, interaction, *, animate=False):
    """Choices commit first; a failed upload never replays damage or rewards."""
    trip = view.trip
    if trip and not await fresh(view, trip):
        return
    state = trip['payload'].get('combat') if trip else None
    if not state or not (animate or trip['status'] == 'battle'):
        view.journey_media = None
        view.render()
        return await view.publish(interaction)
    realm = trip['payload'].get('visual_realm') or trip['realm']
    if view.journey_presentation is None or view.journey_presentation.realm != realm:
        view.journey_presentation = battle_art.BattlePresentation(realm)
    presentation = view.journey_presentation
    view.journey_playing = animate
    preparing, changed = False, False
    try:
        if animate and view.journey_media is None:
            # Show the committed encounter and its preparing state before
            # waiting for any renderer/upload. Controls stay disabled through
            # playback, and a failed render cannot replay a paid adventure.
            artwork = view.art_enabled
            view.art_enabled = False
            try:
                view.render()
                await view.publish(interaction)
                preparing = True
            finally:
                view.art_enabled = artwork
        for event in state.get('events', ()) if animate else ():
            if not await fresh(view, trip):
                changed = True
                return
            before, action, after = trial_combat.decode_event(event)
            art = await presentation.art(before, after, action)
            if not await fresh(view, trip):
                changed = True
                return
            view.journey_media = artifact(art) if art else None
            view.render()
            await view.publish(interaction)
            if art:
                await presentation.sleep(art.seconds + .2)
    finally:
        view.journey_playing = False
        if preparing and changed:
            await restore_saved_controls(view, interaction)
    if not await fresh(view, trip):
        if preparing:
            await restore_saved_controls(view, interaction)
        return
    if trip['status'] == 'battle':
        fighters = trial_combat.snapshot(state)
        art = await presentation.art(fighters, fighters)
        if not await fresh(view, trip):
            if preparing:
                await restore_saved_controls(view, interaction)
            return
        view.journey_media = artifact(art) if art else None
    else:
        view.journey_media = None
    view.render()
    await view.publish(interaction)


async def restore_saved_controls(view, interaction):
    """Another journal advanced this trip while its first artwork prepared."""
    if view.closed or view.is_finished() or view.message_repurposed:
        return
    from utils import journal_state
    view.state = await journal_state.snapshot(view.user_id)
    view.trip = view.state.trip or await expeditions.latest(view.user_id)
    view.journey_media = None
    artwork = view.art_enabled
    view.art_enabled = False
    try:
        view.render()
        await view.publish(interaction)
    finally:
        view.art_enabled = artwork


async def fresh(view, trip):
    if view.closed or view.is_finished() or view.message_repurposed:
        return False
    saved = await expeditions.read(view.user_id, trip['id'])
    return bool(saved and saved['version'] == trip['version'])


async def prepare_still(view):
    trip = view.trip
    if view.page != 'adventure' or not trip or trip['status'] != 'battle' or trip['payload'].get('adventure_version') != 3:
        return
    view.journey_presentation = battle_art.BattlePresentation(trip['payload'].get('visual_realm') or trip['realm'])
    fighters = trial_combat.snapshot(trip['payload']['combat'])
    art = await view.journey_presentation.art(fighters, fighters)
    view.journey_media = artifact(art) if art else None
