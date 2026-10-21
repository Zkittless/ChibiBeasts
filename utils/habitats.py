"""Shared destinations and encounter eligibility for exploration and challenges."""
import random
from functools import lru_cache
from utils.catalog import catalog_revision
from utils.db import get_beast_data
from utils.db import load_beasts
BIOMES = {}
DESTINATION_ALIASES = {}
_DISTINCT_DESTINATIONS = {
    'woods': '🌙 The Moonveil Marsh',
    'ember': '🏜️ The Sunforge Dunes',
    'glacial': '⛰️ The Stormglass Peaks',
    'trenches': '🌋 The Cinderreef Caldera',
    'loom': '✨ The Starfall Gardens',
}


def _plain_name(value):
    value = str(value)
    return value[next((i for i, char in enumerate(value) if char.isalnum()), len(value)):]


for _key, _name, _types, _gate, _deep_gate, _low, _weights, _deep_weights in (
    ('woods', '🌲 The Whispering Woods', {'nature', 'earth', 'arcane', 'light'}, 1, 3, 1,
     {'common': .85, 'uncommon': .15}, {'common': .65, 'uncommon': .30, 'rare': .05}),
    ('ember', '🔥 The Ember Wastes', {'fire', 'earth', 'shadow'}, 6, 9, 11,
     {'common': .20, 'uncommon': .65, 'rare': .15}, {'uncommon': .25, 'rare': .60, 'epic': .15}),
    ('glacial', '❄️ The Glacial Hollows', {'ice', 'wind'}, 12, 16, 21,
     {'rare': .70, 'epic': .30}, {'rare': .35, 'epic': .60, 'legendary': .05}),
    ('trenches', '🌊 The Sunken Abyssal Trenches', {'water', 'shadow'}, 20, 25, 31,
     {'epic': .75, 'legendary': .25}, {'epic': .35, 'legendary': .65}),
    ('loom', '🌌 The Celestial Loom', {'cosmic', 'arcane', 'light'}, 30, 38, 41,
     {'rare': .05, 'epic': .70, 'divine': .25}, {'epic': .40, 'divine': .60}),
):
    BIOMES[_key] = dict(name=_name, min_level=_gate, types=_types,
                        level_range=(_low, _low + 4), weights=_weights, base=_key)
    BIOMES[_key + '_deep'] = dict(name=_DISTINCT_DESTINATIONS[_key], min_level=_deep_gate, types=_types,
                                 level_range=(_low + 5, _low + 9), weights=_deep_weights, base=_key)
    # Saved command labels and old journal links still identify the same band.
    for _alias in (_name + ' · Depths', _plain_name(_name) + ' · Depths',
                   _plain_name(_name).removeprefix('The ') + ' · Depths'):
        DESTINATION_ALIASES[_alias.casefold()] = _key + '_deep'

BASE_BIOMES = tuple(key for key, value in BIOMES.items() if key == value['base'])
# The forest edges shelter the low-tier species of colder and wetter regions;
# the deepest Trenches include volcanic vents where dragons can be found.
BIOMES['woods']['types'] = BIOMES['woods']['types'] | {'ice'}
BIOMES['woods_deep']['types'] = BIOMES['woods_deep']['types'] | {'shadow', 'ice', 'wind', 'water'}
BIOMES['trenches_deep']['types'] = BIOMES['trenches_deep']['types'] | {'fire'}


def destination_key(value):
    if value in BIOMES:
        return value
    normalized = str(value or '').strip().casefold()
    if normalized in DESTINATION_ALIASES:
        return DESTINATION_ALIASES[normalized]
    # Resolve exact names before partial names (especially old Depths labels).
    for key, region in BIOMES.items():
        plain = _plain_name(region['name']).casefold()
        if normalized in (region['name'].casefold(), plain, plain.removeprefix('the ')):
            return key
    return next((key for key, region in BIOMES.items()
                 if normalized and normalized in region['name'].casefold()), 'woods')

def biome_key(value):
    """The original five region IDs remain the material and story families."""
    return BIOMES[destination_key(value)]['base']


def encounter_level(destination, companion_level=None, rng=random):
    """Roll near a visitor while retaining the region's fixed floor and ceiling."""
    # Preserve the former encounter_level(destination, rng) diagnostic API.
    if hasattr(companion_level, 'randint'):
        rng, companion_level = companion_level, None
    low, high = BIOMES[destination_key(destination)]['level_range']
    if companion_level is not None:
        companion_level = int(companion_level)
        nearby_low, nearby_high = max(low, companion_level - 1), min(high, companion_level + 1)
        if nearby_low > nearby_high:
            return max(low, min(high, companion_level))
        low, high = nearby_low, nearby_high
    return rng.randint(low, high)


def destination_label(destination):
    region = BIOMES[destination_key(destination)]
    low, high = region['level_range']
    return f"{region['name']} · Trainer {region['min_level']}+ · Companion {low}–{high}"


