"""Saved exploration fights, sharing resources across sequential pack members."""
from copy import deepcopy
import random

from utils import db as storage, trial_combat, trial_rules
from utils.habitats import BIOMES, encounter_pool

PARTY_PROFILES = {1: (.5, .6), 2: (.75, .85), 3: (1., 1.)}
BALANCE_VERSION = 2
# Per-foe HP/attack, selected once from the departure roster. Packs carry a
# larger total health budget; losing a companion never makes later foes weaker.
# The opening Woods solo route retains its existing budgets. Beyond it, total
# HP × attack pressure rises slower than party size, preserving a team's value.
ENCOUNTER_PROFILES = {
    1: {1: (.60, .72), 2: (.36, .54), 3: (.26, .50)},
    2: {1: (.85, .82), 2: (.52, .66), 3: (.38, .60)},
    3: {1: (1.1, .92), 2: (.66, .74), 3: (.48, .68)},
}
SEASONAL_SPECIES = frozenset({'patchkin', 'wickwing', 'veilmoth', 'hollow_king'})
# Additional temporary PVE normalization for high-rarity seasonal visitors in
# low-level parties. Permanent collection stats and ordinary fights are untouched.
CARNIVAL_PROFILES = {1: (.8, .6), 11: (.9, .7), 26: (1., 1.)}
# A fast Fire rare otherwise locks either fresh Nature starter into healing
# until a charged ultimate KOs it. Keep that burst matchup survivable below11.
CARNIVAL_NOVICE_BURST_ATTACK = .5


def plan(realm, seed, kind, level, *, ticket=None, party_size=3, pool=None, level_bounds=None,
         balance_version=BALANCE_VERSION):
    """Freeze encounter strength independently of gear, rewards and later KOs."""
    from cogs.battle import build_pve_beast_state
    rng = random.Random(f'{seed}:adventure-fight')
    if party_size not in PARTY_PROFILES:
        raise ValueError('An adventure party must have one to three companions.')
    hp_scale, attack_scale = PARTY_PROFILES[party_size]
    count = {'single': 1, 'pack2': 2, 'pack3': 3, 'rare': 1}[kind]
    if balance_version not in (1, 2):
        raise ValueError('Unknown adventure balance version.')
    low, high = level_bounds or BIOMES[realm]['level_range']
    level = max(low, min(high, level))
    onboarding = party_size == 1 and realm == 'woods' and level <= 5
    regional = balance_version == 2 and realm != 'woods' and pool is None
    weights = None
    if regional:
        # Hostile packs used to draw only the lowest populated rarity, even in
        # the Loom. Use the region's real distribution, with the same bounded
        # ordinary Divine pool as towers. This never creates capture tickets.
        tiers = []
        for rarity, weight in BIOMES[realm]['weights'].items():
            candidates = [b for b in encounter_pool(rarity, realm)
                          if rarity != 'divine' or b['id'] in trial_rules.ORDINARY_DIVINES]
            if candidates and weight > 0:
                tiers.extend((b, weight / len(candidates)) for b in candidates)
        pool = [b for b, _ in tiers]
        weights = [weight for _, weight in tiers]
    if pool is None:
        pool = [b for rarity in ('common', 'uncommon') for b in encounter_pool(rarity, realm)]
    elif not pool:
        raise ValueError('This encounter has no available opponents.')
    if not pool:
        # Legacy high-level saves used only the lowest inhabited rarity.
        pool = next((candidates for rarity in ('rare', 'epic', 'legendary', 'divine')
                     if (candidates := encounter_pool(rarity, realm))), [])
    waves = []
    for index in range(count):
        if ticket and index == 0:
            species = storage.get_beast_data(ticket)
        elif weights:
            allowed = [i for i, b in enumerate(pool) if not waves or
                       (b['id'] != waves[-1]['species'] and not (
                           trial_rules.categories(b)['control'] and
                           trial_rules.categories(storage.get_beast_data(waves[-1]['species']))['control']))]
            candidates = allowed or list(range(len(pool)))
            species = pool[rng.choices(candidates, weights=[weights[i] for i in candidates])[0]]
        else:
            species = rng.choice(pool)
        state = build_pve_beast_state(species, level)
        health = .8 if count == 1 else .48 if count == 2 else .34
        attack = .8 if count == 1 else .6
        original_hp = state['max_hp']
        if balance_version == 2 and not onboarding:
            health, attack = ENCOUNTER_PROFILES[party_size][count]
            hp_scale = attack_scale = 1.
        state['hp'] = state['max_hp'] = max(1, round(original_hp * health * hp_scale))
        if balance_version == 2 and not onboarding:
            state['shield'] = round(state.get('shield', 0) * state['max_hp'] / original_hp)
        for key in ('attack', 'base_attack'):
            if key in state:
                state[key] = max(1, round(state[key] * attack * attack_scale))
        waves.append(dict(index=index + 1, kind='normal', species=species['id'], level=level,
                          enemy_template=trial_combat._json(state),
                          round_limit=20 if balance_version == 2 and not onboarding else 16,
                          profile={}, name=species['name'], departure_party_size=party_size,
                          tactics_version=2 if balance_version == 2 and not onboarding else 1))
    return dict(seed=seed, waves=waves, total=count, balance_version=balance_version)


def carnival_plan(realm, seed, kind, level, *, ticket=None, party_size=3, guardian=False,
                  balance_version=BALANCE_VERSION):
    """A seasonal visual overlay and guardian, using the ordinary fight budgets."""
    boss = storage.get_beast_data('hollow_king') if guardian else None
    if guardian and not boss:
        raise ValueError('The Carnival guardian is unavailable; your adventure has not advanced.')
    result = plan(realm, seed, 'single' if guardian else kind, level,
                  ticket=None if guardian else ticket, party_size=party_size,
                  pool=[boss] if guardian else None, level_bounds=(1, 50), balance_version=balance_version)
    hp_scale, attack_scale = CARNIVAL_PROFILES[max(key for key in CARNIVAL_PROFILES if level >= key)]
    for wave in result['waves']:
        state = wave['enemy_template']
        if wave['species'] in SEASONAL_SPECIES:
            state['hp'] = state['max_hp'] = max(1, round(state['max_hp'] * hp_scale))
            for key in ('attack', 'base_attack'):
                state[key] = max(1, round(state[key] * attack_scale))
                if wave['species'] == 'wickwing' and level < 11:
                    state[key] = max(1, round(state[key] * CARNIVAL_NOVICE_BURST_ATTACK))
        else:
            state['armor_id'] = 'cosmetic:ghost_costume_2026'
        wave['event_guardian'] = bool(guardian)
    return result


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
