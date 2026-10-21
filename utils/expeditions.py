"""Saved branching trips and one shared exploration-charge budget.

Every choice is checked against a saved revision in a short writer transaction.
Battle playback and Discord calls run outside that transaction. One trip grants
at most one material or one discovered egg, and one companion, across retries.
"""
import asyncio
from dataclasses import dataclass
import json
import logging
import random
import time
from uuid import uuid4
import aiosqlite
from utils import db as storage
from utils.catalog import load_catalog
from utils.economy_balance import gold_reward_level
from utils.gameplay import add_stack
from utils.habitats import BIOMES, REGIONAL_MATERIALS, biome_key, encounter_level, material_pool, pick_encounter
from utils.progression import encounter_trainer_xp
CAPACITY = 3
INTERVAL = 1800

@dataclass(frozen=True)
class Energy:
    charges: int
    next_in: int
    interval: int

def decode(row):
    return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

async def _energy(db, user_id, now, *, spend=False):
    async with db.execute('SELECT explore_last_at FROM players WHERE user_id=?', (user_id,)) as c:
        player = await c.fetchone()
    if player is None:
        raise ValueError('Choose a companion with /start first.')
    async with db.execute('SELECT perk_id FROM player_perks WHERE user_id=? AND equipped=1', (user_id,)) as c:
        perks = {r[0] for r in await c.fetchall()}
    interval = int(INTERVAL * (0.9 if 'bramble_walker' in perks else 1) * (0.8 if 'chronos_hourglass' in perks else 1))
    async with db.execute('SELECT charges,regenerated_at FROM exploration_energy WHERE user_id=?', (user_id,)) as c:
        row = await c.fetchone()
    charges, anchor = (row[0], row[1]) if row else (0, player[0]) if player[0] else (1, now)
    elapsed = max(0, now - anchor)
    gained = int(elapsed // interval)
    charges = min(CAPACITY, charges + gained)
    anchor = now if charges == CAPACITY else anchor + gained * interval
    if spend:
        if not charges:
            raise ValueError(f'Your next exploration charge is ready in {max(1, round(interval - (now - anchor)))} seconds.')
        charges -= 1
        await db.execute('UPDATE players SET explore_last_at=? WHERE user_id=?', (now, user_id))
    await db.execute('INSERT INTO exploration_energy VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET charges=excluded.charges,regenerated_at=excluded.regenerated_at', (user_id, charges, anchor))
    return Energy(charges, 0 if charges == CAPACITY else max(1, round(interval - (now - anchor))), interval)

async def energy(user_id, *, now=None):
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        result = await _energy(db, user_id, time.time() if now is None else now)
        await db.commit()
    return result

async def current(user_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM expeditions WHERE user_id=? AND status IN ('active','battle')", (user_id,)) as c:
            return decode(await c.fetchone())

async def read(user_id, trip_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM expeditions WHERE id=? AND user_id=?', (trip_id, user_id)) as c:
            return decode(await c.fetchone())

async def latest(user_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM expeditions WHERE user_id=? AND status='complete' ORDER BY updated_at DESC LIMIT 1", (user_id,)) as c:
            return decode(await c.fetchone())

async def _trainer(db, user_id, realm):
    if realm not in BIOMES:
        raise ValueError('Choose a destination from the journal.')
    async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
        player = await c.fetchone()
    if not player:
        raise ValueError('Choose a companion with /start first.')
    if player['level'] < BIOMES[realm]['min_level']:
        raise ValueError(f"Reach trainer level {BIOMES[realm]['min_level']} to visit this realm.")
    async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND is_active=1', (user_id,)) as c:
        companion = await c.fetchone()
    if not companion:
        raise ValueError('Choose an active companion before setting out.')
    async with db.execute('SELECT 1 FROM beast_market WHERE beast_row_id=?', (companion['id'],)) as c:
        if await c.fetchone():
            raise ValueError('Delist your companion before setting out.')
    if storage.is_knocked_out(dict(companion)) or companion['hp'] <= 0:
        raise ValueError('Your companion needs recovery. Choose a healthy companion or use a revive.')
    return (dict(player), dict(companion))

async def begin(user_id, realm, *, quick=False, now=None, rng=random, guild_id=0, legacy=False, adventure_version=2):
    if adventure_version == 3 and not quick and not legacy:
        from utils import journey_service
        return await journey_service.begin(user_id, realm, now=now, rng=rng, guild_id=guild_id)
    from utils.duels import reserve_fighters
    from utils import combat
    now = time.time() if now is None else now
    trip_id = None
    try:
        async with combat.lock_states([user_id]):
            with reserve_fighters([user_id]):
                async with storage.connect() as db:
                    db.row_factory = aiosqlite.Row
                    await storage.recover_beasts(user_id, db)
                    await db.execute('BEGIN IMMEDIATE')
                    from utils.trials import assert_available
                    await assert_available(db, user_id)
                    player, companion = await _trainer(db, user_id, realm)
                    async with db.execute("SELECT status FROM expeditions WHERE user_id=? AND status IN ('active','battle','quick')", (user_id,)) as cursor:
                        active = await cursor.fetchone()
                    if active:
                        raise ValueError('An exploration is already underway. Open your Field Journal to continue it.')
                    await _energy(db, user_id, now, spend=True)
                    payload = {'mode': 'quick'}
                    if not quick:
                        mats = load_catalog('materials.json')['materials']
                        material = rng.choice(material_pool({'id': '', 'type': ''}, realm))
                        tier = mats[material]['rarity']
                        alternatives = [m for m in REGIONAL_MATERIALS[biome_key(realm)] if mats[m]['rarity'] == tier and m != material]
                        offered = [material] + rng.sample(alternatives, min(2, len(alternatives)))
                        from utils.story import quest_material_options
                        story_materials = await quest_material_options(db, user_id, biome_key(realm))
                        offered.extend(mid for mid in story_materials if mid in mats and mid not in offered)
                        level = player['level']
                        destination_level = sum(BIOMES[realm]['level_range']) // 2
                        gold_level = gold_reward_level(destination_level)
                        async with db.execute("SELECT 1 FROM player_perks WHERE user_id=? AND equipped=1 AND perk_id='pixie_pocket'", (user_id,)) as cursor:
                            pixie = bool(await cursor.fetchone())
                        gold = rng.randint(gold_level * 3, gold_level * 6)
                        reward_exp = encounter_trainer_xp(level, destination_level, effort=.55)
                        payload = dict(mode='expedition', stage=0, route='', material=material, offered=offered, gold=int(gold * 1.1) if pixie else gold, exp=reward_exp, base_exp=reward_exp, encounter='', encounter_result='', caught=False, bond=0, happiness=0, restore=0, notes=[], level=level, grant_id=None, guild_id=guild_id)
                        if not legacy:
                            from utils import adventure_party, expedition_events
                            party = await adventure_party.rows(user_id, db=db)
                            for member in party:
                                data = storage.get_beast_data(member['beast_id'])
                                member.update(type=data['type'], name=data['name'])
                            expedition_events.initialize(payload, realm, rng.getrandbits(64), party)
                            expedition_events.prepare_nest(payload, biome_key(realm))
                    trip_id = uuid4().hex
                    await db.execute('INSERT INTO expeditions(id,user_id,realm,status,companion_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (trip_id, user_id, realm, 'quick' if quick else 'active', companion['id'], json.dumps(payload), now, now))
                    await db.commit()
        return await read(user_id, trip_id)
    except BaseException:
        # If cancellation lands just after commit, quick exploration has no
        # resumable page. Remove only this call's marker, keeping its charge.
        if quick and trip_id:
            await asyncio.shield(finish_quick(user_id, trip_id))
        raise

async def finish_quick(user_id, trip_id):
    async with storage.connect() as db:
        await db.execute("DELETE FROM expeditions WHERE id=? AND user_id=? AND status='quick'", (trip_id, user_id))
        await db.commit()

def options(trip):
    p = trip['payload']
    if trip['status'] != 'active':
        return ()
    if p.get('adventure_version') == 3:
        return tuple(c['value'] for c in presentation(trip)['choices'])
    if p.get('adventure_version') == 2:
        return tuple(c['value'] for c in presentation(trip)['choices'])
    if p['stage'] == 0:
        return ('tracks', 'gather', 'shrine')
    if p['stage'] == 1:
        return ('challenge', 'approach', 'observe') if p['route'] == 'tracks' else tuple(('material:' + m for m in p['offered'])) if p['route'] == 'gather' else ('repair', 'study', 'rest')
    return ('return', 'rustle') if not p['encounter'] else ('return',)

def presentation(trip):
    """A pure presentation contract for the saved v2 journal page."""
    from utils import expedition_events
    if trip['payload'].get('adventure_version') == 3:
        from utils import journeys
        return journeys.presentation(trip)
    if trip['payload'].get('adventure_version') != 2:
        return None
    return expedition_events.presentation(trip)

async def _encounter(db, user_id, realm, p, rng):
    if p['encounter']:
        return
    from utils.hatching import promote_rates
    from utils.habitats import encounter_pool, set_rarity_chance
    from utils.crafting import sanctuary_in_transaction
    from utils.story import read_state
    async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
        player = dict(await c.fetchone())
    async with db.execute('SELECT perk_id FROM player_perks WHERE user_id=? AND equipped=1', (user_id,)) as c:
        perks = {r[0] for r in await c.fetchall()}
    rates = dict(BIOMES[realm]['weights'])
    allowed = {rarity for rarity in rates if encounter_pool(rarity, realm)}

    def promote_available(weights, bonuses):
        return promote_rates(weights, {rarity: bonus for rarity, bonus in bonuses.items() if rarity in allowed})

    if 'stardust_touch' in perks:
        rates = promote_available(rates, {'rare': 0.1, 'epic': 0.1})
    sanctuary = await sanctuary_in_transaction(db, user_id)
    from utils.sanctuary import apply_explore_encounter_bonus
    rates = apply_explore_encounter_bonus(rates, sanctuary, allowed_rarities=allowed)
    rates = {rarity: weight for rarity, weight in rates.items() if rarity in allowed}
    total = sum(rates.values())
    rates = {rarity: weight / total for rarity, weight in rates.items()} if total else dict(BIOMES[realm]['weights'])
    if player.get('incense_charges', 0) > 0:
        promoted = promote_available(rates, {'uncommon': 0.08, 'rare': 0.05, 'epic': 0.03})
        if promoted != rates:
            rates = promoted
            await db.execute('UPDATE players SET incense_charges=incense_charges-1 WHERE user_id=?', (user_id,))
    if biome_key(realm) == 'loom':
        charge = 'prism_keys' if player.get('prism_keys', 0) > 0 else 'divine_compass_charges' if player.get('divine_compass_charges', 0) > 0 else None
        if charge:
            current_chance = rates.get('divine', 0) / max(.001, sum(rates.values()))
            rates = set_rarity_chance(rates, realm, 'divine', min(.9, current_chance + (.15 if charge == 'prism_keys' else .10)))
            await db.execute(f'UPDATE players SET {charge}={charge}-1 WHERE user_id=?', (user_id,))
    if (player.get('ancient_compass_charges', 0) > 0
            and any(rates.get(r, 0) > 0 for r in ('common', 'uncommon'))
            and any(rates.get(r, 0) > 0 for r in ('rare', 'epic', 'legendary', 'divine'))):
        rates = {k: v for k, v in rates.items() if k not in {'common', 'uncommon'}}
        await db.execute('UPDATE players SET ancient_compass_charges=ancient_compass_charges-1 WHERE user_id=?', (user_id,))
    beast = pick_encounter(rates, realm)
    story = await read_state(db, user_id)
    progress = story['step_progress'].get('chapter_6', {})
    # This trip's explore event is emitted on return, so include it in the
    # existing third-visit story guarantee without counting unfinished trips.
    if biome_key(realm) == 'loom' and story['current_chapter'] == 'chapter_6' and (progress.get('explore_loom_3', 0) >= 2) and (not progress.get('catch_cosmic_beast', 0)):
        beast = storage.get_beast_data('loom_fragment')
    if not beast:
        raise ValueError('No creature could be found here. Your exploration has not advanced.')
    p['encounter'] = beast['id']
    p['affinity'] = min(0.4, 0.15 * beast['catch_rate'] + (0.05 if 'pixie_pocket' in perks else 0) + (0.15 if player.get('catch_boost_charges', 0) > 0 else 0))
    if player.get('catch_boost_charges', 0) > 0:
        await db.execute('UPDATE players SET catch_boost_charges=catch_boost_charges-1 WHERE user_id=?', (user_id,))
    # Roll once when the encounter is discovered, never on a repeated click.
    p['friendly'] = rng.random() < p['affinity']
    async with db.execute('SELECT level FROM player_beasts WHERE user_id=? AND is_active=1', (user_id,)) as cursor:
        companion = await cursor.fetchone()
    p['encounter_level'] = encounter_level(realm, companion[0] if companion else None, rng)

async def choose(user_id, trip_id, version, choice, *, rng=random):
    saved = await read(user_id, trip_id)
    if saved and saved['payload'].get('adventure_version') == 3:
        from utils import journey_service
        return await journey_service.choose(user_id, trip_id, version, choice)
    from utils.duels import reserve_fighters
    from utils import combat
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id]):
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                async with db.execute('SELECT * FROM expeditions WHERE id=? AND user_id=?', (trip_id, user_id)) as c:
                    trip = decode(await c.fetchone())
                if not trip:
                    raise ValueError('That expedition belongs to another journal or is no longer available.')
                if trip['status'] != 'active' or trip['version'] != version:
                    raise ValueError('That choice belongs to an older page. Use the refreshed expedition.')
                p = trip['payload']
                if p.get('adventure_version') == 2:
                    status = await _choose_v2(db, trip, choice, rng)
                elif choice == 'return_early':
                    choice = 'return'
                elif choice not in options(trip):
                    raise ValueError('Choose a path offered on this page.')
                if p.get('adventure_version') == 2:
                    pass
                elif choice == 'return':
                    await _settle(db, trip)
                    status = 'complete'
                else:
                    status = 'active'
                    if p['stage'] == 0:
                        p.update(route=choice, stage=1)
                        if choice == 'tracks':
                            await _encounter(db, user_id, trip['realm'], p, rng)
                    elif choice == 'rustle':
                        p.update(route='tracks', stage=1)
                        await _encounter(db, user_id, trip['realm'], p, rng)
                    elif choice == 'challenge':
                        await _trainer(db, user_id, trip['realm'])
                        status = 'battle'
                        p['battle_token'] = uuid4().hex
                    else:
                        p['stage'] = 2
                        if choice.startswith('material:'):
                            p['material'] = choice.split(':', 1)[1]
                            p['notes'].append('You gathered ' + load_catalog('materials.json')['materials'][p['material']]['name'] + '.')
                        elif choice == 'repair':
                            p.update(bond=2, happiness=5)
                            p['notes'].append('Together, you mended the little wayside shrine.')
                        elif choice == 'rest':
                            p.update(restore=5, happiness=3)
                            p['notes'].append('You shared a quiet rest beside the landmark.')
                        elif choice == 'study':
                            p['exp'] += max(1, round(p.get('base_exp', p['exp']) * .15))
                            p['notes'].append('You recorded the landmark’s old markings.')
                        elif choice == 'approach':
                            p.update(caught=bool(p['friendly']), encounter_result='befriended' if p['friendly'] else 'escaped')
                            p['notes'].append('The creature chose to come closer.' if p['friendly'] else 'You gave the creature space; it slipped into its habitat.')
                        elif choice == 'observe':
                            p.update(encounter_result='observed', bond=p['bond'] + 1)
                            p['notes'].append('You watched without disturbing the creature.')
                await db.execute('UPDATE expeditions SET status=?,version=version+1,payload=?,updated_at=? WHERE id=?', (status, json.dumps(p), time.time(), trip_id))
                await db.commit()
    result = await read(user_id, trip_id)
    if result['status'] == 'complete':
        await report(result)
    return result

async def _choose_v2(db, trip, choice, rng):
    from utils import adventure_party, expedition_events
    p, uid = trip['payload'], trip['user_id']
    if choice not in options(trip) and choice != 'return_early':
        raise ValueError('Choose a path offered on this page.')
    if choice in ('return', 'return_early'):
        await _settle(db, trip)
        return 'complete'
    if choice.startswith('help:'):
        helper_id = int(choice.split(':', 1)[1])
        available = await adventure_party.rows(uid, db=db, ids=p['party_ids'])
        if helper_id not in {r['id'] for r in available}:
            raise ValueError('That teammate needs recovery or is no longer available. The safe detour is still open.')
    if choice in ('push', 'venture'):
        available = await adventure_party.rows(uid, db=db, ids=p['party_ids'])
        if not any(member['hp'] > 1 for member in available):
            raise ValueError('Your team is too exhausted to risk going deeper. Head home or sketch from the safe entrance.')
    effects = expedition_events.resolve(p, trip['realm'], choice)
    if effects.get('encounter'):
        await _encounter(db, uid, trip['realm'], p, rng)
    if effects.get('battle'):
        party = await adventure_party.rows(uid, db=db, ids=p['party_ids'])
        if not party:
            raise ValueError('Your team needs recovery. You can still observe or return home.')
        p['battle_token'] = uuid4().hex
        p['battle_lead_id'] = party[0]['id']
        return 'battle'
    if effects.get('heal') or effects.get('damage'):
        # All recovery and travel costs are part of the same saved choice.
        # No revival, lethal overworld damage, or repeated camp on stale clicks.
        party = await adventure_party.rows(uid, db=db, ids=p['party_ids'])
        for member in party:
            if effects.get('heal'):
                await db.execute('UPDATE player_beasts SET hp=MIN(max_hp,hp+MAX(1,CAST(max_hp*?/100 AS INTEGER))) WHERE id=? AND user_id=?',
                                 (effects['heal'], member['id'], uid))
            elif member['hp'] > 1:
                await db.execute('UPDATE player_beasts SET hp=MAX(1,hp-MAX(1,CAST(max_hp*?/100 AS INTEGER))) WHERE id=? AND user_id=?',
                                 (effects['damage'], member['id'], uid))
    return 'active'

async def _settle(db, trip):
    from utils.rewards import player_exp
    from utils.crafting import sanctuary_in_transaction
    p = trip['payload']
    uid = trip['user_id']
    if p.get('adventure_version') == 2:
        fraction = min(1., max(0., p['completed'] / 3))
        multiplier = 1 + min(25, p['bonus']) / 100
        p['gold'] = int(p['base_gold'] * fraction * multiplier)
        p['exp'] = int(p['base_exp'] * fraction * multiplier)
        # A saved, explicitly chosen nest reward takes the material's place.
        # This grant shares the gold/XP transaction and stale-click protection.
        egg_id = p.get('egg_reward')
        offer = p.get('egg_offer')
        egg_earned = bool(p.get('nest_version') == 1 and p['completed'] > 0
                          and offer and egg_id == offer['id']
                          and any(row.get('choice') == 'recover_egg' for row in p['history']))
        p['egg_granted'] = egg_earned
        if egg_earned:
            await add_stack(db, 'player_inventory', uid, egg_id)
        p['material_granted'] = p['completed'] > 0 and not egg_earned
        if p['material_granted']:
            await add_stack(db, 'player_materials', uid, p['material'])
        # This is a small shared activity reward, not another battle reward.
        # The combat runner already awards team XP when a fight is played.
        if p['completed'] and not p.get('battle_awarded'):
            from utils import adventure_party
            from utils.progression import encounter_beast_xp
            async with db.execute('SELECT level FROM player_beasts WHERE id=? AND user_id=?', (trip['companion_id'], uid)) as c:
                lead = await c.fetchone()
            if lead:
                high = BIOMES[trip['realm']]['level_range'][1]
                source_level = min(lead[0], high)
                amount = max(1, int(encounter_beast_xp(source_level, effort=.2)
                                    * min(1., high / max(1, lead[0])) * fraction))
                p['party_xp'] = await adventure_party.award_xp_in_transaction(
                    db, uid, p['party_ids'], trip['companion_id'], amount,
                    p.get('encounter_level', BIOMES[trip['realm']]['level_range'][0]),
                    participated=[int(key) for key, value in p['participations'].items() if value])
    else:
        await add_stack(db, 'player_materials', uid, p['material'])
    await db.execute('UPDATE players SET gold=gold+?,total_gold_earned=total_gold_earned+? WHERE user_id=?', (p['gold'], p['gold'], uid))
    sanctuary = await sanctuary_in_transaction(db, uid)
    p['exp_granted'] = round(p['exp'] * 1.15) if sanctuary.get('arcane_library') else p['exp']
    result = await player_exp(db, uid, p['exp_granted'])
    p['new_level'], p['leveled'] = (result[0], result[2])
    await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?),happiness=MIN(100,happiness+?) WHERE id=? AND user_id=?', (p['bond'], p['happiness'], trip['companion_id'], uid))
    if p.get('adventure_version') == 2 and p['completed']:
        # A helpful group event gives reserves a small social benefit as well;
        # the lead retains the original activity reward and the total is capped.
        for row_id in p['party_ids']:
            if row_id != trip['companion_id']:
                await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?),happiness=MIN(100,happiness+?) WHERE id=? AND user_id=?',
                                 (min(1, p['bond']), min(3, p['happiness']), row_id, uid))
    if p['restore']:
        async with db.execute('SELECT * FROM player_beasts WHERE id=? AND user_id=?', (trip['companion_id'], uid)) as c:
            comp = await c.fetchone()
        if comp and comp['hp'] > 0 and (not storage.is_knocked_out(dict(comp))):
            await db.execute('UPDATE player_beasts SET hp=MIN(max_hp,hp+MAX(1,CAST(max_hp*?/100 AS INTEGER))) WHERE id=?', (p['restore'], comp['id']))
    if p['caught']:
        beast = {**storage.get_beast_data(p['encounter']), 'caught_from': 'discover',
                 'level': min(50, p.get('encounter_level', BIOMES[trip['realm']]['level_range'][0]))}
        p['grant_id'] = await storage.grant_beast(db, uid, beast, grant_key='expedition:' + trip['id'])

