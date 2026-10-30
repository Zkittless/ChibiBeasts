"""Authored, finite exploration stories with saved pages and durable progress.

The expedition writer owns resources, encounters and settlement. This module
owns authored choices, bounded intentions and permanent world consequences.
"""
from copy import deepcopy
import hashlib
import json
import time

from utils import adventure_content
from utils.habitats import BIOMES

VERSION = 3
RECENT_WINDOW = 3
MAX_RECOVERY = 20
MAX_BOON = 20
MAX_BONUS = 25
MAX_BOND = 5


def _matches(record, flags):
    return set(record.get('requires_flags', ())) <= flags and not set(record.get('forbid_flags', ())).intersection(flags)


def _dirty(payload):
    return payload.setdefault('world_dirty', dict(progress={}, nodes={}, set_flags=[], clear_flags=[], recent=[]))


async def world(db, user_id, biome):
    """Read a trainer's durable world without selecting or rerolling a page."""
    async with db.execute('SELECT encounter_id,node_id,completed,snapshot FROM adventure_progress WHERE user_id=?', (user_id,)) as cursor:
        progress = {row[0]: dict(node_id=row[1], completed=bool(row[2]),
                                 snapshot=json.loads(row[3]) if row[3] else None)
                    for row in await cursor.fetchall() if adventure_content.belongs(row[0], biome)}
    async with db.execute('SELECT flag FROM adventure_flags WHERE user_id=?', (user_id,)) as cursor:
        flags = [row[0] for row in await cursor.fetchall() if adventure_content.belongs(row[0], biome)]
    async with db.execute('SELECT node_id,resolved FROM adventure_nodes WHERE user_id=?', (user_id,)) as cursor:
        nodes = {row[0]: bool(row[1]) for row in await cursor.fetchall() if adventure_content.belongs(row[0], biome)}
    async with db.execute('SELECT encounter_id FROM adventure_recent WHERE user_id=? AND biome=? ORDER BY id DESC LIMIT ?', (user_id, biome, RECENT_WINDOW)) as cursor:
        recent = [row[0] for row in await cursor.fetchall()][::-1]
    return dict(progress=progress, flags=sorted(flags), nodes=nodes, recent=recent)


def _progress(payload, root_id, node_id, completed=False):
    record = dict(node_id=node_id, completed=completed,
                  snapshot=None if completed else deepcopy(payload['content_snapshot'][root_id]))
    payload['world_progress'][root_id] = record
    _dirty(payload)['progress'][root_id] = record


def _node_seen(payload, node_id, resolved=False):
    value = resolved or payload['world_nodes'].get(node_id, False)
    payload['world_nodes'][node_id] = value
    _dirty(payload)['nodes'][node_id] = value


async def persist(db, user_id, payload):
    """Flush only this page's changes inside the caller's existing transaction."""
    changes = _dirty(payload)
    for root_id, state in changes['progress'].items():
        await db.execute('INSERT INTO adventure_progress(user_id,encounter_id,node_id,completed,snapshot,updated_at) VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT(user_id,encounter_id) DO UPDATE SET node_id=excluded.node_id,completed=excluded.completed,snapshot=excluded.snapshot,updated_at=excluded.updated_at',
                         (user_id, root_id, state['node_id'], int(state['completed']),
                          json.dumps(state['snapshot']) if state['snapshot'] else None, time.time()))
    for node_id, resolved in changes['nodes'].items():
        await db.execute('INSERT INTO adventure_nodes VALUES(?,?,?) ON CONFLICT(user_id,node_id) DO UPDATE SET resolved=MAX(resolved,excluded.resolved)',
                         (user_id, node_id, int(resolved)))
    for flag in changes['set_flags']:
        await db.execute('INSERT OR IGNORE INTO adventure_flags VALUES(?,?)', (user_id, flag))
    for flag in changes['clear_flags']:
        await db.execute('DELETE FROM adventure_flags WHERE user_id=? AND flag=?', (user_id, flag))
    for root_id in changes['recent']:
        await db.execute('INSERT INTO adventure_recent(user_id,biome,encounter_id) VALUES(?,?,?)',
                         (user_id, payload['destination'], root_id))
    if changes['recent']:
        await db.execute('DELETE FROM adventure_recent WHERE user_id=? AND biome=? AND id NOT IN '
                         '(SELECT id FROM adventure_recent WHERE user_id=? AND biome=? ORDER BY id DESC LIMIT ?)',
                         (user_id, payload['destination'], user_id, payload['destination'], RECENT_WINDOW))
    payload['world_dirty'] = dict(progress={}, nodes={}, set_flags=[], clear_flags=[], recent=[])


