"""Own and equip one armor piece and one rune without losing other copies."""
import aiosqlite
from utils import db as storage
from utils.gameplay import add_stack
from utils.duels import exclusive_player

def definitions():
    from utils.catalog import load_catalog
    data=load_catalog('equipment.json')
    return {**data['equipment'],**data['runes']}


def effect_summary(item):
    labels = {'attack':'ATK','defense':'DEF','speed':'SPD','hp_flat':'HP',
              'attack_percent':'ATK','defense_percent':'DEF','speed_percent':'SPD','hp_percent':'max HP',
              'crit_chance':'crit chance','water_attack_percent':'water-type ATK',
              'water_resist_percent':'water damage resistance','reflect_damage_percent':'damage reflected',
              'evasion_percent':'evasion','damage_reduction_flat_percent':'damage reduction',
              'lifesteal_percent':'lifesteal','hp_regen_percent_per_round':'max HP restored each action',
              'mana_regen_on_hit':'MP restored on hit','burn_on_hit_chance':'burn chance on hit',
              'stun_on_hit_chance':'freeze chance on hit','blight_on_hit_chance':'blight chance on hit',
              'petrify_on_crit_chance':'freeze chance on crit','thorns_poison_on_hit_chance':'poison chance when struck',
              'speed_debuff_on_battle_start_chance':'slow chance at battle start'}
    special = {'burn_immunity':'Burn immunity','crit_immunity':'Crit immunity',
               'attack_double_below_25hp':'Double ATK at 25% HP or lower',
               'time_rewind_on_fatal_once':'Once per battle: survive a fatal hit at 30% HP',
               'death_explosion_fire':'On KO: explosion for 30% of your maximum HP'}
    flat = {'attack','defense','speed','hp_flat','mana_regen_on_hit'}
    parts=[]
    for key,value in item.get('effect',{}).items():
        if key in special:
            if value:parts.append(special[key])
        elif key in labels:
            parts.append(f"{value:+g}{'' if key in flat else '%'} {labels[key]}")
        else:
            raise ValueError(f'Unlabelled gear effect: {key}')
    return ' · '.join(parts)

@exclusive_player
async def equip(user_id, number, gear_id):
    gear = definitions()[gear_id]
    async with storage.connect() as db:
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

@exclusive_player
async def unequip(user_id, number):
    gear = definitions()
    removed = []
    async with storage.connect() as db:
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
