"""Persistent tower attempts: one saved combat state and one atomic settlement.

Discord playback never owns progression. Every accepted decision commits its
enemy, party, RNG cursor and revision before returning to a presentation layer.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import logging
import random
import time
from uuid import uuid4

import aiosqlite

from utils import adventure_party, combat, db as storage, trial_locks, trial_rules
from utils.habitats import BIOMES
from utils.progression import encounter_beast_xp, encounter_trainer_xp

ACTIVE = ('between', 'fighting', 'paused')
EXPIRY_SECONDS = 86400
TURN_SECONDS = 45
ITEM_LIMIT = 3


def _json(value):
    return json.dumps(value, default=lambda item: sorted(item) if isinstance(item, set) else list(item))


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    result['payload'] = json.loads(result['payload'])
    _refresh_quotes(result['payload'])
    result['turn_deadline'] = result['payload'].get('turn_deadline')
    return result


def _xp_amount(base, multiplier, fraction, *, scholar=False, library=False):
    amount = int(base * multiplier * fraction)
    if scholar:
        amount = round(amount * 1.1)
    if library:
        amount = round(amount * 1.15)
    return amount


def reward_quote(payload, *, full=False, cleared=None, fraction=None):
    """Quote frozen rewards with the exact settlement rounding order.

    Future full-clear reserve participation is not yet known; amount is the
    current guaranteed split, contributed_amount the participating upper end.
    A bank-now quote is exact for the participation already saved in the run.
    """
    reward, plan = payload['reward_baseline'], payload['plan']
    if fraction is None:
        fraction = 1. if full else trial_rules.progress_fraction(
            payload['cleared'] if cleared is None else cleared, plan['total'])
    participated = set(payload.get('participated', ()))
    multiplier = plan['multiplier']
    party = []
    for member in reward['party']:
        base = member['contributed'] if member['id'] in participated else member['idle']
        kwargs = dict(scholar=reward['scholar'], library=reward['library'])
        party.append(dict(id=member['id'], name=member['name'],
                          amount=_xp_amount(base, multiplier, fraction, **kwargs),
                          contributed_amount=_xp_amount(member['contributed'] if full else base,
                                                        multiplier, fraction, **kwargs)))
    return dict(gold=int(reward['gold'] * multiplier * fraction),
                trainer_xp=_xp_amount(reward['trainer_xp'], multiplier, fraction, library=reward['library']),
                party_xp=party, multiplier=multiplier, fraction=fraction)


def _refresh_quotes(payload):
    if 'reward_baseline' in payload:
        payload['reward_preview'] = reward_quote(payload, full=True)
        fraction = (payload.get('settlement') or {}).get('fraction')
        payload['bank_quote'] = reward_quote(payload, fraction=fraction)


async def assert_available(db, user_id, row_ids=None):
    """Guard global mutations without importing the combat reservation module."""
    async with db.execute("SELECT payload FROM trial_runs WHERE user_id=? AND status IN ('between','fighting','paused')", (user_id,)) as cursor:
        active = await cursor.fetchone()
    if active is None:
        async with db.execute("SELECT payload FROM expeditions WHERE user_id=? AND status IN ('active','battle') AND json_extract(payload,'$.adventure_version')=3", (user_id,)) as cursor:
            journey = await cursor.fetchone()
        if journey is None:
            return
        if row_ids is not None and not set(json.loads(journey[0]).get('party_ids', ())).intersection(int(value) for value in row_ids):
            return
        raise ValueError('Your adventure team is still exploring. Open /explore to continue or return home before changing its preparations.')
    if row_ids is not None:
        p = json.loads(active[0])
        ids = set(p.get('party_ids', [member['id'] for member in p.get('party', [])]))
        if not ids.intersection(int(value) for value in row_ids):
            return
    raise ValueError('Your saved Biome Trial is still underway. Resume it or Retreat & bank before changing its team or preparations.')


async def _load(db, user_id, run_id):
    async with db.execute('SELECT * FROM trial_runs WHERE id=? AND user_id=?', (run_id, user_id)) as cursor:
        run = _decode(await cursor.fetchone())
    if run is None:
        raise ValueError('That trial belongs to another trainer or is no longer available.')
    return run


def _check(run, version, statuses=ACTIVE):
    if run['version'] != version or run['status'] not in statuses:
        raise ValueError('That control belongs to an older trial page. Use the refreshed trial.')


async def _save(db, run, now, *, increment=True):
    _refresh_quotes(run['payload'])
    if increment:
        run['version'] += 1
    run['updated_at'] = now
    run['turn_deadline'] = run['payload'].get('turn_deadline')
    await db.execute('UPDATE trial_runs SET status=?,version=?,payload=?,updated_at=?,reported=? WHERE id=? AND user_id=?',
                     (run['status'], run['version'], _json(run['payload']), now, run['reported'], run['id'], run['user_id']))


async def _ready_at(db, user_id, player):
    async with db.execute('SELECT ready_at FROM trial_cooldowns WHERE user_id=?', (user_id,)) as cursor:
        row = await cursor.fetchone()
    if row:
        return row[0]
    from utils.rewards import cooldown
    last = player.get('challenge_last_at', 0) or 0
    return last + await cooldown(db, user_id, 1200) if last > 0 else 0


def allowed_items():
    """Only existing HP potions and cleanse/ward consumables; never revives."""
    return {key: item for key, item in storage.load_items().items()
            if item.get('effect') and set(item['effect']).issubset({'heal_percent', 'status_ward'})
            and (item['effect'].get('heal_percent') or item['effect'].get('status_ward'))}


async def _snapshot(db, user_id):
    async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise ValueError('Meet your first companion before opening Trials.')
    player = dict(row)
    party = await adventure_party.rows(user_id, db=db)
    async with db.execute('SELECT * FROM player_perks WHERE user_id=? ORDER BY perk_id', (user_id,)) as cursor:
        perks = [dict(row) for row in await cursor.fetchall()]
    async with db.execute('SELECT * FROM player_equipment WHERE user_id=? ORDER BY id', (user_id,)) as cursor:
        gear = [dict(row) for row in await cursor.fetchall()]
    async with db.execute('SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON m.guild_id=s.guild_id WHERE m.user_id=? AND s.arcane_library=1', (user_id,)) as cursor:
        library = bool(await cursor.fetchone())
    return dict(player=player, rows=party, perks=perks, gear=gear, library=library)


async def board(user_id, *, now=None):
    now = time.time() if now is None else now
    run = await current(user_id, now=now)
    await _report_pending(user_id)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as cursor:
            row = await cursor.fetchone()
        player = dict(row) if row else {}
        ready_at = await _ready_at(db, user_id, player)
        from utils.rewards import has_perk
        hourglass = await has_perk(db, user_id, 'chronos_hourglass')
        async with db.execute('SELECT item_id,SUM(quantity) FROM player_inventory WHERE user_id=? AND quantity>0 GROUP BY item_id', (user_id,)) as cursor:
            inventory = dict(await cursor.fetchall())
        async with db.execute('SELECT * FROM trial_stamps WHERE user_id=?', (user_id,)) as cursor:
            stamps = {row['destination']: dict(row) for row in await cursor.fetchall()}
    definitions = allowed_items()
    return dict(player=player, run=run, ready_at=ready_at, now=now, stamps=stamps,
                destinations=[dict(trial_rules.config(key), key=key, name=region['name'],
                                   effective_cooldown_seconds=int(trial_rules.config(key)['cooldown_seconds'] * (.8 if hourglass else 1)),
                                   unlocked=player.get('level', 0) >= region['min_level'])
                              for key, region in BIOMES.items()],
                care_items=[dict(id=key, name=definitions[key]['name'], quantity=quantity,
                                 effect=definitions[key]['effect'])
                            for key, quantity in inventory.items() if key in definitions])


snapshot = board


async def _resources(db, run, now):
    """Write proportions against collection maxima, without temporary gear HP."""
    for state in run['payload']['party']:
        hp = max(0, min(state['max_hp'], state['hp']))
        origin_mana, prepared_mana = state.get('_pve_mana_origin', (0, 0))
        await db.execute('UPDATE player_beasts SET hp=CASE WHEN ?>0 THEN MAX(1,CAST(max_hp*?/? AS INTEGER)) ELSE 0 END,'
                         'mana=MAX(0,MIN(max_mana,CAST(?+max_mana*?/? AS INTEGER))) WHERE user_id=? AND id=?',
                         (hp, hp, max(1, state['max_hp']), origin_mana, state['mana'] - prepared_mana,
                          max(1, state['max_mana']), run['user_id'], state['id']))
        if hp <= 0:
            until = (datetime.fromtimestamp(now, timezone.utc) + timedelta(
                seconds=storage.KO_RECOVERY_SECONDS.get(state.get('rarity'), 1800))).isoformat()
            await db.execute('UPDATE player_beasts SET knocked_out_until=COALESCE(knocked_out_until,?) WHERE user_id=? AND id=?',
                             (until, run['user_id'], state['id']))
    lead = next((state for state in run['payload']['party'] if state['id'] == run['payload']['lead_id']), None)
    if lead:
        await db.execute('UPDATE players SET ultimate_charges=? WHERE user_id=?',
                         (min(3, max(0, lead.get('ultimate_charges', 0))), run['user_id']))


async def _story_event(db, user_id, event, **context):
    from utils import story
    state = await story.read_state(db, user_id)
    chapter = story.load_questline()['chapters'].get(state['current_chapter'])
    if chapter and chapter['id'] not in state['completed_chapters']:
        story.apply_event(state, chapter, event, **context)
        await story.write_state(db, state)


async def _settle(db, run, outcome, now):
    """Wallet, XP, win/story receipt and stamp share the run's writer commit."""
    p = run['payload']
    if p.get('settlement') is not None:
        return
    from utils.duels import beast_exp_in_transaction
    from utils.rewards import player_exp
    total = p['plan']['total']
    clean = (outcome == 'won' and p['cleared'] == total and any(member['hp'] > 0 for member in p['party'])
             and p.get('enemy') is not None and p['enemy']['hp'] <= 0)
    if outcome == 'won' and not clean:
        outcome = 'lost'
    fraction = trial_rules.progress_fraction(min(p['cleared'], total - (not clean)), total)
    quote = reward_quote(p, fraction=fraction)
    gold, trainer_xp = quote['gold'], quote['trainer_xp']
    await _resources(db, run, now)
    await db.execute('UPDATE players SET gold=gold+?,total_gold_earned=total_gold_earned+? WHERE user_id=?',
                     (gold, gold, run['user_id']))
    level, _, leveled = await player_exp(db, run['user_id'], trainer_xp)
    if leveled:
        await _story_event(db, run['user_id'], 'level_up', level=level)
    party_xp = []
    for member in quote['party_xp']:
        amount = member['amount']
        new_level, advanced = await beast_exp_in_transaction(db, run['user_id'], member['id'], amount, bonuses=False)
        party_xp.append(dict(id=member['id'], name=member['name'], amount=amount, level=new_level, leveled=advanced))
    stamp = False
    if clean:
        event_key = 'trial:' + run['id'] + ':battle_win'
        claim = await db.execute("INSERT OR IGNORE INTO gameplay_events(event_key,user_id,event_type) VALUES(?,?,'battle_win')", (event_key, run['user_id']))
        if claim.rowcount:
            await db.execute("INSERT INTO player_progress(user_id,stat_key,value) VALUES(?,'battle_win',1) ON CONFLICT(user_id,stat_key) DO UPDATE SET value=value+1", (run['user_id'],))
            await db.execute('UPDATE players SET wins=wins+1 WHERE user_id=?', (run['user_id'],))
            await db.execute('UPDATE player_beasts SET bond=MIN(100,bond+2) WHERE user_id=? AND id=?', (run['user_id'], p['lead_id']))
            await _story_event(db, run['user_id'], 'battle_win')
        claim = await db.execute('INSERT OR IGNORE INTO trial_stamps VALUES(?,?,?,?,?)',
                                 (run['user_id'], run['destination'], run['id'], now, p.get('actions', 0)))
        stamp = bool(claim.rowcount)
        await db.execute('UPDATE trial_stamps SET best_actions=MIN(best_actions,?) WHERE user_id=? AND destination=?',
                         (p.get('actions', 0), run['user_id'], run['destination']))
    p.update(outcome=outcome, turn_deadline=None,
             settlement=dict(gold=gold, trainer_xp=trainer_xp, party_xp=party_xp,
                             fraction=fraction, outcome=outcome, stamp=stamp))
    run['status'] = 'complete'


