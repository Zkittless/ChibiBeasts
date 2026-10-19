"""One egg catalog for shops, reveals, incubation, research and advertised odds.

An egg's grade is its rarity floor and fixes hatch levels. Its family restricts
the species, including bonus rolls: a themed egg never silently becomes an
unrelated companion. Incubators roll rarity once, then offer distinct species
from that tier; choosing between them cannot improve rarity odds.
"""
from copy import deepcopy
from functools import lru_cache
import random

from utils.catalog import beast_definition, catalog_revision, load_catalog
from utils.economy_balance import INCUBATION_LEVELS, INCUBATION_PRICES

RARITIES = ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine', 'altered_divine')
STARTER_IDS = frozenset({'prismite', 'twine', 'gloop', 'barkley'})
RESEARCH_COST = 8
RESEARCH_MAX = 16
LEVEL_BANDS = {'common': (1, 3), 'uncommon': (6, 9), 'rare': (14, 18),
               'epic': (24, 28), 'legendary': (34, 38), 'divine': (40, 44)}


def _egg(egg_id, name, grade, family, pool, flavor, *, hours=0, price=None,
         types=(), candidates=None, emoji='🥚', lore='', min_level=None,
         level_range=None, shop=True):
    result = {'id': egg_id, 'name': name, 'rarity': grade, 'grade': grade,
              'family': family, 'price': INCUBATION_PRICES[grade] if price is None else price,
              'min_level': INCUBATION_LEVELS[grade] if min_level is None else min_level,
              'level_range': level_range or LEVEL_BANDS[grade],
              'incubation_hours': hours, 'pool': pool, 'flavor': flavor,
              'description': flavor, 'emoji': emoji, 'lore': lore, 'shop': shop}
    if types:
        result['types'] = tuple(types)
    if candidates:
        result['candidate_ids'] = {r: tuple(ids) for r, ids in candidates.items()}
        # Keep curated collection keys for older callers and saved inventory.
        result.update({('altered_pool' if r == 'altered_divine' else r + '_pool'): list(ids)
                       for r, ids in candidates.items()})
    return result


