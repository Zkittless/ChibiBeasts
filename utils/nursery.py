"""Durable egg grants and incubation choices; payment and ownership commit together."""
from datetime import datetime, timedelta, timezone
import json
import logging
import random

import aiosqlite

from utils import db as storage, nursery_catalog as catalog
from utils.duels import exclusive_player
from utils.gameplay import consume_stack

logger = logging.getLogger('chibibeasts.nursery')
MAX_BULK = 25


def _time(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).strftime('%Y-%m-%d %H:%M:%S')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


async def _one(db, sql, args=()):
    async with db.execute(sql, args) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _all(db, sql, args=()):
    async with db.execute(sql, args) as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def _player(db, user_id):
    player = await _one(db, 'SELECT * FROM players WHERE user_id=?', (user_id,))
    if not player:
        raise ValueError('Choose a starter with /start before opening the Nursery.')
    return player


async def _bonuses(db, user_id):
    perks = await _all(db, 'SELECT * FROM player_perks WHERE user_id=? AND equipped=1', (user_id,))
    sanctuary = await _one(db, 'SELECT gs.* FROM guild_members gm JOIN guild_sanctuary gs '
                           'ON gs.guild_id=gm.guild_id WHERE gm.user_id=?', (user_id,))
    return perks, sanctuary or {}


async def _replay(db, user_id, key, operation, arguments):
    if not isinstance(key, (str, int)) or isinstance(key, bool) or not str(key).strip() or len(str(key)) > 200:
        raise ValueError('A valid request receipt is required. Reopen the Nursery.')
    row = await _one(db, 'SELECT * FROM nursery_requests WHERE user_id=? AND request_key=?', (user_id, str(key)))
    if row:
        if row['operation'] != operation or row['arguments'] != _json(arguments):
            raise ValueError('That request already completed with different choices. Refresh the Nursery.')
        return json.loads(row['result'])
    return None


async def _receipt(db, user_id, key, operation, arguments, result):
    await db.execute('INSERT INTO nursery_requests VALUES(?,?,?,?,?)',
                     (user_id, str(key), operation, _json(arguments), _json(result)))


def _gate(player, egg):
    if player['level'] < egg['min_level']:
        raise ValueError(f"This egg needs trainer level {egg['min_level']}. Your egg is safely stored.")


async def _points(db, user_id, egg_id):
    row = await _one(db, 'SELECT points FROM nursery_research WHERE user_id=? AND family=?',
                     (user_id, catalog.research_key(egg_id)))
    return row['points'] if row else 0


async def _research(db, user_id, egg_id, target):
    pool = catalog.research_pool(egg_id)
    beast = next((b for b in pool if b['id'] == target), None)
    if not beast:
        raise ValueError('Research can select a species only from this egg family’s grade floor.')
    if await _points(db, user_id, egg_id) < catalog.RESEARCH_COST:
        raise ValueError(f'You need {catalog.RESEARCH_COST} duplicate research points for this grade and family.')
    await db.execute('UPDATE nursery_research SET points=points-? WHERE user_id=? AND family=?',
                     (catalog.RESEARCH_COST, user_id, catalog.research_key(egg_id)))
    return beast


