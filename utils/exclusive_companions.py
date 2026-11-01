"""Atomic, owner-bound grants for personal developer companions."""
import aiosqlite
from utils import db as storage


async def migrate_owner_bindings(db):
    """Lift the global species cap while preserving existing grants atomically."""
    await db.execute('BEGIN IMMEDIATE')
    try:
        async with db.execute('PRAGMA table_info(exclusive_companions)') as cursor:
            columns = await cursor.fetchall()
        primary_key = [column[1] for column in sorted(columns, key=lambda column: column[5]) if column[5]]
        if primary_key == ['species']:
            await db.execute('''CREATE TABLE exclusive_companions_by_owner (
                species TEXT NOT NULL,user_id INTEGER NOT NULL,
                beast_row_id INTEGER UNIQUE,granted_by INTEGER NOT NULL,
                granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(species,user_id))''')
            await db.execute('''INSERT INTO exclusive_companions_by_owner
                (species,user_id,beast_row_id,granted_by,granted_at)
                SELECT species,user_id,beast_row_id,granted_by,granted_at FROM exclusive_companions''')
            await db.execute('DROP TABLE exclusive_companions')
            await db.execute('ALTER TABLE exclusive_companions_by_owner RENAME TO exclusive_companions')
        await db.execute("UPDATE gameplay_grants SET grant_key='exclusive:hexpurr:' || user_id WHERE grant_key='exclusive:hexpurr'")
        await db.commit()
    except Exception:
        await db.rollback()
        raise


def discord_user_id(value):
    value = str(value).strip()
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**63:
        raise ValueError('Enter a numeric Discord user ID, copied with Developer Mode.')
    return int(value)


async def give_hexpurr(discord_id, *, granted_by):
    """Grant one personal Hexpurr per trainer, at their current lead level.

    Reservation, companion insertion and receipt commit together. Retries return
    the original companion without replacing its level, gear or progress.
    """
    owner = discord_user_id(discord_id)
    definition = storage.get_beast_data('hexpurr')
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM exclusive_companions WHERE species=? AND user_id=?', ('hexpurr', owner)) as cursor:
            binding = await cursor.fetchone()
        if binding:
            async with db.execute('SELECT * FROM player_beasts WHERE id=? AND user_id=?', (binding['beast_row_id'], owner)) as cursor:
                row = await cursor.fetchone()
            if not row:
                raise ValueError('Hexpurr remains bound to this trainer, but its companion record is missing. Restore the record before retrying.')
            created = False
        else:
            async with db.execute('SELECT 1 FROM players WHERE user_id=?', (owner,)) as cursor:
                if not await cursor.fetchone():
                    raise ValueError('That trainer needs to use /start before receiving Hexpurr.')
            async with db.execute('SELECT level FROM player_beasts WHERE user_id=? AND is_active=1', (owner,)) as cursor:
                lead = await cursor.fetchone()
            if not lead:
                raise ValueError('That trainer needs a lead companion first. Its level determines Hexpurr’s starting level.')
            await db.execute('INSERT INTO exclusive_companions(species,user_id,granted_by) VALUES(?,?,?)', ('hexpurr', owner, granted_by))
            row_id = await storage.grant_beast(db, owner, {**definition, 'level': lead['level'], 'caught_from': 'dev'}, grant_key=f'exclusive:hexpurr:{owner}')
            await db.execute('UPDATE exclusive_companions SET beast_row_id=? WHERE species=? AND user_id=?', (row_id, 'hexpurr', owner))
            async with db.execute('SELECT * FROM player_beasts WHERE id=?', (row_id,)) as cursor:
                row = await cursor.fetchone()
            created = True
        await db.commit()
    # This event is independently idempotent, including a retry after a crash
    # between the committed grant and the acquisition notification.
    from utils.events import acquired_beast
    await acquired_beast(owner, row['id'], definition, 'dev')
    return dict(row), created
