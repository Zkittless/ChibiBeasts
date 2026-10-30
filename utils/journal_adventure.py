"""Authored scenes and animated combat inside the existing journal message."""
import hashlib
import re
from types import SimpleNamespace

import discord

from utils import battle_art, combat, expeditions, trial_combat
from utils.team_combat import ASSIST_MANA, assist_effect

LEAD_LIMIT = 200


def scene_lead(body):
    """One or two short sentences; long opening sentences stop at a word."""
    body = ' '.join(str(body or '').split())
    sentences = re.split(r'(?<=[.!?])\s+', body)
    chosen = []
    for sentence in sentences[:2]:
        candidate = ' '.join(chosen + [sentence])
        if len(candidate) > LEAD_LIMIT:
            break
        chosen.append(sentence)
    if chosen:
        return ' '.join(chosen)
    if len(body) <= LEAD_LIMIT:
        return body
    clipped = body[:LEAD_LIMIT - 1]
    if ' ' in clipped:
        clipped = clipped.rsplit(' ', 1)[0]
    return clipped.rstrip(' ,;:-') + '…'


def render_choices(view, trip, choices):
    """Keep each consequence beside its action; returning stays in the footer."""
    # Retain the old values for older journal routers, without displaying a menu.
    view.path_select.options = [discord.SelectOption(label=choice['label'][:100],
        description=choice['description'][:100], value=f"{trip['id']}:{trip['version']}:{choice['value']}")
        for choice in choices]
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
    """The familiar move row, ultimate, and a swap/assist pair per reserve."""
    active = state['party'][state['slot']]
    moves, ultimate, reserves = [], [], []
    for index, action in enumerate(trial_combat.legal_actions(state)):
        kind, label = action['kind'], action['label']
        if kind == 'move':
            cost = combat.move_spec(active, action['choice'][0]).get('mana_cost', 0)
            label += f' · {cost} MP'
        elif kind == 'ultimate':
            label = f"⚡ {label} · {active.get('ultimate_charges', 0)}/3 · 50 MP"
        elif kind == 'assist':
            member = next(member for member in state['party'] if member['id'] == action['choice'][1])
            label = f"{assist_effect(member).title()}: {member['name']} · {ASSIST_MANA} MP"
        style = (discord.ButtonStyle.primary if kind == 'move' else
                 discord.ButtonStyle.danger if kind == 'ultimate' else discord.ButtonStyle.secondary)
        button = view.button(label, f"journey_act:{trip['id']}:{trip['version']}:{index}", style,
                             disabled=not action['enabled'])
        (moves if kind == 'move' else ultimate if kind == 'ultimate' else reserves).append(button)
    if moves:
        view.row(*moves)
    if ultimate:
        view.row(*ultimate)
    for index in range(0, len(reserves), 2):
        view.row(*reserves[index:index + 2])


def render(view, trip):
    p = trip['payload']
    scene = expeditions.presentation(trip)
    target = p.get('battle_target')
    finished = p.get('battles_completed', p.get('fights_won', 0))
    progress = f"Battles won {finished}/{target}" if target else f"{p['completed']}/{p['total']} stops"
    view.text(f"### {battle_art.REALMS[trip['realm']]}\n{progress} · Progress saved")
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
        view.text(scene_lead(previous['outcome']))
    view.text('**' + scene['title'] + '**\n' + scene_lead(scene.get('lead') or scene['body']))
    render_choices(view, trip, scene['choices'])


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
    if view.journey_presentation is None or view.journey_presentation.realm != trip['realm']:
        view.journey_presentation = battle_art.BattlePresentation(trip['realm'])
    presentation = view.journey_presentation
    view.journey_playing = animate
    try:
        for event in state.get('events', ()) if animate else ():
            if not await fresh(view, trip):
                return
            before, action, after = trial_combat.decode_event(event)
            art = await presentation.art(before, after, action)
            if not await fresh(view, trip):
                return
            view.journey_media = artifact(art) if art else None
            view.render()
            await view.publish(interaction)
            if art:
                await presentation.sleep(art.seconds + .2)
    finally:
        view.journey_playing = False
    if not await fresh(view, trip):
        return
    if trip['status'] == 'battle':
        fighters = trial_combat.snapshot(state)
        art = await presentation.art(fighters, fighters)
        if not await fresh(view, trip):
            return
        view.journey_media = artifact(art) if art else None
    else:
        view.journey_media = None
    view.render()
    await view.publish(interaction)


async def fresh(view, trip):
    if view.closed or view.is_finished() or view.message_repurposed:
        return False
    saved = await expeditions.read(view.user_id, trip['id'])
    return bool(saved and saved['version'] == trip['version'])


async def prepare_still(view):
    trip = view.trip
    if view.page != 'adventure' or not trip or trip['status'] != 'battle' or trip['payload'].get('adventure_version') != 3:
        return
    view.journey_presentation = battle_art.BattlePresentation(trip['realm'])
    fighters = trial_combat.snapshot(trip['payload']['combat'])
    art = await view.journey_presentation.art(fighters, fighters)
    view.journey_media = artifact(art) if art else None
