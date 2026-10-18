"""Shared progression gates and currency budgets, independent of Discord views."""
INCUBATION_PRICES = {'common':300, 'uncommon':1200, 'rare':4000, 'epic':12000, 'legendary':24000, 'divine':80000}
INCUBATION_LEVELS = {'common':1, 'uncommon':6, 'rare':12, 'epic':20, 'legendary':30, 'divine':40}
CRAFT_LEVELS = {'common':1, 'uncommon':6, 'rare':12, 'epic':20, 'legendary':30, 'divine':40, 'altered_divine':45}
RELEASE_VALUES = {'common':50, 'uncommon':150, 'rare':500, 'epic':1200, 'legendary':3000, 'divine':8000, 'altered_divine':20000}
MATERIAL_SELL_VALUES = {'common':20, 'uncommon':40, 'rare':80, 'epic':180, 'legendary':400, 'altered_divine':800}
TRAIN_COSTS = {'common':50, 'uncommon':90, 'rare':150, 'epic':250, 'legendary':400,
               'divine':600, 'altered_divine':800, 'corrupted':900, 'ancient':1000, 'dev':0}
TRAIN_HP_MULTIPLIER = 3
TRAIN_DAILY_LIMIT = 4
STAT_TREAT_LIMIT = 30
GUARANTEED_EXPLORE_MATERIAL = True
GUILD_EXP_PER_LEVEL = 250


def shop_level(item):
    if 'incubation_hours' in item:
        return INCUBATION_LEVELS[item['rarity']]
    return item.get('min_level',1)


def recipe_level(item):
    return item.get('min_level', CRAFT_LEVELS.get(item['rarity'],1))


def gold_reward_level(level):
    """Veteran levels still progress; repeatable gold scales through level 50."""
    return max(1,min(50,level))


def item_sell_price(item, merchant=False):
    base = item.get('resale_price', max(5,int(item.get('price',0)*.35)))
    return int(base*1.2) if merchant else base


def material_sources(material_id):
    from utils.habitats import BIOMES, REGIONAL_MATERIALS, biome_key
    routes = list(dict.fromkeys(b['name'] for key,b in BIOMES.items()
                               if material_id in REGIONAL_MATERIALS[biome_key(key)]))
    raids = {'dragon_scale':'Corrupted Dragon', 'sunforge_residue':'Corrupted Dragon',
             'kraken_ink':'Corrupted Leviathan','fenrir_fur':'Corrupted Fenrir',
             'chronos_gear':'Ancient Chronos','tear_of_the_creator':'any Ancient',
             'blighted_world_core':'Ancient Genesis/Abyss'}
    text = 'Explore: '+', '.join(routes) if routes else ''
    if material_id in raids:
        text += ('; ' if text else '')+'Guaranteed: '+raids[material_id]
    return text
