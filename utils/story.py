"""Durable chapter progression and atomic story rewards."""
import json
import aiosqlite
from utils import db as storage
RELATIONSHIP_LEVELS = {'stranger': ('Stranger', '⚪'), 'known': ('Known', '🟢'), 'trusted': ('Trusted', '🔵'), 'companion': ('Companion', '🌸')}

def relationship_order(level):
    return list(RELATIONSHIP_LEVELS).index(level) if level in RELATIONSHIP_LEVELS else 0

def relationship_display(level):
    label, emoji = RELATIONSHIP_LEVELS.get(level, RELATIONSHIP_LEVELS['stranger'])
    return f'{emoji} {label}'

def load_questline():
    from utils.catalog import load_catalog
    return load_catalog('questline.json')['questline']

def load_npcs():
    from utils.catalog import load_catalog
    return load_catalog('npcs.json')['npcs']

def state_from_row(user_id, row):
    if not row:
        return {'user_id': user_id, 'current_chapter': None, 'completed_chapters': [], 'step_progress': {}, 'collected_relics': [], 'npc_relationships': {}}
    return {'user_id': user_id, 'current_chapter': row['current_chapter'], **{k: json.loads(row[k] or default) for k, default in [('completed_chapters', '[]'), ('step_progress', '{}'), ('collected_relics', '[]'), ('npc_relationships', '{}')]}}

async def read_state(db, user_id):
    db.row_factory = aiosqlite.Row
    async with db.execute('SELECT * FROM player_questline WHERE user_id=?', (user_id,)) as c:
        return state_from_row(user_id, await c.fetchone())

async def write_state(db, state):
    await db.execute('INSERT INTO player_questline(user_id,current_chapter,completed_chapters,step_progress,collected_relics,npc_relationships)\n        VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET current_chapter=excluded.current_chapter,\n        completed_chapters=excluded.completed_chapters,step_progress=excluded.step_progress,\n        collected_relics=excluded.collected_relics,npc_relationships=excluded.npc_relationships,last_updated=CURRENT_TIMESTAMP', (state['user_id'], state['current_chapter'], *[json.dumps(state[k]) for k in ('completed_chapters', 'step_progress', 'collected_relics', 'npc_relationships')]))

async def get_quest_state(user_id):
    async with storage.connect() as db:
        return await read_state(db, user_id)

async def save_quest_state(state):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        await write_state(db, state)
        await db.commit()

def get_relationship_level(state, npc_id):
    return state['npc_relationships'].get(npc_id, 'stranger')

def check_chapter_unlock(chapter, state, player):
    if chapter['id'] == 'chapter_1' and player.get('level', 1) < 3:
        return (False, 'Reach trainer level 3. Try /challenge, /explore and /daily.')
    cond = chapter.get('unlock_condition', '')
    if cond.endswith('_complete') and cond[:-9] not in state['completed_chapters']:
        return (False, 'Finish the previous chapter first.')
    return (True, '')

def complete(chapter, state):
    p = state['step_progress'].get(chapter['id'], {})
    return all((p.get(s['id'], 0) >= s.get('target', 1) for s in chapter['steps']))