def initialize(payload, biome, seed, party, saved_world=None, *, content=None, combat_route=False):
    """Choose the first actual scene, freezing all prose and future root graphs."""
    if biome not in BIOMES:
        raise ValueError('Choose a known exploration destination.')
    document = adventure_content.load(biome) if content is None else deepcopy(content)
    saved_world = deepcopy(saved_world or dict(progress={}, flags=[], nodes={}, recent=[]))
    roots = {root['id']: deepcopy(root) for root in document['encounters']}
    # A changed deployment cannot erase an unfinished trainer's next scene.
    for root_id, record in saved_world['progress'].items():
        if not record['completed'] and record.get('snapshot'):
            roots[root_id] = deepcopy(record['snapshot'])
    order = sorted(roots, key=lambda root_id: hashlib.sha256(f'{seed}:authored-roots:{root_id}'.encode()).digest())
    index = list(BIOMES).index(biome)
    identities = [dict(id=member['id'], beast_id=member['beast_id'],
                       name=member.get('nickname') or member.get('name') or member['beast_id'],
                       type=member.get('type', '')) for member in party]
    payload.update(adventure_version=VERSION, destination=biome, seed=str(seed),
                   party_ids=[member['id'] for member in identities], party=identities,
                   completed=0, total=5 + index // 3, target_stops=5 + index // 3,
                   stage=0, bonus=0, bond=0, happiness=0, notes=[], history=[],
                   boons=dict(attack=0, defense=0), recovery_used=0, study_used=False,
                   content_snapshot=roots, root_order=order, generator_cursor=0,
                   world_progress=saved_world['progress'], world_flags=saved_world['flags'],
                   world_nodes=saved_world.get('nodes', {}), world_recent=saved_world['recent'],
                   recent_exclusions=list(saved_world['recent']), visited_roots=[], visited_nodes=[],
                   journey_status='active', pacing_version=0, pending_battle=None, current_root=None,
                   current_node=None, exhausted=False, familiar=False,
                   world_dirty=dict(progress={}, nodes={}, set_flags=[], clear_flags=[], recent=[]))
    _dirty(payload)
    if combat_route:
        configure_combat_route(payload, biome)
    _advance(payload)


def configure_combat_route(payload, biome, *, preserve=False):
    """Freeze mandatory combat beats separately from finite story discoveries."""
    target = 2 if list(BIOMES).index(biome) < 5 else 3
    seed = payload['seed']
    digest = hashlib.sha256(f'{seed}:combat-route-v1'.encode()).digest()
    schedule = ['single', 'pack2'] if target == 2 else ['single', 'pack2' if digest[0] % 2 else 'pack3', 'single']
    won = min(target, payload.get('fights_won', 0)) if preserve else 0
    if preserve:
        payload['inherited_completed'] = payload['completed']
        payload['inherited_fraction'] = min(1., payload['completed'] / max(1, payload['total']))
        if not payload.get('pending_battle') and payload.get('current_root') and payload.get('current_node'):
            payload['route_continuation'] = [payload['current_root'], payload['current_node']]
        if payload.get('pending_battle'):
            payload['pending_battle']['counts_for_route'] = True
    payload.update(pacing_version=1, battle_target=target, battles_completed=won,
                   battle_schedule=schedule, recruitable_beat=digest[1] % target,
                   completed=min(target * 2 - 1, won * 2), total=target * 2 - 1,
                   target_stops=target * 2 - 1, exhausted=False)
    payload['stage'] = payload['completed']


def _route_battle(payload):
    index = min(payload['battles_completed'], payload['battle_target'] - 1)
    kind = payload['battle_schedule'][index]
    pack = kind.startswith('pack')
    payload.update(journey_status='active', current_root=None, current_node=None)
    payload['page'] = dict(title='A wild pack approaches' if pack else 'A wild challenger appears',
        body='Your team spots movement ahead. Prepare to face the pack.' if pack else 'A wild beast steps onto the trail. Your team moves into position.',
        kind='battle', root_id=None, node_id=None, repeatable=True,
        choices=[dict(value=f'fight:{index}', id=f'fight_{index}', label='Face the pack' if pack else 'Enter battle',
                      description='Fight with your current team resources. Retreat remains available.',
                      outcome='The encounter is won.', effects={'battle': kind})])