async def report(trip):
    """Retryable, keyed progression notifications after the atomic item grant."""
    if trip['status'] != 'complete' or trip['reported'] or trip['payload'].get('mode') == 'quick':
        return
    from utils.events import record_event, acquired_beast
    from utils.story import advance_quest_step
    uid = trip['user_id']
    p = trip['payload']
    key = 'expedition:' + trip['id']
    if p.get('adventure_version', 0) >= 2 and not p['completed']:
        # Leaving the trailhead spends its reserved charge, but is not a visit
        # for story, achievements, sightings or resource collection progress.
        async with storage.connect() as db:
            await db.execute('UPDATE expeditions SET reported=1 WHERE id=?', (trip['id'],))
            await db.commit()
        return
    await record_event(uid, 'explore', biome=BIOMES[biome_key(trip['realm'])]['name'], event_key=key + ':explore')
    material_granted = p.get('adventure_version', 0) < 2 or p.get('material_granted', False)
    if material_granted:
        await record_event(uid, 'material_collect', material_id=p['material'], event_key=key + ':material')
    from utils.progress import unlock_simple_achievement, record_bestiary_sighting
    await unlock_simple_achievement(uid, 'first_explore')
    if material_granted:
        async with storage.connect() as db:
            cursor = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'material_story')", (key + ':material-story', uid))
            await db.commit()
        if cursor.rowcount:
            await advance_quest_step(uid, 'material_gained', material_id=p['material'], amount=1)
    if p['grant_id']:
        await acquired_beast(uid, p['grant_id'], storage.get_beast_data(p['encounter']), 'discover')
        if p.get('guild_id'):
            await record_bestiary_sighting(p['guild_id'], p['encounter'], uid)
    if p.get('leveled'):
        await advance_quest_step(uid, 'level_up', level=p['new_level'])
    if any(row.get('leveled') for row in p.get('party_xp', [])):
        # The keyed receipt keeps retried post-commit notifications bounded.
        async with storage.connect() as db:
            cursor = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'team_level')", (key + ':team-level', uid))
            await db.commit()
        if cursor.rowcount:
            from utils.progress import track_quest_event
            await track_quest_event(uid, 'beast_level_up')
    if p.get('adventure_version') == 3:
        from utils import progress
        await progress._track_daily_event(uid, 'explore', event_key=key + ':daily-explore')
        if material_granted:
            await progress._track_daily_event(uid, 'material_collect', event_key=key + ':daily-material')
        if biome_key(trip['realm']) == 'loom':
            await unlock_simple_achievement(uid, 'first_celestial_loom')
        if p.get('fights_won'):
            await progress._track_daily_event(uid, 'battle_win', event_key=key + ':daily-win')
        await progress.check_achievements(uid)
        async with storage.connect() as db:
            async with db.execute("SELECT 1 FROM raid_settlements s,json_each(s.payload,'$.users') member WHERE s.completed=0 AND CAST(member.value AS INTEGER)=? LIMIT 1", (uid,)) as cursor:
                pending_raid = bool(await cursor.fetchone())
        if pending_raid:
            from utils.raids import recover_raids
            await recover_raids(user_id=uid)
    async with storage.connect() as db:
        await db.execute('UPDATE expeditions SET reported=1 WHERE id=?', (trip['id'],))
        await db.commit()

