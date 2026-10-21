"""Seeded, JSON-safe tower plans and bounded whole-run reward policy."""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import math
import random

from utils import combat
from utils.catalog import load_catalog, catalog_revision
from utils.habitats import BIOMES, eligible
from utils.progression import companion_growth

VERSION = 1
WAVE_COUNTS = dict(zip(BIOMES, (5, 6, 7, 8, 9, 10, 11, 12, 13, 15)))
GUARDIANS = dict(zip(BIOMES, (
    ('slime', 'goblin'), ('will_o_wisp', 'kelpie'),
    ('hellhound', 'wyvern'), ('basilisk', 'cerberus'),
    ('glacius', 'yeti'), ('thunderbird', 'yeti'),
    ('hydra', 'kraken'), ('leviathan', 'dragon'),
    ('astraea', 'terminus'), ('zodiac', 'chronos'),
)))
MINIBOSSES = {
    'woods': (), 'woods_deep': (), 'ember': (),
    'ember_deep': ('wyvern', 'basilisk', 'cerberus'),
    'glacial': ('glacius', 'pegasus', 'griffin', 'rukh'),
    'glacial_deep': ('thunderbird', 'yeti'),
    'trenches': ('abyssal_eel', 'wendigo'),
    'trenches_deep': ('hydra', 'kraken'),
    'loom': ('sphinx', 'kitsune_9'),
    'loom_deep': ('astraea', 'horizon', 'nirvana'),
}
# Ordinary waves avoid resurrection, uncapped absorption, heavy passive damage,
# ramping critical hits and bonus-action chains. Their actual species/passives
# remain intact; this is pool curation, not an invisible passive suppression.
ORDINARY_DIVINES = frozenset({'singularity', 'astraea', 'horizon', 'nirvana', 'nebula', 'karma'})
PROFILES = {
    'normal': dict(hp=.45, attack=.65, defense=.85, round_limit=6),
    'miniboss': dict(hp=.80, attack=.85, defense=1., round_limit=10),
    'guardian': dict(hp=1., attack=1., defense=1., round_limit=16),
}


@lru_cache(maxsize=8192)
def _state_template(species, level, hp_ratio, attack_ratio, defense_ratio, revision):
    catalog = load_catalog('beasts.json')['beasts']
    beast = catalog[species]
    growth = companion_growth(species, level, catalog)
    stats = {k: v + growth[k] for k, v in beast['base_stats'].items()}
    state = combat.build_state(beast, {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'],
                                      'level': level, 'happiness': 70}, buffs={'ultimate_charges': 0})
    original_hp = state['max_hp']
    state['max_hp'] = state['hp'] = max(1, int(original_hp * hp_ratio))
    state['shield'] = int(state.get('shield', 0) * state['max_hp'] / original_hp)
    for stat, ratio in (('attack', attack_ratio), ('defense', defense_ratio)):
        state[stat] = max(1, int(state[stat] * ratio))
        state['base_' + stat] = state[stat]
    return state


def profiled_state(species, level, profile):
    return deepcopy(_state_template(species, level, profile['hp'], profile['attack'],
                                    profile['defense'], catalog_revision()))


def power(state):
    return state['max_hp'] + 3 * state['attack'] + 2 * state['defense'] + state['speed'] + .25 * state['max_mana']


def cap_power(state, ceiling):
    """Cap profile power without changing type, moves, speed or passives."""
    if power(state) <= ceiling:
        return 1.
    fixed = state['speed'] + .25 * state['max_mana']
    scalable = state['max_hp'] + 3 * state['attack'] + 2 * state['defense']
    ratio = max(.01, min(1., (ceiling - fixed) / scalable))
    original_hp = state['max_hp']
    state['max_hp'] = state['hp'] = max(1, int(original_hp * ratio))
    state['shield'] = int(state.get('shield', 0) * state['max_hp'] / original_hp)
    for stat in ('attack', 'defense'):
        state[stat] = state['base_' + stat] = max(1, int(state[stat] * ratio))
    return ratio


def reward_multiplier(total):
    return 1.5 if total <= 7 else 2. if total <= 11 else 3.


def cooldown_seconds(total):
    return 1800 if total <= 7 else 2400 if total <= 11 else 3600