def _route_camp(payload):
    payload.update(journey_status='active', current_root=None, current_node=None)
    payload['page'] = dict(title='A moment to regroup', body='The trail is quiet for a moment. Prepare your team for the next encounter.',
        kind='route_camp', root_id=None, node_id=None, repeatable=True,
        choices=[dict(value='breather:heal', id='heal', label='Catch your breath', description='Recover up to 5% team HP within the journey recovery limit.',
                      outcome='The surviving team catches its breath.', effects={'heal': 5}),
                 dict(value='breather:brace', id='brace', label='Prepare a defence', description='+5% team defence until return, within the 20% cap.',
                      outcome='Your team braces for the next encounter.', effects={'defense_boon': 5})])


async def begin(db, user_id, payload, biome, seed, party, *, content=None):
    saved = await world(db, user_id, biome)
    initialize(payload, biome, seed, party, saved, content=content)
    await persist(db, user_id, payload)


def _eligible(payload):
    flags = set(payload['world_flags'])
    groups = [[], [], [], []]
    for root_id in payload['root_order']:
        if root_id in payload['visited_roots']:
            continue
        root = payload['content_snapshot'][root_id]
        progress = payload['world_progress'].get(root_id)
        if progress and not progress['completed']:
            # A started location remains resumable even if an earlier branch
            # changed its discovery gate; its node choices still check flags.
            groups[0].append((root_id, progress['node_id']))
            continue
        if not _matches(root, flags):
            continue
        if root['repeatable']:
            if root_id not in payload['recent_exclusions']:
                groups[3].append((root_id, root['entry']))
        elif not progress or not progress['completed']:
            groups[1 if root.get('requires_flags') else 2].append((root_id, root['entry']))
    return groups


def _home(payload, *, exhausted=False):
    if payload.get('pacing_version') == 1 and payload['battles_completed'] < payload['battle_target']:
        _route_battle(payload)
        return
    if exhausted:
        payload['total'] = payload['target_stops'] = payload['completed']
        payload['exhausted'] = True
    payload.update(journey_status='homeward', current_root=None, current_node=None)
    payload['page'] = dict(title='Familiar territory' if exhausted else 'The way home',
                           body=('The available trails for this visit are explored. Completed stories stay complete; '
                                 'the journal will offer fresh discoveries, unfinished locations or different familiar wilderness on another visit.'
                                 if exhausted else 'Your team has finished the planned journey. Your choices and unfinished locations are saved. Return to bank the one earned reward bundle.'),
                           choices=[dict(value='return', label='Return and bank rewards',
                                         description='Finish this journey and claim its one earned bundle.')],
                           root_id=None, node_id=None, kind='homeward', repeatable=False)


def _advance(payload, continuation=None):
    paced = payload.get('pacing_version') == 1
    if paced and continuation:
        payload['route_continuation'] = list(continuation)
    if payload['completed'] >= payload['total']:
        _home(payload)
        return
    if paced:
        if payload['completed'] % 2 == 0:
            _route_battle(payload)
            return
        continuation = payload.pop('route_continuation', None)
    if continuation is not None:
        _show(payload, *continuation)
        return
    groups = _eligible(payload)
    selected = next((group[0] for group in groups if group), None)
    if selected is None:
        if paced:
            _route_camp(payload)
        else:
            _home(payload, exhausted=True)
        return
    root_id, node_id = selected
    payload['generator_cursor'] += 1
    payload['visited_roots'].append(root_id)
    root = payload['content_snapshot'][root_id]
    if root['repeatable']:
        payload['familiar'] = True
        payload['world_recent'] = (payload['world_recent'] + [root_id])[-RECENT_WINDOW:]
        _dirty(payload)['recent'].append(root_id)
    _show(payload, root_id, node_id)


def _effective(payload, raw):
    effects = deepcopy(raw)
    for key, stat in (('attack_boon', 'attack'), ('defense_boon', 'defense')):
        if key in effects:
            effects[key] = max(0, min(effects[key], MAX_BOON - payload['boons'][stat]))
    if 'heal' in effects:
        effects['heal'] = max(0, min(effects['heal'], MAX_RECOVERY - payload['recovery_used']))
    if 'bonus' in effects:
        effects['bonus'] = max(0, min(effects['bonus'], MAX_BONUS - payload['bonus']))
    if 'bond' in effects:
        effects['bond'] = max(0, min(effects['bond'], MAX_BOND - payload['bond']))
    if effects.get('study') and payload['study_used']:
        effects['study'] = 0
    if effects.get('material') and len(payload.get('offered', ())) < 2:
        projected = min(MAX_BONUS, payload['bonus'] + effects.get('bonus', 0) + (5 if effects.get('study') else 0))
        effects['material_bonus'] = min(2, MAX_BONUS - projected)
    return effects


