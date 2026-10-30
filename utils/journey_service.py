"""Transactional bridge between authored journeys, shared combat and rewards."""
from copy import deepcopy
import hashlib
import json
import random
import time
from uuid import uuid4

import aiosqlite

from utils import adventure_party, combat, db as storage, expeditions, journey_combat, trial_locks
from utils.habitats import BIOMES, REGIONAL_MATERIALS, HOLLOWLIGHT, biome_key, carnival_underlay, material_pool, encounter_level
from utils.progression import encounter_beast_xp, encounter_trainer_xp


def _dump(value):
    return json.dumps(value, default=lambda item: sorted(item) if isinstance(item, set) else list(item))


def _material_offers(material, realm, seed):
    """Keep the rolled/quest rarity; seed only its equivalent regional choices."""
    from utils.catalog import load_catalog
    materials = load_catalog('materials.json')['materials']
    rarity = materials[material]['rarity']
    alternatives = [key for key in REGIONAL_MATERIALS[biome_key(realm)]
                    if key != material and materials[key]['rarity'] == rarity]
    alternatives.sort(key=lambda key: hashlib.sha256(f'{seed}:material-offer:{key}'.encode()).digest())
    return [material] + alternatives[:2]


def _material_effect(payload, effect):
    """Offer a replacement, never a second material or another journey step."""
    if not effect.get('material'):
        return
    offered = payload.get('offered', [payload['material']])
    if len(offered) < 2:
        gained = effect.get('material_bonus', 0)
        payload['notes'].append(f'The saved material stays in your bundle; careful gathering adds {gained}% bonus within the 25% run cap.')
        return
    from utils.catalog import load_catalog
    materials = load_catalog('materials.json')['materials']
    payload['material_next_page'] = deepcopy(payload['page'])
    payload['material_next_status'] = payload['journey_status']
    payload['journey_status'] = 'material'
    payload['page'] = dict(title='Choose your material',
        body='Your team found several materials of the same rarity. Choose one for the journey\'s existing bundle. This replaces its material; nothing is granted until you return.',
        choices=[dict(value='material:' + key, label=materials[key]['name'],
                      description='Replace the one bundle with ' + materials[key]['name'] + '; no extra material.') for key in offered],
        root_id=None, node_id=None, kind='material', repeatable=False)