def starter_training_destination(beast, companion_level, trainer_level):
    """A visible training suggestion; access to other paths remains available."""
    if not beast.get('starter') or (beast.get('evolution') or {}).get('form') != 'bond':
        return None
    return 'woods_deep' if companion_level >= 8 and trainer_level >= BIOMES['woods_deep']['min_level'] else 'woods'


def encounter_difficulty(companion, enemy):
    """Compare actual battle stats, so weak species and strong builds pay less."""
    def power(state):
        return (state.get('max_hp', state.get('hp', 1)) + 3 * state.get('attack', 0)
                + 2 * state.get('defense', 0) + state.get('speed', 0)
                + .25 * state.get('max_mana', state.get('mana', 0)))
    ratio = power(enemy) / max(1, power(companion))
    return 'trivial' if ratio < .40 else 'easy' if ratio < .70 else 'normal' if ratio < 1.30 else 'hard' if ratio < 1.80 else 'boss'

def eligible(beast):
    return not beast.get('starter') and (not beast.get('evolution_only')) and beast.get('wild_encounter', True) and (beast.get('catch_rate', 0) > 0) and (beast['rarity'] not in {'dev', 'ancient', 'corrupted', 'altered_divine'})

def encounter_pool(rarity,biome=None,element=None):
    return [get_beast_data(key) for key in _pool_ids(rarity,destination_key(biome) if biome else None,element,catalog_revision())]

@lru_cache(maxsize=128)
def _pool_ids(rarity,biome,element,revision):
    habitat=BIOMES.get(biome)
    return tuple(b['id'] for b in load_beasts().values() if b['rarity']==rarity and eligible(b) and (not habitat or b['type'] in habitat['types'] and habitat['weights'].get(rarity, 0) > 0) and (not element or b['type']==element))

def pick_encounter(weights,biome=None,element=None):
    revision=catalog_revision();region=destination_key(biome) if biome else None
    pools={r:_pool_ids(r,region,element,revision) for r in weights}
    choices=[r for r in weights if pools[r] and weights[r]>0]
    if not choices:return None
    rarity=random.choices(choices,weights=[weights[r] for r in choices])[0]
    return get_beast_data(random.choice(pools[rarity]))


def set_rarity_chance(weights, biome, rarity, chance):
    """Exact advertised odds after excluding tiers with no eligible inhabitants."""
    available = {r:w for r,w in weights.items() if w>0 and encounter_pool(r,biome)}
    other = sum(w for r,w in available.items() if r!=rarity)
    if rarity not in available or not other:
        return available
    return {r:chance if r==rarity else w/other*(1-chance) for r,w in available.items()}


def challenge_weights(weights, rarity):
    tiers=('common','uncommon','rare','epic','legendary','divine')
    rank=tiers.index(rarity) if rarity in tiers else len(tiers)-1
    biased={r:w*(3 if tiers.index(r)==rank else 1.5 if abs(tiers.index(r)-rank)==1 else 1) for r,w in weights.items()}
    total=sum(biased.values())
    return {r:w/total for r,w in biased.items()}

SPECIES_MATERIALS = {'fairy':'pixie_silk','pixie':'pixie_silk','goblin':'gnome_iron_ore','slime':'enchanted_bark','dryad':'enchanted_bark','harpy':'harpy_down','kelpie':'kelpie_scale','hellhound':'hellhound_ember','unicorn':'unicorn_hair','manticore':'manticore_stinger','basilisk':'basilisk_eye','phoenix':'phoenix_ash','thunderbird':'thunderbird_talon','sphinx':'sphinx_papyrus','dragon':'dragon_scale','kraken':'kraken_ink','fenrir':'fenrir_fur'}
REGIONAL_MATERIALS = {'woods':['pixie_silk','enchanted_bark','gnome_iron_ore','kelpie_scale','spirit_crystal'], 'ember':['gnome_iron_ore','hellhound_ember','sunforge_residue','phoenix_ash','dragon_scale'], 'glacial':['enchanted_bark','harpy_down','unicorn_hair','fenrir_fur','thunderbird_talon'], 'trenches':['kelpie_scale','kraken_ink','manticore_stinger','void_essence'], 'loom':['spirit_crystal','sphinx_papyrus','basilisk_eye','unicorn_hair']}


def material_pool(beast, biome):
    """Named resources follow species and habitat; rare forging resources are reachable."""
    from utils.catalog import load_catalog
    materials = load_catalog('materials.json')['materials']
    weights = {'common':4,'uncommon':3,'rare':2,'epic':1,'legendary':1}
    result = [mid for mid in REGIONAL_MATERIALS[biome_key(biome)] for _ in range(weights[materials[mid]['rarity']])]
    if beast['id'] in SPECIES_MATERIALS:
        result.extend([SPECIES_MATERIALS[beast['id']]] * 3)
    if beast['type'] == 'shadow':
        result.extend(['void_essence'] * 2)
    if beast['type'] == 'arcane':
        result.extend(['spirit_crystal'] * 2)
    if beast['type'] in {'fire', 'earth'}:
        result.extend(['sunforge_residue'] * 2)
    return result
