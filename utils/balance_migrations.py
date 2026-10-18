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


# Frozen pre-rebalance catalog/growth: never infer earned investment from new stats.
PROGRESSION_REVISION = 'progression-pacing-2026-10-v1'
LEGACY_CATALOG = {
    'fairy': ('common', {'hp': 80, 'attack': 35, 'defense': 30, 'speed': 55, 'mana': 60}),
    'pixie': ('common', {'hp': 75, 'attack': 40, 'defense': 25, 'speed': 60, 'mana': 65}),
    'goblin': ('common', {'hp': 100, 'attack': 45, 'defense': 40, 'speed': 35, 'mana': 55}),
    'imp': ('common', {'hp': 70, 'attack': 50, 'defense': 20, 'speed': 65, 'mana': 55}),
    'slime': ('common', {'hp': 110, 'attack': 30, 'defense': 55, 'speed': 25, 'mana': 55}),
    'kitsune_1': ('uncommon', {'hp': 90, 'attack': 55, 'defense': 40, 'speed': 70, 'mana': 75}),
    'dryad': ('uncommon', {'hp': 105, 'attack': 50, 'defense': 60, 'speed': 40, 'mana': 70}),
    'hellhound': ('uncommon', {'hp': 95, 'attack': 65, 'defense': 45, 'speed': 60, 'mana': 55}),
    'harpy': ('uncommon', {'hp': 85, 'attack': 60, 'defense': 35, 'speed': 75, 'mana': 65}),
    'unicorn': ('rare', {'hp': 110, 'attack': 65, 'defense': 60, 'speed': 75, 'mana': 90}),
    'pegasus': ('rare', {'hp': 105, 'attack': 70, 'defense': 55, 'speed': 90, 'mana': 75}),
    'griffin': ('rare', {'hp': 120, 'attack': 75, 'defense': 65, 'speed': 70, 'mana': 65}),
    'basilisk': ('rare', {'hp': 130, 'attack': 70, 'defense': 75, 'speed': 40, 'mana': 70}),
    'cerberus': ('rare', {'hp': 140, 'attack': 80, 'defense': 70, 'speed': 55, 'mana': 60}),
    'wyvern': ('rare', {'hp': 115, 'attack': 80, 'defense': 60, 'speed': 75, 'mana': 65}),
    'phoenix': ('epic', {'hp': 130, 'attack': 90, 'defense': 65, 'speed': 85, 'mana': 95}),
    'thunderbird': ('epic', {'hp': 125, 'attack': 95, 'defense': 60, 'speed': 90, 'mana': 90}),
    'sphinx': ('epic', {'hp': 135, 'attack': 85, 'defense': 80, 'speed': 65, 'mana': 110}),
    'yeti': ('epic', {'hp': 160, 'attack': 90, 'defense': 85, 'speed': 45, 'mana': 70}),
    'minotaur': ('epic', {'hp': 155, 'attack': 100, 'defense': 80, 'speed': 50, 'mana': 65}),
    'kitsune_9': ('epic', {'hp': 140, 'attack': 95, 'defense': 75, 'speed': 100, 'mana': 120}),
    'dragon': ('legendary', {'hp': 180, 'attack': 120, 'defense': 100, 'speed': 80, 'mana': 110}),
    'kraken': ('legendary', {'hp': 200, 'attack': 115, 'defense': 110, 'speed': 55, 'mana': 100}),
    'hydra': ('legendary', {'hp': 220, 'attack': 110, 'defense': 105, 'speed': 50, 'mana': 95}),
    'fenrir': ('legendary', {'hp': 190, 'attack': 125, 'defense': 95, 'speed': 90, 'mana': 100}),
    'charybdis': ('legendary', {'hp': 185, 'attack': 110, 'defense': 100, 'speed': 70, 'mana': 115}),
    'leviathan': ('legendary', {'hp': 210, 'attack': 120, 'defense': 110, 'speed': 60, 'mana': 105}),
    'singularity': ('divine', {'hp': 250, 'attack': 140, 'defense': 130, 'speed': 100, 'mana': 150}),
    'astraea': ('divine', {'hp': 240, 'attack': 145, 'defense': 120, 'speed': 115, 'mana': 155}),
    'chronos': ('divine', {'hp': 245, 'attack': 135, 'defense': 135, 'speed': 130, 'mana': 160}),
    'aetherius': ('divine', {'hp': 280, 'attack': 130, 'defense': 145, 'speed': 85, 'mana': 150}),
    'genesis': ('divine', {'hp': 255, 'attack': 150, 'defense': 125, 'speed': 110, 'mana': 155}),
    'terminus': ('divine', {'hp': 245, 'attack': 140, 'defense': 140, 'speed': 105, 'mana': 160}),
    'paradox': ('divine', {'hp': 250, 'attack': 145, 'defense': 130, 'speed': 120, 'mana': 165}),
    'horizon': ('divine', {'hp': 255, 'attack': 140, 'defense': 135, 'speed': 115, 'mana': 150}),
    'karma': ('divine', {'hp': 240, 'attack': 135, 'defense': 130, 'speed': 125, 'mana': 160}),
    'zodiac': ('divine', {'hp': 260, 'attack': 138, 'defense': 138, 'speed': 108, 'mana': 155}),
    'abyss': ('divine', {'hp': 255, 'attack': 150, 'defense': 125, 'speed': 120, 'mana': 150}),
    'nebula': ('divine', {'hp': 250, 'attack': 140, 'defense': 135, 'speed': 110, 'mana': 160}),
    'supernova': ('divine', {'hp': 245, 'attack': 155, 'defense': 120, 'speed': 115, 'mana': 155}),
    'nirvana': ('divine', {'hp': 240, 'attack': 130, 'defense': 145, 'speed': 105, 'mana': 170}),
    'asgard': ('divine', {'hp': 265, 'attack': 145, 'defense': 140, 'speed': 105, 'mana': 145}),
    'atlas': ('divine', {'hp': 300, 'attack': 120, 'defense': 170, 'speed': 60, 'mana': 145}),
    'prismite': ('common', {'hp': 90, 'attack': 40, 'defense': 50, 'speed': 45, 'mana': 70}),
    'twine': ('common', {'hp': 85, 'attack': 45, 'defense': 35, 'speed': 80, 'mana': 65}),
    'gloop': ('common', {'hp': 110, 'attack': 35, 'defense': 45, 'speed': 35, 'mana': 60}),
    'barkley': ('common', {'hp': 110, 'attack': 35, 'defense': 55, 'speed': 20, 'mana': 55}),
    'centry': ('uncommon', {'hp': 115, 'attack': 60, 'defense': 55, 'speed': 50, 'mana': 55}),
    'wisp': ('common', {'hp': 65, 'attack': 40, 'defense': 25, 'speed': 75, 'mana': 60}),
    'mudpup': ('common', {'hp': 85, 'attack': 45, 'defense': 40, 'speed': 40, 'mana': 55}),
    'strix': ('common', {'hp': 60, 'attack': 50, 'defense': 20, 'speed': 70, 'mana': 65}),
    'kelpie': ('uncommon', {'hp': 95, 'attack': 65, 'defense': 45, 'speed': 70, 'mana': 55}),
    'manticore': ('uncommon', {'hp': 100, 'attack': 70, 'defense': 40, 'speed': 65, 'mana': 55}),
    'will_o_wisp': ('uncommon', {'hp': 90, 'attack': 60, 'defense': 35, 'speed': 85, 'mana': 60}),
    'rukh': ('rare', {'hp': 130, 'attack': 80, 'defense': 65, 'speed': 80, 'mana': 60}),
    'glacius': ('rare', {'hp': 160, 'attack': 75, 'defense': 95, 'speed': 35, 'mana': 55}),
    'behemoth': ('epic', {'hp': 220, 'attack': 90, 'defense': 130, 'speed': 25, 'mana': 60}),
    'wendigo': ('epic', {'hp': 170, 'attack': 120, 'defense': 75, 'speed': 95, 'mana': 80}),
    'jormungandr': ('legendary', {'hp': 205, 'attack': 115, 'defense': 100, 'speed': 70, 'mana': 110}),
    'simurgh': ('legendary', {'hp': 185, 'attack': 105, 'defense': 95, 'speed': 95, 'mana': 125}),
    'void_chronos': ('altered_divine', {'hp': 306, 'attack': 168, 'defense': 168, 'speed': 162, 'mana': 200}),
    'fractured_genesis': ('altered_divine', {'hp': 318, 'attack': 187, 'defense': 156, 'speed': 137, 'mana': 193}),
    'abyssal_nebula': ('altered_divine', {'hp': 318, 'attack': 187, 'defense': 156, 'speed': 150, 'mana': 187}),
    'corrupted_leviathan': ('corrupted', {'hp': 273, 'attack': 156, 'defense': 143, 'speed': 78, 'mana': 136}),
    'corrupted_fenrir': ('corrupted', {'hp': 247, 'attack': 162, 'defense': 123, 'speed': 117, 'mana': 130}),
    'corrupted_dragon': ('corrupted', {'hp': 234, 'attack': 156, 'defense': 130, 'speed': 104, 'mana': 143}),
    'ancient_chronos': ('ancient', {'hp': 367, 'attack': 202, 'defense': 202, 'speed': 195, 'mana': 240}),
    'ancient_genesis': ('ancient', {'hp': 382, 'attack': 225, 'defense': 187, 'speed': 165, 'mana': 232}),
    'ancient_abyss': ('ancient', {'hp': 382, 'attack': 225, 'defense': 187, 'speed': 180, 'mana': 225}),
    'ouroboros': ('dev', {'hp': 99999, 'attack': 9999, 'defense': 9999, 'speed': 9999, 'mana': 9999}),
    'ascended_slime': ('divine', {'hp': 260, 'attack': 145, 'defense': 170, 'speed': 60, 'mana': 100}),
    'ascended_unicorn': ('divine', {'hp': 250, 'attack': 140, 'defense': 140, 'speed': 110, 'mana': 130}),
    'ascended_pegasus': ('divine', {'hp': 235, 'attack': 150, 'defense': 115, 'speed': 160, 'mana': 110}),
    'ascended_phoenix': ('divine', {'hp': 265, 'attack': 165, 'defense': 120, 'speed': 120, 'mana': 140}),
    'radiant_goblin': ('epic', {'hp': 190, 'attack': 130, 'defense': 110, 'speed': 75, 'mana': 80}),
    'radiant_imp': ('rare', {'hp': 130, 'attack': 100, 'defense': 55, 'speed': 120, 'mana': 90}),
    'radiant_hydra': ('divine', {'hp': 310, 'attack': 145, 'defense': 150, 'speed': 70, 'mana': 100}),
    'radiant_kitsune': ('divine', {'hp': 255, 'attack': 150, 'defense': 130, 'speed': 175, 'mana': 145}),
    'frost_sprite': ('common', {'hp': 85, 'attack': 40, 'defense': 45, 'speed': 50, 'mana': 60}),
    'permafrost': ('uncommon', {'hp': 140, 'attack': 45, 'defense': 95, 'speed': 20, 'mana': 55}),
    'abyssal_eel': ('epic', {'hp': 155, 'attack': 110, 'defense': 75, 'speed': 115, 'mana': 110}),
    'loom_fragment': ('rare', {'hp': 130, 'attack': 75, 'defense': 80, 'speed': 85, 'mana': 120}),
    'prismara': ('divine', {'hp': 225, 'attack': 140, 'defense': 145, 'speed': 110, 'mana': 145}),
    'timestitch': ('divine', {'hp': 205, 'attack': 140, 'defense': 110, 'speed': 165, 'mana': 150}),
    'nebuloom': ('divine', {'hp': 270, 'attack': 135, 'defense': 140, 'speed': 100, 'mana': 145}),
    'rootwarden': ('divine', {'hp': 290, 'attack': 130, 'defense': 165, 'speed': 80, 'mana': 130}),
}
LEGACY_GROWTH = {
    'common': {'hp': 4, 'attack': 1, 'defense': 1, 'speed': 1, 'mana': 2},
    'uncommon': {'hp': 5, 'attack': 2, 'defense': 1, 'speed': 1, 'mana': 2},
    'rare': {'hp': 6, 'attack': 2, 'defense': 2, 'speed': 2, 'mana': 3},
    'epic': {'hp': 8, 'attack': 3, 'defense': 2, 'speed': 2, 'mana': 4},
    'legendary': {'hp': 10, 'attack': 4, 'defense': 3, 'speed': 3, 'mana': 5},
    'divine': {'hp': 12, 'attack': 5, 'defense': 4, 'speed': 4, 'mana': 6},
    'altered_divine': {'hp': 15, 'attack': 6, 'defense': 5, 'speed': 5, 'mana': 7},
    'corrupted': {'hp': 18, 'attack': 7, 'defense': 6, 'speed': 6, 'mana': 8},
    'ancient': {'hp': 20, 'attack': 8, 'defense': 7, 'speed': 7, 'mana': 9},
    'dev': {'hp': 50, 'attack': 20, 'defense': 20, 'speed': 20, 'mana': 20},
}