async def report(run):
    """Keyed post-commit daily/achievement work; core win/story is already atomic."""
    if run['status'] != 'complete' or run.get('reported'):
        return
    from utils import progress
    if run['payload']['outcome'] == 'won':
        await progress._track_daily_event(run['user_id'], 'battle_win', event_key='trial:' + run['id'] + ':daily-win')
    if any(row['leveled'] for row in run['payload']['settlement']['party_xp']):
        await progress._track_daily_event(run['user_id'], 'beast_level_up', event_key='trial:' + run['id'] + ':daily-level')
    await progress.check_achievements(run['user_id'])
    async with storage.connect() as db:
        async with db.execute("SELECT 1 FROM raid_settlements s,json_each(s.payload,'$.users') member "
                              'WHERE s.completed=0 AND CAST(member.value AS INTEGER)=? LIMIT 1', (run['user_id'],)) as cursor:
            pending_raid = bool(await cursor.fetchone())
    if pending_raid:
        # Old overlapping saves defer raid HP/XP until their trial settles.
        # Filtered recovery must not refund unrelated raids still preparing.
        from utils.raids import recover_raids
        await recover_raids(user_id=run['user_id'])
    async with storage.connect() as db:
        await db.execute('UPDATE trial_runs SET reported=1 WHERE id=? AND user_id=?', (run['id'], run['user_id']))
        await db.commit()
    run['reported'] = 1


