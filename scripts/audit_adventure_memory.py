"""Replay real authored journeys against disposable SQLite to audit world memory.

Battle intentions are resolved as wins: this tests the story planner, not combat.
No player database, reward settlement, HP, inventory or Discord UI is exercised.
"""
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiosqlite
from utils import adventure_content, journeys
from utils.habitats import BIOMES

SCHEMA = '''
CREATE TABLE adventure_progress(user_id,encounter_id,node_id,completed,snapshot,updated_at,
    PRIMARY KEY(user_id,encounter_id));
CREATE TABLE adventure_nodes(user_id,node_id,resolved,PRIMARY KEY(user_id,node_id));
CREATE TABLE adventure_flags(user_id,flag,PRIMARY KEY(user_id,flag));
CREATE TABLE adventure_recent(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id,biome,encounter_id);
'''
BEASTS = json.loads((ROOT / 'data' / 'beasts.json').read_text(encoding='utf-8'))['beasts']
PARTY = [dict(id=i, beast_id=species, name=BEASTS[species]['name'], type=BEASTS[species]['type'])
         for i, species in enumerate(('twine', 'gloop', 'barkley'), 1)]
METRICS = ('visits', 'steps', 'root_starts', 'familiar_root_starts', 'continuation_resumptions',
           'explicit_continuation_resume_probes', 'exhausted_visits', 'shortened_visits',
           'battle_intentions_assumed_won', 'published_scene_choices_resolved_in_rollback')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(record, flags):
    return (set(record.get('requires_flags', ())) <= flags
            and not set(record.get('forbid_flags', ())).intersection(flags))


def choose(choices, policy, rng):
    if policy == 'safe':
        return next(c for c in choices if not c['effects'].get('battle') and not c['effects'].get('hp_cost'))
    return choices[-1] if policy == 'last' else rng.choice(choices)


async def apply(db, trip, token, totals):
    result = await journeys.resolve(db, trip, token)
    if result.get('battle'):
        totals['battle_intentions_assumed_won'] += 1
        await journeys.finish_battle(db, trip, won=True)


async def assert_durable(db, trip):
    p = trip['payload']
    saved = await journeys.world(db, trip['user_id'], p['destination'])
    assert saved['progress'] == p['world_progress'], 'Progress was not durably saved.'
    assert saved['flags'] == sorted(p['world_flags']), 'Flags were not durably saved.'
    assert saved['nodes'] == p['world_nodes'], 'Node resolution was not durably saved.'
    assert saved['recent'] == p['world_recent'][-journeys.RECENT_WINDOW:], 'Recent window changed.'


async def verify_choices(db, trip, totals):
    """Actually resolve each published scene alternative, then roll back its DB writes."""
    p = trip['payload']
    node = p['content_snapshot'][p['current_root']]['nodes'][p['current_node']]
    expected = [c for c in node['choices'] if matches(c, set(p['world_flags']))]
    shown = p['page']['choices']
    assert [c['id'] for c in shown] == [c['id'] for c in expected], 'Published choice gates differ.'
    assert len({c['value'] for c in shown}) == len(shown), 'Published tokens collide.'
    presentation_before = deepcopy(trip)
    presented = journeys.presentation(trip)
    assert trip == presentation_before, 'Read-only presentation changed the trip.'
    assert set(c['value'] for c in presented['choices']) == set(journeys.options(trip))
    for choice, authored in zip(shown, expected):
        assert choice['effects'] == authored['effects'] and choice.get('next') == authored.get('next')
        assert choice['value'] in journeys.options(trip)
        await db.execute('SAVEPOINT published_choice')
        try:
            probe = json.loads(json.dumps(trip))
            await apply(db, probe, choice['value'], Counter())
            await assert_durable(db, probe)
            assert probe['payload']['completed'] == p['completed'] + 1
            totals['published_scene_choices_resolved_in_rollback'] += 1
        finally:
            await db.execute('ROLLBACK TO published_choice')
            await db.execute('RELEASE published_choice')


