"""Continuous raid budgets from obtainable companion growth, never equipped stats."""
import math
import statistics
from functools import lru_cache
from utils import db as storage

TARGET_SECONDS = 420
ACTION_SECONDS = 5.0
MIN_ACTION_SECONDS = 3.5
MAX_ACTION_SECONDS = 6.5
BOSS_INTERVAL = 8
MIN_TRAINER_LEVEL = 15
MIN_COMPANION_LEVEL = 15
GUILD_ENTRY_TOKENS = 50
GUILD_VICTORY_TOKENS = 15

# Natural catalog companions replace the former assumption that every starter
# becomes Divine at fifteen. Interpolating *stats*, rather than switching tiers,
# makes every intervening level count without the old level-25/40 cliffs.
REFERENCE_TEAMS = (
    (10, ('griffin', 'basilisk', 'wyvern')),
    (15, ('griffin', 'basilisk', 'wyvern')),
    (25, ('phoenix', 'thunderbird', 'yeti')),
    (40, ('dragon', 'kraken', 'fenrir')),
    (50, ('atlas', 'ascended_phoenix', 'radiant_hydra')),
)

BOSS_PROFILES = {
    # Matchup/resource factors are interpolated at levels 10/25/40/50.
    'corrupted_leviathan': {'hp': 1.00, 'attack': .82, 'defense': 1.10, 'tier_hp': (.74, 1, .85, .90)},
    'corrupted_fenrir': {'hp': .96, 'attack': 1.06, 'defense': .90, 'tier_hp': (.88, 1, 1, 1.12)},
    'corrupted_dragon': {'hp': 1.00, 'attack': 1.00, 'defense': 1.00, 'tier_hp': (1, 1.24, 1, 1.12)},
    'ancient_chronos': {'hp': 1.12, 'attack': 1.08, 'defense': 1.08, 'tier_hp': (1, .84, 1, 1.10)},
    'ancient_genesis': {'hp': 1.08, 'attack': 1.12, 'defense': 1.00, 'tier_hp': (1.06, .90, 1, 1.15)},
    'ancient_abyss': {'hp': 1.12, 'attack': 1.05, 'defense': 1.12, 'tier_hp': (1, .90, 1, 1.10)},
}


def reference_species(level):
    """An obtainable comparison party; difficulty itself never switches tiers."""
    return min(REFERENCE_TEAMS, key=lambda anchor: abs(anchor[0] - level))[1]


def _team_stats(level, species):
    from utils.progression import companion_growth
    catalog = storage.load_beasts()
    definitions = [storage.get_beast_data(key) for key in species]
    stats = []
    for data in definitions:
        growth = companion_growth(data['id'], level, catalog)
        stats.append({stat: value + growth[stat] for stat, value in data['base_stats'].items()})
    return {stat: statistics.mean(row[stat] for row in stats) for stat in stats[0]}


def interpolate(level, anchors):
    """Linear interpolation includes anchor endpoints and clamps outliers."""
    level = max(anchors[0][0], min(anchors[-1][0], float(level)))
    for (low, start), (high, end) in zip(anchors, anchors[1:]):
        if level <= high:
            return start + (end - start) * (level - low) / (high - low)
    return anchors[-1][1]


def reference_stats(level):
    from utils.catalog import catalog_revision
    anchors = _reference_anchors(catalog_revision())
    return {stat: interpolate(level, [(at, row[stat]) for at, row in anchors])
            for stat in anchors[0][1]}


@lru_cache(maxsize=4)
def _reference_anchors(revision):
    return tuple((at, _team_stats(at, species)) for at, species in REFERENCE_TEAMS)


def _health_budget(level, profile):
    # Model the resource/matchup rotation at milestones, then interpolate its
    # budget. Carrying forward an earlier peak prevents a more durable new
    # reference tier from making a higher-level boss lose health.
    anchors = []
    previous = 0
    for at, _ in REFERENCE_TEAMS:
        reference = reference_stats(at)
        defense = round(reference['defense'] * .65 * profile['defense'])
        basic = reference['attack'] * 100 / (100 + defense)
        rotation = interpolate(at, [(10, 1.), (25, .8), (40, 1.), (50, 1.)])
        matchup = interpolate(at, list(zip((10, 25, 40, 50), profile['tier_hp'])))
        hp = basic * .90 / ACTION_SECONDS * TARGET_SECONDS * profile['hp'] * rotation * matchup
        previous = max(previous, hp)
        anchors.append((at, previous))
    return interpolate(level, anchors)


def budget(boss_id, party):
    # A high-level bench counts, so swapping after launch cannot evade scaling.
    peaks = [max(state.get('level', 1) for state in companions) for companions in party.values()]
    level = min(50, max(10, statistics.median(peaks)))
    reference = reference_stats(level)
    profile = BOSS_PROFILES[boss_id]
    defense = round(reference['defense'] * .65 * profile['defense'])
    hp = round(_health_budget(level, profile) * len(party) ** .95)
    pressure = interpolate(level, [(10, .135), (15, .135), (25, .15), (50, .15)])
    attack = round(reference['hp'] * pressure * (1 + reference['defense']/100) * profile['attack'])
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
