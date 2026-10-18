"""Own and equip one armor piece and one rune without losing other copies."""
import aiosqlite
from utils import db as storage
from utils.gameplay import add_stack
from utils.duels import exclusive_player
from utils.economy_balance import recipe_level
_UNSET = object()

def definitions():
    from utils.catalog import load_catalog
    data=load_catalog('equipment.json')
    return {**data['equipment'],**data['runes']}


def dormant_label(item, trainer_level):
    required = recipe_level(item)
    return f'Dormant until Trainer Lv.{required}' if trainer_level < required else ''


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
async def equip(user_id, number, gear_id, *, replace=False, expected_row_id=None,
                expected_species=None, expected_equipment=_UNSET):
    catalog = definitions()
    gear = catalog[gear_id]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, number)) as c:
            beast = await c.fetchone()
        if not beast:
            raise ValueError('That companion is not in your collection.')
        async with db.execute('SELECT level FROM players WHERE user_id=?', (user_id,)) as cursor:
            player = await cursor.fetchone()
        if player is None or player['level'] < recipe_level(gear):
            raise ValueError(f"Reach trainer level {recipe_level(gear)} to equip {gear['name']}.")
        if (expected_row_id is not None and beast['id'] != expected_row_id or
                expected_species is not None and beast['beast_id'] != expected_species):
            raise ValueError('That companion changed. Review the updated selection before equipping.')
        async with db.execute('SELECT 1 FROM beast_market WHERE beast_row_id=?', (beast['id'],)) as c:
            if await c.fetchone():
                raise ValueError('Delist this companion before changing its equipment.')
        async with db.execute('SELECT id,equipment_id FROM player_equipment WHERE user_id=? AND beast_row_id=? ORDER BY id', (user_id, beast['id'])) as c:
            bound = await c.fetchall()
        legacy_runes = [(row['id'], row['equipment_id']) for row in bound if catalog.get(row['equipment_id'], {}).get('slot') == 'rune']
        if gear.get('slot') == 'rune':
            previous = beast['rune_id'] or (legacy_runes[0][1] if legacy_runes else None)
        else:
            previous = next((row['equipment_id'] for row in bound if row['equipment_id'] in catalog and catalog[row['equipment_id']].get('slot') != 'rune'), None)
        if expected_equipment is not _UNSET and previous != expected_equipment:
            raise ValueError('This companion’s equipment changed. Review it before replacing anything.')
        if previous == gear_id:
            raise ValueError('This companion already has that gear equipped.')
        if gear.get('slot') == 'rune':
            if previous and not replace:
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
            # A legacy equipped row and the matching rune_id describe one copy.
            returned = [key for _, key in legacy_runes]
            if beast['rune_id'] and beast['rune_id'] not in returned:
                returned.append(beast['rune_id'])
            for key in returned:
                await add_stack(db, 'player_inventory', user_id, key)
            for row_id, _ in legacy_runes:
                await db.execute('DELETE FROM player_equipment WHERE id=?', (row_id,))
            await db.execute('UPDATE player_beasts SET rune_id=? WHERE id=?', (gear_id, beast['id']))
        else:
            async with db.execute('SELECT id FROM player_equipment WHERE user_id=? AND equipment_id=? AND beast_row_id IS NULL', (user_id, gear_id)) as c:
                owned = await c.fetchone()
            if not owned:
                raise ValueError('Craft an unequipped copy of this armor first.')
            for row in bound:
                if row['equipment_id'] in catalog and catalog[row['equipment_id']].get('slot') != 'rune':
                    await db.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE id=?', (row['id'],))
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
        async with db.execute('SELECT equipment_id FROM player_equipment WHERE user_id=? AND beast_row_id=?', (user_id, beast['id'])) as c:
            ids = [r[0] for r in await c.fetchall()]
        if beast['rune_id']:
            if beast['rune_id'] not in ids:
                await add_stack(db, 'player_inventory', user_id, beast['rune_id'])
                removed.append(gear.get(beast['rune_id'], {}).get('name', beast['rune_id']))
            await db.execute('UPDATE player_beasts SET rune_id=NULL WHERE id=?', (beast['id'],))
        removed.extend((gear.get(key, {}).get('name', key) for key in ids))
        await db.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE user_id=? AND beast_row_id=?', (user_id, beast['id']))
        await db.commit()
    return removed