async def verify_resume(db, trip, root_id, next_node, totals):
    """A full visit need not stop mid-location, so explicitly probe early departure."""
    await db.execute('SAVEPOINT resume_location')
    try:
        saved = await journeys.world(db, trip['user_id'], trip['payload']['destination'])
        assert saved['progress'][root_id]['node_id'] == next_node
        resumed = {'offered': []}
        journeys.initialize(resumed, trip['payload']['destination'], 'resume-probe', PARTY, saved)
        assert (resumed['current_root'], resumed['current_node']) == (root_id, next_node)
        frozen = saved['progress'][root_id]['snapshot']['nodes'][next_node]
        assert resumed['page']['body'] == frozen['body'], 'Resumption changed the frozen scene.'
        await journeys.persist(db, trip['user_id'], resumed)
        await assert_durable(db, dict(user_id=trip['user_id'], payload=resumed))
        totals['explicit_continuation_resume_probes'] += 1
    finally:
        await db.execute('ROLLBACK TO resume_location')
        await db.execute('RELEASE resume_location')


async def replay(db, biome, policy, visits, uid, context):
    document = adventure_content.load(biome)
    roots = {r['id']: r for r in document['encounters']}
    totals, completed_once, resumed, familiar, length_counts = Counter(), set(), set(), Counter(), Counter()
    for visit in range(visits):
        context.update(biome=biome, policy=policy, visit=visit, node=None)
        saved = await journeys.world(db, uid, biome)
        incomplete = {rid: state for rid, state in saved['progress'].items() if not state['completed']}
        prior_recent = set(saved['recent'])
        completed_before = {rid for rid, state in saved['progress'].items()
                            if state['completed'] and not roots[rid]['repeatable']}
        assert completed_once == completed_before, 'Completed stories disappeared between visits.'
        p = {'offered': []}
        journeys.initialize(p, biome, f'memory:{biome}:{policy}:{visit}', PARTY, saved)
        planned = p['total']
        if incomplete:
            rid = p['current_root']
            assert rid in incomplete, 'An unfinished location was not prioritized.'
            assert p['current_node'] == incomplete[rid]['node_id'], 'An unfinished location restarted.'
            if p['current_node'] != roots[rid]['entry']:
                totals['continuation_resumptions'] += 1
                resumed.add(rid)
        await journeys.persist(db, uid, p)
        await db.commit()
        trip = dict(user_id=uid, status='active', payload=p)
        await assert_durable(db, trip)
        rng = random.Random(f'memory-choice:{biome}:{policy}:{visit}')
        for step in range(planned + 1):
            p = trip['payload']
            if p['journey_status'] == 'homeward':
                break
            context['node'] = p['current_node']
            assert len(p['visited_roots']) == len(set(p['visited_roots'])), 'A root restarted within one visit.'
            for rid in p['visited_roots']:
                if roots[rid]['repeatable']:
                    assert rid not in prior_recent, 'Recent familiar territory was reselected.'
                else:
                    assert rid not in completed_before, 'A completed one-time story restarted.'
            await verify_choices(db, trip, totals)
            choice = choose(p['page']['choices'], policy, rng)
            root_id = p['current_root']
            await apply(db, trip, choice['value'], totals)
            await db.commit()
            await assert_durable(db, trip)
            if choice.get('next'):
                await verify_resume(db, trip, root_id, choice['next'], totals)
            totals['steps'] += 1
            # Persisted JSON must be sufficient for the next choice, including its frozen graph.
            trip = json.loads(json.dumps(trip))
        else:
            raise AssertionError('A journey exceeded its advertised stop budget.')
        p = trip['payload']
        assert p['journey_status'] == 'homeward'
        assert len(p['visited_roots']) == len(set(p['visited_roots']))
        assert p['current_root'] is None and p['current_node'] is None
        assert [c['value'] for c in p['page']['choices']] == ['return']
        assert p['page']['kind'] == 'homeward' and p['completed'] == len(p['history'])
        if p['exhausted']:
            assert p['total'] == p['target_stops'] == p['completed'] <= planned
            assert p['page']['title'] == 'Familiar territory'
            remaining = [rid for rid, root in roots.items() if rid not in p['visited_roots']
                         and ((rid in p['world_progress'] and not p['world_progress'][rid]['completed'])
                              or (matches(root, set(p['world_flags'])) and
                                  (root['repeatable'] and rid not in prior_recent or
                                   not root['repeatable'] and not p['world_progress'].get(rid, {}).get('completed'))))]
            assert not remaining, ('Exhaustion concealed an eligible root.', remaining)
            totals['exhausted_visits'] += 1
            totals['shortened_visits'] += p['completed'] < planned
        else:
            assert p['completed'] == p['total'] == planned
            assert p['page']['title'] == 'The way home'
        after = await journeys.world(db, uid, biome)
        now = {rid for rid, state in after['progress'].items() if state['completed'] and not roots[rid]['repeatable']}
        assert completed_once <= now, 'A completed story became unresolved.'
        completed_once = now
        for rid in p['visited_roots']:
            if roots[rid]['repeatable']:
                familiar[rid] += 1
                totals['familiar_root_starts'] += 1
        totals['visits'] += 1
        totals['root_starts'] += len(p['visited_roots'])
        length_counts[p['completed']] += 1
    final = await journeys.world(db, uid, biome)
    uncompleted = [dict(id=rid, requires_flags=root.get('requires_flags', []),
                        unmet_flags=sorted(set(root.get('requires_flags', ())) - set(final['flags'])))
                   for rid, root in roots.items() if not root['repeatable'] and rid not in completed_once]
    assert totals['explicit_continuation_resume_probes'], 'No next-node resumption was tested.'
    return dict(biome=biome, policy=policy, **{key: totals[key] for key in METRICS},
                unique_completed_one_time_roots=len(completed_once), completed_root_ids=sorted(completed_once),
                resumed_location_ids=sorted(resumed),
                familiar_start_counts=dict(sorted(familiar.items())), steps_per_visit=dict(sorted(length_counts.items())),
                final_flag_count=len(final['flags']), final_resolved_nodes=sum(final['nodes'].values()),
                uncompleted_one_time_roots=uncompleted)