def _show(payload, root_id, node_id):
    root = payload['content_snapshot'][root_id]
    node = root['nodes'][node_id]
    flags = set(payload['world_flags'])
    choices = []
    for choice in node['choices']:
        if not _matches(choice, flags):
            continue
        frozen = deepcopy(choice)
        # Discord caps select values at 100 characters, including the service's
        # trip/revision prefix. Permanent authored IDs remain in the snapshot;
        # the offered token is compact, deterministic and revision-checked.
        token = hashlib.sha256(f'{node_id}:{choice["id"]}'.encode()).hexdigest()[:20]
        frozen['value'] = 'scene:' + token
        bounded = _effective(payload, choice['effects'])
        capped = [key for key in ('heal', 'attack_boon', 'defense_boon', 'bonus', 'bond', 'study')
                  if key in bounded and bounded[key] != choice['effects'][key]]
        if capped:
            readable = {'heal': 'recovery', 'attack_boon': 'attack boon', 'defense_boon': 'defense boon',
                        'bonus': 'bundle bonus', 'bond': 'bond', 'study': 'research'}
            frozen['description'] = 'Run limit: ' + ', '.join(f'{readable[key]} +{bounded[key]}' for key in capped) + '. Other listed effects still apply.'
        if choice['effects'].get('material'):
            prefix = 'After battle: ' if choice['effects'].get('battle') else ''
            if choice['effects'].get('hp_cost'):
                prefix += f"Pay {choice['effects']['hp_cost']}% HP; "
            benefit = ('Choose one same-rarity material; replaces your bundle.' if len(payload.get('offered', ())) >= 2 else
                       f"Bundle bonus +{bounded['material_bonus']}% (25% cap); keep the saved material.")
            frozen['description'] = prefix + benefit + (' World remembers.' if choice['effects'].get('set_flags') else '')
        choices.append(frozen)
    if not choices:
        raise ValueError('This saved scene has no available choice. Its content needs review.')
    payload.update(current_root=root_id, current_node=node_id, journey_status='active')
    if node_id not in payload['visited_nodes']:
        payload['visited_nodes'].append(node_id)
    _node_seen(payload, node_id)
    _progress(payload, root_id, node_id)
    payload['page'] = dict(title=('Familiar territory · ' if root['repeatable'] else '') + root['title'],
                           body=node['body'], choices=choices, root_id=root_id, node_id=node_id,
                           kind=root['kind'], repeatable=root['repeatable'])


def options(trip):
    if trip['status'] != 'active':
        return ()
    values = tuple(choice['value'] for choice in trip['payload']['page']['choices'])
    if 'return' in values:
        return values + (('return_egg',) if trip['payload'].get('egg_offer') and trip['payload']['completed'] else ())
    return values + ('return_early',)


def presentation(trip):
    p = trip['payload']
    page = deepcopy(p['page'])
    page['choices'] = [dict(value=choice['value'], label=choice['label'], description=choice['description'])
                       for choice in page['choices']] if trip['status'] == 'active' else []
    if trip['status'] == 'active' and not any(choice['value'] == 'return' for choice in page['choices']):
        page['choices'].append(dict(value='return_early', label='Retreat and return' if p.get('pacing_version') == 1 else 'Head home now',
                                    description='Bank the earned fraction; this location resumes at its next unresolved scene.'))
    elif trip['status'] == 'active' and 'return_egg' in options(trip):
        page['choices'].append(dict(value='return_egg', label='Shelter the ' + p['egg_offer']['name'],
                                    description='Take the saved nest egg instead of the journey\'s one material bundle.'))
    page.update(progress=dict(completed=p['completed'], total=p['total']), party_ids=list(p['party_ids']),
                footer=f"{p['completed']}/{p['total']} steps · one material or nest egg · one possible catch · bonus {p['bonus']}%")
    return page


