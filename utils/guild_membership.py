"""Revalidate guild decisions inside the same transaction as membership changes."""
import aiosqlite
from utils import db as storage


async def _one(db, sql, args):
    async with db.execute(sql, args) as cursor:
        return await cursor.fetchone()


async def _invitable(db, inviter_id, recipient_id, guild_id=None):
    guild = await _one(db, 'SELECT g.*,gm.rank FROM guild_members gm JOIN guilds g ON g.id=gm.guild_id '
                       'WHERE gm.user_id=?', (inviter_id,))
    if not guild or guild['rank'] not in {'leader', 'officer'} or (guild_id is not None and guild['id'] != guild_id):
        raise ValueError('This invitation needs a current Guild Leader or Officer. Ask for a new invitation.')
    if not await _one(db, 'SELECT 1 FROM players WHERE user_id=?', (recipient_id,)):
        raise ValueError('The invited trainer needs to begin with /start before joining a guild.')
    if await _one(db, 'SELECT 1 FROM guild_members WHERE user_id=?', (recipient_id,)):
        raise ValueError('This trainer is already in a guild.')
    count = (await _one(db, 'SELECT COUNT(*) FROM guild_members WHERE guild_id=?', (guild['id'],)))[0]
    if count >= guild['max_members']:
        raise ValueError('This guild is full. Ask for another invitation when a place opens.')
    return dict(guild)


async def invitation(inviter_id, recipient_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        return await _invitable(db, inviter_id, recipient_id)


async def accept_invitation(inviter_id, recipient_id, guild_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        guild = await _invitable(db, inviter_id, recipient_id, guild_id)
        await db.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(?,?)', (guild_id, recipient_id))
        await db.execute('UPDATE players SET guild_id=? WHERE user_id=?', (guild_id, recipient_id))
        await _recount(db, guild_id)
        await db.commit()
        return guild


async def _recount(db, guild_id):
    await db.execute('UPDATE guilds SET member_count=(SELECT COUNT(*) FROM guild_members WHERE guild_id=?) WHERE id=?',
                     (guild_id, guild_id))


async def leave(user_id, guild_id, *, disband=False, successor_id=None):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        membership = await _one(db, 'SELECT gm.rank,g.leader_id FROM guild_members gm JOIN guilds g ON g.id=gm.guild_id '
                               'WHERE gm.user_id=? AND gm.guild_id=?', (user_id, guild_id))
        if not membership:
            raise ValueError('Your guild membership changed. Reopen /guild_leave before leaving.')
        leader = membership['rank'] == 'leader' and membership['leader_id'] == user_id
        if disband or successor_id is not None:
            if not leader:
                raise ValueError('Only the current guild leader can make this change. Reopen /guild_leave.')
        elif membership['rank'] == 'leader' or membership['leader_id'] == user_id:
            raise ValueError('You are now the guild leader. Reopen /guild_leave to review leadership first.')
        if disband:
            count = (await _one(db, 'SELECT COUNT(*) FROM guild_members WHERE guild_id=?', (guild_id,)))[0]
            if count != 1:
                raise ValueError('Your guild has other members now. Reopen /guild_leave to transfer leadership.')
            await db.execute('DELETE FROM guild_sanctuary WHERE guild_id=?', (guild_id,))
            await db.execute('DELETE FROM guilds WHERE id=?', (guild_id,))
            await db.execute('UPDATE players SET guild_tokens=0 WHERE user_id=?', (user_id,))
        elif successor_id is not None:
            successor = await _one(db, 'SELECT 1 FROM guild_members WHERE guild_id=? AND user_id=? AND user_id!=?',
                                   (guild_id, successor_id, user_id))
            if not successor:
                raise ValueError('The chosen successor has left your guild. Reopen /guild_leave to choose again.')
            await db.execute('UPDATE guilds SET leader_id=? WHERE id=?', (successor_id, guild_id))
            await db.execute("UPDATE guild_members SET rank='leader' WHERE guild_id=? AND user_id=?", (guild_id, successor_id))
        await db.execute('DELETE FROM guild_members WHERE guild_id=? AND user_id=?', (guild_id, user_id))
        await db.execute('UPDATE players SET guild_id=NULL WHERE user_id=? AND guild_id=?', (user_id, guild_id))
        if not disband:
            await _recount(db, guild_id)
        await db.commit()
