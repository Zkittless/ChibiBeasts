"""Saved exploration fights, sharing resources across sequential pack members."""
from copy import deepcopy
import random

from utils import db as storage, trial_combat
from utils.habitats import BIOMES, encounter_pool


def plan(realm, seed, kind, level, *, ticket=None):
    """A pack divides one encounter's health budget; it is not three rewards."""
    from cogs.battle import build_pve_beast_state
    rng = random.Random(f'{seed}:adventure-fight')
    count = {'single': 1, 'pack2': 2, 'pack3': 3, 'rare': 1}[kind]
    low, high = BIOMES[realm]['level_range']
    level = max(low, min(high, level))
    pool = [b for rarity in ('common', 'uncommon') for b in encounter_pool(rarity, realm)]
    if not pool:
        # High-level habitats may start at Rare. Pack fillers use that lowest
        # inhabited tier, rather than drawing extra Legendary/Divine tickets.
        pool = next((candidates for rarity in ('rare', 'epic', 'legendary', 'divine')
                     if (candidates := encounter_pool(rarity, realm))), [])
    waves = []
    for index in range(count):
        species = storage.get_beast_data(ticket) if ticket and index == 0 else rng.choice(pool)
        state = build_pve_beast_state(species, level)
        health = .8 if count == 1 else .48 if count == 2 else .34
        attack = .8 if count == 1 else .6
        state['hp'] = state['max_hp'] = max(1, round(state['max_hp'] * health))
        for key in ('attack', 'base_attack'):
            if key in state:
                state[key] = max(1, round(state[key] * attack))
        waves.append(dict(index=index + 1, kind='normal', species=species['id'], level=level,
                          enemy_template=trial_combat._json(state), round_limit=16,
                          profile={}, name=species['name']))
    return dict(seed=seed, waves=waves, total=count)


def start(payload, lineup):
    """Keep HP, mana, conditions, entry passives and assist charge between fights."""
    state = payload.get('combat')
    if state is None:
        payload['prepared_party'] = trial_combat._json(payload['prepared_party'])
        state = trial_combat.new_run(payload['prepared_party'], lineup)
        payload['combat'] = state
    else:
        state.update(plan=deepcopy(lineup), enemy=None, status='between', cleared=0,
                     wave_index=0, round=0, turn_order=[], cursor=0, phase='idle',
                     awaiting=False, events=[], reason='')
    payload['fights_started'] = payload.get('fights_started', 0) + 1
    return trial_combat.start_wave(state)


def action(payload, choice):
    return trial_combat.act(payload['combat'], choice)


def continue_pack(payload):
    return trial_combat.start_wave(payload['combat'])


def party(payload):
    return payload['combat']['party'] if payload.get('combat') else payload['prepared_party']


def effects(payload, effect):
    """Temporary stats never reach the collection; travel damage cannot KO."""
    for member in party(payload):
        if member['hp'] <= 0:
            continue
        if effect.get('heal'):
            member['hp'] = min(member['max_hp'], member['hp'] + max(1, member['max_hp'] * effect['heal'] // 100))
        if effect.get('hp_cost'):
            member['hp'] = max(1, member['hp'] - max(1, member['max_hp'] * effect['hp_cost'] // 100))
    # Derive each increase against its saved original, avoiding rounding drift
    # and retaining combat's own temporary changes on top of the journey boon.
    for stat, effect_key in (('attack', 'attack_boon'), ('defense', 'defense_boon')):
        if not effect.get(effect_key):
            continue
        for member in party(payload):
            key = '_journey_' + stat
            base = member.setdefault(key, member[stat])
            old = member.get(key + '_bonus', 0)
            new = min(20, old + effect[effect_key])
            change = round(base * new / 100) - round(base * old / 100)
            member[stat] += change
            if 'base_' + stat in member:
                member['base_' + stat] += change
            member[key + '_bonus'] = new
