"""Shared destinations and encounter eligibility for exploration and challenges."""
import random
from functools import lru_cache
from utils.catalog import catalog_revision
from utils.db import get_beast_data
from utils.db import load_beasts
BIOMES = {'woods': {'name': '🌲 The Whispering Woods', 'min_level': 1, 'types': {'nature', 'earth', 'arcane', 'light'}, 'weights': {'common': 0.5, 'uncommon': 0.3, 'rare': 0.15, 'epic': 0.05}}, 'ember': {'name': '🔥 The Ember Wastes', 'min_level': 3, 'types': {'fire', 'earth', 'shadow'}, 'weights': {'common': 0.15, 'uncommon': 0.35, 'rare': 0.3, 'epic': 0.15, 'legendary': 0.05}}, 'glacial': {'name': '❄️ The Glacial Hollows', 'min_level': 5, 'types': {'ice', 'wind'}, 'weights': {'common': 0.15, 'uncommon': 0.3, 'rare': 0.3, 'epic': 0.2, 'legendary': 0.05}}, 'trenches': {'name': '🌊 The Sunken Abyssal Trenches', 'min_level': 8, 'types': {'water', 'shadow'}, 'weights': {'uncommon': 0.3, 'rare': 0.35, 'epic': 0.25, 'legendary': 0.1}}, 'loom': {'name': '🌌 The Celestial Loom', 'min_level': 10, 'types': {'cosmic', 'arcane', 'light'}, 'weights': {'rare': 0.35, 'epic': 0.35, 'legendary': 0.2, 'divine': 0.1}}}

def biome_key(value):
    if value in BIOMES:
        return value
    return next((k for k, b in BIOMES.items() if b['name'] == value), 'woods')

def eligible(beast):
    return not beast.get('starter') and (not beast.get('evolution_only')) and beast.get('wild_encounter', True) and (beast.get('catch_rate', 0) > 0) and (beast['rarity'] not in {'dev', 'ancient', 'corrupted', 'altered_divine'})

def encounter_pool(rarity,biome=None,element=None):
    return [get_beast_data(key) for key in _pool_ids(rarity,biome_key(biome) if biome else None,element,catalog_revision())]

@lru_cache(maxsize=128)
def _pool_ids(rarity,biome,element,revision):
    habitat=BIOMES.get(biome)
    return tuple(b['id'] for b in load_beasts().values() if b['rarity']==rarity and eligible(b) and (not habitat or b['type'] in habitat['types']) and (not element or b['type']==element))

def pick_encounter(weights,biome=None,element=None):
    revision=catalog_revision();region=biome_key(biome) if biome else None
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
