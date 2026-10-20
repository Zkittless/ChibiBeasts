"""Owner-scoped history sources shared by /history and the Field Journal."""
from datetime import datetime, timezone

import aiosqlite

from utils import db as storage

KINDS = {'expeditions': 'Expeditions', 'battles': 'Battles', 'raids': 'Raids', 'trades': 'Trades', 'divines': 'Altered divines'}
SOURCES = {
    'battles': ("b.*,p1.username AS challenger_name,p2.username AS opponent_name",
                'battles b LEFT JOIN players p1 ON b.challenger_id=p1.user_id LEFT JOIN players p2 ON b.opponent_id=p2.user_id',
                '(b.challenger_id=? OR b.opponent_id=?)', 'b.created_at', 'b.id'),
    'raids': ('r.*,rp.damage_dealt', 'raid_participants rp JOIN raids r ON rp.raid_id=r.id', 'rp.user_id=?', 'r.started_at', 'r.id'),
    'trades': ('t.*,p1.username AS sender_name,p2.username AS receiver_name,pb1.beast_id AS sent_beast_id,pb2.beast_id AS received_beast_id',
               'trades t LEFT JOIN players p1 ON t.sender_id=p1.user_id LEFT JOIN players p2 ON t.receiver_id=p2.user_id LEFT JOIN player_beasts pb1 ON t.sender_beast_id=pb1.id LEFT JOIN player_beasts pb2 ON t.receiver_beast_id=pb2.id',
               '(t.sender_id=? OR t.receiver_id=?)', 't.created_at', 't.id'),
    'divines': ('*', 'altered_divines', 'caught_by=?', 'caught_at', 'id'),
}


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            stamp = int(value)
        else:
            stamp = int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
        return f'<t:{stamp}:d>'
    except (ValueError, TypeError, OverflowError):
        return 'Date unavailable'


async def load(user_id, kind, page, *, size=3):
    if kind not in SOURCES:
        raise ValueError('Choose a history category from the current panel.')
    fields, source, where, date, key = SOURCES[kind]
    params = (user_id, user_id) if kind in {'battles', 'trades'} else (user_id,)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f'SELECT COUNT(*) FROM {source} WHERE {where}', params) as c:
            total = (await c.fetchone())[0]
        page = max(0, min(page, max(0, (total - 1) // size)))
        async with db.execute(f'SELECT {fields} FROM {source} WHERE {where} ORDER BY {date} DESC,{key} DESC LIMIT ? OFFSET ?', (*params, size, page * size)) as c:
            entries = [dict(row) for row in await c.fetchall()]
    return total, page, entries


def text(user_id, kind, row, safe):
    if kind == 'battles':
        other = row['opponent_name'] if row['challenger_id'] == user_id else row['challenger_name']
        status = row.get('status', '')
        outcome = 'Win' if row['winner_id'] == user_id else 'Loss' if row['winner_id'] is not None else 'Draw' if status in {'completed', 'finished'} else status.title()
        battle_type = row.get('battle_type') or 'pvp'
        name = 'vs ' + safe(other or 'Unknown trainer') if battle_type == 'pvp' else 'NPC spar' if battle_type == 'sparr' else 'Wild battle'
        return f"**{outcome} · {name}**\n{timestamp(row['created_at'])}"
    if kind == 'raids':
        return f"**{safe(row['boss_name'])} · {safe(row['status'].title())}**\n{row['damage_dealt']:,} damage · {timestamp(row['started_at'])}"
    if kind == 'divines':
        beast = storage.get_beast_data(row['beast_id']) or {}
        return f"**{safe(row['altered_name'])} · {safe(beast.get('name', row['beast_id']))}**\n{timestamp(row['caught_at'])}"
    other = row['receiver_name'] if row['sender_id'] == user_id else row['sender_name']
    sent = (storage.get_beast_data(row.get('sent_beast_id')) or {}).get('name', 'Unknown beast')
    received = (storage.get_beast_data(row.get('received_beast_id')) or {}).get('name', 'Open offer')
    gold = f" + {row['gold_offered']:,} gold" if row.get('gold_offered') else ''
    return f"**Trade with {safe(other or 'Unknown trainer')}**\n{safe(sent)}{gold} ↔ {safe(received)}\n{safe(row.get('status', 'Recorded').title())} · {timestamp(row['created_at'])}"
