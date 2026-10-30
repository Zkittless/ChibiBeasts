"""Replay seasonal combat and real story interludes with transparent fixed builds."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aiosqlite
from scripts.audit_combat_adventures import SCHEMA, make_party, player_action, interlude_choice, summary
from utils import adventure_content, expeditions, journey_combat, journeys
from utils.habitats import carnival_underlay


def fixtures(trials, starter_trials):
    for species in ('prismite', 'twine', 'gloop', 'barkley'):
        yield dict(biome='hollowlight', mode='fresh_solo_l1', team=[species], level=1,
                   trainer_level=1, prepared=False, happiness=100, trials=starter_trials)
    for team in [('twine', 'gloop'), ('twine', 'gloop', 'barkley')]:
        yield dict(biome='hollowlight', mode='fresh_team_l1', team=team, level=1,
                   trainer_level=1, prepared=False, happiness=100, trials=trials)
    yield dict(biome='hollowlight', mode='mixed_departure_levels', team=['twine', 'hydra'],
               member_levels=[1, 50], level=50, trainer_level=38,
               prepared=False, happiness=100, trials=trials)
    for trainer, level, team in [
        (6, 11, ['twine', 'fairy', 'goblin']),
        (12, 21, ['timestitch', 'unicorn', 'wyvern']),
        (20, 31, ['timestitch', 'hydra', 'abyssal_eel']),
        (30, 41, ['timestitch', 'sphinx', 'kitsune_9']),
        (38, 50, ['chronos', 'zodiac', 'sphinx'])]:
        for prepared in (False, True):
            yield dict(biome='hollowlight', mode='prepared_checkpoint' if prepared else 'bare_checkpoint',
                       team=team, level=level, trainer_level=trainer, prepared=prepared,
                       happiness=100 if prepared else 70, trials=trials)


def party_for(fixture):
    if not fixture.get('member_levels'):
        return make_party(fixture)
    members = []
    for index, (species, level) in enumerate(zip(fixture['team'], fixture['member_levels']), 1):
        party, gear, reachability = make_party({**fixture, 'team': [species], 'level': level})
        party[0].update(id=index, companion_id=index)
        members.extend(party)
    return members, gear, reachability


async def replay(db, fixture, seed, uid):
    party, _, _ = party_for(fixture)
    level = max(member['level'] for member in party)
    realm = carnival_underlay(fixture['trainer_level'], level)
    p = dict(prepared_party=party, offered=[], encounter='', caught=False,
             event_id='hollowlight_2026', event_level=level, event_trainer_level=fixture['trainer_level'])
    route_seed = f'hollowlight:{seed}'
    journeys.initialize(p, 'hollowlight', route_seed, party,
                        await journeys.world(db, uid, 'hollowlight'), combat_route=True)
    await journeys.persist(db, uid, p)
    target, counts = p['battle_target'], Counter()
    rates = {'uncommon': .68, 'rare': .30, 'legendary': .02} if fixture['trainer_level'] >= 30 else {'uncommon': .70, 'rare': .30}
    ticket = expeditions.roll_hollowlight_ticket(rates, fixture['trainer_level'], random.Random(f"{p['seed']}:capture"))
    ticket_id, ticket_beats, opponents, interludes = ticket['id'], 0, [], []
    trip, loss = dict(user_id=uid, status='active', payload=p), None
    for _ in range(target * 2):
        p = trip['payload']
        if p['journey_status'] == 'homeward':
            break
        if p['page']['kind'] != 'battle':
            selected = interlude_choice(p)
            entry = dict(root=p['current_root'], node=p['current_node'], choice=selected['id'])
            effect = await journeys.resolve(db, trip, selected['value'])
            assert not effect.get('battle')
            journey_combat.effects(p, effect)
            entry['effects'] = effect
            interludes.append(entry)
        else:
            index = p['battles_completed']
            guardian = index == target - 1
            recruitable = index == p['recruitable_beat']
            assert not guardian or not recruitable
            effect = await journeys.resolve(db, trip, p['page']['choices'][0]['value'])
            seed_string = f"{p['seed']}:{p.get('fights_started', 0)}"
            lineup = journey_combat.carnival_plan(realm, seed_string, effect['battle_kind'], level,
                party_size=len(p['party_ids']), guardian=guardian, ticket=ticket_id if recruitable else None)
            assert lineup == journey_combat.carnival_plan(realm, seed_string, effect['battle_kind'], level,
                party_size=len(p['party_ids']), guardian=guardian, ticket=ticket_id if recruitable else None)
            assert all(wave['level'] == level for wave in lineup['waves'])
            if guardian:
                assert lineup['total'] == 1 and lineup['waves'][0]['species'] == 'hollow_king'
            if recruitable:
                ticket_beats += 1
                assert lineup['waves'][0]['species'] == ticket_id
            for wave in lineup['waves']:
                if wave['species'] not in journey_combat.SEASONAL_SPECIES:
                    assert wave['enemy_template']['armor_id'] == 'cosmetic:ghost_costume_2026'
            opponents.append(dict(beat=index, guardian=guardian, recruitable=recruitable,
                                  species=[wave['species'] for wave in lineup['waves']]))
            journey_combat.start(p, lineup)
            for _ in range(300):
                state = p['combat']
                if state['status'] == 'between':
                    journey_combat.continue_pack(p)
                elif state['status'] == 'fighting':
                    action = player_action(state)
                    counts['actions'] += 1
                    counts['action_' + action['kind']] += 1
                    journey_combat.action(p, action['choice'])
                else:
                    break
                p['combat'] = json.loads(json.dumps(p['combat']))
            else:
                raise AssertionError('A Carnival fight exceeded300 decisions.')
            won = p['combat']['status'] == 'won'
            counts['foes_defeated'] += p['combat']['cleared']
            if won:
                counts['fights_won'] += 1
                counts['recruitment_encounters_won' if recruitable else 'hostile_fights_won'] += 1
            effect = await journeys.finish_battle(db, trip, won)
            journey_combat.effects(p, effect)
            if not won:
                loss = dict(beat=index, guardian=guardian, recruitable=recruitable,
                            reason=p['combat'].get('reason'), species=p['combat']['enemy']['beast_id'])
                break
        assert p['recovery_used'] <= 20 and max(p['boons'].values()) <= 20
        await db.commit()
        trip = json.loads(json.dumps(trip))
    p, members = trip['payload'], journey_combat.party(trip['payload'])
    complete = loss is None and p['journey_status'] == 'homeward'
    if complete:
        assert p['battles_completed'] == target and ticket_beats == 1
    return dict(complete=complete, opening_won=counts['fights_won'] > 0, **counts,
                ticket=ticket_id, ticket_rarity=ticket['rarity'], recruitable_beat=p['recruitable_beat'],
                schedule=p['battle_schedule'], opponents=opponents, interludes=interludes, lost_at=loss,
                remaining_hp_fraction=sum(s['hp'] for s in members)/sum(s['max_hp'] for s in members),
                knocked_out=sum(s['hp'] <= 0 for s in members))


async def audit(output, trials, starter_trials):
    paths = [Path(__file__), ROOT/'scripts/audit_combat_adventures.py', ROOT/'scripts/audit_progression.py',
             ROOT/'utils/journeys.py', ROOT/'utils/journey_combat.py', ROOT/'utils/journey_service.py',
             ROOT/'utils/expeditions.py', ROOT/'utils/habitats.py', ROOT/'utils/combat.py',
             ROOT/'utils/trial_combat.py', ROOT/'utils/team_combat.py', ROOT/'utils/duels.py',
             ROOT/'utils/adventure_content.py', ROOT/'utils/type_chart.py', ROOT/'data/beasts.json',
             ROOT/'data/equipment.json', ROOT/'data/adventures/hollowlight.json']
    hashes = {str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = dict(status='running', source_sha256=hashes,
        fingerprint_method='SHA256 of raw checkout bytes; LF/CRLF differences change these hashes.',
        departure_party_profiles=journey_combat.PARTY_PROFILES, seasonal_enemy_profiles=journey_combat.CARNIVAL_PROFILES,
        novice_wickwing_attack_scale=journey_combat.CARNIVAL_NOVICE_BURST_ATTACK,
        fixtures=[], assumptions=[
            'Production Carnival schedule and enemy planner; two fights below strongest departing level26, three from26 onward, with a hostile final guardian. Low-team opening encounters vary by saved departure seed between a single and two opponents sharing one encounter budget.',
            'Actual production combat AI and legal-action policy reused from audit_combat_adventures; surviving resources, conditions, assist meter and entry state persist.',
            'Fresh solo starters have full saved collection HP/MP, level1, happiness100 and no gear, perks, training or preparations. Prepared checkpoint builds are synthetic, with stated accessible gear and bounded training.',
            'The mixed-departure fixture uses actual level1 Twine plus level50 Hydra: every opponent stays level50 and all three required fights retain the departing two-member profile after a knockout.',
            'One real safe authored interlude is chosen between required encounters. No opening boon or fabricated recovery is added.',
            'One unboosted seasonal capture ticket uses the production picker; Veilmoth only enters the pool at trainer30. The final guardian never recruits.',
            'Every combat decision and route beat JSON-roundtrips. The story database has four in-memory production progress tables.',
            'This measures fixed-build combat completion, not live Discord timing, account acquisition timelines or currency settlement. Passing means invariants and termination held, not that every route was won.'])
    async with aiosqlite.connect(':memory:') as db:
        await db.executescript(SCHEMA)
        uid = 0
        for fixture in fixtures(trials, starter_trials):
            runs = []
            for seed in range(fixture['trials']):
                uid += 1
                runs.append(await replay(db, fixture, seed, uid))
            row = summary(fixture, runs)
            if fixture.get('member_levels'):
                row['opening_resources'] = [{key: member[key] for key in ('level', 'hp', 'mana', 'max_hp', 'max_mana')}
                                            | {'species': member['beast_id']} for member in party_for(fixture)[0]]
            report['fixtures'].append(row)
            print(json.dumps({key:row[key] for key in ('mode','team','level','trials','complete','opening_wins')}), flush=True)
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==expected for path,expected in hashes.items()), 'Audited sources changed during replay.'
    report.update(status='passed', replays=sum(row['trials'] for row in report['fixtures']))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], replays=report['replays'], output=str(output))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/hollowlight-combat-balance.json')
    parser.add_argument('--trials', type=int, default=20)
    parser.add_argument('--starter-trials', type=int, default=100)
    args = parser.parse_args()
    asyncio.run(audit(args.output, args.trials, args.starter_trials))