def rebase_companion(row, catalog):
    """Replace only automatic catalog/growth stats, retaining earned extras.

    Legacy saves have no per-level rarity history. Their old evolution service
    explicitly recomputed every prior level at the destination rarity, so the
    frozen legacy baseline is exact for normally evolved saves.
    """
    from utils.progression import MAX_LEVEL, companion_growth, rarity_growth
    species = row['beast_id']
    previous_level = max(1, int(row['level'] or 1))
    level = min(MAX_LEVEL, previous_level)
    if species not in catalog or species not in LEGACY_CATALOG:
        # An unknown custom species has no safe catalog baseline. Only remove
        # automatic growth above the cap; leave its identity and rarity alone.
        removed = rarity_growth(row['rarity'], max(0, previous_level - level))
        stats = {stat: max(1, int(row[column]) - removed[stat])
                 for stat, column in STAT_COLUMNS.items()}
        return level, row['rarity'], stats
    old_rarity, old_base = LEGACY_CATALOG[species]
    native_rarity = catalog[species]['rarity']
    custom_rarity = bool(row.get('is_altered_divine')) or row['rarity'] != old_rarity
    rarity = row['rarity'] if custom_rarity else native_rarity
    old_base = apply_disposition(old_base, row.get('disposition'))
    new_base = apply_disposition(catalog[species]['base_stats'], row.get('disposition'))
    old_rates = LEGACY_GROWTH.get(row['rarity'], LEGACY_GROWTH['common'])
    new_growth = (rarity_growth(rarity, level - 1) if custom_rarity
                  else companion_growth(species, level, catalog))
    stats = {}
    for stat, column in STAT_COLUMNS.items():
        old_automatic = old_base[stat] + old_rates[stat] * (previous_level - 1)
        earned = max(0, int(row[column]) - old_automatic)
        stats[stat] = max(1, new_base[stat] + new_growth[stat] + earned)
    return level, rarity, stats


