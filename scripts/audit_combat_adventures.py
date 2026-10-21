"""Replay combat-first adventures with production scheduling, combat and real interludes.

This is a reproducible fixed-build balance sample, not a lifetime progression,
reward-settlement, capture-roll or live Discord timing simulation.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiosqlite
from cogs.economy import TRAIN_GAIN
from scripts.audit_progression import companion_state, build_reachability
from utils import adventure_content, combat, journey_combat, journeys, trial_combat
from utils.catalog import load_catalog
from utils.duels import ai_move
from utils.economy_balance import TRAIN_HP_MULTIPLIER
from utils.habitats import BIOMES, eligible, encounter_level, encounter_pool
from utils.team_combat import assist_effect

BEASTS = load_catalog('beasts.json')['beasts']
SCHEMA = '''
CREATE TABLE adventure_progress(user_id,encounter_id,node_id,completed,snapshot,updated_at,
    PRIMARY KEY(user_id,encounter_id));
CREATE TABLE adventure_nodes(user_id,node_id,resolved,PRIMARY KEY(user_id,node_id));
CREATE TABLE adventure_flags(user_id,flag,PRIMARY KEY(user_id,flag));
CREATE TABLE adventure_recent(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id,biome,encounter_id);
'''
BAND_TEAMS = [('twine', 'fairy', 'goblin')] * 3 + [
    ('timestitch', 'unicorn', 'wyvern'), ('timestitch', 'unicorn', 'griffin'),
    ('timestitch', 'yeti', 'thunderbird'), ('timestitch', 'hydra', 'abyssal_eel'),
    ('timestitch', 'hydra', 'dragon'), ('chronos', 'sphinx', 'kitsune_9'),
    ('chronos', 'zodiac', 'sphinx')]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gear_for(trainer):
    if trainer >= 30:
        return ['dragon_lord_aegis', 'core_of_the_phoenix']
    if trainer >= 20:
        return ['unicorn_healer_vestments', 'thunderbird_feathers']
    if trainer >= 12:
        return ['unicorn_healer_vestments', 'iron_dust_chip']
    return ['emberstone_vest' if trainer >= 6 else 'mossbark_wrapping', 'iron_dust_chip']


def fixtures(trials, starter_trials):
    for species in ('prismite', 'twine', 'gloop', 'barkley'):
        yield dict(biome='woods', mode='fresh_solo_l1', team=[species], level=1,
                   trainer_level=1, prepared=False, happiness=100, trials=starter_trials)
    for (biome, region), team in zip(BIOMES.items(), BAND_TEAMS):
        low, high = region['level_range']
        for mode, level, prepared in [('entry_bare', low, False), ('entry_prepared', low, True),
                                      ('upper_prepared', high, True)]:
            yield dict(biome=biome, mode=mode, team=team, level=level,
                       trainer_level=region['min_level'], prepared=prepared,
                       happiness=100 if prepared else 70, trials=trials)
        yield dict(biome=biome, mode='entry_duo_bare', team=team[:2], level=low,
                   trainer_level=region['min_level'], prepared=False, happiness=70, trials=trials)
        if biome in ('loom', 'loom_deep'):
            yield dict(biome=biome, mode='entry_prepared_no_divine',
                       team=['timestitch', 'sphinx', 'kitsune_9'], level=low,
                       trainer_level=region['min_level'], prepared=True, happiness=100, trials=trials)


def make_party(fixture):
    gear = gear_for(fixture['trainer_level']) if fixture['prepared'] else []
    reachability = build_reachability(gear, fixture['trainer_level'])
    assert reachability['valid'], reachability
    result = []
    for index, species in enumerate(fixture['team'], 1):
        gain = TRAIN_GAIN[BEASTS[species]['rarity']]
        extra = dict(attack=4 * gain, hp=4 * gain * TRAIN_HP_MULTIPLIER) if fixture['prepared'] else {}
        state = companion_state(species, fixture['level'], bond=100 if fixture['prepared'] else 0,
                                happiness=fixture['happiness'], charges=0, gear=gear, extras=extra)
        state.update(id=index, companion_id=index)
        # journey_service.begin maps full saved collection mana to full battle
        # mana after prepare_state; the generic build_state 40% duel cap is not
        # the actual adventure entry state. There are no later free refills.
        state['hp'], state['mana'] = state['max_hp'], state['max_mana']
        result.append(state)
    return result, gear, reachability


def player_action(state):
    actions = [a for a in trial_combat.legal_actions(state) if a['enabled']]
    assert actions, 'Production combat has no legal response.'
    actor = trial_combat.active(state)
    if actor['hp'] < actor['max_hp'] * .25:
        swaps = [a for a in actions if a['kind'] == 'swap'
                 and next(s for s in state['party'] if s['id'] == a['choice'][1])['hp'] > actor['hp'] * 2]
        if swaps:
            return max(swaps, key=lambda a: next(s for s in state['party'] if s['id'] == a['choice'][1])['hp'])
    if actor['hp'] < actor['max_hp'] * .55:
        assist = next((a for a in actions if a['kind'] == 'assist'
                       and assist_effect(next(s for s in state['party'] if s['id'] == a['choice'][1])) == 'heal'), None)
        if assist:
            return assist
    preferred = list(ai_move(actor, state['enemy'], 'optimal'))
    return next((a for a in actions if a['choice'] == preferred), actions[0])


def interlude_choice(payload):
    options = [c for c in payload['page']['choices']
               if not c['effects'].get('battle') and not c['effects'].get('hp_cost')]
    assert options, 'No safe noncombat interlude choice exists.'
    members = [s for s in journey_combat.party(payload) if s['hp'] > 0]
    injured = sum(s['hp'] for s in members) < .7 * sum(s['max_hp'] for s in members)
    def score(choice):
        effect = choice['effects']
        return (effect.get('heal', 0) * (3 if injured else 0)
                + effect.get('attack_boon', 0) * 2 + effect.get('defense_boon', 0))
    return max(options, key=score)


def weighted_ticket(realm, seed):
    region, rng = BIOMES[realm], random.Random(f'{seed}:base-habitat-ticket')
    tiers = [r for r, weight in region['weights'].items() if weight > 0 and encounter_pool(r, realm)]
    rarity = rng.choices(tiers, weights=[region['weights'][r] for r in tiers])[0]
    return rng.choice(encounter_pool(rarity, realm))['id']


async def replay(db, fixture, seed, uid):
    realm, totals = fixture['biome'], Counter()
    party, _, _ = make_party(fixture)
    p = dict(prepared_party=party, offered=[], encounter='', caught=False)
    route_seed = f'combat-adventure:{realm}:{seed}'
    saved = await journeys.world(db, uid, realm)
    journeys.initialize(p, realm, route_seed, party, saved, combat_route=True)
    await journeys.persist(db, uid, p)
    target = 2 if list(BIOMES).index(realm) < 5 else 3
    assert p['battle_target'] == target and p['total'] == target * 2 - 1
    assert p['page']['kind'] == 'battle' and not any(p['boons'].values()) and not p['recovery_used']
    assert p['battle_schedule'][0] == 'single'
    ticket, ticket_beats, interludes, opponents, beat_order = weighted_ticket(realm, route_seed), 0, [], [], []
    trip = dict(user_id=uid, status='active', payload=p)
    lost_at = None
    for _ in range(target * 2):
        p = trip['payload']
        if p['journey_status'] == 'homeward':
            break
        if p['page']['kind'] != 'battle':
            choice = interlude_choice(p)
            entry = dict(root=p['current_root'], node=p['current_node'], choice=choice['id'])
            effect = await journeys.resolve(db, trip, choice['value'])
            assert not effect.get('battle')
            journey_combat.effects(p, effect)
            entry['effects'] = effect
            interludes.append(entry)
            beat_order.append('event')
        else:
            index = p['battles_completed']
            effect = await journeys.resolve(db, trip, p['page']['choices'][0]['value'])
            kind = effect['battle_kind']
            assert kind == p['battle_schedule'][index]
            recruitable = index == p['recruitable_beat']
            fight_seed = f"{p['seed']}:{p.get('fights_started', 0)}"
            if recruitable:
                ticket_beats += 1
                p['encounter'] = ticket
                rng = random.Random(f"{p['seed']}:capture")
                rng.random()  # Production consumes the friendly-affinity roll before the level roll.
                level = encounter_level(realm, fixture['level'], rng)
                p['encounter_level'] = level
            else:
                level = encounter_level(realm, fixture['level'], random.Random(fight_seed + ':level'))
            departure_size = len(p['party_ids'])
            lineup = journey_combat.plan(realm, fight_seed, kind, level, ticket=ticket if recruitable else None,
                                        party_size=departure_size)
            assert lineup == journey_combat.plan(realm, fight_seed, kind, level, ticket=ticket if recruitable else None,
                                                  party_size=departure_size)
            for wave in lineup['waves']:
                assert wave['departure_party_size'] == departure_size
                species = BEASTS[wave['species']]
                assert eligible(species) and species['type'] in BIOMES[realm]['types']
                assert BIOMES[realm]['weights'].get(species['rarity'], 0) > 0
            if recruitable:
                assert lineup['waves'][0]['species'] == ticket
            opponents.append(dict(beat=index, kind=kind, level=level, recruitable=recruitable,
                                  species=[w['species'] for w in lineup['waves']]))
            journey_combat.start(p, lineup)
            for _ in range(300):
                state = p['combat']
                if state['status'] == 'between':
                    journey_combat.continue_pack(p)
                elif state['status'] == 'fighting':
                    action = player_action(state)
                    totals['actions'] += 1
                    totals['action_' + action['kind']] += 1
                    journey_combat.action(p, action['choice'])
                else:
                    break
                p['combat'] = json.loads(json.dumps(p['combat']))
            else:
                raise AssertionError('Production fight did not terminate within300 decisions.')
            won = p['combat']['status'] == 'won'
            totals['foes_defeated'] += p['combat']['cleared']
            if won:
                totals['fights_won'] += 1
                totals['recruitment_encounters_won' if recruitable else 'hostile_fights_won'] += 1
                p['fights_won'] = totals['fights_won']
            effect = await journeys.finish_battle(db, trip, won)
            journey_combat.effects(p, effect)
            beat_order.append('battle')
            if not won:
                lost_at = dict(beat=index, kind=kind, recruitable=recruitable,
                               reason=p['combat'].get('reason', ''), species=p['combat']['enemy']['beast_id'])
                break
        assert p['recovery_used'] <= 20 and max(p['boons'].values()) <= 20
        await db.commit()
        trip = json.loads(json.dumps(trip))
    p = trip['payload']
    complete = lost_at is None and p['journey_status'] == 'homeward'
    if complete:
        assert p['battles_completed'] == target and p['completed'] == p['total']
        assert beat_order == ['battle', 'event'] * (target - 1) + ['battle']
        assert ticket_beats == 1 and len(interludes) == target - 1
    assert ticket_beats <= 1
    assert totals['recruitment_encounters_won'] <= 1
    members = journey_combat.party(p)
    return dict(complete=complete, opening_won=totals['fights_won'] > 0, **totals,
                ticket=ticket, ticket_rarity=BEASTS[ticket]['rarity'], recruitable_beat=p['recruitable_beat'],
                schedule=p['battle_schedule'], opponents=opponents, interludes=interludes, lost_at=lost_at,
                remaining_hp_fraction=sum(s['hp'] for s in members) / sum(s['max_hp'] for s in members),
                knocked_out=sum(s['hp'] <= 0 for s in members))


def summary(fixture, runs):
    party, gear, reachability = make_party(fixture)
    count = len(runs)
    failures = Counter((r['lost_at']['species'], r['lost_at']['reason']) for r in runs if r['lost_at'])
    actions = sorted(r.get('actions', 0) for r in runs)
    return dict(**fixture, gear=gear, gear_reachability=reachability,
                opening_resources=[dict(species=s['beast_id'], hp=s['hp'], mana=s['mana'],
                                        max_hp=s['max_hp'], max_mana=s['max_mana']) for s in party],
                complete=sum(r['complete'] for r in runs), opening_wins=sum(r['opening_won'] for r in runs),
                mean_fights_won=round(sum(r.get('fights_won', 0) for r in runs) / count, 3),
                mean_actions=round(sum(actions) / count, 3), p90_actions=actions[math.ceil(count * .9) - 1],
                mean_remaining_hp_fraction=round(sum(r['remaining_hp_fraction'] for r in runs) / count, 3),
                mean_knocked_out=round(sum(r['knocked_out'] for r in runs) / count, 3),
                planned_ticket_tiers=dict(Counter(r['ticket_rarity'] for r in runs)),
                encountered_ticket_tiers=dict(Counter(r['ticket_rarity'] for r in runs
                                                     if any(fight['recruitable'] for fight in r['opponents']))),
                recruitment_encounters_won=sum(r.get('recruitment_encounters_won', 0) for r in runs),
                recruitable_beat_counts=dict(Counter(r['recruitable_beat'] for r in runs)),
                failure_beats=dict(Counter(r['lost_at']['beat'] for r in runs if r['lost_at'])),
                failure_opponents=[dict(species=s, reason=why, count=n) for (s, why), n in failures.most_common()],
                interlude_effects=dict(Counter(key for r in runs for event in r['interludes']
                                              for key in event['effects'] if key in ('heal', 'attack_boon', 'defense_boon'))),
                examples=[dict(seed=i, **run) for i, run in enumerate(runs) if run['lost_at']][:3])


async def audit(output, trials, starter_trials):
    assert trials > 0 and starter_trials > 0
    sources = [Path(__file__), ROOT / 'scripts' / 'audit_progression.py']
    sources += [ROOT / name for name in ('utils/journeys.py', 'utils/journey_service.py', 'utils/journey_combat.py',
        'utils/trial_combat.py', 'utils/combat.py', 'utils/team_combat.py', 'utils/duels.py', 'utils/habitats.py',
        'utils/economy_balance.py', 'utils/progression.py', 'utils/adventure_content.py',
        'utils/type_chart.py', 'utils/expeditions.py',
        'cogs/battle.py', 'cogs/economy.py', 'data/beasts.json', 'data/equipment.json')]
    sources += [ROOT / 'data' / 'adventures' / f'{biome}.json' for biome in BIOMES]
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in sources}
    report = dict(status='running', kind=__doc__, source_sha256=hashes, fixtures=[],
        departure_party_profiles=journey_combat.PARTY_PROFILES,
        strategy='Production optimal NPC move heuristic, intersected with enabled actions. Emergency swap below25%HP to the largest available reserve with over twice current HP; otherwise healing assist below55%; otherwise NPC choice, with enabled-basic fallback. No future enemy lookup.',
        interlude_policy='Exactly one actual noncombat, no-HP-cost authored choice at each event beat; prefer surviving-team healing below70% aggregate HP, otherwise attack boon, then defence. No fabricated healing or opening boon.',
        assumptions=['Mandatory production schedule is B,E,B for first five biomes and B,E,B,E,B for last five; pack kind and recruitable beat are seeded by journeys.configure_combat_route.',
            'Four fresh solo starters use actual level1 base/growth arithmetic, happiness100, bond0, no gear/training/perks/preparations/ultimate charges. Starters are disposition-exempt.',
            'All fixtures start with full saved collection HP/MP, matching journey_service.begin resource mapping. HP/MP, conditions, charges, party participation and assist meter then carry across every foe and encounter without refill.',
            'Enemy HP/attack use the saved departure party size for the entire route, including after a KO. The same seeded species/levels are retained; three-member and legacy profiles stay unchanged. Two-member entry-bare rows use the first two members of the inherited band team.',
            'Prepared checkpoint means four attack and four HP training gains per member, bond100 and happiness100 plus trainer-accessible gear. Training requires at least two daily budgets and grants XP; acquisition, timeline and arrival at this exact level are not simulated.',
            'Nonstarter dispositions are neutral synthetic controls; real nonstarter grants roll a disposition. Gear reachability checks gate and ingredient access, not acquisition time or three copies.',
            'One recruitment ticket is sampled from unboosted habitat rarity/species pools and used only for wave zero of the designated encounter. The locally seeded marginal sample matches production rates/uniform species pools; it does not reproduce the service global-RNG species stream. Other opponents are hostile, even if species IDs happen to match the ticket. Incense/sanctuary/perks/quest overrides are absent.',
            'One run reward/material-or-egg/catch budget is unchanged. This script never invokes reward settlement, grants items, or counts each foe as a reward. It measures combat completion rather than capture probabilities or animation timing.',
            'An in-memory four-table story database exercises actual initialize/persist/resolve/finish_battle. Every fixture seed starts with an unvisited story world; each event is a real saved catalog node, not a guaranteed boon.',
            'Combat state JSON-roundtrips after every decision; complete journey payloads roundtrip between route beats. Results are finite samples under one transparent policy, not universal win rates.'])
    context = {}
    try:
        async with aiosqlite.connect(':memory:') as db:
            await db.executescript(SCHEMA)
            uid = 0
            for fixture in fixtures(trials, starter_trials):
                runs = []
                for seed in range(fixture['trials']):
                    uid += 1
                    context = dict(biome=fixture['biome'], mode=fixture['mode'], team=fixture['team'], seed=seed)
                    runs.append(await replay(db, fixture, seed, uid))
                row = summary(fixture, runs)
                report['fixtures'].append(row)
                print(json.dumps({key:row[key] for key in ('biome','mode','team','trials','complete','opening_wins')}), flush=True)
        assert all(digest(ROOT / p) == expected for p, expected in hashes.items()), 'Audited sources changed during replay.'
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = dict(context=context, type=type(error).__name__, message=str(error))
    report['replays'] = sum(row['trials'] for row in report['fixtures'])
    report['interpretation'] = 'Passed means scheduling/resource/capture-budget assertions held and all fights terminated; completion counts are the measured balance result and can be below100%.'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], replays=report['replays'], error=report.get('error'))))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'combat-adventure-balance.json')
    parser.add_argument('--trials', type=int, default=20)
    parser.add_argument('--starter-trials', type=int, default=100)
    args = parser.parse_args()
    asyncio.run(audit(args.output, args.trials, args.starter_trials))
