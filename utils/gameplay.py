"""Transactional grants, bond growth, evolution and incubation time helpers."""
import json
from datetime import datetime, timedelta, timezone
import aiosqlite
from utils import db as storage
STARTER_FORMS = {'prismite': 'prismara', 'twine': 'timestitch', 'gloop': 'nebuloom', 'barkley': 'rootwarden'}
BOND_LEVEL = 15
BOND_REQUIRED = 30

async def equip_perk(user_id,perk_id):
    definitions=storage.load_perks()['perks']
    if perk_id not in definitions:raise ValueError('Unknown perk.')
    async with aiosqlite.connect(storage.DB_PATH) as db:
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

async def grant_material(user_id, material_id, amount=1):
    from utils.artwork import PROJECT_ROOT
    materials = json.loads((PROJECT_ROOT / 'data/materials.json').read_text(encoding='utf-8'))['materials']
    if material_id not in materials:
        raise ValueError(f'Unknown material: {material_id}')
    async with aiosqlite.connect(storage.DB_PATH) as db:
        await db.execute('BEGIN IMMEDIATE')
        await add_stack(db, 'player_materials', user_id, material_id, amount)
        await db.commit()
    from utils.events import record_event
    from cogs.questline import advance_quest_step
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
    async with aiosqlite.connect(storage.DB_PATH) as db:
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
    old_growth = storage.calc_stat_growth({'rarity': before['rarity']}, row['level'] - 1)
    new_growth = storage.calc_stat_growth({'rarity': after['rarity']}, row['level'] - 1)
    names = {'hp': 'max_hp', 'attack': 'attack', 'defense': 'defense', 'speed': 'speed', 'mana': 'max_mana'}
    return {key: new_base[key] + new_growth[key] + max(0, row[col] - old_base[key] - old_growth[key]) for key, col in names.items()}

async def evolve_beast(user_id, player_number):
    from utils.duels import active_fighters
    if user_id in active_fighters:
        raise ValueError('Finish your battle before evolving a companion.')
    beasts = storage.load_beasts()
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, player_number)) as c:
            row = await c.fetchone()
        if not row:
            raise ValueError('That beast is not in your collection.')
        row = dict(row)
        before = beasts[row['beast_id']]
        evo = before.get('evolution')
        if storage.is_knocked_out(row):
            raise ValueError('Let your companion recover before evolving.')
        if not evo:
            raise ValueError(f"{before['name']} has no further evolution.")
        if row['level'] < evo['level_required']:
            raise ValueError(f"Reach beast level {evo['level_required']} first.")
        if evo.get('form') == 'bond':
            if row['bond'] < BOND_REQUIRED:
                raise ValueError(f"Reach {BOND_REQUIRED} bond first ({row['bond']}/{BOND_REQUIRED}). Play, battle and train together.")
            if row['happiness'] < 70:
                raise ValueError('Your companion needs at least 70 happiness to awaken.')
        else:
            method = evo['method']
            cursor = await db.execute('UPDATE player_inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=? AND quantity>0', (user_id, method))
            if not cursor.rowcount and evo.get('form') == 'radiant':
                cursor = await db.execute("UPDATE player_inventory SET quantity=quantity-1 WHERE user_id=? AND item_id='sunforge_core' AND quantity>0", (user_id,))
            if not cursor.rowcount:
                raise ValueError(f"You need {method.replace('_', ' ').title()}. Check /recipes or /ancient.")
            await db.execute('DELETE FROM player_inventory WHERE user_id=? AND quantity<=0', (user_id,))
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
