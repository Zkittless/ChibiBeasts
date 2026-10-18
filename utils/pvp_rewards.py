"""Atomic, shared normal/ranked PvP rewards with UTC daily growth budgets."""
from datetime import datetime, timezone

import aiosqlite

from utils import db as storage
from utils.progression import (MAX_LEVEL, advance_beast_xp,
                               beast_exp_for_level, trainer_exp_for_level)
from utils.rewards import player_exp

BEAST_XP_DAILY_CAP = 60
TRAINER_XP_DAILY_CAP = 80


def _utc_date(now):
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, (int, float)) and not isinstance(now, bool):
        now = datetime.fromtimestamp(now, timezone.utc)
    if not isinstance(now, datetime):
        raise ValueError('now must be a datetime or Unix timestamp.')
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def _actual_xp(level, exp, amount, curve):
    """Discard neither a daily allowance nor a reported award at level 50."""
    if level >= MAX_LEVEL:
        return 0
    until_cap = sum(curve(n) for n in range(level, MAX_LEVEL)) - exp
    return min(amount, max(0, until_cap))


async def award_match(match_key, ids, beast_row_ids, winner_index, *, clean_ko, now=None):
    """Return two reward dicts in participant order; retrying grants nothing.

    ``match_key`` identifies the completed match globally, including its mode.
    ``now`` may be an aware datetime, a naive UTC datetime, or a Unix timestamp.
    Both modes share opponent claims and per-trainer daily XP counters. An
    eligible result means this is today's first clean match against that
    opponent; its XP can still be zero after the daily or level cap.
    """
    if not isinstance(match_key, str) or not match_key.strip():
        raise ValueError('A match needs a nonempty global key.')
    ids, beast_row_ids = tuple(ids), tuple(beast_row_ids)
    if (len(ids) != 2 or len(beast_row_ids) != 2 or ids[0] == ids[1]
            or any(isinstance(uid, bool) or not isinstance(uid, int) for uid in ids)):
        raise ValueError('A PvP match needs two distinct trainers and their companion rows.')
    if winner_index is not None and (type(winner_index) is not int or winner_index not in (0, 1)):
        raise ValueError('winner_index must be 0, 1 or None.')
    date = _utc_date(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        players, beasts = [], []
        # Validate both owners before writing either participant's claims.
        for uid, row_id in zip(ids, beast_row_ids):
            async with db.execute('SELECT * FROM players WHERE user_id=?', (uid,)) as cursor:
                player = await cursor.fetchone()
            async with db.execute('SELECT * FROM player_beasts WHERE id=? AND user_id=?', (row_id, uid)) as cursor:
                beast = await cursor.fetchone()
            if player is None or beast is None:
                raise ValueError('Each PvP companion must belong to its participating trainer.')
            players.append(dict(player))
            beasts.append(dict(beast))
        results = [dict(user_id=uid, eligible=False, beast_xp=0, trainer_xp=0,
                        beast_level=beast['level'], trainer_level=player['level'],
                        beast_leveled=False, trainer_leveled=False, gold=0)
                   for uid, player, beast in zip(ids, players, beasts)]
        if not clean_ko or winner_index is None:
            await db.commit()
            return results
        for index, (uid, player, beast, result) in enumerate(zip(ids, players, beasts, results)):
            match_claim = await db.execute(
                "INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'pvp_match_reward')",
                (f'pvp:match:{match_key}:{uid}', uid))
            if not match_claim.rowcount:
                continue
            pair_claim = await db.execute(
                "INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'pvp_opponent_reward')",
                (f'pvp:opponent:{date}:{uid}:{ids[1 - index]}', uid))
            if not pair_claim.rowcount:
                continue
            result['eligible'] = True
            won = index == winner_index
            amounts = []
            for kind, requested, cap, level, exp, curve in (
                ('beast', 60 if won else 20, BEAST_XP_DAILY_CAP, beast['level'], beast['exp'], beast_exp_for_level),
                ('trainer', 80 if won else 25, TRAINER_XP_DAILY_CAP, player['level'], player['exp'], trainer_exp_for_level),
            ):
                stat_key = f'pvp_{kind}_xp:{date}'
                async with db.execute('SELECT value FROM player_progress WHERE user_id=? AND stat_key=?', (uid, stat_key)) as cursor:
                    row = await cursor.fetchone()
                earned = max(0, row[0]) if row else 0
                amount = _actual_xp(level, exp, min(requested, max(0, cap - earned)), curve)
                amounts.append(amount)
                if amount:
                    await db.execute(
                        'INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,?,?) '
                        'ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+excluded.value',
                        (uid, stat_key, amount))
            beast_xp, trainer_xp = amounts
            beast_level, beast_exp = advance_beast_xp(beast['level'], beast['exp'], beast_xp)
            # Bypass generic PvE bonuses: these amounts are the actual budgets.
            await storage.apply_beast_levelup(db, beast, beast_level, beast_exp)
            trainer_level, _, trainer_leveled = await player_exp(db, uid, trainer_xp)
            gold = 100 if won else 25
            await db.execute(
                'UPDATE players SET gold=gold+?,total_gold_earned=total_gold_earned+? WHERE user_id=?',
                (gold, gold, uid))
            result.update(beast_xp=beast_xp, trainer_xp=trainer_xp, beast_level=beast_level,
                          trainer_level=trainer_level, beast_leveled=beast_level > beast['level'],
                          trainer_leveled=trainer_leveled, gold=gold)
        await db.commit()
    return results
