"""One gameplay result feeds story, daily quests, milestones and companion bonds."""
import json
from utils import db as storage

async def record_event(user_id, event, amount=1, **context):
    from utils.progress import _track_daily_event, check_achievements, unlock_simple_achievement
    from cogs.questline import advance_quest_step
    if not isinstance(amount,int) or amount<=0:raise ValueError('Event amount must be a positive integer.')
    key = context.get('event_key')
    async with storage.connect() as db:
        if key:
            async with db.execute('SELECT 1 FROM gameplay_events WHERE event_key=?',(key,)) as c:
                if await c.fetchone():return []
        await db.execute('BEGIN IMMEDIATE')
        if key:
            cursor = await db.execute('INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,?)', (key, user_id, event))
            if not cursor.rowcount:
                return []
        await db.execute('INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,?,?) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+excluded.value', (user_id, event, amount))
        if event == 'catch':
            await db.execute('UPDATE players SET total_catches=total_catches+? WHERE user_id=?', (amount, user_id))
        if event == 'battle_win':
            await db.execute('UPDATE players SET wins=wins+? WHERE user_id=?', (amount, user_id))
        await db.commit()
    completed = await _track_daily_event(user_id, event, amount)
    if event in {'pvp_win', 'spar_win'}:
        combined = {**context}
        if combined.get('event_key'):
            combined['event_key'] += '/battle_win'
        completed += await record_event(user_id, 'battle_win', amount, **combined)
    story_event = {'catch': 'catch', 'explore': 'explore', 'craft': 'craft', 'battle_win': 'battle_win', 'raid_damage': 'raid_damage', 'raid_attack': 'raid_participate', 'raid_complete': 'raid_complete'}.get(event)
    if story_event:
        await advance_quest_step(user_id, story_event, amount=amount, **{k: v for k, v in context.items() if k != 'event_key'})
    if event == 'catch' and context.get('rarity') in {'rare', 'epic', 'legendary', 'divine', 'altered_divine', 'ancient', 'corrupted'}:
        completed += await _track_daily_event(user_id, 'catch_rare', amount)
    if event in {'battle_win', 'play', 'train', 'hatch'}:
        from utils.gameplay import bond_with
        await bond_with(user_id, context.get('beast_row_id'), 3 if event == 'play' else 2 if event == 'battle_win' else 1)
    if event == 'explore':
        from utils.habitats import biome_key
        if biome_key(context.get('biome', 'woods')) == 'loom':
            await unlock_simple_achievement(user_id, 'first_celestial_loom')
    if event == 'raid_complete':
        await unlock_simple_achievement(user_id, 'first_raid_win')
        if context.get('raid_type') == 'ancient':
            await unlock_simple_achievement(user_id, 'first_ancient_win')
    await check_achievements(user_id)
    return completed

async def acquired_beast(user_id, row_id, beast, source):
    """Called once after a committed grant; UI paths never increment catches twice."""
    from utils.progress import _track_daily_event
    from cogs.questline import advance_quest_step
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'acquire')", (f'acquire:{row_id}', user_id))
        if not cursor.rowcount:
            return
        await db.execute('INSERT INTO global_catch_counts(beast_id,catch_count) VALUES(?,1) ON CONFLICT(beast_id) DO UPDATE SET catch_count=catch_count+1', (beast['id'],))
        async with db.execute('SELECT COUNT(*) FROM player_beasts WHERE user_id=?', (user_id,)) as c:
            count = (await c.fetchone())[0]
        await db.commit()
    await advance_quest_step(user_id, 'beast_count_check', count=count)
    if source in {'discover', 'wild_battle', 'wild'}:
        await record_event(user_id, 'catch', beast_id=beast['id'], beast_type=beast['type'], rarity=beast['rarity'], event_key=f'catch:{row_id}')
PERK_UNLOCKS = {'whimsy_merchant': ('level', 3), 'pixie_pocket': ('total_catches', 3), 'fable_scholar': ('level', 5), 'bramble_walker': ('explore', 5), 'tether_of_fate': ('wins', 5), 'stardust_touch': ('total_catches', 15), 'spellbound_focus': ('wins', 15), 'chronos_hourglass': ('level', 15), 'aura_of_phoenix': ('raid_complete', 3), 'astral_resonance': ('chapter', 5), 'glimmering_fortune': ('chapter', 10), 'genesis_spark': ('level', 25)}

async def unlock_perks(db, user_id, player):
    async with db.execute('SELECT stat_key,value FROM player_progress WHERE user_id=?', (user_id,)) as c:
        counters = dict(await c.fetchall())
    async with db.execute('SELECT completed_chapters FROM player_questline WHERE user_id=?', (user_id,)) as c:
        state = await c.fetchone()
    counters['chapter'] = len(json.loads(state[0] or '[]')) if state else 0
    counters.update({k: player.get(k, 0) for k in ('level', 'wins', 'total_catches')})
    newly = []
    for perk, (stat, target) in PERK_UNLOCKS.items():
        if counters.get(stat, 0) < target:
            continue
        async with db.execute('SELECT 1 FROM player_perks WHERE user_id=? AND perk_id=?', (user_id, perk)) as c:
            owned = await c.fetchone()
        if not owned:
            await db.execute('INSERT INTO player_perks(user_id,perk_id,equipped) VALUES(?,?,0)', (user_id, perk))
            newly.append(perk)
    return newly