def apply_event(state, chapter, event, amount=1, **ctx):
    p = state['step_progress'].setdefault(chapter['id'], {})
    for s in chapter['steps']:
        key = s['id']
        kind = s['type']
        target = s.get('target', 1)
        current = p.get(key, 0)
        if current >= target:
            continue
        add = 0
        if kind == 'catch_count' and event == 'catch':
            add = amount
        elif kind == 'catch_specific_beast' and event == 'catch' and (ctx.get('beast_id') in s.get('target_beasts', [])):
            add = amount
        elif kind == 'catch_type' and event == 'catch' and (ctx.get('beast_type') in s.get('beast_types', [])):
            add = amount
        elif kind == 'explore_count' and event == 'explore':
            biome = ctx.get('biome', '')
            if s.get('track_biome') and s['track_biome'] != biome:
                continue
            if s.get('track_unique_biomes'):
                visited = set(json.loads(p.get(key + '_biomes', '[]')))
                if biome:
                    visited.add(biome)
                p[key + '_biomes'] = json.dumps(sorted(visited))
                p[key] = len(visited)
                continue
            add = amount
        elif kind == 'material_collect' and event == 'material_gained' and (ctx.get('material_id') == s.get('material_id')):
            add = amount
        elif kind == 'material_collect_specific' and event == 'material_gained':
            for mid, qty in s['materials'].items():
                if ctx.get('material_id') == mid:
                    p[key + '_' + mid] = min(qty, p.get(key + '_' + mid, 0) + amount)
            p[key] = int(all((p.get(key + '_' + mid, 0) >= qty for mid, qty in s['materials'].items())))
            continue
        elif kind == 'relic_collect':
            if event == 'explore' and s.get('biome') in ctx.get('biome', ''):
                search = key + '_search'
                p[search] = min(3, p.get(search, 0) + 1)
                if p[search] >= 3:
                    if s['relic_id'] not in state['collected_relics']:
                        state['collected_relics'].append(s['relic_id'])
                    add = 1
            elif event == 'relic_found' and ctx.get('relic_id') == s['relic_id']:
                if s['relic_id'] not in state['collected_relics']:
                    state['collected_relics'].append(s['relic_id'])
                add = 1
        elif kind == 'battle_win' and event == 'battle_win':
            add = amount
        elif kind == 'craft_count' and event == 'craft':
            add = amount
        elif kind == 'raid_damage' and event == 'raid_damage':
            add = amount
        elif kind == 'raid_participate' and event == 'raid_participate':
            add = 1
        elif kind == 'raid_count' and event == 'raid_complete' and (not s.get('raid_type') or s['raid_type'] == ctx.get('raid_type')):
            add = amount
        elif kind == 'raid_complete' and event == 'raid_complete':
            add = 1
        elif kind == 'use_command' and event == 'questline_command':
            add = 1
        elif kind == 'seed_hatched' and event == 'seed_hatched':
            add = 1
        elif kind == 'player_level' and event == 'level_up':
            p[key] = min(target, ctx.get('level', 0))
            continue
        elif kind == 'server_bestiary_count' and event == 'bestiary_update':
            p[key] = min(target, ctx.get('count', 0))
            continue
        elif kind == 'beast_count' and event == 'beast_count_check':
            p[key] = min(target, ctx.get('count', 0))
            continue
        p[key] = min(target, current + add)

async def advance_quest_step(user_id, event_type, **kwargs):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        state = await read_state(db, user_id)
        chapter = load_questline()['chapters'].get(state['current_chapter'])
        if not chapter or chapter['id'] in state['completed_chapters']:
            return None
        apply_event(state, chapter, event_type, **kwargs)
        await write_state(db, state)
        await db.commit()
        return chapter['id'] if complete(chapter, state) else None