async def begin(user_id, realm, *, now=None, rng=random, guild_id=0, combat_route=True):
    from utils import journeys, expedition_events
    from utils.duels import prepare_state, reserve_fighters
    from utils.trials import _snapshot, assert_available
    from utils.economy_balance import gold_reward_level
    now = time.time() if now is None else now
    carnival = realm == HOLLOWLIGHT
    if carnival:
        combat_route = True
    if realm not in BIOMES and not carnival:
        raise ValueError('Choose a destination from the journal.')
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id]):
            await storage.get_player_beasts(user_id)
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                before = await _snapshot(db, user_id)
            rows, player = before['rows'], before['player']
            if not rows or not rows[0]['is_active']:
                raise ValueError('Choose a healthy, unlisted lead before setting out.')
            event_level = max(1, min(50, max(row['level'] for row in rows))) if carnival else None
            if carnival:
                realm = carnival_underlay(player['level'], event_level)
            prepared = []
            for index, row in enumerate(rows):
                state = await prepare_state(user_id, row, storage.get_beast_data(row['beast_id']), before['perks'],
                                            consume=False, party_reserve=index > 0, preserve_resources=True)
                state['mana'] = min(state['max_mana'], (row['mana'] * state['max_mana'] + max(1, row['max_mana']) - 1)
                                    // max(1, row['max_mana']) + (20 if 'genesis_spark' in state['perks'] else 0))
                state['_pve_mana_origin'] = (row['mana'], state['mana'])
                prepared.append(state)
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                await assert_available(db, user_id)
                player, lead = await expeditions._trainer(db, user_id, realm)
                if _dump(await _snapshot(db, user_id)) != _dump(before):
                    raise ValueError('Your team or preparations changed. Review your team and try again.')
                if carnival:
                    from utils import hollowlight
                    event_window = await hollowlight.departure(db, now)
                async with db.execute("SELECT 1 FROM expeditions WHERE user_id=? AND status IN ('active','battle','quick')", (user_id,)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError('An adventure is already underway. Open /explore to continue it.')
                async with db.execute("SELECT 1 FROM raid_settlements s,json_each(s.payload,'$.users') member WHERE s.completed=0 AND CAST(member.value AS INTEGER)=? LIMIT 1", (user_id,)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError('Your previous raid is still settling. Finish its recovery before setting out.')
                await expeditions._energy(db, user_id, now, spend=True)
                source = sum(BIOMES[realm]['level_range']) // 2
                gold_level = gold_reward_level(source)
                perks = {r['perk_id'] for r in before['perks'] if r['equipped']}
                gold = int(rng.randint(gold_level * 6, gold_level * 10) * (1.1 if 'pixie_pocket' in perks else 1))
                xp = encounter_trainer_xp(player['level'], source, effort=.8)
                material = rng.choice(material_pool({'id': '', 'type': ''}, realm))
                # Keep existing quest materials reachable through the single bundle.
                from utils.story import quest_material_options
                quest = await quest_material_options(db, user_id, biome_key(realm))
                if quest:
                    material = rng.choice(quest)
                seed = rng.getrandbits(64)
                payload = dict(mode='expedition', gold=gold, base_gold=gold, exp=xp, base_exp=xp,
                               material=material, offered=_material_offers(material, realm, seed), encounter='', encounter_result='',
                               caught=False, grant_id=None, bond=0, happiness=0, restore=0, notes=[],
                               level=player['level'], guild_id=guild_id, prepared_party=prepared)
                story_destination = HOLLOWLIGHT if carnival else realm
                if carnival:
                    payload.update(event_id=hollowlight.EVENT_ID, hollowlight=event_window,
                                   visual_realm=HOLLOWLIGHT, event_level=event_level,
                                   event_trainer_level=player['level'], underlying_destination=realm)
                world = await journeys.world(db, user_id, story_destination)
                for member in rows:
                    data = storage.get_beast_data(member['beast_id'])
                    member.update(name=data['name'], type=data['type'])
                journeys.initialize(payload, story_destination, seed, rows, world, combat_route=combat_route)
                await journeys.persist(db, user_id, payload)
                if not carnival:
                    expedition_events.prepare_nest(payload, biome_key(realm))
                trip_id = uuid4().hex
                trip = dict(id=trip_id, user_id=user_id, realm=realm, status='active', companion_id=lead['id'], payload=payload, created_at=now)
                if combat_route:
                    effects = await journeys.resolve(db, trip, payload['page']['choices'][0]['value'])
                    await _start_battle(db, trip, effects['battle_kind'])
                await db.execute("INSERT INTO expeditions(id,user_id,realm,status,companion_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                 (trip_id, user_id, realm, trip['status'], lead['id'], _dump(payload), now, now))
                await db.commit()
                if trip['status'] != 'complete':
                    trial_locks.lock(user_id)
    result = await expeditions.read(user_id, trip_id)
    if result['status'] == 'complete':
        await expeditions.report_safely(result)
    return result


async def ensure_paced(trip):
    """Upgrade deployed story-only v3 saves without replaying a combat cursor."""
    if (not trip or trip['status'] == 'complete' or trip['payload'].get('adventure_version') != 3
            or 'pacing_version' in trip['payload']):
        return trip
    from utils import journeys
    from utils.duels import reserve_fighters
    uid = trip['user_id']
    async with combat.lock_states([uid]):
        with reserve_fighters([uid], allow_trial=True):
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                async with db.execute('SELECT * FROM expeditions WHERE id=? AND user_id=?', (trip['id'], uid)) as cursor:
                    current = expeditions.decode(await cursor.fetchone())
                if current['status'] != 'complete' and 'pacing_version' not in current['payload']:
                    p = current['payload']
                    material_page = deepcopy(p['page']) if p.get('material_next_page') else None
                    journeys.configure_combat_route(p, current['realm'], preserve=True)
                    if p.get('encounter'):
                        p['recruitable_beat'] = -1  # The existing ticket remains the only ticket.
                    if current['status'] != 'battle':
                        journeys._advance(p)
                        if material_page:
                            p['material_next_page'] = deepcopy(p['page'])
                            p['material_next_status'] = p['journey_status']
                            p['page'], p['journey_status'] = material_page, 'material'
                    await journeys.persist(db, uid, p)
                    current['updated_at'] = time.time()
                    await db.execute('UPDATE expeditions SET payload=?,version=version+1,updated_at=? WHERE id=?', (_dump(p), current['updated_at'], current['id']))
                    current['version'] += 1
                await db.commit()
                if current['status'] != 'complete':
                    trial_locks.lock(uid)
    return current


async def _start_battle(db, trip, kind):
    p, uid = trip['payload'], trip['user_id']
    pending = p.get('pending_battle') or {}
    paced = p.get('pacing_version') == 1
    recruitable = (pending.get('route_battle') and p['battles_completed'] == p['recruitable_beat'] and not p.get('encounter')) if paced else not p.get('encounter')
    if recruitable:
        await expeditions._encounter(db, uid, trip['realm'], p, random.Random(f"{p['seed']}:capture"))
    if not p.get('preparations_spent'):
        await db.execute('UPDATE players SET next_defense_boost=0,next_speed_boost=0,status_ward_charges=MAX(0,status_ward_charges-1) WHERE user_id=?', (uid,))
        p['preparations_spent'] = True
    p['capture_fight'] = bool(recruitable)
    seed = f"{p['seed']}:{p.get('fights_started', 0)}"
    carnival = p.get('event_id') == 'hollowlight_2026'
    guardian = carnival and pending.get('route_battle') and p['battles_completed'] == p['battle_target'] - 1
    level = p['event_level'] if carnival else p['encounter_level'] if recruitable else encounter_level(trip['realm'], p['prepared_party'][0]['level'], random.Random(seed + ':level'))
    p['current_enemy_level'] = level
    p['event_guardian'] = bool(guardian)
    plan = journey_combat.carnival_plan if carnival else journey_combat.plan
    lineup = plan(trip['realm'], seed, kind, level, ticket=p['encounter'] if recruitable else None,
                  party_size=len(p['party_ids']) if paced else 3, **({'guardian': guardian} if carnival else {}))
    journey_combat.start(p, lineup)
    trip['status'] = 'battle'
    await _battle_end(db, trip)


async def _settle(db, trip, now):
    """One run bundle, independent of the number of foes or narrative pages."""
    from utils import trials
    from utils.gameplay import add_stack
    p, uid = trip['payload'], trip['user_id']
    earned = bool(p['completed'] or p.get('inherited_completed', 0))
    fraction = min(1., max(p.get('inherited_fraction', 0), p['completed'] / max(1, p['total'])))
    if p.get('pacing_version') == 1:
        p['outcome'] = p.get('outcome', 'cleared' if p['battles_completed'] >= p['battle_target'] and p['completed'] >= p['total'] else 'retreated')
    multiplier = 1 + min(25, p.get('bonus', 0)) / 100
    p['gold'] = int(p['base_gold'] * fraction * multiplier)
    p['exp'] = int(p['base_exp'] * fraction * multiplier)
    p['restore'] = 0
    p['material_granted'] = earned and not p.get('egg_reward')
    if p['material_granted']:
        await add_stack(db, 'player_materials', uid, p['material'])
    p['egg_granted'] = bool(earned and p.get('egg_reward') and p.get('egg_offer')
                            and p['egg_reward'] == p['egg_offer']['id'])
    if p['egg_granted']:
        await add_stack(db, 'player_inventory', uid, p['egg_reward'])
    # Resource persistence is the same proportion-preserving path as Trials.
    await trials._resources(db, dict(user_id=uid, payload={'party': journey_combat.party(p), 'lead_id': trip['companion_id']}), now)
    if earned:
        high = BIOMES[trip['realm']]['level_range'][1]
        lead = p['prepared_party'][0]
        effort = .85 if p.get('fights_won') else .2
        base = max(1, int(encounter_beast_xp(min(lead['level'], high), effort=effort)
                          * min(1., high / max(1, lead['level'])) * fraction))
        p['party_xp'] = await adventure_party.award_xp_in_transaction(db, uid, p['party_ids'], trip['companion_id'],
            base, p.get('encounter_level', high), participated=p.get('combat', {}).get('participated', ()))
    await _settlement_tail(db, trip)
    if p.get('event_id') == 'hollowlight_2026':
        from utils import hollowlight
        await hollowlight.award_trip(db, trip, now)
    if earned:
        key = 'expedition:' + trip['id']
        # Core activity and story receipts belong to the payout transaction.
        # The old generic event helper claims before its auxiliary callbacks,
        # so v3 reports explicitly retry keyed daily work below instead.
        for event, suffix in [('explore', ':explore')] + ([('material_collect', ':material')] if p['material_granted'] else []):
            claim = await db.execute('INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,?)', (key + suffix, uid, event))
            if claim.rowcount:
                await db.execute('INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,?,1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1', (uid, event))
                if event == 'explore':
                    await trials._story_event(db, uid, 'explore', biome='🎃 Hollowlight Carnival' if p.get('event_id') == 'hollowlight_2026' else BIOMES[biome_key(trip['realm'])]['name'])
        if p['material_granted']:
            claim = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'material_story')", (key + ':material-story', uid))
            if claim.rowcount:
                await trials._story_event(db, uid, 'material_gained', material_id=p['material'], amount=1)
    if p.get('fights_won'):
        claim = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'battle_win')",
                                 ('expedition:' + trip['id'] + ':battle-win', uid))
        if claim.rowcount:
            await db.execute("INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,'battle_win',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1", (uid,))
            await db.execute('UPDATE players SET wins=wins+1 WHERE user_id=?', (uid,))
            await trials._story_event(db, uid, 'battle_win')
    trip['status'] = 'complete'
    p['notes'].append('The team returned home. Journey boons expired; earned supplies were banked.')


async def _settlement_tail(db, trip):
    from utils.rewards import player_exp
    from utils.crafting import sanctuary_in_transaction
    p, uid = trip['payload'], trip['user_id']
    await db.execute('UPDATE players SET gold=gold+?,total_gold_earned=total_gold_earned+? WHERE user_id=?', (p['gold'], p['gold'], uid))
    sanctuary = await sanctuary_in_transaction(db, uid)
    p['exp_granted'] = round(p['exp'] * 1.15) if sanctuary.get('arcane_library') else p['exp']
    result = await player_exp(db, uid, p['exp_granted'])
    p['new_level'], p['leveled'] = result[0], result[2]
    for index, row_id in enumerate(p['party_ids']):
        await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+?),happiness=MIN(100,happiness+?) WHERE user_id=? AND id=?',
                         (min(3 if index == 0 else 1, p.get('bond', 0)), min(5, p.get('happiness', 0)), uid, row_id))
    if p['caught']:
        beast = {**storage.get_beast_data(p['encounter']), 'caught_from': 'discover', 'level': min(50, p['encounter_level'])}
        p['grant_id'] = await storage.grant_beast(db, uid, beast, grant_key='expedition:' + trip['id'])


