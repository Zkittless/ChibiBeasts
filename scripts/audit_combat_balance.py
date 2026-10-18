"""Audit real combat/raid arithmetic with seeded time and no Discord/database I/O."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils import combat, db as storage
from utils.catalog import load_catalog
from utils.duels import ai_move
from utils.raids import RaidSession, RaidCooldownError
from cogs.ancient import ANCIENT_BOSSES
from cogs.guilds import RAID_BOSSES
from utils.type_chart import get_type_multiplier
from utils.raid_balance import BOSS_INTERVAL

BEASTS = storage.load_beasts()
BOSSES = [*RAID_BOSSES['corrupted'], *ANCIENT_BOSSES]
GEAR = load_catalog('equipment.json')
BUILDS = {
    'bare': [],
    'entry': ['gnome_iron_plating', 'iron_dust_chip'],
    'prepared': ['dragon_lord_aegis', 'wyvern_talon'],
    'progression': ['dragon_lord_aegis', 'iron_dust_chip'],
    'midgame': ['unicorn_healer_vestments', 'iron_dust_chip'],
    'advanced': ['thunderbird_storm_mail', 'iron_dust_chip'],
    'sustain': ['unicorn_healer_vestments', 'ouroboros_ring'],
    'burn': ['cinder_guard_vest', 'ember_spark'],
}

# Trainer stages follow destination access and the trainer-15 raid floor.
# In particular, a level-25 party cannot assume trainer-30 Legendary crafts.
PROGRESSION_STAGES = {
    15: {'trainer_level': 15, 'build': 'entry'},
    25: {'trainer_level': 15, 'build': 'midgame'},
    40: {'trainer_level': 25, 'build': 'advanced'},
    50: {'trainer_level': 38, 'build': 'progression'},
}


def state_for(key, level, build='bare', *, charges=0):
    from utils.progression import companion_growth
    data = BEASTS[key]
    growth = companion_growth(key, level, BEASTS)
    stats = {stat: value + growth[stat] for stat, value in data['base_stats'].items()}
    row = {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'], 'level': level,
           'happiness': 70, 'id': key}
    definitions = {**GEAR['equipment'], **GEAR['runes']}
    gear = [definitions[key] for key in BUILDS[build]]
    return combat.build_state(data, row, gear=gear, buffs={'ultimate_charges': charges})


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*fraction))]


def summary(rows):
    wins = [r for r in rows if r['outcome'] == 'win']
    return {'runs': len(rows), 'win_rate': round(len(wins)/len(rows), 4),
            'median_seconds': round(statistics.median(r['seconds'] for r in wins), 1) if wins else None,
            'p10_seconds': round(percentile([r['seconds'] for r in wins], .1), 1) if wins else None,
            'p90_seconds': round(percentile([r['seconds'] for r in wins], .9), 1) if wins else None}


def raid_choice(raid, uid, now, coordinated=True):
    state = raid.state(uid)
    if state['hp'] <= 0:
        return 'swap' if any(s['hp'] > 0 for s in raid.party[uid]) else None
    if coordinated and raid.pending and now - raid.pattern_started >= 2 and uid not in raid.weavers:
        return 'weave'
    skill = combat.move_spec(state, state['moves'][1])
    if state['hp'] < state['max_hp'] * .5 and skill.get('heal_percent') and combat.can_use(state, state['moves'][1]):
        return 'skill'
    if state['hp'] < state['max_hp'] * .7 and state['mana'] >= 15 and now-raid.last.get((uid, 'tend'), 0) >= 8:
        return 'tend'
    if combat.can_use(state, state['ultimate'], True):
        return 'ultimate'
    if skill.get('status') and combat.can_receive_status(raid.enemy, skill['status']) and combat.can_use(state, state['moves'][1]):
        return 'skill'
    return 'attack'


def simulate_raid(species, level, boss, count, cadence, *, build='bare', seed=0, coordinated=True, party_species=None):
    random.seed(f'{species}:{level}:{boss["id"]}:{count}:{cadence}:{build}:{seed}')
    raid = RaidSession(None, boss, set(range(count)), 5 if boss['id'].startswith('corrupted_') else None)
    now = [1000.0]
    raid.clock = lambda: now[0]
    raid.party = {uid: [state_for(key, level, build, charges=0) for key in (party_species or [species]*3)] for uid in raid.users}
    for uid in raid.users:
        raid.totems[uid] = False
    raid.build_enemy()
    for uid in raid.users:
        combat.start_battle(raid.state(uid), raid.enemy, [])
    hp = raid.enemy['max_hp']
    due = {uid: float(cadence) for uid in raid.users}
    next_boss = float(BOSS_INTERVAL)
    actions = skips = ticks = swaps = 0
    result = 'timeout'
    seconds = 600.0
    while min(next_boss, min(due.values())) <= 600:
        next_action = min(due.values())
        seconds = min(next_boss, next_action)
        now[0] = 1000 + seconds
        if next_boss <= next_action:
            skips += int(raid.enemy.get('status') in {'freeze', 'sleep'})
            raid.resolve_boss_turn()
            ticks += 1
            next_boss += BOSS_INTERVAL
        else:
            for uid in raid.users:
                if due[uid] > seconds:
                    continue
                kind = raid_choice(raid, uid, now[0], coordinated)
                if kind:
                    try:
                        raid.resolve_action(uid, kind)
                        actions += 1
                        swaps += kind == 'swap'
                    except RaidCooldownError:
                        pass
                interval = max(cadence, raid.action_cooldown(uid)) if hasattr(raid, 'action_cooldown') else cadence
                due[uid] = seconds + interval
                if raid.enemy['hp'] <= 0:
                    break
        if raid.enemy['hp'] <= 0:
            result = 'win'
            break
        if not any(state['hp'] > 0 for party in raid.party.values() for state in party):
            result = 'party_ko'
            break
    combat.active_states.clear()
    return {'creature': species, 'rarity': BEASTS[species]['rarity'], 'level': level, 'boss': boss['id'],
            'trainers': count, 'cadence': cadence, 'build': build, 'seed': seed,
            'coordinated': coordinated, 'boss_hp': hp, 'boss_attack': raid.enemy['attack'],
            'boss_defense': raid.enemy['defense'], 'seconds': round(min(seconds, 600), 2),
            'outcome': result, 'actions': actions, 'boss_ticks': ticks, 'boss_turns_skipped': skips,
            'swaps': swaps, 'boss_hp_remaining': round(max(0, raid.enemy['hp'])/hp, 4)}


def damage_sheet():
    rows = []
    for key, data in BEASTS.items():
        for level in (1, 10, 15, 30, 50):
            attacker = state_for(key, level)
            target = state_for('griffin', level)
            target['beast_type'] = attacker['beast_type']  # Same type is neutral in this chart.
            values = []
            for sample in range(64):
                random.seed(f'damage:{key}:{level}:{sample}')
                values.append(combat.calc_damage(attacker, target, attacker['moves'][0])[0])
            skill = combat.move_spec(attacker, attacker['moves'][1])
            rows.append({'creature': key, 'name': data['name'], 'rarity': data['rarity'], 'element': data['type'],
                         'level': level, 'hp': attacker['max_hp'], 'attack': attacker['attack'],
                         'defense': attacker['defense'], 'speed': attacker['speed'], 'max_mana': attacker['max_mana'],
                         'start_mana': attacker['mana'], 'neutral_target_defense': target['defense'],
                         'mean_basic_damage': round(statistics.mean(values), 2), 'basic_min': min(values), 'basic_max': max(values),
                         'skill': attacker['moves'][1], 'skill_power': skill.get('power', 0),
                         'skill_mana': skill.get('mana_cost', 0), 'skill_status': skill.get('status', ''),
                         'skill_heal_percent': skill.get('heal_percent', 0),
                         'ultimate_power': combat.passive(attacker).get('ultimate_damage_mod', 2.5),
                         'passive': combat.pid(attacker), 'developer_only': data['rarity']=='dev'})
    return rows


def duel_sweep():
    outcomes = []
    for level in (1, 15, 30, 50):
        for key, data in BEASTS.items():
            if data['rarity'] == 'dev':
                continue
            peers = [other for other, entry in BEASTS.items() if entry['rarity'] == data['rarity']]
            for other in peers:
                for seed in range(2):
                    random.seed(f'duel:{key}:{other}:{level}:{seed}')
                    states = [state_for(key, level, charges=seed*3), state_for(other, level, charges=seed*3)]
                    log = []
                    combat.start_battle(*states, log)
                    for turn in range(1, 21):
                        for index in sorted(range(2), key=lambda i: combat.effective_speed(states[i]), reverse=True):
                            state, enemy = states[index], states[1-index]
                            if min(s['hp'] for s in states) <= 0:
                                break
                            if combat.begin_turn(state, enemy, log):
                                name, ultimate = ai_move(state, enemy, 'optimal')
                                combat.resolve_move(state, enemy, name, ultimate, log)
                                if min(s['hp'] for s in states) > 0 and combat.bonus_action(state):
                                    combat.resolve_move(state, enemy, state['moves'][0], False, log, grant_charge=False)
                            combat.end_turn(state, enemy, log)
                        if min(s['hp'] for s in states) <= 0:
                            break
                    ratios = [s['hp']/s['max_hp'] for s in states]
                    winner = None if abs(ratios[0]-ratios[1]) < 1e-8 else int(ratios[1] > ratios[0])
                    outcomes.append({'creature': key, 'opponent': other, 'rarity': data['rarity'], 'level': level,
                                     'charges': seed*3, 'rounds': turn, 'won': winner == 0,
                                     'judged': min(s['hp'] for s in states) > 0})
    return outcomes


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_roster_summary(output):
    """One comparable row per creature; full matchups stay in the source CSVs."""
    def read(name):
        with (output/name).open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))
    stats = {(row['creature'], row['level']): row for row in read('creature-stats.csv')}
    peers = {(row['creature'], row['level']): row for row in read('peer-summary.csv')}
    raids = read('raids.csv')
    rows = []
    for key, data in BEASTS.items():
        low, high = stats[key, '1'], stats[key, '30']
        peer = peers.get((key, '30'), {})
        trials = [r for r in raids if r['creature']==key and r['level']=='30' and r['trainers']=='3' and r['cadence']=='4']
        won = [float(r['seconds']) for r in trials if r['outcome']=='win']
        role = 'heal/cleanse' if float(high['skill_heal_percent']) else 'condition: '+high['skill_status'] if high['skill_status'] else 'damage'
        rows.append({'creature': key, 'name': data['name'], 'rarity': data['rarity'], 'element': data['type'],
                     'skill_role': role, 'passive': high['passive'], 'developer_only': low['developer_only'],
                     'level_1_hp': low['hp'], 'level_1_max_mana': low['max_mana'],
                     'level_30_hp': high['hp'], 'level_30_attack': high['attack'], 'level_30_defense': high['defense'],
                     'level_30_speed': high['speed'], 'level_30_neutral_basic': high['mean_basic_damage'],
                     'level_30_peer_win_rate': peer.get('win_rate', ''), 'level_30_peer_judge_rate': peer.get('judge_rate', ''),
                     'bare_raid_wins_of_six': len(won) if trials else '',
                     'bare_raid_median_winning_seconds': round(statistics.median(won), 1) if won else ''})
    write_csv(output/'roster-overview.csv', rows)


def calibration_matrix(seeds=(0, 1), builds=('bare', 'entry', 'prepared', 'sustain')):
    from utils.raid_balance import reference_species
    rows = []
    for level in (10, 15, 30, 50):
        for boss in BOSSES:
            element = BEASTS[boss['id']]['type']
            party = sorted(reference_species(level), key=lambda key: BEASTS[key]['base_stats']['attack'] * get_type_multiplier(BEASTS[key]['type'], element), reverse=True)
            for count in (1, 3, 6, 10):
                for build in builds:
                    for seed in seeds:
                        row = simulate_raid(party[0], level, boss, count, 4, build=build, seed=seed, party_species=party)
                        row['party_species'] = ','.join(party)
                        rows.append(row)
    return rows


def progression_matrix(seeds=range(20, 26)):
    """Attainable parties and crafts at each actual progression destination."""
    from utils.raid_balance import reference_species
    rows = []
    for level, stage in PROGRESSION_STAGES.items():
        for boss in BOSSES:
            element = BEASTS[boss['id']]['type']
            party = sorted(reference_species(level), key=lambda key:
                state_for(key, level)['attack'] * get_type_multiplier(BEASTS[key]['type'], element), reverse=True)
            for count in (1, 3, 10):
                for seed in seeds:
                    row = simulate_raid(party[0], level, boss, count, 4, build=stage['build'],
                                        seed=seed, party_species=party)
                    row.update(trainer_level=stage['trainer_level'], party_species=','.join(party),
                               equipment=','.join(BUILDS[stage['build']]))
                    rows.append(row)
    return rows


def holdout(output):
    """Use fresh seeds after tuning, plus slower inputs/missed-pattern trials."""
    output.mkdir(parents=True, exist_ok=True)
    rows = calibration_matrix(seeds=range(20, 26), builds=('prepared',))
    write_csv(output/'holdout-parties.csv', rows)
    behavior = []
    from utils.raid_balance import reference_species
    for boss in BOSSES:
        element = BEASTS[boss['id']]['type']
        party = sorted(reference_species(30), key=lambda key: BEASTS[key]['base_stats']['attack'] * get_type_multiplier(BEASTS[key]['type'], element), reverse=True)
        for cadence in (4, 8):
            for coordinated in (True, False):
                for seed in range(20, 24):
                    behavior.append(simulate_raid(party[0], 30, boss, 3, cadence, build='prepared', seed=seed, coordinated=coordinated, party_species=party))
    write_csv(output/'holdout-behavior.csv', behavior)
    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'prepared_parties': summary(rows),
              'by_level': {str(level): summary([r for r in rows if r['level']==level]) for level in (10, 15, 30, 50)},
              'by_boss': {boss['id']: summary([r for r in rows if r['boss']==boss['id']]) for boss in BOSSES},
              'behavior': {f'cadence_{cadence}_patterns_{coordinated}': summary([r for r in behavior if r['cadence']==cadence and r['coordinated']==coordinated]) for cadence in (4, 8) for coordinated in (True, False)},
              'scope': 'Fresh seeds 20–25, prepared mixed reference parties, same production resolvers. Behavior trials: level 30, three trainers, seeds 20–23. Winning times exclude losses/timeouts. Synthetic model, not human playtesting.'}
    (output/'holdout-summary.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


def main(output, quick):
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    damage = damage_sheet()
    write_csv(output/'creature-stats.csv', damage)
    duels = duel_sweep()
    write_csv(output/'duels.csv', duels)
    print(f'Computed {len(damage)} stat profiles and {len(duels)} peer fights.', flush=True)
    raid_rows = []
    playable = [key for key, data in BEASTS.items() if data['rarity'] != 'dev']
    for i, key in enumerate(playable):
        for level in ((15,) if quick else (10, 30)):
            for count in ((3,) if quick else (1, 3, 10)):
                for cadence in ((4,) if quick else (2, 4, 6)):
                    for boss in BOSSES:
                        raid_rows.append(simulate_raid(key, level, boss, count, cadence))
        if (i+1) % 10 == 0:
            print(f'Raids: {i+1}/{len(playable)} creatures; {len(raid_rows)} scenarios.', flush=True)
    write_csv(output/'raids.csv', raid_rows)
    gear_rows = []
    for key in ('griffin', 'atlas', 'radiant_kitsune', 'corrupted_dragon', 'frost_sprite', 'ancient_genesis', 'fractured_genesis', 'rootwarden'):
        for build in BUILDS:
            for count in (1, 3, 10):
                for boss in BOSSES:
                    gear_rows.append(simulate_raid(key, 30, boss, count, 4, build=build))
    write_csv(output/'build-comparison.csv', gear_rows)
    calibration = calibration_matrix()
    write_csv(output/'reference-parties.csv', calibration)
    by_creature = defaultdict(list)
    for row in duels:
        by_creature[(row['creature'], row['level'])].append(row)
    peer = [{'creature': key, 'level': level, 'win_rate': round(statistics.mean(r['won'] for r in rows), 3),
             'median_rounds': statistics.median(r['rounds'] for r in rows),
             'judge_rate': round(statistics.mean(r['judged'] for r in rows), 3)} for (key, level), rows in by_creature.items()]
    write_csv(output/'peer-summary.csv', peer)
    write_roster_summary(output)
    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'creatures': len(BEASTS),
              'raid_creatures': len(playable), 'developer_only_excluded_from_balance': ['ouroboros'],
              'stat_profiles': len(damage), 'peer_fights': len(duels), 'raid_scenarios': len(raid_rows),
              'build_scenarios': len(gear_rows), 'raid_summary': summary(raid_rows),
              'reference_by_build': {build: summary([r for r in calibration if r['build']==build]) for build in ('bare', 'entry', 'prepared', 'sustain')},
              'raid_by_cadence': {str(c): summary([r for r in raid_rows if r['cadence']==c]) for c in sorted({r['cadence'] for r in raid_rows})},
              'peer_judge_rate': round(statistics.mean(r['judged'] for r in duels), 4),
              'wall_seconds': round(time.perf_counter()-started, 2),
              'scope': 'Production combat/raid resolvers; deterministic synthetic clock. Three companions per trainer; no consumables or training. Each simulated trainer submits one input per cadence, including support. This is a model, not human playtesting.'}
    (output/'summary.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--calibrate-only', action='store_true')
    parser.add_argument('--holdout-only', action='store_true')
    args = parser.parse_args()
    if args.holdout_only:
        holdout(args.output.resolve())
    elif args.calibrate_only:
        args.output.mkdir(parents=True, exist_ok=True)
        rows = calibration_matrix()
        write_csv(args.output/'reference-parties.csv', rows)
        print(json.dumps({build: summary([r for r in rows if r['build']==build]) for build in ('bare', 'entry', 'prepared', 'sustain')}, indent=2))
    else:
        main(args.output.resolve(), args.quick)
