"""Season-scoped Carnival rewards; frozen eggs, receipts and bounded currency.

Egg vouchers are deliberately separate from the normal Nursery and market pools.
They retain their species pool and level after the Carnival closes. Every payment,
gift, hatch and exploration award is committed with its durable receipt.
"""
from copy import deepcopy
from functools import lru_cache
import json
import logging
import math
import random
import time
from uuid import uuid4

import aiosqlite

from utils import db as storage
from utils.artwork import PROJECT_ROOT
from utils.duels import exclusive_player
from utils.gameplay import add_stack

EVENT_ID = 'hollowlight_2026'
GHOST_UNLOCK = 'ghost_costume_2026'
TITLE_UNLOCK = 'hollowlight_title_2026'
TITLE = 'Lanternkeeper · 2026'
logger = logging.getLogger('chibibeasts.hollowlight')


@lru_cache(maxsize=1)
def _config():
    return json.loads((PROJECT_ROOT / 'data' / 'hollowlight.json').read_text(encoding='utf-8'))


def config():
    return deepcopy(_config())


def _now(value=None):
    value = time.time() if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('A valid Carnival time is required.')
    return float(value)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


async def _one(db, sql, args=()):
    async with db.execute(sql, args) as cursor:
        row = await cursor.fetchone()
        return dict(zip((column[0] for column in cursor.description), row)) if row else None


async def initialize(db, *, now=None):
    """First deployment starts the window once; ordinary restarts cannot extend it."""
    now, rules = _now(now), _config()
    end = now + rules['duration_days'] * 86400
    await db.execute('INSERT OR IGNORE INTO hollowlight_events VALUES(?,?,?,?)',
                     (EVENT_ID, now, end, end + rules['redemption_grace_days'] * 86400))


async def window(db):
    row = await _one(db, 'SELECT * FROM hollowlight_events WHERE event_id=?', (EVENT_ID,))
    if not row:
        raise ValueError('The Carnival has not been initialized. Restart the bot before opening it.')
    return row


async def require_active(db, now=None):
    now, saved = _now(now), await window(db)
    if not saved['starts_at'] <= now < saved['ends_at']:
        raise ValueError('The Hollowlight Carnival is closed for new adventures and welcome gifts.')
    return {**saved, 'started_at': now}


departure = require_active


async def _player(db, user_id):
    row = await _one(db, 'SELECT * FROM players WHERE user_id=?', (user_id,))
    if not row or not await _one(db, 'SELECT 1 FROM player_beasts WHERE user_id=? LIMIT 1', (user_id,)):
        raise ValueError('Choose your starter with /start before claiming Carnival rewards.')
    return row


async def _receipt(db, user_id, request_key, operation, arguments):
    if (isinstance(request_key, bool) or not isinstance(request_key, (str, int))
            or not str(request_key).strip() or len(str(request_key)) > 200):
        raise ValueError('A valid Carnival request receipt is required. Refresh the panel.')
    row = await _one(db, 'SELECT * FROM hollowlight_requests WHERE user_id=? AND request_key=?',
                     (user_id, str(request_key)))
    if row:
        if row['operation'] != operation or row['arguments'] != _json(arguments):
            raise ValueError('That Carnival request already completed with different choices.')
        return json.loads(row['result'])


async def _save_receipt(db, user_id, key, operation, arguments, result):
    await db.execute('INSERT INTO hollowlight_requests VALUES(?,?,?,?,?)',
                     (user_id, str(key), operation, _json(arguments), _json(result)))


async def _wallet(db, user_id):
    await db.execute('INSERT OR IGNORE INTO hollowlight_wallets(user_id,event_id) VALUES(?,?)', (user_id, EVENT_ID))
    return await _one(db, 'SELECT * FROM hollowlight_wallets WHERE user_id=? AND event_id=?', (user_id, EVENT_ID))


async def _credit(db, user_id, amount):
    held = await _wallet(db, user_id)
    paid = max(0, min(int(amount), _config()['wallet_cap'] - held['embers']))
    await db.execute('UPDATE hollowlight_wallets SET embers=embers+?,total_earned=total_earned+? '
                     'WHERE user_id=? AND event_id=?', (paid, paid, user_id, EVENT_ID))
    return paid


async def _hatch_level(db, user_id, player):
    from utils.habitats import BIOMES
    ceiling = max(region['level_range'][1] for key, region in BIOMES.items()
                  if key != 'hollowlight' and not region.get('event_id') and region['min_level'] <= player['level'])
    row = await _one(db, 'SELECT MAX(level) AS level FROM player_beasts WHERE user_id=?', (user_id,))
    return max(1, min(50, ceiling, row['level'] or 1))


