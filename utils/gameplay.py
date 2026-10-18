"""Transactional grants, bond growth, evolution and incubation time helpers."""
import json
from datetime import datetime, timedelta, timezone
import aiosqlite
from utils import db as storage
from utils.progression import (AWAKENING_LEVEL, AWAKENING_TRAINER_LEVEL,
                               AWAKENING_BOND, AWAKENING_HAPPINESS,
                               companion_growth, evolution_blockers)
STARTER_FORMS = {'prismite': 'prismara', 'twine': 'timestitch', 'gloop': 'nebuloom', 'barkley': 'rootwarden'}
BOND_LEVEL = AWAKENING_LEVEL
BOND_REQUIRED = AWAKENING_BOND

async def equip_perk(user_id,perk_id):
    definitions=storage.load_perks()['perks']
    if perk_id not in definitions:raise ValueError('Unknown perk.')
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT level FROM players WHERE user_id=?',(user_id,)) as c:player=await c.fetchone()
        async with db.execute('SELECT equipped FROM player_perks WHERE user_id=? AND perk_id=?',(user_id,perk_id)) as c:owned=await c.fetchone()
        if not player or not owned:raise ValueError('Earn this perk before equipping it. Check /perks for milestones.')
        if owned[0]:raise ValueError('This perk is already equipped.')
        async with db.execute('SELECT perk_id FROM player_perks WHERE user_id=? AND equipped=1',(user_id,)) as c:active=[r[0] for r in await c.fetchall()]
        used=sum(definitions[key]['slot_cost'] for key in active if key in definitions)
        total=storage.get_perk_slots(player[0]);cost=definitions[perk_id]['slot_cost']
        if used+cost>total:raise ValueError(f'Not enough perk slots: {used}/{total} used; this perk needs {cost}.')
        await db.execute('UPDATE player_perks SET equipped=1 WHERE user_id=? AND perk_id=?',(user_id,perk_id));await db.commit()
    return used+cost,total

async def add_stack(db, table, user_id, key, quantity=1):
    if table not in {'player_inventory', 'player_materials'} or quantity <= 0:
        raise ValueError('Invalid item grant')
    column = 'item_id' if table == 'player_inventory' else 'material_id'
    async with db.execute(f'SELECT id FROM {table} WHERE user_id=? AND {column}=?', (user_id, key)) as c:
        row = await c.fetchone()
    if row:
        await db.execute(f'UPDATE {table} SET quantity=quantity+? WHERE id=?', (quantity, row[0]))
    else:
        await db.execute(f'INSERT INTO {table} (user_id,{column},quantity) VALUES (?,?,?)', (user_id, key, quantity))


async def consume_stack(db, table, user_id, key, quantity=1):
    """Consume an exact amount, including legacy split stacks, inside a writer transaction."""
    if table not in {'player_inventory', 'player_materials'} or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError('Invalid item consumption')
    column = 'item_id' if table == 'player_inventory' else 'material_id'
    async with db.execute(f'SELECT id,quantity FROM {table} WHERE user_id=? AND {column}=? AND quantity>0 ORDER BY id', (user_id, key)) as cursor:
        rows = await cursor.fetchall()
    if sum(row[1] for row in rows) < quantity:
        return False
    remaining = quantity
    for row_id, held in rows:
        paid = min(remaining, held)
        await db.execute(f'UPDATE {table} SET quantity=quantity-? WHERE id=?', (paid, row_id))
        await db.execute(f'DELETE FROM {table} WHERE id=? AND quantity<=0', (row_id,))
        remaining -= paid
        if not remaining:
            break
    return True

async def grant_material(user_id, material_id, amount=1):
    from utils.artwork import PROJECT_ROOT
    materials = json.loads((PROJECT_ROOT / 'data/materials.json').read_text(encoding='utf-8'))['materials']
    if material_id not in materials:
        raise ValueError(f'Unknown material: {material_id}')
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        await add_stack(db, 'player_materials', user_id, material_id, amount)
        await db.commit()
    from utils.events import record_event
    from utils.story import advance_quest_step
    await advance_quest_step(user_id, 'material_gained', material_id=material_id, amount=amount)
    await record_event(user_id, 'material_collect', amount=amount)

async def grant_loot(user_id, item_id, amount=1):
    from utils.artwork import PROJECT_ROOT
    materials = json.loads((PROJECT_ROOT / 'data/materials.json').read_text(encoding='utf-8'))['materials']
    if item_id in materials:
        return await grant_material(user_id, item_id, amount)
    from cogs.world import EGGS
    if item_id not in storage.load_items() and item_id not in EGGS:
        raise ValueError(f'Undefined loot: {item_id}')
    await storage.add_item(user_id, item_id, amount)

async def bond_with(user_id, beast_row_id=None, points=1):
    async with storage.connect() as db:
        if beast_row_id is None:
            await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?) WHERE user_id=? AND is_active=1', (points, user_id))
        else:
            await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?) WHERE user_id=? AND id=?', (points, user_id, beast_row_id))
        await db.commit()