async def report_safely(trip):
    """Auxiliary outages do not hide a committed return; journal open retries."""
    try:
        await report(trip)
    except Exception:
        logging.getLogger('chibibeasts.expeditions').exception('Expedition follow-up pending: %s', trip['id'])

async def battle_result(user_id, trip_id, token, won, *, interrupted=False, only_unstarted=False, runner_id=None):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute("SELECT * FROM expeditions WHERE id=? AND user_id=? AND status='battle'", (trip_id, user_id)) as c:
            trip = decode(await c.fetchone())
        if not trip or trip['payload'].get('battle_token') != token:
            return
        if only_unstarted and trip['payload'].get('battle_started'):
            return
        if runner_id is not None and trip['payload'].get('runner_id') != runner_id:
            return
        p = trip['payload']
        if p.get('adventure_version') == 2:
            from utils import expedition_events
            expedition_events.finish_battle(p, trip['realm'], won, interrupted)
            # Conservatively treat a launched runner as rewarded: never add a
            # second activity budget after cancellation during result playback.
            p['battle_awarded'] = bool(p.get('battle_started'))
        else:
            p.update(stage=2, caught=bool(won), encounter_result='interrupted' if interrupted else 'won' if won else 'escaped')
            p['notes'].append('The encounter was interrupted; your gathered supplies are safe.' if interrupted else 'The creature recognised your strength and chose to follow.' if won else 'The creature withdrew. You kept your gathered supplies.')
        if won and p.get('adventure_version') != 2:
            p['gold'] = int(p['gold'] * 1.25)
            p['exp'] = encounter_trainer_xp(p['level'], p.get('encounter_level', BIOMES[trip['realm']]['level_range'][0]),
                                           difficulty=p.get('battle_difficulty', 'normal'))
        await db.execute("UPDATE expeditions SET status='active',version=version+1,payload=?,updated_at=? WHERE id=?", (json.dumps(p), time.time(), trip_id))
        await db.commit()