async def _grant(db, user_id, egg_id, beast, level, grant_key, *, bond=0, researched=False):
    # Remember the collection before granting. Releasing a hatch cannot make its
    # species "new" again, and legacy collections count on their first new hatch.
    await db.execute('INSERT OR IGNORE INTO nursery_seen SELECT user_id,beast_id FROM player_beasts WHERE user_id=?', (user_id,))
    duplicate = bool(await _one(db, 'SELECT 1 FROM nursery_seen WHERE user_id=? AND beast_id=?', (user_id, beast['id'])))
    await db.execute('INSERT OR IGNORE INTO nursery_seen VALUES(?,?)', (user_id, beast['id']))
    before_points = await _points(db, user_id, egg_id)
    if duplicate and not researched:
        await db.execute('INSERT INTO nursery_research VALUES(?,?,1) ON CONFLICT(user_id,family) '
                         'DO UPDATE SET points=MIN(?,points+1)', (user_id, catalog.research_key(egg_id), catalog.RESEARCH_MAX))
    row_id = await storage.grant_beast(db, user_id, {**beast, 'level': level,
        'caught_from': ('incubation:' if catalog.definition(egg_id)['incubation_hours'] else 'hatch:') + egg_id}, grant_key=grant_key)
    if beast['rarity'] == 'altered_divine':
        await db.execute('UPDATE player_beasts SET is_altered_divine=1,altered_name=? WHERE id=?', (beast['name'], row_id))
    # Only the very first companion is automatically selected.
    owned = await _one(db, 'SELECT COUNT(*) AS n FROM player_beasts WHERE user_id=?', (user_id,))
    if owned['n'] == 1:
        await db.execute('UPDATE player_beasts SET is_active=1 WHERE id=?', (row_id,))
    await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?) WHERE user_id=? AND id=?', (bond, user_id, row_id))
    grant = {'id': row_id, 'egg_id': egg_id, 'beast_id': beast['id'], 'name': beast['name'],
             'rarity': beast['rarity'], 'level': level, 'duplicate': duplicate, 'bond': bond,
             'research': await _points(db, user_id, egg_id), 'researched': researched, 'notified': False}
    grant['research_earned'] = grant['research'] - before_points
    await db.execute('INSERT INTO nursery_grants(id,user_id,egg_id,payload) VALUES(?,?,?,?)',
                     (row_id, user_id, egg_id, _json(grant)))
    return grant


@exclusive_player(allow_trial=True)
async def hatch(user_id, egg_id, quantity=1, *, request_key, rng=random, research_target=None):
    egg = catalog.definition(egg_id)
    if egg['incubation_hours']:
        raise ValueError('This egg needs incubation. Start it in the Nursery.')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_BULK:
        raise ValueError(f'Hatch between 1 and {MAX_BULK} eggs at a time.')
    if research_target and quantity != 1:
        raise ValueError('Research selects one companion from one paid egg at a time.')
    args = [egg_id, quantity, research_target]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _replay(db, user_id, request_key, 'hatch', args)
        if replay is not None:
            grants = replay
        else:
            player = await _player(db, user_id)
            _gate(player, egg)
            perks, sanctuary = await _bonuses(db, user_id)
            if not await consume_stack(db, 'player_inventory', user_id, egg_id, quantity):
                raise ValueError('You do not have that many eggs. Buy one in the Nursery or /shop.')
            grants = []
            for index in range(quantity):
                if research_target:
                    beast = await _research(db, user_id, egg_id, research_target)
                else:
                    rarity = catalog.choose_rarity(egg_id, rng, perks, sanctuary)
                    pool = catalog.species_pool(egg_id, rarity)
                    # Consume attunement only when a same-family/same-tier match exists.
                    matching = [b for b in pool if b['type'] == player.get('astral_type')]
                    if matching:
                        pool = matching
                        player['astral_type'] = None
                        await db.execute('UPDATE players SET astral_type=NULL WHERE user_id=?', (user_id,))
                    beast = rng.choice(pool)
                grants.append(await _grant(db, user_id, egg_id, beast, catalog.hatch_level(egg_id, rng),
                    f'nursery:{user_id}:{request_key}:{index}', researched=bool(research_target)))
            await _receipt(db, user_id, request_key, 'hatch', args, grants)
            await db.commit()
    await notify_grants(user_id, grants)
    return grants


def _incubation(row, count, now):
    ready = _time(row['ready_at']) <= now
    care_at = _time(row['next_tend_at'] or row['started_at'])
    return {**row, 'care_count': count, 'bond_bonus': count, 'ready': ready,
            'care_available': not ready and count < 3 and care_at <= now, 'kind': 'incubation'}