async def audit(output, visits):
    assert visits >= 10, 'At least ten visits are required to exercise return boundaries.'
    assert not adventure_content.validate_all(), 'The real authored catalog must validate first.'
    sources = [ROOT / 'utils' / (name + '.py') for name in ('journeys', 'adventure_content', 'habitats')]
    sources += [ROOT / 'data' / 'beasts.json', Path(__file__)] + [
        ROOT / 'data' / 'adventures' / f'{biome}.json' for biome in BIOMES]
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in sources}
    report = dict(status='running', kind='Production story-planner persistence replay, not a combat simulation.',
                  visits_per_biome_policy=visits, policies=['safe', 'last', 'seeded'], source_sha256=hashes,
                  assumptions=['In-memory SQLite contains only the four real adventure memory tables.',
                               'Each biome/policy has independent trainer history; all visits reload persisted world state.',
                               'Safe chooses the first no-battle/no-HP-cost option; last chooses the final offered option; seeded uses a stable local RNG.',
                               'Every battle intention is finished as won=True; no combat, gear, HP, item use, catches or economic settlement is simulated.',
                               'Every published scene alternative is also resolved in a SQLite savepoint and rolled back; actual history retains only the chosen option.',
                               'After each chosen location transition, a rollback-protected fresh initialization explicitly probes early-departure resumption. Natural journey-boundary resumptions are counted separately.',
                               'Three existing beast identities are planner inputs only. Active trip payloads JSON-roundtrip after every step.',
                               'Not all 20 one-time roots must be reachable under one branch policy. Unmet flag gates are recorded, not counted as reset failures.'],
                  checks=['Completed one-time roots never restart; completion records never regress.',
                          'Unfinished locations have priority and resume at the exact saved unresolved node.',
                          'A root starts at most once per visit; familiar roots exclude the prior persisted recent window.',
                          'All published scene choices have legal flags/tokens and actually resolve; returned state matches durable progress, nodes, flags and recent history.',
                          'Exhausted visits expose no eligible remaining root, shorten the denominator to earned steps and publish the explicit familiar-territory return page.'],
                  results=[], errors=[])
    context = {}
    try:
        async with aiosqlite.connect(':memory:') as db:
            await db.executescript(SCHEMA)
            for index, biome in enumerate(BIOMES):
                for policy_index, policy in enumerate(report['policies']):
                    row = await replay(db, biome, policy, visits, 100 + index * 3 + policy_index, context)
                    report['results'].append(row)
                    print(json.dumps({k:row[k] for k in ('biome','policy','visits','steps','unique_completed_one_time_roots','continuation_resumptions')}), flush=True)
        assert all(digest(ROOT / key) == value for key, value in hashes.items()), 'Audited sources changed during replay.'
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['errors'].append(dict(context=context.copy(), error=type(error).__name__, message=str(error)))
    report['totals'] = {key: sum(r[key] for r in report['results']) for key in METRICS}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], totals=report['totals'], errors=report['errors'])))
    if report['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'adventure-memory-audit.json')
    parser.add_argument('--visits', type=int, default=100)
    args = parser.parse_args()
    asyncio.run(audit(args.output, args.visits))
