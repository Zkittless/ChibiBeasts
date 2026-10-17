"""Raid budgets use level bands and reference companions, never equipped stats."""
import math
import statistics
from utils import db as storage

TARGET_SECONDS = 420
ACTION_SECONDS = 5.0
MIN_ACTION_SECONDS = 3.5
MAX_ACTION_SECONDS = 6.5
BOSS_INTERVAL = 8

BOSS_PROFILES = {
    # Tier HP factors account for the reference teams' matchups and resources.
    # Bands: rare (10–14), epic (15–24), legendary (25–39), divine (40–50).
    'corrupted_leviathan': {'hp': 1.08, 'attack': .96, 'defense': 1.10, 'tier_hp': (.80, 1, .85, 1)},
    'corrupted_fenrir': {'hp': .96, 'attack': 1.06, 'defense': .90, 'tier_hp': (.88, 1, 1, 1)},
    'corrupted_dragon': {'hp': 1.00, 'attack': 1.00, 'defense': 1.00, 'tier_hp': (1, 1.08, 1, 1)},
    'ancient_chronos': {'hp': 1.12, 'attack': 1.08, 'defense': 1.08, 'tier_hp': (1, .84, 1, 1)},
    'ancient_genesis': {'hp': 1.08, 'attack': 1.12, 'defense': 1.00, 'tier_hp': (1.06, .90, 1, 1)},
    'ancient_abyss': {'hp': 1.12, 'attack': 1.05, 'defense': 1.12, 'tier_hp': (1, .90, 1, 1)},
}


def reference_species(level):
    if level < 15:
        return ('griffin', 'basilisk', 'wyvern')
    if level < 25:
        return ('sphinx', 'thunderbird', 'yeti')
    if level < 40:
        return ('dragon', 'kraken', 'fenrir')
    return ('atlas', 'ascended_phoenix', 'nebuloom')


def reference_stats(level):
    definitions = [storage.get_beast_data(key) for key in reference_species(level)]
    stats = []
    for data in definitions:
        growth = storage.calc_stat_growth(data, level - 1)
        stats.append({stat: value + growth[stat] for stat, value in data['base_stats'].items()})
    return {stat: statistics.mean(row[stat] for row in stats) for stat in stats[0]}


def budget(boss_id, party):
    # A high-level bench counts, so swapping after launch cannot evade scaling.
    peaks = [max(state.get('level', 1) for state in companions) for companions in party.values()]
    level = min(50, max(10, int(statistics.median(peaks)) // 5 * 5))
    reference = reference_stats(level)
    profile = BOSS_PROFILES[boss_id]
    defense = round(reference['defense'] * .65 * profile['defense'])
    basic = reference['attack'] * 100 / (100 + defense)
    # Includes the mana rotation and time spent tending/responding to patterns.
    reference_dps = basic * .90 / ACTION_SECONDS
    # Epic teams lack the late-game resource/sustain passives of higher tiers.
    tier_rotation = .8 if 15 <= level < 25 else 1.0
    tier = 0 if level < 15 else 1 if level < 25 else 2 if level < 40 else 3
    hp = round(reference_dps * TARGET_SECONDS * len(party) ** .95 * profile['hp'] * tier_rotation * profile['tier_hp'][tier])
    attack = round(reference['hp'] * .15 * (1 + reference['defense']/100) * profile['attack'])
    return {'level': level, 'hp': max(1000, hp), 'defense': defense, 'attack': attack,
            'speed': 1, 'mana': 100, 'max_mana': 100, 'happiness': 70,
            'reference_speed': reference['speed'], 'reference_attack': reference['attack']}


def action_interval(state, reference_speed):
    from utils.combat import effective_speed
    speed = effective_speed(state)
    if not math.isfinite(speed):
        return MIN_ACTION_SECONDS
    return round(max(MIN_ACTION_SECONDS, min(MAX_ACTION_SECONDS,
                    ACTION_SECONDS * (reference_speed/max(1, speed)) ** .3)), 2)


def reward_scale(level):
    """Higher difficulty pays more instead of making low-level farming optimal."""
    return 1 + (min(50, max(10, level)) - 10) / 40
