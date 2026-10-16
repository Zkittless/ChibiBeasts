"""Own and equip one armor piece and one rune without losing other copies."""
import json
import aiosqlite
from utils import db as storage
from utils.artwork import PROJECT_ROOT
from utils.gameplay import add_stack

def definitions():
    data = json.loads((PROJECT_ROOT / 'data/equipment.json').read_text(encoding='utf-8'))
    return {**data['equipment'], **data['runes']}

async def equip(user_id, number, gear_id):
    from utils.duels import active_fighters
    if user_id in active_fighters:
        raise ValueError('Finish your battle before changing gear.')
    gear = definitions()[gear_id]
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, number)) as c:
            beast = await c.fetchone()
        if not beast:
            raise ValueError('That companion is not in your collection.')
        if gear.get('slot') == 'rune':
            if beast['rune_id']:
                raise ValueError('Unequip the current rune first.')
            async with db.execute('SELECT id FROM player_inventory WHERE user_id=? AND item_id=? AND quantity>0', (user_id, gear_id)) as c:
                owned = await c.fetchone()
            if owned:
                await db.execute('UPDATE player_inventory SET quantity=quantity-1 WHERE id=?', (owned['id'],))
                await db.execute('DELETE FROM player_inventory WHERE id=? AND quantity<=0', (owned['id'],))
            else:
                async with db.execute('SELECT id FROM player_equipment WHERE user_id=? AND equipment_id=? AND beast_row_id IS NULL', (user_id, gear_id)) as c:
                    legacy = await c.fetchone()
                if not legacy:
                    raise ValueError('Craft this rune with /craft first.')
                await db.execute('DELETE FROM player_equipment WHERE id=?', (legacy['id'],))
            await db.execute('UPDATE player_beasts SET rune_id=? WHERE id=?', (gear_id, beast['id']))
        else:
            async with db.execute('SELECT id FROM player_equipment WHERE user_id=? AND equipment_id=? AND beast_row_id IS NULL', (user_id, gear_id)) as c:
                owned = await c.fetchone()
            if not owned:
                raise ValueError('Craft an unequipped copy of this armor first.')
            await db.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE user_id=? AND beast_row_id=?', (user_id, beast['id']))
            await db.execute('UPDATE player_equipment SET beast_row_id=? WHERE id=?', (beast['id'], owned['id']))
        await db.commit()
    return (dict(beast), gear)

async def unequip(user_id, number):
    from utils.duels import active_fighters
    if user_id in active_fighters:
        raise ValueError('Finish your battle before changing gear.')
    gear = definitions()
    removed = []
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, number)) as c:
            beast = await c.fetchone()
        if not beast:
            raise ValueError('That companion is not in your collection.')
        if beast['rune_id']:
            await add_stack(db, 'player_inventory', user_id, beast['rune_id'])
            removed.append(gear.get(beast['rune_id'], {}).get('name', beast['rune_id']))
            await db.execute('UPDATE player_beasts SET rune_id=NULL WHERE id=?', (beast['id'],))
        async with db.execute('SELECT equipment_id FROM player_equipment WHERE user_id=? AND beast_row_id=?', (user_id, beast['id'])) as c:
            ids = [r[0] for r in await c.fetchall()]
        removed.extend((gear.get(key, {}).get('name', key) for key in ids))
        await db.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE user_id=? AND beast_row_id=?', (user_id, beast['id']))
        await db.commit()
    return removed
