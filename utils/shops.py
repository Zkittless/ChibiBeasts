"""Fresh, atomic purchases. Open shop messages never determine ownership or balance."""
import json
from datetime import datetime, timezone
import aiosqlite
from utils import db as storage
from utils.gameplay import add_stack
GUILD_ITEMS = {'raid_totem': (20, 1, "Boost your next raid's damage by 20%."), 'beastkeeper_seal': (15, 1, 'Boost your next three wild encounters.'), 'guild_elixir': (10, 1, 'Restore 50% HP.'), 'ancient_compass': (35, 5, 'Find Rare or higher on your next exploration.'), 'guild_crest': (50, 5, 'Unlock the Guild Heart title.'), 'loom_fragment': (80, 10, "Move an egg's tending and hatch schedule six hours closer."), 'sanctuary_blueprint': (150, 15, 'Pay for one eligible Sanctuary upgrade instead of tokens.')}

async def shard_effect(db, user_id, sid, item):
    kind = item['type']
    if kind == 'explore_boost':
        await db.execute('UPDATE players SET divine_compass_charges=divine_compass_charges+3 WHERE user_id=?', (user_id,))
        return 'Three Celestial Loom explorations now have 20% Divine odds. Charges wait until you visit the Loom.'
    if kind == 'key':
        await db.execute('UPDATE players SET prism_keys=prism_keys+1 WHERE user_id=?', (user_id,))
        return 'Your next Celestial Loom exploration has 30% Divine odds.'
    grant = item.get('grant_item_id', sid)
    await add_stack(db, 'player_inventory', user_id, grant)
    return 'Added to inventory. ' + ('Use /ancient to summon.' if kind == 'grant_item' else 'Use /use to activate it when ready.')

async def purchase_shard(user_id, sid):
    from cogs.utilities import SHARD_SHOP
    item = SHARD_SHOP[sid]
    week = datetime.now(timezone.utc).strftime('%G-W%V')
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT celestial_shards,shard_shop_week FROM players WHERE user_id=?', (user_id,)) as c:
            player = await c.fetchone()
        if not player or player['celestial_shards'] < item['cost']:
            raise ValueError(f"You need {item['cost']} Celestial Shards.")
        try:
            limits = json.loads(player['shard_shop_week'] or '{}')
        except json.JSONDecodeError:
            limits = {}
        if limits.get('week') != week:
            limits = {'week': week}
        if item['weekly_limit'] and limits.get(sid, 0) >= item['weekly_limit']:
            raise ValueError('You have bought the weekly limit. Resets Monday UTC.')
        await db.execute('UPDATE players SET celestial_shards=celestial_shards-? WHERE user_id=?', (item['cost'], user_id))
        limits[sid] = limits.get(sid, 0) + 1
        await db.execute('UPDATE players SET shard_shop_week=? WHERE user_id=?', (json.dumps(limits), user_id))
        result = await shard_effect(db, user_id, sid, item)
        await db.commit()
        return (result, player['celestial_shards'] - item['cost'], limits)

async def purchase_guild(user_id, item_id):
    cost, required, _ = GUILD_ITEMS[item_id]
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT g.level FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.user_id=?', (user_id,)) as c:
            g = await c.fetchone()
        if not g or g['level'] < required:
            raise ValueError(f'Join a level-{required} guild first.')
        cur = await db.execute('UPDATE players SET guild_tickets=guild_tickets-? WHERE user_id=? AND guild_tickets>=?', (cost, user_id, cost))
        if not cur.rowcount:
            raise ValueError(f'You need {cost} Guild Tickets.')
        await add_stack(db, 'player_inventory', user_id, item_id)
        await db.commit()
    return 'Added to inventory. Use /build to spend a Blueprint; other items use /use.'
