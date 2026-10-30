"""Workshop reads and atomic crafts shared by slash commands and buttons."""
from collections import Counter
from dataclasses import dataclass

import aiosqlite

from utils import db as storage
from utils.catalog import load_catalog
from utils.economy_balance import recipe_level
from utils.gameplay import add_stack, consume_stack
from utils.sanctuary import apply_craft_discount

SECTIONS = ('armor', 'runes', 'evolution')
RARITIES = ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine', 'altered_divine')


def recipes():
    gear = load_catalog('equipment.json')
    groups = (gear['equipment'], gear['runes'],
              {key: item for key, item in storage.load_items().items() if item.get('recipe')})
    return {key: {**item, 'id': key, 'section': section}
            for section, items in zip(SECTIONS, groups) for key, item in items.items()}


def recipe_ids(section):
    return tuple(key for key, item in sorted(recipes().items(), key=lambda pair:
        RARITIES.index(pair[1]['rarity'])) if item['section'] == section)


def resolve_recipe(value):
    value = value.strip()
    if not value:
        return None
    catalog = recipes()
    key = value.lower().replace(' ', '_').replace('-', '_')
    if key in catalog:
        return key
    return next((key for key, item in catalog.items() if value.lower() in item['name'].lower()), None)


async def sanctuary_in_transaction(db, user_id):
    async with db.execute('SELECT gs.* FROM guild_members gm JOIN guild_sanctuary gs '
                          'ON gm.guild_id=gs.guild_id WHERE gm.user_id=?', (user_id,)) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else {}


@dataclass
class WorkshopState:
    level: int
    materials: dict
    owned: dict
    sanctuary: dict
    companions: list


async def workshop_state(user_id):
    """One consistent, read-only snapshot; it never opens a writer transaction."""
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN')
        async with db.execute('SELECT level FROM players WHERE user_id=?', (user_id,)) as cursor:
            trainer = await cursor.fetchone()
        if not trainer:
            raise ValueError('Begin your journey with /start before opening the workshop.')
        async with db.execute('SELECT material_id,SUM(quantity) AS quantity FROM player_materials '
                              'WHERE user_id=? AND quantity>0 GROUP BY material_id', (user_id,)) as cursor:
            materials = {row['material_id']: row['quantity'] for row in await cursor.fetchall()}
        async with db.execute('SELECT item_id,SUM(quantity) AS quantity FROM player_inventory '
                              'WHERE user_id=? AND quantity>0 GROUP BY item_id', (user_id,)) as cursor:
            owned = Counter({row['item_id']: row['quantity'] for row in await cursor.fetchall()})
        async with db.execute('SELECT equipment_id,beast_row_id FROM player_equipment WHERE user_id=? ORDER BY id', (user_id,)) as cursor:
            equipment = await cursor.fetchall()
        gear = load_catalog('equipment.json')
        armor, legacy_runes = {}, {}
        for row in equipment:
            if row['beast_row_id'] is None:
                owned[row['equipment_id']] += 1
            elif row['equipment_id'] in gear['equipment']:
                armor.setdefault(row['beast_row_id'], row['equipment_id'])
            elif row['equipment_id'] in gear['runes']:
                legacy_runes.setdefault(row['beast_row_id'], row['equipment_id'])
        async with db.execute('SELECT pb.*,EXISTS(SELECT 1 FROM beast_market bm WHERE bm.beast_row_id=pb.id) AS listed '
                              'FROM player_beasts pb WHERE pb.user_id=? ORDER BY pb.player_number,pb.id', (user_id,)) as cursor:
            companions = [{**dict(row), 'armor_id': armor.get(row['id']),
                'rune_id': row['rune_id'] or legacy_runes.get(row['id'])} for row in await cursor.fetchall()]
        from utils import cosmetics
        dressed = await cosmetics.equipped(db, user_id)
        for member in companions:
            member['cosmetic_id'] = dressed.get(member['id'])
            member['appearance_id'] = cosmetics.appearance_id(member['cosmetic_id']) or member['armor_id'] or ''
        sanctuary = await sanctuary_in_transaction(db, user_id)
    return WorkshopState(trainer['level'], materials, dict(owned), sanctuary, companions)


@dataclass(frozen=True)
class CraftResult:
    item: dict
    costs: dict
    quests: list


async def craft_item(user_id, item_id):
    item = recipes().get(item_id)
    if not item:
        raise ValueError('That recipe is no longer available. Refresh the workshop.')
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT level FROM players WHERE user_id=?', (user_id,)) as cursor:
            trainer = await cursor.fetchone()
        if not trainer or trainer['level'] < recipe_level(item):
            raise ValueError(f"Reach trainer level {recipe_level(item)} to craft {item['name']}.")
        costs = apply_craft_discount(item['recipe'], await sanctuary_in_transaction(db, user_id))
        for material, amount in costs.items():
            if not await consume_stack(db, 'player_materials', user_id, material, amount):
                label = load_catalog('materials.json')['materials'][material]['name']
                raise ValueError(f'Not enough {label}. Your materials have been refreshed.')
        await db.execute('DELETE FROM player_materials WHERE user_id=? AND quantity<=0', (user_id,))
        if item['section'] == 'armor':
            await db.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(?,NULL,?)', (user_id, item_id))
        else:
            await add_stack(db, 'player_inventory', user_id, item_id)
        await db.commit()
    from utils.progress import track_quest_event, unlock_simple_achievement
    quests = await track_quest_event(user_id, 'craft')
    await unlock_simple_achievement(user_id, 'first_craft')
    if item['section'] == 'armor' and item['rarity'] == 'legendary':
        await unlock_simple_achievement(user_id, 'first_legendary_gear')
    return CraftResult(item, costs, quests)
