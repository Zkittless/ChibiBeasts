"""Wallet and EXP updates shared by commands; callers own the transaction."""
from utils import db as storage
from datetime import datetime, timezone
from utils.progression import advance_trainer_xp, activity_beast_xp, ACTIVITY_XP


async def activity_exp(db, user_id, beast, kind, *, requested=None):
    """Claim a companion's UTC daily growth budget inside the caller's transaction."""
    date = datetime.now(timezone.utc).date().isoformat()
    key = f'activity_xp:{beast["id"]}:{kind}:{date}'
    # A trade keeps the companion ID: its daily budget travels with it.
    async with db.execute('SELECT COALESCE(SUM(value),0) FROM player_progress WHERE stat_key=?', (key,)) as cursor:
        row = await cursor.fetchone()
    earned = row[0] if row else 0
    amount = activity_beast_xp(kind, beast['level'], earned)
    if requested is not None and beast['level'] < 50:
        amount = min(max(0, int(requested)), max(0, ACTIVITY_XP[kind][1] - earned))
    if amount:
        await db.execute('INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,?,?) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+excluded.value', (user_id, key, amount))
    return amount

async def has_perk(db, user_id, perk_id):
    async with db.execute('SELECT 1 FROM player_perks WHERE user_id=? AND perk_id=? AND equipped=1', (user_id, perk_id)) as cursor:
        return bool(await cursor.fetchone())

async def shards(db, user_id, amount):
    """Retain fractional Astral bonuses so small rewards still receive exactly 15%."""
    bonus = 0
    if amount > 0 and await has_perk(db, user_id, 'astral_resonance'):
        async with db.execute('SELECT shard_bonus_remainder FROM players WHERE user_id=?', (user_id,)) as c:
            row = await c.fetchone()
        bonus, remainder = divmod((row[0] if row else 0) + amount * 15, 100)
        await db.execute('UPDATE players SET shard_bonus_remainder=? WHERE user_id=?', (remainder, user_id))
    await db.execute('UPDATE players SET celestial_shards=celestial_shards+? WHERE user_id=?', (amount + bonus, user_id))
    return amount + bonus

async def player_exp(db, user_id, amount):
    async with db.execute('SELECT level,exp FROM players WHERE user_id=?', (user_id,)) as c:
        row = await c.fetchone()
    if not row:
        return (1, 0, False)
    old_level, exp = row
    level, exp = advance_trainer_xp(old_level, exp, amount)
    await db.execute('UPDATE players SET level=?,exp=? WHERE user_id=?', (level, exp, user_id))
    if level > old_level:
        from cogs.battle import LEVEL_MILESTONES
        for milestone_level in range(old_level + 1, level + 1):
            reward = LEVEL_MILESTONES.get(milestone_level)
            if not reward:
                continue
            claim = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'level_milestone')", (f'milestone:{user_id}:{milestone_level}', user_id))
            if claim.rowcount:
                await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (reward['reward_gold'], user_id))
                await shards(db, user_id, reward['reward_shards'])
    return (level, exp, level > old_level)

async def cooldown(db, user_id, seconds):
    return int(seconds * (0.8 if await has_perk(db, user_id, 'chronos_hourglass') else 1))