def _apply(payload, effects):
    result = _effective(payload, effects)
    for key, stat in (('attack_boon', 'attack'), ('defense_boon', 'defense')):
        payload['boons'][stat] += result.get(key, 0)
    payload['recovery_used'] += result.get('heal', 0)
    payload['bonus'] += result.get('bonus', 0)
    payload['bond'] += result.get('bond', 0)
    if result.get('study'):
        payload['study_used'] = True
        payload['bonus'] = min(MAX_BONUS, payload['bonus'] + 5)
    payload['bonus'] += result.get('material_bonus', 0)
    flags = set(payload['world_flags'])
    changes = _dirty(payload)
    for flag in result.get('set_flags', ()):
        flags.add(flag)
        if flag not in changes['set_flags']:
            changes['set_flags'].append(flag)
        if flag in changes['clear_flags']:
            changes['clear_flags'].remove(flag)
    for flag in result.get('clear_flags', ()):
        flags.discard(flag)
        if flag not in changes['clear_flags']:
            changes['clear_flags'].append(flag)
        if flag in changes['set_flags']:
            changes['set_flags'].remove(flag)
    payload['world_flags'] = sorted(flags)
    return result


def _finish_node(payload, root_id, node_id, next_node):
    _node_seen(payload, node_id, resolved=True)
    payload['completed'] += 1
    payload['stage'] = payload['completed']
    _progress(payload, root_id, next_node or node_id, completed=not bool(next_node))
    _advance(payload, (root_id, next_node) if next_node else None)


async def resolve(db, trip, choice):
    """Resolve only a frozen offered choice; the caller guards row revision."""
    p, uid = trip['payload'], trip['user_id']
    if trip['status'] != 'active' or p['journey_status'] == 'battle':
        raise ValueError('Finish the current encounter before choosing another scene.')
    frozen = next((item for item in p['page']['choices'] if item['value'] == choice), None)
    if frozen is None or choice == 'return':
        raise ValueError('Choose a current authored scene option, or bank through the expedition service.')
    root_id, node_id = p['current_root'], p['current_node']
    if frozen['effects'].get('battle'):
        # Persist the intention, not victory consequences. A loss or retreat
        # must leave this exact unresolved scene available on the next visit.
        p['pending_battle'] = dict(root_id=root_id, node_id=node_id, choice=frozen['id'],
                                   next=frozen.get('next'), outcome=frozen['outcome'],
                                   effects={key: deepcopy(value) for key, value in frozen['effects'].items() if key != 'battle'})
        if p['page']['kind'] == 'battle' and p.get('pacing_version') == 1:
            p['pending_battle']['route_battle'] = True
        p['journey_status'] = 'battle'
        await persist(db, uid, p)
        return dict(battle=True, battle_kind=frozen['effects']['battle'], status='battle')
    effects = _apply(p, frozen['effects'])
    p['history'].append(dict(root_id=root_id, node_id=node_id, choice=frozen['id'], outcome=frozen['outcome']))
    p['notes'].append(frozen['outcome'])
    if p['page']['kind'] == 'route_camp':
        p['completed'] += 1
        p['stage'] = p['completed']
        _advance(p)
    else:
        _finish_node(p, root_id, node_id, frozen.get('next'))
    await persist(db, uid, p)
    return dict(effects, status='active')


async def finish_battle(db, trip, won, interrupted=False):
    p = trip['payload']
    pending = p.get('pending_battle')
    if not pending or p['journey_status'] != 'battle':
        raise ValueError('This scene battle has already resolved.')
    p['pending_battle'] = None
    effects = {}
    if won and not interrupted:
        effects = _apply(p, pending['effects'])
        p['notes'].append(pending['outcome'])
        p['history'].append(dict(root_id=pending['root_id'], node_id=pending['node_id'],
                                 choice=pending['choice'], outcome=pending['outcome'], result='won'))
        if pending.get('route_battle'):
            p['battles_completed'] += 1
            p['completed'] += 1
            p['stage'] = p['completed']
            _advance(p)
        else:
            if pending.get('counts_for_route'):
                p['battles_completed'] += 1
            _finish_node(p, pending['root_id'], pending['node_id'], pending['next'])
    else:
        p['journey_status'] = 'active'
        outcome = ('The encounter paused; this unresolved scene remains saved.' if interrupted else
                   'The team steps back. This unresolved scene remains available on another visit.')
        p['notes'].append(outcome)
        p['history'].append(dict(root_id=pending['root_id'], node_id=pending['node_id'],
                                 choice=pending['choice'], outcome=outcome,
                                 result='interrupted' if interrupted else 'lost'))
    await persist(db, trip['user_id'], p)
    return dict(effects, status=p['journey_status'])
