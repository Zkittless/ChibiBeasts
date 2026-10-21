"""Fast fighter reservations backed by persisted tower runs.

Startup and run commits refresh this cache before yielding. Database guards
still recheck mutations inside their writer transaction. Keys include the DB
path so test databases and the running game never share reservations.
"""
from pathlib import Path
import json

_users = {}


def _key():
    from utils import db as storage
    return str(Path(storage.DB_PATH).resolve())


def locked(user_id):
    return user_id in _users.get(_key(), set())


def lock(user_id):
    _users.setdefault(_key(), set()).add(user_id)


def unlock(user_id):
    _users.get(_key(), set()).discard(user_id)


async def reload(db):
    async with db.execute("SELECT user_id FROM trial_runs WHERE status IN ('between','fighting','paused')") as cursor:
        _users[_key()] = {row[0] for row in await cursor.fetchall()}


async def member_ids(db, user_id):
    async with db.execute("SELECT payload FROM trial_runs WHERE user_id=? AND status IN ('between','fighting','paused')", (user_id,)) as cursor:
        run = await cursor.fetchone()
    return tuple(json.loads(run[0]).get('party_ids', ())) if run else ()