_DEFINITIONS = {
    e['id']: e for e in (
        _egg('common_egg', '🥚 Common Egg', 'common', 'open',
             {'common': .75, 'uncommon': .25},
             'A warm first companion. Common or better, ready immediately.', price=200),
        _egg('wildheart_egg', '🌿🥚 Wildheart Egg', 'uncommon', 'open',
             {'uncommon': .75, 'rare': .25},
             'A lively new teammate. Uncommon or better, ready immediately.', price=700),
        _egg('rare_egg', '🥚✨ Rare Egg', 'rare', 'open',
             {'rare': .80, 'epic': .20},
             'A dependable discovery. Every hatch is Rare or better.', price=1500),
        _egg('resonant_egg', '🔮🥚 Resonant Egg', 'epic', 'open',
             {'epic': .85, 'legendary': .15},
             'A powerful pulse from within. Every hatch is Epic or better.', price=4000),
        _egg('celestial_egg', '🌌🥚 Celestial Egg', 'legendary', 'open',
             {'legendary': .90, 'divine': .10},
             'A legend ready to join your team. Legendary or better.', price=8000),
        _egg('abyssal_egg', '🌊💎 Abyssal Egg', 'divine', 'open',
             {'divine': .99, 'altered_divine': .01},
             'An immediate Divine companion from the full celestial roster, with a 1% Altered chance.',
             price=25000),
        _egg('sprout_pod', 'Sprout Pod', 'common', 'woodland',
             {'common': .55, 'uncommon': .35, 'rare': .10},
             'Woodland spirits of nature and light. Choose a hatchling when ready.',
             hours=1, types=('nature', 'light'), emoji='🌱'),
        _egg('pebble_shell', 'Pebble Shell', 'common', 'stone',
             {'common': .65, 'uncommon': .35},
             'Earth companions sheltered in a stone shell. Choose a hatchling when ready.',
             hours=1, types=('earth',), emoji='🪨'),
        _egg('soot_hatchling', 'Soot Hatchling', 'common', 'embers',
             {'common': .45, 'uncommon': .40, 'rare': .15},
             'Fire and shadow companions, warm with restless embers.',
             hours=1, types=('fire', 'shadow'), emoji='🖤'),
        _egg('dewdrop_bulb', 'Dewdrop Bulb', 'uncommon', 'rivers',
             {'uncommon': .30, 'rare': .50, 'epic': .20},
             'Water and ice companions from cold rivers and deep pools.',
             hours=4, types=('water', 'ice'), emoji='💧'),
        _egg('gale_nest', 'Gale Nest', 'uncommon', 'sky',
             {'uncommon': .28, 'rare': .48, 'epic': .24},
             'Wind companions carried by gentle breezes and gathering storms.',
             hours=4, types=('wind',), emoji='🌬️'),
        _egg('cavern_core', 'Cavern Core', 'uncommon', 'caverns',
             {'uncommon': .30, 'rare': .50, 'epic': .20},
             'Earth, arcane and shadow companions from hidden caverns.',
             hours=4, types=('earth', 'arcane', 'shadow'), emoji='💎'),
        _egg('prism_sphere', 'Prism Sphere', 'rare', 'prismatic',
             {'rare': .40, 'epic': .40, 'legendary': .20},
             'Radiant unicorns, Loom fragments, sphinxes, kitsune and Simurgh.', hours=8,
             candidates={'rare': ('unicorn', 'loom_fragment'), 'epic': ('sphinx', 'kitsune_9'),
                         'legendary': ('simurgh',)}, emoji='🔮'),
        _egg('glow_spore', 'Glow-Spore Cluster', 'rare', 'darkwood',
             {'rare': .38, 'epic': .42, 'legendary': .20},
             'Watchful guardians and elusive predators of the deep, dark woods.', hours=8,
             candidates={'rare': ('basilisk', 'cerberus'), 'epic': ('wendigo', 'behemoth'),
                         'legendary': ('fenrir', 'jormungandr')}, emoji='🍄'),
        _egg('eclipse_pebble', 'Eclipse Pebble', 'rare', 'eclipse',
             {'rare': .35, 'epic': .40, 'legendary': .25},
             'Ice, shadow and cosmic companions of the long twilight.', hours=10,
             types=('ice', 'shadow', 'cosmic'), emoji='🌗'),
        _egg('volcanic_core', 'Volcanic Core', 'epic', 'volcanic',
             {'epic': .35, 'legendary': .40, 'divine': .25},
             'Forge-born giants, firebirds, dragons and the powers that shaped them.', hours=18,
             candidates={'epic': ('phoenix', 'minotaur', 'behemoth'), 'legendary': ('dragon',),
                         'divine': ('supernova', 'genesis', 'asgard', 'atlas')}, emoji='🌋'),
        _egg('nimbus_cloud', 'Nimbus Cloud', 'epic', 'tempest',
             {'epic': .35, 'legendary': .40, 'divine': .25},
             'Storm birds, sky serpents and the celestial keepers of the heavens.', hours=18,
             candidates={'epic': ('thunderbird', 'yeti'), 'legendary': ('simurgh', 'charybdis'),
                         'divine': ('astraea', 'aetherius', 'zodiac', 'horizon')}, emoji='⛈️'),
        _egg('monolith_relic', 'Monolith Relic', 'epic', 'relic',
             {'epic': .30, 'legendary': .40, 'divine': .30},
             'Ancient guardians, great serpents and the architects of forgotten ages.', hours=24,
             candidates={'epic': ('sphinx', 'minotaur', 'behemoth'),
                         'legendary': ('hydra', 'jormungandr'),
                         'divine': ('atlas', 'chronos', 'terminus', 'paradox')}, emoji='🗿'),
        _egg('abyssal_trench_orb', 'Abyssal Trench Orb', 'legendary', 'abyssal',
             {'legendary': .60, 'divine': .38, 'altered_divine': .02},
             'Deep-sea legends and the celestial forces of the abyss.', hours=48,
             candidates={'legendary': ('kraken', 'leviathan', 'charybdis', 'jormungandr'),
                         'divine': ('aetherius', 'nirvana', 'abyss', 'nebula'),
                         'altered_divine': ('abyssal_nebula',)}, emoji='🌊'),
        _egg('dragon_hoard_scale', 'Dragon-Hoard Scale', 'legendary', 'hoard',
             {'legendary': .60, 'divine': .38, 'altered_divine': .02},
             'Hoard guardians and the powers of creation that dragons remember.', hours=48,
             candidates={'legendary': ('dragon', 'fenrir', 'hydra'),
                         'divine': ('supernova', 'genesis', 'asgard', 'atlas'),
                         'altered_divine': ('fractured_genesis',)}, emoji='🐉'),
        _egg('glacial_monolith', 'Glacial Monolith', 'legendary', 'glacial',
             {'legendary': .60, 'divine': .38, 'altered_divine': .02},
             'Legends frozen beyond time and the celestial masters of its passage.', hours=48,
             candidates={'legendary': ('simurgh', 'fenrir', 'dragon', 'jormungandr'),
                         'divine': ('chronos', 'terminus', 'zodiac', 'paradox', 'horizon'),
                         'altered_divine': ('void_chronos',)}, emoji='🧊'),
        _egg('genesis_matrix', 'Genesis Matrix', 'divine', 'architects', {'divine': 1.0},
             'Choose Genesis, Terminus or Paradox: the three Architects of Reality.', hours=96,
             candidates={'divine': ('genesis', 'terminus', 'paradox')}, emoji='🏛️'),
        _egg('constellation_spool', 'Constellation Spool', 'divine', 'constellations', {'divine': 1.0},
             'Choose Zodiac, Karma or Horizon: the three voices of the Celestial Loom.', hours=96,
             candidates={'divine': ('zodiac', 'karma', 'horizon')}, emoji='⏳'),
        _egg('singularity_core', 'Singularity Core', 'divine', 'primordial', {'divine': 1.0},
             'Choose Supernova, Nebula or Abyss: the three Primordial Aspects.', hours=96,
             candidates={'divine': ('supernova', 'nebula', 'abyss')}, emoji='🎴'),
        _egg('world_tree_seed', 'World-Tree Seed', 'divine', 'pillars', {'divine': 1.0},
             'An earned story seed. Choose Atlas, Asgard or Nirvana, the Mythological Pillars.',
             hours=24, candidates={'divine': ('atlas', 'asgard', 'nirvana')}, emoji='🏮',
             min_level=1, level_range=(15, 18), shop=False),
    )
}