async def reconcile(db, state, chapter, guild_id=0):
    uid = state['user_id']
    p = state['step_progress'].setdefault(chapter['id'], {})
    async with db.execute('SELECT * FROM players WHERE user_id=?', (uid,)) as c:
        player = dict(await c.fetchone())
    async with db.execute('SELECT beast_id FROM player_beasts WHERE user_id=?', (uid,)) as c:
        owned = [r[0] for r in await c.fetchall()]
    async with db.execute('SELECT material_id,quantity FROM player_materials WHERE user_id=?', (uid,)) as c:
        mats = dict(await c.fetchall())
    async with db.execute('SELECT COUNT(*) FROM bestiary WHERE guild_id=?', (guild_id,)) as c:
        bestiary = (await c.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM incubating_eggs WHERE user_id=? AND egg_type='world_tree_seed' AND hatched=1", (uid,)) as c:
        seed = (await c.fetchone())[0]
    for s in chapter['steps']:
        key = s['id']
        kind = s['type']
        target = s.get('target', 1)
        if kind == 'player_level':
            p[key] = min(target, player['level'])
        elif kind == 'beast_count':
            p[key] = min(target, len(owned))
        elif kind == 'server_bestiary_count':
            p[key] = min(target, bestiary)
        elif kind == 'material_collect':
            p[key] = min(target, mats.get(s['material_id'], 0))
        elif kind == 'material_collect_specific':
            for mid, qty in s['materials'].items():
                p[key + '_' + mid] = min(qty, mats.get(mid, 0))
            p[key] = int(all((mats.get(mid, 0) >= qty for mid, qty in s['materials'].items())))
        elif kind == 'relic_collect':
            p[key] = int(s['relic_id'] in state['collected_relics'])
        elif kind == 'seed_hatched':
            p[key] = int(seed > 0)
        elif kind == 'catch_specific_beast' and chapter['id'] in {'chapter_6', 'chapter_8'}:
            p[key] = int(bool(set(owned) & set(s['target_beasts'])))
        elif kind == 'raid_complete':
            async with db.execute("SELECT value FROM player_progress WHERE user_id=? AND stat_key='raid_complete'", (uid,)) as c:
                r = await c.fetchone()
            p[key] = max(p.get(key, 0), int(bool(r and r[0] > 0)))
    return player

async def journal(user_id, guild_id=0):
    chapters = load_questline()['chapters']
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        state = await read_state(db, user_id)
        remaining = [key for key in chapters if key not in state['completed_chapters']]
        if not remaining:
            state['current_chapter'] = None
            await write_state(db, state)
            await db.commit()
            return (state, None, False)
        key = remaining[0]
        async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
            player = dict(await c.fetchone())
        allowed, reason = check_chapter_unlock(chapters[key], state, player)
        if not allowed:
            raise ValueError(reason)
        state['current_chapter'] = key
        chapter = chapters[key]
        p = state['step_progress'].setdefault(key, {})
        introduced = bool(p.get('_introduced'))
        p['_introduced'] = 1
        await reconcile(db, state, chapter, guild_id)
        if introduced:
            apply_event(state, chapter, 'questline_command')
        await write_state(db, state)
        await db.commit()
        return (state, chapter, not introduced)

async def claim_chapter(user_id, chapter_id, guild_id=0, choice=None):
    from utils.gameplay import add_stack
    from utils.progress import grant_achievement
    chapters = load_questline()['chapters']
    chapter = chapters[chapter_id]
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        state = await read_state(db, user_id)
        if state['current_chapter'] != chapter_id or chapter_id in state['completed_chapters']:
            raise ValueError('That chapter has already been claimed or is not active.')
        player = await reconcile(db, state, chapter, guild_id)
        if not complete(chapter, state):
            raise ValueError('Finish the objectives first. Check /questline.')
        if not set(chapter.get('required_relics', [])) <= set(state['collected_relics']):
            raise ValueError('Bring all four relics first.')
        if chapter.get('choices'):
            if choice not in chapter['choices']:
                raise ValueError('Choose your answer before completing this chapter.')
            await db.execute('INSERT INTO story_choices VALUES(?,?,?)', (user_id, chapter_id, choice))
        cursor = await db.execute('INSERT OR IGNORE INTO story_claims(user_id,chapter_id) VALUES(?,?)', (user_id, chapter_id))
        if not cursor.rowcount:
            raise ValueError('That reward has already been claimed.')
        for mid, qty in chapter.get('consume_materials', {}).items():
            cur = await db.execute('UPDATE player_materials SET quantity=quantity-? WHERE user_id=? AND material_id=? AND quantity>=?', (qty, user_id, mid, qty))
            if not cur.rowcount:
                raise ValueError(f"Bring {qty} {mid.replace('_', ' ')} to hand in.")
        reward = chapter['reward']
        from utils.rewards import shards, player_exp
        await player_exp(db, user_id, reward.get('exp', 0))
        await shards(db, user_id, reward.get('celestial_shards', 0))
        await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (reward.get('gold', 0), user_id))
        for group, table in [('items', 'player_inventory'), ('materials', 'player_materials')]:
            for iid, qty in reward.get(group, {}).items():
                await add_stack(db, table, user_id, iid, qty)
        if reward.get('relic') and reward['relic'] not in state['collected_relics']:
            state['collected_relics'].append(reward['relic'])
        for npc, rel in reward.get('relationship_unlock', {}).items():
            if relationship_order(rel) > relationship_order(state['npc_relationships'].get(npc, 'stranger')):
                state['npc_relationships'][npc] = rel
        state['completed_chapters'].append(chapter_id)
        state['current_chapter'] = next((key for key in chapters if key not in state['completed_chapters']), None)
        await write_state(db, state)
        if reward.get('achievement'):
            await grant_achievement(db, user_id, reward['achievement'])
        if chapter_id == 'chapter_10':
            await db.execute("INSERT INTO player_progress VALUES(?,'loom_restored',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=1", (user_id,))
            await db.execute('UPDATE players SET title=? WHERE user_id=?', (chapter['choices'][choice]['title'], user_id))
        await db.commit()
    from utils.progress import check_achievements
    await check_achievements(user_id)
    return (state, reward)