async def run_battle(interaction, trip, *, receipt=None):
    from cogs.battle import build_pve_beast_state, run_pve_battle
    from utils.duels import reserve_fighters
    uid = interaction.user.id
    token = trip['payload']['battle_token']
    runner_id = uuid4().hex
    claimed = False
    try:
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute("SELECT * FROM expeditions WHERE id=? AND user_id=? AND status='battle'", (trip['id'], uid)) as cursor:
                latest = decode(await cursor.fetchone())
            if not latest or latest['payload'].get('battle_token') != token or latest['payload'].get('battle_started'):
                raise ValueError('This encounter has already started or ended. Return to your journal.')
            latest['payload']['battle_started'] = True
            latest['payload']['runner_id'] = runner_id
            await db.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(latest['payload']), trip['id']))
            claimed = True
            await db.commit()
            if receipt is not None:
                receipt.update(owned=True, token=token, runner_id=runner_id)
        with reserve_fighters([uid]):
            from utils import adventure_party
            party = None
            if latest['payload'].get('adventure_version') == 2:
                party = await adventure_party.rows(uid, ids=latest['payload']['party_ids'])
                row = party[0] if party else None
            else:
                row = await storage.get_active_beast(uid)
            if not row:
                raise ValueError('Choose an active companion before continuing the encounter.')
            if row['hp'] <= 0 or storage.is_knocked_out(row):
                raise ValueError('Your companion needs recovery before this encounter.')
            beast = storage.get_beast_data(latest['payload']['encounter'])
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute('SELECT * FROM player_perks WHERE user_id=? AND equipped=1', (uid,)) as cursor:
                    perks = [dict(r) for r in await cursor.fetchall()]
                async with db.execute('SELECT 1 FROM beast_market WHERE beast_row_id=?', (row['id'],)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError('Delist your companion before continuing the encounter.')

            async def win(embed, p_state, e_state, timed_out=False):
                await battle_result(uid, trip['id'], token, not timed_out and e_state.get('hp', 1) <= 0, runner_id=runner_id)

            async def loss(embed, *args):
                await battle_result(uid, trip['id'], token, False, runner_id=runner_id)
            from utils.duels import prepare_state
            enemy = build_pve_beast_state(beast, latest['payload'].get('encounter_level', BIOMES[latest['realm']]['level_range'][0]))
            if party is None:
                # Legacy saved routes now use the current adventure loadout.
                # Freeze that exact roster for both the reward preview and the
                # runner, just as version-two trips freeze their travelling IDs.
                party = await adventure_party.rows(uid)
                if not party or party[0]['id'] != row['id']:
                    raise ValueError('Your active companion changed while the battle was preparing. Try again.')
            prepared = []
            for index, member in enumerate(party):
                prepared.append(await prepare_state(uid, member, storage.get_beast_data(member['beast_id']), perks,
                                                    consume=False, party_reserve=index > 0, preserve_resources=True))
            async with storage.connect() as db:
                latest['payload']['battle_difficulty'] = adventure_party.encounter_difficulty(prepared, enemy)
                await db.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(latest['payload']), trip['id']))
                await db.commit()
            kwargs = dict(reserved=True, realm=latest['realm'], party_rows=party)
            await run_pve_battle(interaction, row, storage.get_beast_data(row['beast_id']), perks, enemy, 'aggressive', 'Wild ' + beast['name'], win, loss, **kwargs)
    finally:
        if claimed:
            await asyncio.shield(battle_result(uid, trip['id'], token, False, interrupted=True, runner_id=runner_id))
    return await read(uid, trip['id'])

