"""Apply base-stat deltas once, preserving disposition and earned stats."""
import aiosqlite
from utils.dispositions import apply_disposition

REVISION = 'combat-base-stats-2026-10'
BASE_STAT_CHANGES = {
    'prismite': ({'hp': 85, 'attack': 35, 'defense': 50, 'speed': 45, 'mana': 70},
                 {'hp': 90, 'attack': 40, 'defense': 50, 'speed': 45, 'mana': 70}),
    'twine': ({'hp': 70, 'attack': 40, 'defense': 30, 'speed': 70, 'mana': 65},
              {'hp': 85, 'attack': 45, 'defense': 35, 'speed': 80, 'mana': 65}),
    'gloop': ({'hp': 105, 'attack': 30, 'defense': 40, 'speed': 35, 'mana': 60},
              {'hp': 110, 'attack': 35, 'defense': 45, 'speed': 35, 'mana': 60}),
    'barkley': ({'hp': 115, 'attack': 40, 'defense': 55, 'speed': 20, 'mana': 35},
                {'hp': 110, 'attack': 35, 'defense': 55, 'speed': 20, 'mana': 55}),
    'goblin': ({'hp': 100, 'attack': 45, 'defense': 40, 'speed': 35, 'mana': 40},
               {'hp': 100, 'attack': 45, 'defense': 40, 'speed': 35, 'mana': 55}),
    'slime': ({'hp': 110, 'attack': 30, 'defense': 55, 'speed': 25, 'mana': 40},
              {'hp': 110, 'attack': 30, 'defense': 55, 'speed': 25, 'mana': 55}),
    'centry': ({'hp': 115, 'attack': 60, 'defense': 55, 'speed': 50, 'mana': 50},
               {'hp': 115, 'attack': 60, 'defense': 55, 'speed': 50, 'mana': 55}),
    'mudpup': ({'hp': 85, 'attack': 45, 'defense': 40, 'speed': 40, 'mana': 50},
               {'hp': 85, 'attack': 45, 'defense': 40, 'speed': 40, 'mana': 55}),
    'manticore': ({'hp': 100, 'attack': 70, 'defense': 40, 'speed': 65, 'mana': 50},
                  {'hp': 100, 'attack': 70, 'defense': 40, 'speed': 65, 'mana': 55}),
}


async def apply_base_stat_balance(db):
    async with db.execute('SELECT 1 FROM game_meta WHERE key=?', (REVISION,)) as cursor:
        if await cursor.fetchone():
            return
    await db.execute('BEGIN IMMEDIATE')
    async with db.execute('SELECT 1 FROM game_meta WHERE key=?', (REVISION,)) as cursor:
        if await cursor.fetchone():
            return
    previous_factory = db.row_factory
    try:
        db.row_factory = aiosqlite.Row
        for key, (old, new) in BASE_STAT_CHANGES.items():
            async with db.execute('SELECT * FROM player_beasts WHERE beast_id=?', (key,)) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                before = apply_disposition(old, row['disposition'])
                after = apply_disposition(new, row['disposition'])
                delta = {stat: after[stat]-before[stat] for stat in before}
                maximum = max(1, row['max_hp']+delta['hp'])
                hp = 0 if row['hp'] <= 0 else max(1, min(maximum, row['hp']+delta['hp']))
                max_mana = max(1, row['max_mana'] + delta['mana'])
                mana = min(max_mana, max(0, row['mana'] + delta['mana']))
                await db.execute('UPDATE player_beasts SET max_hp=?,hp=?,max_mana=?,mana=?,attack=MAX(1,attack+?),defense=MAX(1,defense+?),speed=MAX(1,speed+?) WHERE id=?',
                                 (maximum, hp, max_mana, mana, delta['attack'], delta['defense'], delta['speed'], row['id']))
        await db.execute('INSERT INTO game_meta(key,value) VALUES(?,?)', (REVISION, 'applied'))
        await db.commit()
    finally:
        db.row_factory = previous_factory