def config(destination):
    if destination not in BIOMES:
        raise ValueError('Choose a known trial biome.')
    region, total = BIOMES[destination], WAVE_COUNTS[destination]
    return dict(destination=destination, total=total, waves=total,
                min_level=region['min_level'], level_range=list(region['level_range']),
                cooldown_seconds=cooldown_seconds(total), multiplier=reward_multiplier(total),
                camp_wave=(total + 1) // 2, version=VERSION)


def progress_fraction(cleared, total):
    if total < 3 or not 0 <= cleared <= total:
        raise ValueError('Invalid trial progress.')
    if cleared == total:
        return 1.
    midpoint = (total + 1) // 2
    return .30 * cleared / midpoint if cleared <= midpoint else .30 + .20 * (cleared - midpoint) / (total - 1 - midpoint)


def categories(beast):
    effects = beast.get('move_effects', {}).values()
    return {'heal': any(s.get('heal_percent', 0) for s in effects),
            'control': any(s.get('status') in {'freeze', 'sleep'} for s in effects)}


def hint(beast):
    effects = beast.get('move_effects', {})
    condition = next((s['status'] for s in effects.values() if s.get('status')), '')
    if condition in {'freeze', 'sleep'}:
        return f'{condition.title()} can interrupt an action. Enter healthy; guard reduces damage, not the condition.'
    if condition == 'blind':
        return 'Blind can spoil attacks. Recover or guard while accuracy is impaired.'
    if condition in {'burn', 'poison', 'blight'}:
        return f'{condition.title()} outlasts a hit. Cleanse when available and watch remaining health.'
    if condition in {'slow', 'paralyze'}:
        return 'Speed can fall. Do not rely on acting before this opponent.'
    if categories(beast)['heal']:
        return 'This opponent can heal once. Keep enough mana for sustained pressure.'
    if (beast.get('divine_passive') or {}).get('passive_id') == 'constellation_charge':
        return 'Attack and speed grow toward a fixed cap. Maintain pressure rather than stalling.'
    return 'Guard prepares a stronger strike. Recover resources, then protect against its follow-up.'


def generate_plan(destination, seed):
    """Resolve the complete lineup once; callers persist this, not only its seed.

    Weighted species order is sampled without replacement at each backtracking
    step. Search is finite; an invalid catalog fails before an attempt is spent.
    """
    cfg, rng = config(destination), random.Random(seed)
    region = BIOMES[destination]
    catalog = load_catalog('beasts.json')['beasts']
    pool = [b for b in catalog.values() if eligible(b) and b['type'] in region['types']
            and region['weights'].get(b['rarity'], 0) > 0]
    normal = [b for b in pool if b['rarity'] != 'divine' or b['id'] in ORDINARY_DIVINES]
    finals = [catalog[k] for k in GUARDIANS[destination] if catalog[k] in pool]
    if not normal or not finals:
        raise ValueError('This biome has no reviewed trial opponents.')
    guardian = rng.choice(finals)
    count, midpoint = cfg['total'], cfg['camp_wave']
    minibosses = [catalog[k] for k in MINIBOSSES[destination] if catalog[k] in pool and k != guardian['id']]
    tier_counts = Counter(b['rarity'] for b in normal)
    flags = {b['id']: categories(b) for b in pool}
    counts, selected, nodes = Counter(), [], 0

    def valid(beast, index):
        identity = beast['id']
        if counts[identity] >= 2 or selected and selected[-1]['id'] == identity:
            return False
        # Reserve one occurrence and control-budget slot for the saved guardian;
        # otherwise a plausible prefix can force an enormous dead-end search.
        if index < count - 1 and identity == guardian['id'] and counts[identity] >= 1:
            return False
        previous = flags[selected[-1]['id']] if selected else {}
        if any(previous.get(k) and flags[identity][k] for k in ('heal', 'control')):
            return False
        controls = sum(flags[b['id']]['control'] for b in selected)
        reserved = flags[guardian['id']]['control'] if index < count - 1 else 0
        if controls + flags[identity]['control'] + reserved > math.ceil(count / 4):
            return False
        if index == midpoint - 1 and count >= 8 and identity == guardian['id']:
            return False
        if index == count - 2 and (identity == guardian['id'] or
                any(flags[identity][k] and flags[guardian['id']][k] for k in ('heal', 'control'))):
            return False
        return True

    def choose(index):
        nonlocal nodes
        nodes += 1
        if nodes > 30000:
            return False
        if index == count:
            return True
        candidates = [guardian] if index == count - 1 else minibosses if count >= 8 and index == midpoint - 1 else normal
        ranked = sorted(candidates, key=lambda b: rng.random() ** (1 / max(.0001,
            region['weights'][b['rarity']] / tier_counts.get(b['rarity'], 1))), reverse=True)
        for beast in ranked:
            if not valid(beast, index):
                continue
            selected.append(beast)
            counts[beast['id']] += 1
            if choose(index + 1):
                return True
            counts[beast['id']] -= 1
            selected.pop()
        return False

    if not choose(0):
        raise ValueError('No fair trial lineup fits this biome. Nothing has been spent.')
    waves = []
    low, high = region['level_range']
    tiers = ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine')
    ordinary_floor = min(tiers.index(b['rarity']) for b in normal)
    ordinary_reference = [b for b in normal if tiers.index(b['rarity']) == ordinary_floor]
    for index, beast in enumerate(selected, 1):
        kind = 'guardian' if index == count else 'miniboss' if count >= 8 and index == midpoint else 'normal'
        profile = deepcopy(PROFILES[kind])
        level = low + (high - low) * (index - 1) // (count - 1)
        reference = finals if kind == 'guardian' else minibosses if kind == 'miniboss' else ordinary_reference
        ceiling = min(power(profiled_state(b['id'], level, profile)) for b in reference) * (1.35 if kind == 'normal' else 1.25)
        resolved = profiled_state(beast['id'], level, profile)
        before_power = power(resolved)
        scaling = cap_power(resolved, int(ceiling))
        resolved['perks'] = []  # Enemy templates have no perks; make plans JSON-safe.
        waves.append(dict(index=index, species=beast['id'], name=beast['name'], type=beast['type'],
                          rarity=beast['rarity'], level=level, power_cap=int(ceiling),
                          profile_scaling=scaling, power_before=before_power, power_after=power(resolved),
                          enemy_template=resolved,
                          kind=kind, profile=profile, round_limit=profile['round_limit'],
                          hint=hint(beast), categories=flags[beast['id']]))
    return {**cfg, 'seed': seed, 'waves': waves}