def _eligible(beast):
    return (beast.get('rarity') in RARITIES and beast.get('id') not in STARTER_IDS
            and not beast.get('starter') and not beast.get('evolution_only')
            and beast.get('wild_encounter', True) and beast.get('catch_rate', 0) > 0)


@lru_cache(maxsize=16)
def _roster(revision):
    return load_catalog('beasts.json')['beasts']


def _pool_ids(egg, rarity):
    candidates = egg.get('candidate_ids')
    allowed = set(candidates.get(rarity, ())) if candidates is not None else None
    types = set(egg.get('types', ()))
    return [key for key, beast in _roster(catalog_revision()).items()
            if _eligible(beast) and beast['rarity'] == rarity
            and (allowed is None or key in allowed) and (not types or beast['type'] in types)]


def _normalize(pool):
    total = sum(pool.values())
    if total <= 0:
        raise ValueError('This egg has no eligible hatchlings. Its inventory must be preserved.')
    return {r: p / total for r, p in pool.items() if p > 0}


def definition(egg_id):
    """Return a private, validated definition; unknown IDs never become cheap eggs."""
    try:
        egg = deepcopy(_DEFINITIONS[egg_id])
    except (KeyError, TypeError):
        raise ValueError('Unknown nursery egg.') from None
    floor = RARITIES.index(egg['grade'])
    allowed = {r: p for r, p in egg['pool'].items()
               if p > 0 and RARITIES.index(r) >= floor and _pool_ids(egg, r)}
    removed = [r for r, p in egg['pool'].items() if p > 0 and r not in allowed]
    egg['pool'] = _normalize(allowed)
    egg['pool_notes'] = ([f"No eligible {', '.join(removed)} hatchlings; those odds are redistributed "
                          'proportionally among the displayed family tiers.'] if removed else [])
    return egg