async def _egg(db, user_id, name, pool, level, now):
    egg = dict(id=uuid4().hex, name=name, level=level, pool=deepcopy(pool), hatched=False,
               beast_id=None, beast_row_id=None)
    await db.execute('INSERT INTO hollowlight_eggs(id,user_id,event_id,name,level,pool,created_at) VALUES(?,?,?,?,?,?,?)',
                     (egg['id'], user_id, EVENT_ID, name, level, _json(pool), now))
    return egg


async def owns_unlock(db, user_id, unlock_id):
    return bool(await _one(db, 'SELECT 1 FROM hollowlight_unlocks WHERE user_id=? AND event_id=? AND unlock_id=?',
                           (user_id, EVENT_ID, unlock_id)))


async def unlocks(user_id):
    async with storage.connect() as db:
        async with db.execute('SELECT unlock_id FROM hollowlight_unlocks WHERE user_id=? AND event_id=? ORDER BY unlock_id',
                              (user_id, EVENT_ID)) as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def status(user_id, *, now=None):
    now = _now(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN')
        saved = await window(db)
        player = await _one(db, 'SELECT * FROM players WHERE user_id=?', (user_id,)) or {'level': 1}
        wallet = await _one(db, 'SELECT * FROM hollowlight_wallets WHERE user_id=? AND event_id=?', (user_id, EVENT_ID))
        gift = await _one(db, 'SELECT 1 FROM hollowlight_gifts WHERE user_id=? AND event_id=?', (user_id, EVENT_ID))
        async with db.execute('SELECT offer_id,quantity FROM hollowlight_purchases WHERE user_id=? AND event_id=?', (user_id, EVENT_ID)) as cursor:
            purchases = dict(await cursor.fetchall())
        async with db.execute('SELECT * FROM hollowlight_eggs WHERE user_id=? AND event_id=? ORDER BY created_at,id', (user_id, EVENT_ID)) as cursor:
            eggs = [dict(row) for row in await cursor.fetchall()]
        phase = ('active' if saved['starts_at'] <= now < saved['ends_at'] else
                 'redeeming' if saved['ends_at'] <= now < saved['redeem_until'] else 'ended')
        offers, balance = [], wallet['embers'] if wallet else 0
        for source in _config()['offers']:
            offer = deepcopy(source)
            offer['remaining'] = max(0, offer['limit'] - purchases.get(offer['id'], 0))
            offer['reason'] = ('The redemption window has closed.' if phase == 'ended' else
                               f"Trainer level {offer['min_level']} required." if player['level'] < offer['min_level'] else
                               'Already redeemed this season.' if not offer['remaining'] else
                               'Not enough Lantern Embers.' if balance < offer['cost'] else '')
            offer['available'] = not offer['reason']
            offers.append(offer)
        for egg in eggs:
            egg['pool'] = json.loads(egg['pool'])
            egg['hatched'] = bool(egg['hatched'])
        result = dict(event_id=EVENT_ID, name=_config()['name'], phase=phase, **{k: saved[k] for k in
                      ('starts_at', 'ends_at', 'redeem_until')}, embers=balance,
                      total_earned=wallet['total_earned'] if wallet else 0, gift_claimed=bool(gift),
                      offers=offers, eggs=eggs, title_equipped=player.get('title') == TITLE)
    result['unlocks'] = await unlocks(user_id)
    await notify_pending(user_id)
    return result


async def claim_gift(user_id, *, request_key, now=None):
    now, args = _now(now), []
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _receipt(db, user_id, request_key, 'gift', args)
        if replay is not None:
            return replay
        previous = await _one(db, 'SELECT result FROM hollowlight_gifts WHERE user_id=? AND event_id=?', (user_id, EVENT_ID))
        if previous:
            result = {**json.loads(previous['result']), 'already_claimed': True}
        else:
            await require_active(db, now)
            player = await _player(db, user_id)
            gift = _config()['gift']
            for item, quantity in gift['items'].items():
                await add_stack(db, 'player_inventory', user_id, item, quantity)
            result = dict(already_claimed=False, embers=await _credit(db, user_id, gift['embers']),
                          items=gift['items'], egg=await _egg(db, user_id, gift['egg']['name'], gift['egg']['pool'],
                          await _hatch_level(db, user_id, player), now))
            await db.execute('INSERT INTO hollowlight_gifts VALUES(?,?,?,?)', (user_id, EVENT_ID, now, _json(result)))
        await _save_receipt(db, user_id, request_key, 'gift', args, result)
        await db.commit()
    return result


async def redeem(user_id, offer_id, *, request_key, now=None, quoted_cost=None):
    now = _now(now)
    if quoted_cost is not None and (isinstance(quoted_cost, bool) or not isinstance(quoted_cost, int)):
        raise ValueError('Review the current Carnival price before confirming.')
    args = [offer_id] if quoted_cost is None else [offer_id, quoted_cost]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _receipt(db, user_id, request_key, 'redeem', args)
        if replay is not None:
            return replay
        saved, player = await window(db), await _player(db, user_id)
        if not saved['starts_at'] <= now < saved['redeem_until']:
            raise ValueError('The Carnival shop redemption window has closed.')
        offer = next((row for row in _config()['offers'] if row['id'] == offer_id), None)
        if not offer:
            raise ValueError('Choose an offer from the Carnival shop.')
        if quoted_cost is not None and offer['cost'] != quoted_cost:
            raise ValueError('That Carnival price changed. Review the offer again before confirming.')
        if player['level'] < offer['min_level']:
            raise ValueError(f"This offer requires trainer level {offer['min_level']}.")
        bought = await _one(db, 'SELECT quantity FROM hollowlight_purchases WHERE user_id=? AND event_id=? AND offer_id=?',
                            (user_id, EVENT_ID, offer_id))
        if bought and bought['quantity'] >= offer['limit']:
            raise ValueError('You already redeemed this offer this season.')
        held = await _wallet(db, user_id)
        if held['embers'] < offer['cost']:
            raise ValueError('Not enough Lantern Embers for this offer.')
        await db.execute('UPDATE hollowlight_wallets SET embers=embers-? WHERE user_id=? AND event_id=?',
                         (offer['cost'], user_id, EVENT_ID))
        await db.execute('INSERT INTO hollowlight_purchases VALUES(?,?,?,1) ON CONFLICT(user_id,event_id,offer_id) '
                         'DO UPDATE SET quantity=quantity+1', (user_id, EVENT_ID, offer_id))
        result = dict(offer_id=offer_id, cost=offer['cost'], kind=offer['kind'])
        if offer['kind'] == 'egg':
            result['egg'] = await _egg(db, user_id, offer['name'], offer['pool'], await _hatch_level(db, user_id, player), now)
        else:
            await db.execute('INSERT INTO hollowlight_unlocks VALUES(?,?,?,?)', (user_id, EVENT_ID, offer['unlock_id'], now))
            result['unlock_id'] = offer['unlock_id']
        await _save_receipt(db, user_id, request_key, 'redeem', args, result)
        await db.commit()
    return result


async def award_trip(db, trip, now=None):
    """One capped receipt per run; optional foes and pack size add no currency."""
    now, payload = _now(now), trip['payload']
    meta = payload.get('hollowlight') or {}
    if payload.get('event_id') != EVENT_ID or meta.get('event_id') != EVENT_ID:
        return 0
    saved = await window(db)
    if (meta.get('starts_at') != saved['starts_at'] or meta.get('ends_at') != saved['ends_at']
            or not isinstance(meta.get('started_at'), (int, float)) or isinstance(meta.get('started_at'), bool)
            or not saved['starts_at'] <= meta['started_at'] < saved['ends_at']):
        raise ValueError('This Carnival run has no valid saved admission.')
    target, won = payload.get('battle_target'), payload.get('battles_completed')
    if (type(target) is not int or target not in (2, 3) or type(won) is not int or not 0 <= won <= target):
        raise ValueError('This Carnival run has an invalid battle receipt.')
    previous = await _one(db, 'SELECT user_id,amount FROM hollowlight_trip_awards WHERE event_id=? AND trip_id=?', (EVENT_ID, trip['id']))
    if previous:
        if previous['user_id'] != trip['user_id']:
            raise ValueError('That Carnival receipt belongs to another trainer.')
        payload['hollowlight_embers'] = previous['amount']
        return previous['amount']
    amount = await _credit(db, trip['user_id'], _config()['clear_embers'] * won // target)
    await db.execute('INSERT INTO hollowlight_trip_awards VALUES(?,?,?,?,?)', (EVENT_ID, trip['id'], trip['user_id'], amount, now))
    payload['hollowlight_embers'] = amount
    return amount


async def set_title(user_id, enabled, *, request_key):
    """Owned seasonal titles remain wearable; removing one preserves later story titles."""
    if type(enabled) is not bool:
        raise ValueError('Choose whether to wear the Carnival title.')
    args = [enabled]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _receipt(db, user_id, request_key, 'title', args)
        if replay is not None:
            return replay
        if not await owns_unlock(db, user_id, TITLE_UNLOCK):
            raise ValueError('Earn the 2026 Carnival title before wearing it.')
        player = await _one(db, 'SELECT title FROM players WHERE user_id=?', (user_id,))
        if not player:
            raise ValueError('Create a trainer before changing a title.')
        current = player['title']
        if enabled and current != TITLE:
            await db.execute('INSERT INTO hollowlight_titles VALUES(?,?,?) ON CONFLICT(user_id,event_id) '
                             'DO UPDATE SET previous_title=excluded.previous_title', (user_id, EVENT_ID, current))
            current = TITLE
        elif not enabled and current == TITLE:
            prior = await _one(db, 'SELECT previous_title FROM hollowlight_titles WHERE user_id=? AND event_id=?', (user_id, EVENT_ID))
            current = prior['previous_title'] if prior else None
        await db.execute('UPDATE players SET title=? WHERE user_id=?', (current, user_id))
        result = dict(enabled=current == TITLE, title=current)
        await _save_receipt(db, user_id, request_key, 'title', args, result)
        await db.commit()
    return result


@exclusive_player(allow_trial=True)
async def hatch_egg(user_id, egg_id, *, request_key, now=None, rng=random):
    """A paid, stored voucher never expires or rerolls its level after closure."""
    now, args = _now(now), [egg_id]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _receipt(db, user_id, request_key, 'hatch', args)
        if replay is not None:
            result = replay
        else:
            # An earned voucher remains usable if the collection becomes empty.
            if not await _one(db, 'SELECT 1 FROM players WHERE user_id=?', (user_id,)):
                raise ValueError('Create a trainer with /start before hatching a Carnival egg.')
            egg = await _one(db, 'SELECT * FROM hollowlight_eggs WHERE id=? AND user_id=? AND event_id=?', (egg_id, user_id, EVENT_ID))
            if not egg or egg['hatched']:
                raise ValueError('That Carnival egg was already hatched or belongs to another trainer.')
            pool = json.loads(egg['pool'])
            species = rng.choices(list(pool), weights=list(pool.values()), k=1)[0]
            beast = storage.get_beast_data(species)
            if not beast or species not in {'patchkin', 'wickwing', 'veilmoth'}:
                raise ValueError('This Carnival hatchling is unavailable; your egg remains safely stored.')
            row_id = await storage.grant_beast(db, user_id, {**beast, 'level': egg['level'], 'caught_from': 'hollowlight'},
                                              grant_key='hollowlight-egg:' + egg_id)
            result = dict(egg_id=egg_id, beast_id=species, beast_row_id=row_id, name=beast['name'],
                          rarity=beast['rarity'], level=egg['level'])
            await db.execute('UPDATE hollowlight_eggs SET hatched=1,beast_id=?,beast_row_id=?,hatch_result=? WHERE id=?',
                             (species, row_id, _json(result), egg_id))
            await _save_receipt(db, user_id, request_key, 'hatch', args, result)
            await db.commit()
    await notify_pending(user_id)
    return result


async def notify_pending(user_id):
    """Post-commit progression retries never re-spend an egg or recreate a beast."""
    from utils.events import acquired_beast
    from utils.progress import _track_daily_event, check_achievements, unlock_simple_achievement
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM hollowlight_eggs WHERE user_id=? AND event_id=? AND hatched=1 AND notified=0', (user_id, EVENT_ID)) as cursor:
            pending = [dict(row) for row in await cursor.fetchall()]
    for egg in pending:
        try:
            beast = storage.get_beast_data(egg['beast_id'])
            await acquired_beast(user_id, egg['beast_row_id'], beast, 'hatch')
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                receipt = await db.execute('INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,?)',
                    ('hollowlight-hatch:' + egg['id'], user_id, 'hatch'))
                if receipt.rowcount:
                    await db.execute("INSERT INTO player_progress VALUES(?,'hatch',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1", (user_id,))
                    await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+1) WHERE id=? AND user_id=?', (egg['beast_row_id'], user_id))
                await db.commit()
            await _track_daily_event(user_id, 'hatch', event_key='hollowlight-daily:' + egg['id'])
            await unlock_simple_achievement(user_id, 'first_steps')
            await check_achievements(user_id)
            async with storage.connect() as db:
                await db.execute('UPDATE hollowlight_eggs SET notified=1 WHERE id=? AND user_id=?', (egg['id'], user_id))
                await db.commit()
        except Exception:
            logger.exception('Carnival hatch %s is committed; progression will retry on the next visit.', egg['id'])