STAT_COLUMNS = {'hp': 'max_hp', 'attack': 'attack', 'defense': 'defense',
                'speed': 'speed', 'mana': 'max_mana'}


def _proportional_resource(value, old_maximum, new_maximum, *, hp=False):
    if value <= 0:
        return 0
    # Keep a living companion alive, and preserve KO rather than healing it.
    scaled = round(value / max(1, old_maximum) * new_maximum)
    return min(new_maximum, max(1 if hp else 0, scaled))


async def apply_progression_balance(db):
    """Atomic, idempotent startup conversion; callers supply their connection."""
    import json
    from utils.catalog import load_catalog
    from utils.progression import MAX_LEVEL
    previous_factory = db.row_factory
    await db.execute('SAVEPOINT progression_balance')
    try:
        claim = await db.execute('INSERT OR IGNORE INTO game_meta(key,value) VALUES(?,?)',
                                 (PROGRESSION_REVISION, 'applying'))
        if not claim.rowcount:
            await db.execute('RELEASE SAVEPOINT progression_balance')
            return
        db.row_factory = aiosqlite.Row
        catalog = load_catalog('beasts.json')['beasts']
        async with db.execute('SELECT * FROM player_beasts') as cursor:
            rows = await cursor.fetchall()
        for record in rows:
            row = dict(record)
            level, rarity, stats = rebase_companion(row, catalog)
            hp = _proportional_resource(row['hp'], row['max_hp'], stats['hp'], hp=True)
            mana = _proportional_resource(row['mana'], row['max_mana'], stats['mana'])
            if row.get('knocked_out_until') or row['hp'] <= 0:
                hp = 0
            exp = 0 if level == MAX_LEVEL else max(0, int(row['exp'] or 0))
            await db.execute('''UPDATE player_beasts SET level=?,exp=?,rarity=?,hp=?,max_hp=?,
                                attack=?,defense=?,speed=?,mana=?,max_mana=? WHERE id=?''',
                             (level, exp, rarity, hp, stats['hp'], stats['attack'],
                              stats['defense'], stats['speed'], mana, stats['mana'], row['id']))
        await db.execute('UPDATE players SET level=MIN(?,MAX(1,COALESCE(level,1))),'
                         'exp=CASE WHEN level>=? THEN 0 ELSE MAX(0,COALESCE(exp,0)) END',
                         (MAX_LEVEL, MAX_LEVEL))
        await db.execute('UPDATE game_meta SET value=? WHERE key=?',
                         (json.dumps({'status': 'applied', 'companions': len(rows)}), PROGRESSION_REVISION))
        await db.execute('RELEASE SAVEPOINT progression_balance')
    except BaseException:
        await db.execute('ROLLBACK TO SAVEPOINT progression_balance')
        await db.execute('RELEASE SAVEPOINT progression_balance')
        raise
    finally:
        db.row_factory = previous_factory