async def recover_interrupted(db):
    # Called only at startup. Never infer a live battle is abandoned from a
    # second journal view while its network upload is still in flight.
    async with db.execute("SELECT id,status,payload FROM expeditions WHERE status IN ('battle','quick')") as c:
        rows = await c.fetchall()
    for trip_id, status, payload in rows:
        if status == 'quick':
            await db.execute('DELETE FROM expeditions WHERE id=?', (trip_id,))
            continue
        p = json.loads(payload)
        if p.get('adventure_version') == 3:
            # Every combat cursor is committed before animation. Resume exactly.
            continue
        if status == 'battle':
            if p.get('adventure_version') == 2:
                from utils import expedition_events
                async with db.execute('SELECT realm FROM expeditions WHERE id=?', (trip_id,)) as cursor:
                    realm = (await cursor.fetchone())[0]
                expedition_events.finish_battle(p, realm, False, True)
                p['battle_awarded'] = bool(p.get('battle_started'))
            else:
                p.update(stage=2, caught=False, encounter_result='interrupted')
                p.setdefault('notes', []).append('The encounter was interrupted. Return with your gathered supplies.')
        await db.execute("UPDATE expeditions SET status='active',version=version+1,payload=?,updated_at=? WHERE id=?", (json.dumps(p), time.time(), trip_id))
