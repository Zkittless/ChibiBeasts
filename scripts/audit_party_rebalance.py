"""Paired before/after PvE replays with stated policies, not a live win-rate claim."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiosqlite
from scripts import audit_combat_adventures as adventures
from utils import combat, trial_combat, trial_rules, trials
from utils.pve_tactics import choose, damage_estimate
from utils.team_combat import assist_effect


def attack_only(payload):
    actions = [a for a in trial_combat.legal_actions(payload) if a['enabled']]
    return next((a for a in actions if a['kind'] == 'ultimate'), actions[0])


def tactical(payload):
    """Visible-state heuristic: type-aware swaps, heal assists and burst defense.

    No saved RNG, future foes, trial resets or knowledge of the enemy's choice.
    This is a reproducible player policy, not an optimal-play claim.
    """
    actions = [a for a in trial_combat.legal_actions(payload) if a['enabled']]
    active, enemy = trial_combat.active(payload), payload['enemy']
    selected = list(choose(active, enemy))
    move = next((a for a in actions if a['choice'] == selected), actions[0])
    if (damage_estimate(active, enemy, *move['choice']) * .85 >= enemy['hp']
            and combat.effective_speed(active) >= combat.effective_speed(enemy)):
        return move
    threat_move = (enemy['ultimate'], True) if combat.can_use(enemy, enemy['ultimate'], True) else (enemy['moves'][0], False)
    def matchup(member):
        outgoing = damage_estimate(member, enemy, member['moves'][0])
        incoming = max(1, damage_estimate(enemy, member, *threat_move))
        return outgoing / incoming, incoming
    ratio, danger = matchup(active)
    swaps = []
    for action in actions:
        if action['kind'] != 'swap':
            continue
        member = next(s for s in payload['party'] if s['id'] == action['choice'][1])
        value, incoming = matchup(member)
        if (member['hp'] > incoming * 1.8 and member['hp'] > member['max_hp'] * .3
                and ((active['hp'] < danger * 1.25 and member['hp'] / incoming > active['hp'] / max(1, danger) + 1)
                     or value > ratio * 1.8 and member['hp'] > member['max_hp'] * .6)):
            swaps.append((value * min(3, member['hp'] / incoming), action))
    if swaps:
        return max(swaps, key=lambda entry: entry[0])[1]
    if active['hp'] < active['max_hp'] * .5 and danger < active['max_hp'] * .10:
        helper = next((a for a in actions if a['kind'] == 'assist'
                       and assist_effect(next(s for s in payload['party'] if s['id'] == a['choice'][1])) == 'heal'), None)
        if helper:
            return helper
    return move


def summarize(runs):
    count = len(runs)
    return dict(runs=count, wins=sum(r['complete'] for r in runs),
                clean_wins=sum(r['complete'] and not r['knocked_out'] for r in runs),
                mean_hp=round(sum(r['remaining_hp_fraction'] for r in runs) / count, 3),
                mean_kos=round(sum(r['knocked_out'] for r in runs) / count, 3),
                mean_actions=round(sum(r['actions'] for r in runs) / count, 2),
                mean_members=round(sum(r['members_used'] for r in runs) / count, 2),
                swaps=sum(r.get('action_swap', 0) for r in runs),
                assists=sum(r.get('action_assist', 0) for r in runs),
                defenses=sum(r.get('defensive_actions', 0) for r in runs),
                losses=dict(Counter(r['lost_at']['reason'] for r in runs if r['lost_at'])))


def replay_trial(fixture, seed, version, policy):
    party, _, _ = adventures.make_party(fixture)
    p = trial_combat.new_run(party, trial_rules.generate_plan(fixture['biome'], seed, balance_version=version))
    p.update(items_used=0, camp_used=False)
    totals = Counter()
    for _ in range(600):
        if p['status'] == 'between':
            trials._camp(p)
            if fixture['prepared'] and p['items_used'] < 3:
                injured = [s for s in p['party'] if 0 < s['hp'] < s['max_hp'] * .75]
                if injured:
                    member = min(injured, key=lambda s: s['hp'] / s['max_hp'])
                    combat.heal(member, int(member['max_hp'] * .20))
                    p['items_used'] += 1
            trial_combat.start_wave(p)
        elif p['status'] == 'fighting':
            action = policy(p)
            totals['actions'] += 1
            totals['action_' + action['kind']] += 1
            if action['kind'] in {'move', 'ultimate'}:
                spec = combat.move_spec(trial_combat.active(p), *action['choice'])
                totals['defensive_actions'] += bool(spec.get('guard_percent') or spec.get('heal_percent'))
            trial_combat.act(p, action['choice'])
        else:
            break
        p = json.loads(json.dumps(p))
    else:
        raise AssertionError('Tower did not terminate in 600 decisions')
    members = p['party']
    return dict(complete=p['status'] == 'won', **totals,
                remaining_hp_fraction=sum(s['hp'] for s in members) / sum(s['max_hp'] for s in members),
                members_used=len(p['participated']), knocked_out=sum(s['hp'] <= 0 for s in members),
                lost_at=dict(reason=p['reason']) if p['status'] != 'won' else None)


async def audit(args):
    report = dict(scope=__doc__, seeds=args.seeds, rows=[],
        policies=dict(attack_only='Basic attack and affordable ultimate; no optional swaps, skills, healing or defense. Forced replacement still works.',
                      tactical=tactical.__doc__),
        assumptions=[adventures.__doc__,
            'Each row is a separate fixed build, not simulated acquisition. Entry/upper levels are biome bounds; prepared gear is trainer-accessible, bond/happiness100, four HP and four attack training gains using gameplay rarity. Bare builds have no gear/training/bond and happiness70.',
            'Full saved HP/MP on departure matches the production adventure and trial services. No refill per foe. Adventure interludes use actual content and recovery/boon caps. Trials use the actual25% midpoint camp and prepared teams may spend up to three20%HP potions between waves.',
            'Both versions share route seeds, fixed player builds and policies. Version2 intentionally changes hostile species selection. Both policies select the same safe story choices; neither reads future enemies or the combat RNG.',
            'Only combat completion, attrition and decisions are measured. No live Discord latency, capture success, player acquisition timeline, or reward settlement is simulated. Passed means replay invariants held, not that every build won.'],
        sources={str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in [Path(__file__), ROOT / 'scripts/audit_combat_adventures.py',
                           ROOT / 'scripts/audit_progression.py',
                           *[ROOT / ('utils/' + n + '.py') for n in ('journey_combat', 'journey_service', 'expeditions', 'pve_tactics', 'trial_combat', 'combat', 'journeys', 'trial_rules', 'trials', 'team_combat', 'habitats', 'progression', 'type_chart')],
                           *[ROOT / ('data/' + n + '.json') for n in ('beasts', 'equipment', 'items')],
                           *[ROOT / ('data/adventures/' + n + '.json') for n in adventures.BIOMES]]})
    uid = 0
    async with aiosqlite.connect(':memory:') as db:
        await db.executescript(adventures.SCHEMA)
        fixtures = list(adventures.fixtures(args.seeds, args.seeds))
        fixtures += [{**f, 'mode': mode, 'team': f['team'][:size]}
                     for f in list(fixtures) if f['mode'] == 'entry_prepared'
                     for mode, size in [('entry_solo_prepared', 1), ('entry_duo_prepared', 2)]]
        fixtures += [dict(biome=biome, mode=mode, team=team,
                          level=adventures.BIOMES[biome]['level_range'][0],
                          trainer_level=adventures.BIOMES[biome]['min_level'], prepared=True, happiness=100)
                     for biome in ('loom', 'loom_deep')
                     for mode, team in [('entry_legendary', ['dragon', 'hydra', 'fenrir']),
                                        ('entry_hexpurr', ['hexpurr', 'hydra', 'dragon']),
                                        ('entry_raikyr_neutral', ['raikyr', 'hydra', 'dragon']),
                                        ('entry_raikyr', ['raikyr', 'fenrir', 'hydra'])]]
        for fixture in fixtures:
            if args.modes and fixture['mode'] not in args.modes:
                continue
            for version in args.versions:
                for label in args.policies:
                    policy = {'tactical': tactical, 'attack_only': attack_only}[label]
                    for activity in args.activities:
                        if activity == 'trial' and len(fixture['team']) != 3:
                            continue
                        runs = []
                        for seed in range(args.seeds):
                            uid += 1
                            if activity == 'trial':
                                runs.append(replay_trial(fixture, seed, version, policy))
                            else:
                                runs.append(await adventures.replay(db, fixture, seed, uid,
                                                                    balance_version=version, policy=policy))
                        row = dict(activity=activity, biome=fixture['biome'], mode=fixture['mode'], team=fixture['team'],
                                   level=fixture['level'], prepared=fixture['prepared'], version=version,
                                   policy=label, **summarize(runs))
                        report['rows'].append(row)
                        print(json.dumps(row), flush=True)
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest
               for p, digest in report['sources'].items()), 'Audited sources changed during replay.'
    report['status'] = 'passed'
    report['replays'] = sum(row['runs'] for row in report['rows'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=20)
    parser.add_argument('--versions', nargs='+', type=int, default=[1, 2])
    parser.add_argument('--policies', nargs='+', choices=['attack_only', 'tactical'], default=['attack_only', 'tactical'])
    parser.add_argument('--modes', nargs='+')
    parser.add_argument('--activities', nargs='+', choices=['adventure', 'trial'], default=['adventure', 'trial'])
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/party-rebalance.json')
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error('seeds must be positive')
    asyncio.run(audit(args))