@exclusive_player(allow_trial=True)
async def incubate(user_id, egg_id, *, request_key, now=None):
    egg, now = catalog.definition(egg_id), _time(now)
    if not egg['incubation_hours']:
        raise ValueError('Instant eggs hatch immediately. Choose Hatch in the Nursery.')
    args = [egg_id]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _replay(db, user_id, request_key, 'incubate', args)
        if replay is not None:
            return replay
        player = await _player(db, user_id)
        _gate(player, egg)
        active = await _one(db, 'SELECT COUNT(*) AS n FROM incubating_eggs WHERE user_id=? AND hatched=0', (user_id,))
        if active['n'] >= 3:
            raise ValueError('Your three incubator slots are full. Choose a ready hatchling first.')
        perks, _ = await _bonuses(db, user_id)
        hours = egg['incubation_hours'] * (.8 if any(p['perk_id'] == 'chronos_hourglass' for p in perks) else 1)
        deadline = now + timedelta(hours=hours)
        care_at = now + (deadline - now) / 4
        cursor = await db.execute('INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,started_at,ready_at,'
            'tends_required,tends_done,next_tend_at) VALUES(?,?,?,?,?,?,3,0,?)',
            (user_id, egg_id, egg['name'], egg['grade'], _stamp(now), _stamp(deadline), _stamp(care_at)))
        if not await consume_stack(db, 'player_inventory', user_id, egg_id):
            raise ValueError('You do not own this egg yet. Buy one in the Nursery or /shop.')
        await db.execute('INSERT INTO nursery_incubations VALUES(?,0)', (cursor.lastrowid,))
        row = await _one(db, 'SELECT * FROM incubating_eggs WHERE id=?', (cursor.lastrowid,))
        result = _incubation(row, 0, now)
        await _receipt(db, user_id, request_key, 'incubate', args, result)
        await db.commit()
    return result


async def _owned_incubation(db, user_id, incubation_id):
    row = await _one(db, 'SELECT * FROM incubating_eggs WHERE id=? AND user_id=? AND hatched=0', (incubation_id, user_id))
    if not row:
        raise ValueError('That incubation has already hatched or belongs to another trainer.')
    metadata = await _one(db, 'SELECT * FROM nursery_incubations WHERE incubation_id=?', (incubation_id,))
    count = metadata['care_count'] if metadata else min(3, row.get('tends_done') or 0)
    return row, count


