"""Early-game adventure loadouts and bounded, atomic companion progress.

The active companion leads, with up to two explicitly chosen reserves. Raid
assignments are independent. Saved expeditions supply IDs so neither a changed
loadout nor a new catch can silently replace a travelling companion.
"""
import aiosqlite

from utils import db as storage
from utils.progression import encounter_beast_xp


def encounter_difficulty(states, enemy):
    """Rate rewards by the strongest available member, independent of order.

    A weak lead cannot turn an opponent trivial to its reserve into a boss XP
    reward. Use the existing individual tiers without adding another scale.
    """
    from utils.habitats import encounter_difficulty as individual_difficulty
    tiers = ('trivial', 'easy', 'normal', 'hard', 'boss')
    if not states:
        raise ValueError('Prepare at least one companion before rating an encounter.')
    return min((individual_difficulty(state, enemy) for state in states), key=tiers.index)


async def rows(user_id, db=None, ids=None):
    if db is None:
        async with storage.connect() as connection:
            connection.row_factory = aiosqlite.Row
            await storage.recover_beasts(user_id, connection)
            return await rows(user_id, db=connection, ids=ids)
    db.row_factory = aiosqlite.Row
    if ids is None:
        async with db.execute('SELECT id FROM player_beasts WHERE user_id=? AND is_active=1', (user_id,)) as cursor:
            active = await cursor.fetchone()
        async with db.execute('SELECT beast_row_id FROM adventure_team WHERE user_id=? ORDER BY slot', (user_id,)) as cursor:
            ids = ([active[0]] if active else []) + [row[0] for row in await cursor.fetchall()]
    ids = list(dict.fromkeys(int(value) for value in ids))[:3]
    if not ids:
        return []
    placeholders = ','.join('?' for _ in ids)
    async with db.execute(f'SELECT b.* FROM player_beasts b WHERE b.user_id=? AND b.id IN ({placeholders}) '
                          'AND NOT EXISTS(SELECT 1 FROM beast_market m WHERE m.beast_row_id=b.id)', (user_id, *ids)) as cursor:
        found = {row['id']: dict(row) for row in await cursor.fetchall()}
    return [found[value] for value in ids if value in found and found[value]['hp'] > 0
            and not storage.is_knocked_out(found[value])]


async def assignments(user_id):
    """Return explicit slots for the journal, including a recovering reserve."""
    async with storage.connect() as db:
        async with db.execute('SELECT t.slot,t.beast_row_id FROM adventure_team t JOIN player_beasts b '
                              'ON b.id=t.beast_row_id AND b.user_id=t.user_id WHERE t.user_id=? ORDER BY t.slot', (user_id,)) as cursor:
            return {row[0]: row[1] for row in await cursor.fetchall()}


async def _check_edit(db, user_id):
    async with db.execute("SELECT 1 FROM expeditions WHERE user_id=? AND status IN ('active','battle','quick')", (user_id,)) as cursor:
        if await cursor.fetchone():
            raise ValueError('Return from your saved expedition before changing your adventure team.')


async def set_slot(user_id, row_id, slot):
    from utils.duels import reserve_fighters
    from utils import combat
    if slot not in (1, 2):
        raise ValueError('Choose reserve slot 1 or 2.')
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id]):
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await storage.recover_beasts(user_id, db)
                await db.execute('BEGIN IMMEDIATE')
                await _check_edit(db, user_id)
                candidates = await rows(user_id, db=db, ids=[row_id])
                if not candidates:
                    raise ValueError('Choose a healthy companion from your collection and delist it first.')
                if candidates[0]['is_active']:
                    raise ValueError('Your active companion already leads the team. Choose another reserve.')
                await db.execute('DELETE FROM adventure_team WHERE user_id=? AND (slot=? OR beast_row_id=?)', (user_id, slot, row_id))
                await db.execute('INSERT INTO adventure_team VALUES(?,?,?)', (user_id, slot, row_id))
                await db.commit()


async def clear_slot(user_id, slot):
    from utils.duels import reserve_fighters
    from utils import combat
    if slot not in (1, 2):
        raise ValueError('Choose reserve slot 1 or 2.')
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id]):
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                await _check_edit(db, user_id)
                await db.execute('DELETE FROM adventure_team WHERE user_id=? AND slot=?', (user_id, slot))
                await db.commit()


def xp_budget(level, lead_level, enemy_level, base_amount, *, lead=False, contributed=False):
    """Preserve lead rewards; reserve progress fits its own level's XP curve."""
    if level >= 50 or base_amount <= 0:
        return 0
    if lead:
        return max(0, int(base_amount))
    rate = .50 if contributed else .35
    if lead_level - level >= 3:
        rate = min(.60, rate + .10)
    own_budget = encounter_beast_xp(level, enemy_level)
    reward = min(max(0, int(base_amount)), own_budget) * rate
    if enemy_level is not None and level > enemy_level + 5:
        reward *= max(.1, (enemy_level + 5) / level)
    return max(1, round(reward)) if reward > 0 else 0


async def award_xp_in_transaction(db, user_id, roster_ids, lead_id, base_amount, enemy_level, *, participated=()):
    from utils.duels import beast_exp_in_transaction
    db.row_factory = aiosqlite.Row
    ids = list(dict.fromkeys(int(value) for value in roster_ids))[:3]
    if not ids or base_amount <= 0:
        return []
    placeholders = ','.join('?' for _ in ids)
    async with db.execute(f'SELECT * FROM player_beasts WHERE user_id=? AND id IN ({placeholders})', (user_id, *ids)) as cursor:
        members = {row['id']: dict(row) for row in await cursor.fetchall()}
    lead_level = members.get(lead_id, {}).get('level', max((row['level'] for row in members.values()), default=1))
    result = []
    for row_id in ids:
        row = members.get(row_id)
        if row is None:
            continue
        amount = xp_budget(row['level'], lead_level, enemy_level, base_amount, lead=row_id == lead_id, contributed=row_id in participated)
        if not amount:
            continue
        # Calculate bonus-adjusted totals explicitly so the summary is exact.
        from utils.rewards import has_perk
        if await has_perk(db, user_id, 'fable_scholar'):
            amount = round(amount * 1.1)
        async with db.execute('SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON s.guild_id=m.guild_id '
                              'WHERE m.user_id=? AND s.arcane_library=1', (user_id,)) as cursor:
            if await cursor.fetchone():
                amount = round(amount * 1.15)
        level, leveled = await beast_exp_in_transaction(db, user_id, row_id, amount, bonuses=False)
        result.append(dict(id=row_id, name=row.get('nickname') or storage.get_beast_data(row['beast_id'])['name'],
                           amount=amount, level=level, leveled=leveled))
    return result


async def award_xp(user_id, roster_ids, lead_id, base_amount, enemy_level, *, participated=()):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        result = await award_xp_in_transaction(db, user_id, roster_ids, lead_id, base_amount, enemy_level, participated=participated)
        await db.commit()
    if any(row['leveled'] for row in result):
        from utils.progress import track_quest_event
        await track_quest_event(user_id, 'beast_level_up')
    return result