def evolved_stats(row, before, after):
    """Carry every earned stat point, training investment and disposition forward."""
    from utils.dispositions import apply_disposition
    old_base = apply_disposition(before['base_stats'], row.get('disposition'))
    new_base = apply_disposition(after['base_stats'], row.get('disposition'))
    catalog = storage.load_beasts()
    old_growth = companion_growth(before['id'], row['level'], catalog)
    new_growth = companion_growth(after['id'], row['level'], catalog)
    names = {'hp': 'max_hp', 'attack': 'attack', 'defense': 'defense', 'speed': 'speed', 'mana': 'max_mana'}
    return {key: new_base[key] + new_growth[key] + max(0, row[col] - old_base[key] - old_growth[key]) for key, col in names.items()}

async def evolve_beast(user_id, player_number, *, expected_row_id=None, expected_species=None, required_item=None):
    from utils.duels import reserve_fighters
    with reserve_fighters([user_id]):
        return await _evolve_beast(user_id, player_number, expected_row_id=expected_row_id,
                                   expected_species=expected_species, required_item=required_item)

async def _evolve_beast(user_id, player_number, *, expected_row_id=None, expected_species=None, required_item=None):
    beasts = storage.load_beasts()
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, player_number)) as c:
            row = await c.fetchone()
        if not row:
            raise ValueError('That beast is not in your collection.')
        row = dict(row)
        if (expected_row_id is not None and row['id'] != expected_row_id or
                expected_species is not None and row['beast_id'] != expected_species):
            raise ValueError('That companion changed. Review its evolution again.')
        async with db.execute('SELECT 1 FROM beast_market WHERE beast_row_id=?', (row['id'],)) as c:
            if await c.fetchone():
                raise ValueError('Delist this companion before evolving it.')
        before = beasts[row['beast_id']]
        evo = before.get('evolution')
        if storage.is_knocked_out(row):
            raise ValueError('Let your companion recover before evolving.')
        if not evo:
            raise ValueError(f"{before['name']} has no further evolution.")
        if required_item is not None and evo.get('method') != required_item:
            raise ValueError('The selected catalyst does not match this companion’s next form.')
        async with db.execute('SELECT level FROM players WHERE user_id=?', (user_id,)) as c:
            trainer = await c.fetchone()
        blockers = evolution_blockers(row, trainer['level'] if trainer else 0, evo)
        if blockers:
            raise ValueError(blockers[0])
        if evo.get('form') != 'bond':
            method = evo['method']
            consumed = await consume_stack(db, 'player_inventory', user_id, method)
            if not consumed and evo.get('form') == 'radiant' and required_item is None:
                consumed = await consume_stack(db, 'player_inventory', user_id, 'sunforge_core')
            if not consumed:
                raise ValueError(f"You need {method.replace('_', ' ').title()}. Check /craft or /ancient.")
        after = beasts[evo['evolves_to']]
        stats = evolved_stats(row, before, after)
        await db.execute('UPDATE player_beasts SET beast_id=?,rarity=?,hp=?,max_hp=?,attack=?,defense=?,speed=?,mana=?,max_mana=? WHERE id=?', (after['id'], after['rarity'], stats['hp'], stats['hp'], stats['attack'], stats['defense'], stats['speed'], stats['mana'], stats['mana'], row['id']))
        await db.commit()
    from utils.progress import unlock_simple_achievement, check_achievements
    await unlock_simple_achievement(user_id, 'first_evolution')
    if evo.get('form') in {'radiant', 'ascended'}:
        await unlock_simple_achievement(user_id, 'first_' + evo['form'])
    await check_achievements(user_id)
    return (before, after, stats)

async def shorten_incubation(db, user_id, *, hours=0, percent=0):
    db.row_factory = aiosqlite.Row
    async with db.execute('SELECT * FROM incubating_eggs WHERE user_id=? AND hatched=0 ORDER BY started_at,id LIMIT 1', (user_id,)) as c:
        egg = await c.fetchone()
    if not egg:
        raise ValueError('Start an egg with /incubate first; nothing has been consumed.')
    from cogs.world import EGGS
    now = datetime.now(timezone.utc)
    parse = lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    deadline = parse(egg['ready_at'])
    remaining = max(0, (deadline - now).total_seconds())
    seconds = min(remaining, hours * 3600 + remaining * percent / 100)
    if seconds <= 0:
        raise ValueError('That egg is already ready. Use /tend to hatch it.')
    new_ready = deadline - timedelta(seconds=seconds)
    next_time = parse(egg['next_tend_at']) if egg['next_tend_at'] else now
    next_time = max(now, next_time - timedelta(seconds=seconds))
    await db.execute('UPDATE incubating_eggs SET ready_at=?,next_tend_at=? WHERE id=?', (new_ready.strftime('%Y-%m-%d %H:%M:%S'), next_time.strftime('%Y-%m-%d %H:%M:%S'), egg['id']))
    return f"{egg['egg_name']} is {seconds / 3600:.1f} hours closer. Tending and hatching use the new deadline."