@exclusive_player(allow_trial=True)
async def care(user_id, incubation_id, *, now=None):
    now = _time(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        row, count = await _owned_incubation(db, user_id, incubation_id)
        state = _incubation(row, count, now)
        if state['ready']:
            raise ValueError('Your egg is ready. Choose a hatchling; no further care is needed.')
        if not state['care_available']:
            raise ValueError('Optional care is not available yet, or all three bond bonuses are earned.')
        count += 1
        interval = max(timedelta(minutes=1), (_time(row['ready_at']) - _time(row['started_at'])) / 4)
        row.update(tends_done=count, next_tend_at=_stamp(min(_time(row['ready_at']), now + interval)))
        await db.execute('INSERT INTO nursery_incubations VALUES(?,?) ON CONFLICT(incubation_id) DO UPDATE SET care_count=excluded.care_count', (incubation_id, count))
        await db.execute('UPDATE incubating_eggs SET tends_done=?,next_tend_at=? WHERE id=?', (count, row['next_tend_at'], incubation_id))
        care_key = f'care:{incubation_id}:{count}'
        await _receipt(db, user_id, care_key, 'care_notify', [],
                       {'incubation_id': incubation_id, 'care_count': count})
        await db.commit()
    await notify_care(user_id, request_key=care_key)
    return _incubation(row, count, now)


async def notify_care(user_id, *, request_key=None):
    """Finish saved care progress without replaying the care visit or its bond."""
    from utils.progress import _track_daily_event
    try:
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            sql = 'SELECT request_key FROM nursery_requests WHERE user_id=? AND operation=?'
            args = [user_id, 'care_notify']
            if request_key is not None:
                sql += ' AND request_key=?'
                args.append(request_key)
            pending = await _all(db, sql + ' ORDER BY request_key', args)
    except Exception:
        logger.exception('Nursery care saved; pending progression will retry on next visit for trainer %s', user_id)
        return
    for receipt in pending:
        key = receipt['request_key']
        event_key = f'nursery-tend:{user_id}:{key}'
        try:
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                recorded = await db.execute('INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,?)',
                                            (event_key, user_id, 'tend'))
                if recorded.rowcount:
                    await db.execute("INSERT INTO player_progress VALUES(?,'tend',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1", (user_id,))
                await db.commit()
            await _track_daily_event(user_id, 'tend', event_key=event_key + ':daily')
            async with storage.connect() as db:
                await db.execute('UPDATE nursery_requests SET operation=? WHERE user_id=? AND request_key=? AND operation=?',
                                 ('care_done', user_id, key, 'care_notify'))
                await db.commit()
        except Exception:
            logger.exception('Nursery care %s saved; progression will retry on next visit', key)


def _offer(row):
    return {**row, 'candidates': json.loads(row['candidates']), 'claimed': bool(row['claimed'])}


@exclusive_player(allow_trial=True)
async def prepare_offer(user_id, incubation_id, *, now=None, rng=random):
    now = _time(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        previous = await _one(db, 'SELECT * FROM nursery_offers WHERE incubation_id=? AND user_id=?', (incubation_id, user_id))
        if previous:
            return _offer(previous)
        row, count = await _owned_incubation(db, user_id, incubation_id)
        if _time(row['ready_at']) > now:
            raise ValueError('This egg is still incubating. It finishes even while you are away.')
        egg_id = row['egg_type']
        egg = catalog.definition(egg_id)
        player = await _player(db, user_id)
        perks, sanctuary = await _bonuses(db, user_id)
        rarity = catalog.choose_rarity(egg_id, rng, perks, sanctuary)
        pool = catalog.species_pool(egg_id, rarity)
        chosen = rng.sample(pool, min(3, len(pool)))
        level = catalog.hatch_level(egg_id, rng)
        # Old paid schedules remain claimable below new gates without bypassing
        # the trainer progression introduced after they were purchased.
        if player['level'] < egg['min_level']:
            level = min(level, max(1, player['level']))
        candidates = [{'index': i, 'beast_id': b['id'], 'name': b['name'], 'rarity': rarity,
                       'level': level, 'description': b.get('description', ''), 'type': b['type']}
                      for i, b in enumerate(chosen)]
        cursor = await db.execute('INSERT INTO nursery_offers(user_id,incubation_id,egg_id,candidates) VALUES(?,?,?,?)',
                                  (user_id, incubation_id, egg_id, _json(candidates)))
        result = _offer(await _one(db, 'SELECT * FROM nursery_offers WHERE id=?', (cursor.lastrowid,)))
        await db.commit()
    return result


@exclusive_player(allow_trial=True)
async def claim_offer(user_id, offer_id, index, *, request_key, research_target=None):
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError('Choose one of the saved hatchlings.')
    args = [offer_id, index, research_target]
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        replay = await _replay(db, user_id, request_key, 'claim', args)
        if replay is not None:
            grant = replay
        else:
            offer = await _one(db, 'SELECT * FROM nursery_offers WHERE id=? AND user_id=?', (offer_id, user_id))
            if not offer or offer['claimed']:
                raise ValueError('That hatchling was already chosen or belongs to another trainer.')
            row, count = await _owned_incubation(db, user_id, offer['incubation_id'])
            candidates = json.loads(offer['candidates'])
            if not 0 <= index < len(candidates):
                raise ValueError('Choose one of the saved hatchlings.')
            candidate = candidates[index]
            beast = (await _research(db, user_id, offer['egg_id'], research_target) if research_target
                     else storage.get_beast_data(candidate['beast_id']))
            grant = await _grant(db, user_id, offer['egg_id'], beast, candidate['level'],
                                 f"egg:{row['id']}", bond=count, researched=bool(research_target))
            await db.execute('UPDATE incubating_eggs SET hatched=1 WHERE id=?', (row['id'],))
            await db.execute('UPDATE nursery_offers SET claimed=1,grant_id=? WHERE id=?', (grant['id'], offer_id))
            await _receipt(db, user_id, request_key, 'claim', args, grant)
            await db.commit()
    await notify_grants(user_id, [grant])
    return grant


async def research_targets(user_id, egg_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await _player(db, user_id)
        return catalog.research_pool(egg_id) if await _points(db, user_id, egg_id) >= catalog.RESEARCH_COST else []


async def notify_grants(user_id, grants):
    """Retry keyed progression after a committed reward; never retry payment."""
    from utils.events import acquired_beast
    from utils.progress import check_achievements, unlock_simple_achievement, _track_daily_event
    for grant in grants:
        try:
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                saved = await _one(db, 'SELECT * FROM nursery_grants WHERE id=? AND user_id=?', (grant['id'], user_id))
            if not saved or saved['notified']:
                grant['notified'] = bool(saved and saved['notified'])
                continue
            beast = storage.get_beast_data(grant['beast_id'])
            await acquired_beast(user_id, grant['id'], beast, 'hatch')
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                receipt = await db.execute('INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,?)',
                    (f"nursery-hatch:{grant['id']}", user_id, 'hatch'))
                if receipt.rowcount:
                    await db.execute("INSERT INTO player_progress VALUES(?,'hatch',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1", (user_id,))
                    await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+1) WHERE id=? AND user_id=?', (grant['id'], user_id))
                await db.commit()
            await _track_daily_event(user_id, 'hatch', event_key=f"nursery-daily:{grant['id']}")
            await unlock_simple_achievement(user_id, 'first_steps')
            await _collection_reward(user_id, beast)
            if grant['egg_id'] == 'world_tree_seed':
                from utils.story import advance_quest_step
                await advance_quest_step(user_id, 'seed_hatched')
            await check_achievements(user_id)
            async with storage.connect() as db:
                await db.execute('UPDATE nursery_grants SET notified=1 WHERE id=? AND user_id=?', (grant['id'], user_id))
                await db.commit()
            grant['notified'] = True
        except Exception:
            logger.exception('Nursery reward %s saved; progression will retry on next visit', grant['id'])


async def _collection_reward(user_id, beast):
    # Existing collection rewards remain keyed by their existing achievement.
    from cogs.hatch import COLLECTION_REWARDS
    from utils.rewards import shards
    collection = beast.get('collection')
    if collection not in COLLECTION_REWARDS:
        return
    required = {key for key, b in storage.load_beasts().items() if b.get('collection') == collection}
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT DISTINCT beast_id FROM player_beasts WHERE user_id=?', (user_id,)) as cursor:
            owned = {r[0] for r in await cursor.fetchall()}
        if not required or not required.issubset(owned):
            return
        claimed = await db.execute('INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(?,?)',
            (user_id, 'collection_' + collection.lower().replace(' ', '_')))
        if claimed.rowcount:
            reward = COLLECTION_REWARDS[collection]
            await shards(db, user_id, reward['celestial_shards'])
            await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (reward['gold'], user_id))
        await db.commit()


async def snapshot(user_id, *, now=None):
    now = _time(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        pending = await _all(db, 'SELECT payload FROM nursery_grants WHERE user_id=? AND notified=0 ORDER BY id', (user_id,))
    await notify_grants(user_id, [json.loads(r['payload']) for r in pending])
    await notify_care(user_id)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        player = await _player(db, user_id)
        perks, sanctuary = await _bonuses(db, user_id)
        inventory = {r['item_id']: r['quantity'] for r in await _all(db,
            'SELECT item_id,SUM(quantity) AS quantity FROM player_inventory WHERE user_id=? GROUP BY item_id', (user_id,))}
        research = {r['family']: r['points'] for r in await _all(db, 'SELECT * FROM nursery_research WHERE user_id=?', (user_id,))}
        eggs = []
        for key, egg in catalog.definitions().items():
            odds = catalog.rarity_pool(key, perks, sanctuary)
            candidates = []
            for rarity in odds:
                pool = catalog.species_pool(key, rarity)
                matching = [b for b in pool if b['type'] == player.get('astral_type')]
                candidates.extend(matching if matching and not egg['incubation_hours'] else pool)
            eggs.append({**egg, 'quantity': inventory.get(key, 0), 'odds': odds,
                'candidates': candidates, 'attunement': player.get('astral_type') if not egg['incubation_hours'] else None,
                'research': research.get(catalog.research_key(key), 0), 'research_cost': catalog.RESEARCH_COST,
                'locked': player['level'] < egg['min_level']})
        incubations = []
        for row in await _all(db, 'SELECT e.*,n.care_count FROM incubating_eggs e LEFT JOIN nursery_incubations n '
                              'ON n.incubation_id=e.id WHERE e.user_id=? AND e.hatched=0 ORDER BY e.ready_at,e.id', (user_id,)):
            count = row['care_count'] if row['care_count'] is not None else min(3, row.get('tends_done') or 0)
            incubations.append(_incubation(row, count, now))
        offers = [_offer(r) for r in await _all(db, 'SELECT * FROM nursery_offers WHERE user_id=? AND claimed=0 ORDER BY id', (user_id,))]
        recent = [{**json.loads(r['payload']), 'notified': bool(r['notified'])} for r in await _all(db,
            'SELECT payload,notified FROM nursery_grants WHERE user_id=? ORDER BY id DESC LIMIT 10', (user_id,))]
    return {'player': player, 'eggs': eggs, 'incubations': incubations, 'offers': offers, 'recent': recent}