async def _after_commit(run):
    try:
        await report(run)
    except Exception:
        # The committed result is still reviewable. A later board/read retries
        # its keyed auxiliary claims instead of repeating the run's payout.
        logging.getLogger('chibibeasts.trials').exception('Trial follow-up pending: %s', run['id'])


async def _report_pending(user_id):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trial_runs WHERE user_id=? AND status='complete' AND reported=0", (user_id,)) as cursor:
            pending = [_decode(row) for row in await cursor.fetchall()]
    for run in pending:
        await _after_commit(run)


async def current(user_id, *, now=None):
    now = time.time() if now is None else now
    async with combat.lock_states([user_id]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute("SELECT * FROM trial_runs WHERE user_id=? AND status IN ('between','fighting','paused') ORDER BY created_at DESC LIMIT 1", (user_id,)) as cursor:
                run = _decode(await cursor.fetchone())
            if run and run['expires_at'] <= now:
                await _settle(db, run, 'expired', now)
                await _save(db, run, now)
            await db.commit()
            if run and run['status'] == 'complete':
                trial_locks.unlock(user_id)
            elif run:
                trial_locks.lock(user_id)
    if run and run['status'] == 'complete':
        await _after_commit(run)
        return None
    return run


async def read(user_id, run_id, *, now=None):
    await current(user_id, now=now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        run = await _load(db, user_id, run_id)
    await _after_commit(run)
    return run


async def begin(user_id, destination, *, seed=None, now=None):
    from utils.duels import prepare_state, reserve_fighters
    now = time.time() if now is None else now
    config = trial_rules.config(destination)
    await current(user_id, now=now)
    await _report_pending(user_id)
    seed = random.SystemRandom().getrandbits(64) if seed is None else int(seed)
    plan = trial_rules.generate_plan(destination, seed)
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id]):
            await storage.get_player_beasts(user_id)
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                before = await _snapshot(db, user_id)
            player, rows = before['player'], before['rows']
            if player['level'] < config['min_level']:
                raise ValueError(f"Reach trainer level {config['min_level']} for this trial.")
            if not rows or not rows[0]['is_active']:
                raise ValueError('Choose a healthy, unlisted lead companion before beginning a trial.')
            prepared = []
            for index, row in enumerate(rows):
                state = await prepare_state(user_id, row, storage.get_beast_data(row['beast_id']), before['perks'],
                                            consume=False, party_reserve=index > 0, preserve_resources=True)
                # Towers carry current resources, rather than reapplying solo
                # battle's opening mana cap or a fresh mana refill every wave.
                base_max = max(1, row['max_mana'])
                state['mana'] = min(state['max_mana'],
                                    (row['mana'] * state['max_mana'] + base_max - 1) // base_max
                                    + (20 if 'genesis_spark' in state['perks'] else 0))
                state['_pve_mana_origin'] = (row['mana'], state['mana'])
                prepared.append(state)
            from utils import trial_combat
            payload = trial_combat.new_run(prepared, plan)
            final = plan['waves'][-1]
            enemy = trial_combat.build_enemy(final)
            difficulty = adventure_party.encounter_difficulty(prepared, enemy)
            lead_level = rows[0]['level']
            base = encounter_beast_xp(lead_level, final['level'], difficulty=difficulty)
            baseline_party = [dict(id=row['id'], name=row.get('nickname') or storage.get_beast_data(row['beast_id'])['name'],
                                   level=row['level'],
                                   idle=adventure_party.xp_budget(row['level'], lead_level, final['level'], base, lead=index == 0),
                                   contributed=adventure_party.xp_budget(row['level'], lead_level, final['level'], base,
                                                                       lead=index == 0, contributed=True))
                              for index, row in enumerate(rows)]
            scholar = any(perk['perk_id'] == 'fable_scholar' and perk['equipped'] for perk in before['perks'])
            reward = dict(gold=random.Random(seed ^ 0x7A11).randint(final['level'] * 8, final['level'] * 15),
                          trainer_xp=encounter_trainer_xp(player['level'], final['level'], difficulty=difficulty),
                          party=baseline_party, scholar=scholar, library=before['library'], difficulty=difficulty)
            payload.update(party_ids=[row['id'] for row in rows], lead_id=rows[0]['id'], entry_rows=rows,
                           reward_baseline=reward, items_used=0, camp_used=False, actions=0,
                           settlement=None, turn_deadline=None)
            _refresh_quotes(payload)
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                await assert_available(db, user_id)
                async with db.execute("SELECT 1 FROM raid_settlements s,json_each(s.payload,'$.users') member "
                                      'WHERE s.completed=0 AND CAST(member.value AS INTEGER)=? LIMIT 1', (user_id,)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError('Your previous raid rewards are still settling. Finish their recovery before beginning a trial.')
                fresh = await _snapshot(db, user_id)
                if _json(fresh) != _json(before):
                    raise ValueError('Your team or preparations changed while entering. Review the board and try again.')
                async with db.execute("SELECT 1 FROM expeditions WHERE user_id=? AND status IN ('active','battle','quick')", (user_id,)) as cursor:
                    if await cursor.fetchone():
                        raise ValueError('Return from your saved expedition before beginning a trial.')
                ready = await _ready_at(db, user_id, player)
                if ready > now:
                    raise ValueError(f'Trials are cooling down for {max(1, int(ready - now))} more seconds.')
                from utils.rewards import cooldown
                ready = now + await cooldown(db, user_id, config['cooldown_seconds'])
                run_id = uuid4().hex
                await db.execute('INSERT INTO trial_runs(id,user_id,destination,status,payload,created_at,updated_at,expires_at) VALUES(?,?,?,\'between\',?,?,?,?)',
                                 (run_id, user_id, destination, _json(payload), now, now, now + EXPIRY_SECONDS))
                await db.execute('INSERT INTO trial_cooldowns VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET ready_at=excluded.ready_at', (user_id, ready))
                await db.execute('UPDATE players SET challenge_last_at=?,next_defense_boost=0,next_speed_boost=0,status_ward_charges=MAX(0,status_ward_charges-1) WHERE user_id=?', (now, user_id))
                run = await _load(db, user_id, run_id)
                await db.commit()
                trial_locks.lock(user_id)
    return run


def _camp(payload):
    if payload.get('camp_used') or payload['cleared'] != payload['plan']['camp_wave']:
        return False
    for member in payload['party']:
        if member['hp'] > 0:
            member.update(status=None, status_turns=0)
            member['hp'] = min(member['max_hp'], member['hp'] + max(1, member['max_hp'] // 4))
            member['mana'] = min(member['max_mana'], member['mana'] + max(1, member['max_mana'] // 4))
    payload['camp_used'] = True
    payload['log'].append('Midpoint camp: surviving teammates recover 25% max HP and MP; conditions cleared. Knockouts stay out.')
    return True


async def _mutate(user_id, run_id, version, operation, *, now=None, stale_ok=False):
    from utils.duels import reserve_fighters
    now = time.time() if now is None else now
    async with combat.lock_states([user_id]):
        with reserve_fighters([user_id], allow_trial=True):
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute('BEGIN IMMEDIATE')
                run = await _load(db, user_id, run_id)
                if stale_ok and (run['status'] not in ACTIVE or run['version'] != version):
                    await db.commit()
                    return run
                _check(run, version)
                if run['expires_at'] <= now:
                    await _settle(db, run, 'expired', now)
                else:
                    result = await operation(db, run, now)
                    if result is False:
                        await db.commit()
                        return run
                    p = run['payload']
                    if p['status'] in ('won', 'lost'):
                        await _settle(db, run, p['status'], now)
                    elif run['status'] != 'complete':
                        run['status'] = p['status']
                        if p['status'] == 'between':
                            _camp(p)
                        await _resources(db, run, now)
                await _save(db, run, now)
                await db.commit()
                if run['status'] == 'complete':
                    trial_locks.unlock(user_id)
    await _after_commit(run)
    return run


async def next_wave(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('between',))
        from utils import trial_combat
        trial_combat.start_wave(run['payload'])
        run['payload']['turn_deadline'] = None
    return await _mutate(user_id, run_id, version, change, now=now)


async def act(user_id, run_id, version, choice, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('fighting',))
        p = run['payload']
        if p.get('turn_deadline') and clock >= p['turn_deadline']:
            await _settle(db, run, 'timeout', clock)
            return
        from utils import trial_combat
        trial_combat.act(p, choice)
        p['actions'] += 1
        p['turn_deadline'] = None
        if p['status'] == 'between':
            _camp(p)
    return await _mutate(user_id, run_id, version, change, now=now)


async def arm_turn(user_id, run_id, version, *, now=None):
    now = time.time() if now is None else now
    async with combat.lock_states([user_id]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            run = await _load(db, user_id, run_id)
            _check(run, version, ('fighting',))
            if not run['payload'].get('turn_deadline'):
                run['payload']['turn_deadline'] = now + TURN_SECONDS
                await _save(db, run, now, increment=False)
            await db.commit()
    return run


async def timeout(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        if run['status'] != 'fighting' or not run['payload'].get('turn_deadline') or clock < run['payload']['turn_deadline']:
            return False
        await _settle(db, run, 'timeout', clock)
    return await _mutate(user_id, run_id, version, change, now=now, stale_ok=True)


async def pause(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('between',))
        run['payload'].update(paused_from='between', status='paused', turn_deadline=None)
    return await _mutate(user_id, run_id, version, change, now=now)


async def resume(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('paused',))
        run['payload']['status'] = run['payload'].pop('paused_from', 'between')
        run['payload']['turn_deadline'] = None
    return await _mutate(user_id, run_id, version, change, now=now)


async def retreat(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        await _settle(db, run, 'retreated', clock)
    return await _mutate(user_id, run_id, version, change, now=now)


async def settle(user_id, run_id, version, *, now=None):
    """Only a persisted terminal combat state can be settled as a win/loss."""
    async def change(db, run, clock):
        if run['payload']['status'] not in ('won', 'lost'):
            raise ValueError('Finish the trial or choose Retreat & bank before settlement.')
    return await _mutate(user_id, run_id, version, change, now=now)


async def camp(user_id, run_id, version, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('between',))
        if not _camp(run['payload']):
            raise ValueError('The midpoint camp is automatic and can be used only once.')
    return await _mutate(user_id, run_id, version, change, now=now)


async def use_item(user_id, run_id, version, item_id, target_row_id=None, *, now=None):
    async def change(db, run, clock):
        _check(run, version, ('between',))
        p = run['payload']
        if p['items_used'] >= ITEM_LIMIT:
            raise ValueError('This trial has used its three care items.')
        item = allowed_items().get(item_id)
        if not item:
            raise ValueError('Choose an owned HP potion or Aether Tonic. Revives, growth and queued boosts stay outside Trials.')
        target = next((member for member in p['party'] if member['id'] == target_row_id), None) if target_row_id is not None else p['party'][p['slot']]
        if target is None or target['hp'] <= 0:
            raise ValueError('Choose a surviving member of this saved trial team. Knockouts cannot be revived.')
        effect = item['effect']
        if effect.get('heal_percent'):
            gain = min(target['max_hp'] - target['hp'], int(target['max_hp'] * effect['heal_percent'] / 100))
            if gain <= 0 or target.get('status') == 'blight':
                raise ValueError('This item cannot restore HP right now. Nothing was consumed.')
            target['hp'] += gain
        if effect.get('status_ward'):
            if target.get('status_ward') and not target.get('status'):
                raise ValueError('This companion already has an Aether ward. Nothing was consumed.')
            target.update(status=None, status_turns=0, status_ward=True)
        async with db.execute('SELECT id FROM player_inventory WHERE user_id=? AND item_id=? AND quantity>0 ORDER BY id LIMIT 1', (user_id, item_id)) as cursor:
            inventory = await cursor.fetchone()
        if inventory is None:
            raise ValueError('You do not own that item. Nothing was consumed.')
        await db.execute('UPDATE player_inventory SET quantity=quantity-1 WHERE id=? AND quantity>0', (inventory[0],))
        await db.execute('DELETE FROM player_inventory WHERE id=? AND quantity<=0', (inventory[0],))
        p['items_used'] += 1
        p['log'].append(f"Used {item['name']} on {target['name']} ({p['items_used']}/{ITEM_LIMIT} items).")
    return await _mutate(user_id, run_id, version, change, now=now)


async def recover_interrupted(db):
    """An app restart pauses the exact wave rather than resetting its opponent."""
    previous_factory = db.row_factory
    db.row_factory = aiosqlite.Row
    try:
        async with db.execute("SELECT * FROM trial_runs WHERE status='fighting'") as cursor:
            rows = await cursor.fetchall()
    finally:
        db.row_factory = previous_factory
    for row in rows:
        run = _decode(row)
        run['payload'].update(status='paused', paused_from='fighting', turn_deadline=None)
        run['status'] = 'paused'
        await _save(db, run, time.time())