async def _mutate(uid, trip_id, version, operation):
    from utils.duels import reserve_fighters
    async with combat.lock_states([uid]):
        with reserve_fighters([uid], allow_trial=True):
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                async with db.execute('SELECT * FROM expeditions WHERE id=? AND user_id=?', (trip_id, uid)) as cursor:
                    trip = expeditions.decode(await cursor.fetchone())
                if not trip or trip['payload'].get('adventure_version') != 3 or trip['status'] == 'complete' or trip['version'] != version:
                    raise ValueError('That choice belongs to an older page. Use the current adventure controls.')
                await operation(db, trip)
                await db.execute('UPDATE expeditions SET status=?,version=version+1,payload=?,updated_at=? WHERE id=?',
                                 (trip['status'], _dump(trip['payload']), time.time(), trip_id))
                await db.commit()
                if trip['status'] == 'complete':
                    trial_locks.unlock(uid)
    result = await expeditions.read(uid, trip_id)
    if result['status'] == 'complete':
        await expeditions.report_safely(result)
    return result


async def choose(uid, trip_id, version, choice):
    from utils import journeys
    async def operation(db, trip):
        p = trip['payload']
        if trip['status'] != 'active':
            raise ValueError('Finish this encounter or return home using its battle controls.')
        if choice not in expeditions.options(trip):
            raise ValueError('Choose an option on the current adventure page.')
        if choice in ('return', 'return_early', 'return_egg'):
            if choice != 'return_early' and p.get('pacing_version') == 1 and p['battles_completed'] < p['battle_target']:
                raise ValueError('Finish the required battles, or use Retreat and return to bank partial progress.')
            if choice == 'return_egg':
                if not p.get('egg_offer') or not p['completed']:
                    raise ValueError('No abandoned egg was found on this adventure.')
                p['egg_reward'] = p['egg_offer']['id']
            await _settle(db, trip, time.time())
            return
        if p.get('material_next_page') is not None:
            # The saved page's offered values, checked above, are authoritative
            # even after catalog changes or a process restart.
            if not choice.startswith('material:'):
                raise ValueError('Choose one material on the saved gathering page.')
            selected = choice.split(':', 1)[1]
            if selected not in p['offered']:
                raise ValueError('That material was not offered on this journey.')
            p['material'] = selected
            name = next(item['label'] for item in p['page']['choices'] if item['value'] == choice)
            outcome = name + ' replaces the journey\'s one material; it will be banked on return.'
            p['notes'].append(outcome)
            p['history'].append(dict(root_id=None, node_id=None, choice=choice, outcome=outcome, phase='material'))
            p['page'] = p.pop('material_next_page')
            p['journey_status'] = p.pop('material_next_status')
            return
        effect = await journeys.resolve(db, trip, choice)
        journey_combat.effects(p, effect)
        _material_effect(p, effect)
        kind = effect.get('battle_kind') or effect.get('battle')
        if kind:
            await _start_battle(db, trip, kind)
    return await _mutate(uid, trip_id, version, operation)


async def _battle_end(db, trip):
    from utils import journeys
    p, state = trip['payload'], trip['payload']['combat']
    if state['status'] not in ('won', 'lost'):
        return
    won = state['status'] == 'won'
    if won:
        p['fights_won'] = p.get('fights_won', 0) + 1
    if p.get('capture_fight'):
        p['caught'] = won
        p['encounter_result'] = 'won' if won else 'escaped'
    effects = await journeys.finish_battle(db, trip, won)
    if won:
        journey_combat.effects(p, effects)
        _material_effect(p, effects)
    trip['status'] = 'active'
    if not won:
        if p.get('pacing_version') == 1:
            p['outcome'] = 'knocked_out'
        await _settle(db, trip, time.time())


async def battle_action(uid, trip_id, version, choice):
    async def operation(db, trip):
        if trip['status'] != 'battle':
            raise ValueError('That encounter has already ended.')
        if choice == 'return':
            await _settle(db, trip, time.time())
            return
        if choice == 'next':
            journey_combat.continue_pack(trip['payload'])
        else:
            journey_combat.action(trip['payload'], choice)
        await _battle_end(db, trip)
    return await _mutate(uid, trip_id, version, operation)