def definitions():
    return {key: definition(key) for key in _DEFINITIONS}


def instant_definitions():
    return {key: egg for key, egg in definitions().items() if not egg['incubation_hours']}


def incubation_definitions():
    return {key: egg for key, egg in definitions().items() if egg['incubation_hours']}


def species_pool(egg_id, rarity):
    """All eligible species for this family/tier, never a fallback global roster."""
    egg = definition(egg_id)
    if rarity not in RARITIES or RARITIES.index(rarity) < RARITIES.index(egg['grade']):
        return []
    return [beast_definition(key) for key in _pool_ids(egg, rarity)]


def _equipped(perks, perk_id):
    return any(p.get('perk_id', p.get('id')) == perk_id and p.get('equipped') for p in perks or ())


def rarity_pool(egg_id, perks=(), sanctuary=None):
    """The exact distribution used for the roll and odds display, bonuses included.

    Stardust and Observatory move probability only into tiers already offered.
    Fortune is one 0.05% Divine jackpot, drawn from the same eligible family;
    it never replaces an Altered roll or downgrades a Divine-grade egg.
    """
    egg = definition(egg_id)
    pool = egg['pool'].copy()

    def promote(target, amount):
        if target not in pool:
            return
        donors = [r for r in pool if RARITIES.index(r) < RARITIES.index(target)]
        available = sum(pool[r] for r in donors)
        moved = min(amount, available)
        if not moved:
            return
        for r in donors:
            pool[r] -= moved * pool[r] / available
        pool[target] += moved

    if _equipped(perks, 'stardust_touch'):
        promote('rare', .10)
        promote('epic', .10)
    if sanctuary and sanctuary.get('celestial_observatory'):
        promote('epic', .02)
        promote('legendary', .02)
    if (_equipped(perks, 'glimmering_fortune')
            and RARITIES.index(egg['grade']) < RARITIES.index('divine')
            and _pool_ids(egg, 'divine')):
        # The existing Altered chance stays exact: Fortune only redirects lower tiers.
        donors = [r for r in pool if RARITIES.index(r) < RARITIES.index('divine')]
        moved = sum(pool[r] for r in donors) * .0005
        for r in donors:
            pool[r] *= .9995
        if moved:
            pool['divine'] = pool.get('divine', 0) + moved
    return _normalize(pool)


def choose_rarity(egg_id, rng=random, perks=(), sanctuary=None):
    pool = rarity_pool(egg_id, perks, sanctuary)
    roll = rng.random()
    cumulative = 0.0
    for rarity, chance in pool.items():
        cumulative += chance
        if roll < cumulative:
            return rarity
    return next(reversed(pool))


def hatch_level(egg_id, rng=random):
    return rng.randint(*definition(egg_id)['level_range'])


def research_key(egg_id):
    egg = definition(egg_id)
    return f"{egg['grade']}:{egg['family']}"


def research_pool(egg_id):
    """Research selects the egg's floor tier, never a lucky upgrade/jackpot."""
    egg = definition(egg_id)
    return species_pool(egg_id, egg['grade'])
