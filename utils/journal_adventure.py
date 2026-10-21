"""Authored scenes and animated combat inside the existing journal message."""
import hashlib
from types import SimpleNamespace

import discord

from utils import battle_art, expeditions, trial_combat


def render(view, trip):
    p = trip['payload']
    scene = expeditions.presentation(trip)
    view.text(f"### {battle_art.REALMS[trip['realm']]}\n"
              f"{p['completed']}/{p['total']} stops · Progress saved")
    if view.journey_media and view.art_enabled:
        view.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
            'attachment://' + view.journey_media.name, description='Adventure encounter')))
    state = p.get('combat')
    fighting = trip['status'] == 'battle' or view.journey_playing
    if fighting and state:
        enemy = state.get('enemy') or {}
        count = state['plan']['total']
        view.text(f"**{enemy.get('name', 'Wild encounter')}** · HP {max(0, enemy.get('hp', 0))}/{enemy.get('max_hp', 1)}\n"
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
            actions = trial_combat.legal_actions(state)
            control = view.select('journey_action', 'Choose a move, swap or assist')
            control.options = [discord.SelectOption(label=action['label'][:100],
                description=('Ultimate' if action['kind'] == 'ultimate' else 'Uses this turn · ' + action['kind'].title()),
                value=f"{trip['id']}:{trip['version']}:{index}") for index, action in enumerate(actions) if action['enabled']]
            if control.options:
                view.row(control)
        view.row(view.button('Retreat & return', f"journey_return:{trip['id']}:{trip['version']}"))
        return
    view.render_compact_team(trip)
    if p.get('history'):
        view.text(p['history'][-1]['outcome'][:450])
    boons = p.get('boons', {})
    if any(boons.values()):
        view.text('**Until return** · ' + ' · '.join(f"+{value}% {stat}" for stat, value in boons.items() if value))
    view.text('**' + scene['title'] + '**\n' + scene['body'])
    view.path_select.options = [discord.SelectOption(label=choice['label'][:100], description=choice['description'][:100],
        value=f"{trip['id']}:{trip['version']}:{choice['value']}") for choice in scene['choices']]
    if view.path_select.options:
        view.row(view.path_select)


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
