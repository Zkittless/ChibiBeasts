"""Reproducible production-rule trials of adventure teams and reward ceilings.

Synthetic, ungeared teams with a simple policy. This is a regression and economy
sanity check, not human win-rate telemetry. No database or Discord connection.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import adventure_party, combat, db as storage
from utils.duels import ai_move
from utils.habitats import BIOMES, pick_encounter
from utils.progression import encounter_beast_xp
from utils.team_combat import PartyBattle, assist_effect


def fight(party, enemy, seed):
    random.seed(seed)
    battle = PartyBattle(deepcopy(party), deepcopy(enemy), -12345)
    combat.start_battle(battle.active, battle.enemy, battle.log)
    counts = Counter()
    try:
        for turn in range(1, 21):
            order = sorted(range(2), key=lambda i: combat.effective_speed(battle.states[i]), reverse=True)
            for index in order:
                if not battle.alive or battle.enemy['hp'] <= 0:
                    break
                actor, target = (battle.active, battle.enemy) if index == 0 else (battle.enemy, battle.active)
                if combat.begin_turn(actor, target, battle.log):
                    if index == 0:
                        choice = ai_move(actor, target, 'optimal')
                        healthy = [row for row in battle.bench() if row['hp'] / row['max_hp'] > .65]
                        healers = [row for row in battle.bench() if battle.can_assist(row) and assist_effect(row) == 'heal']
                        strikers = [row for row in battle.bench() if battle.can_assist(row) and assist_effect(row) == 'attack']
                        if healers and actor['hp'] / actor['max_hp'] < .55:
                            choice = ('assist', healers[0]['id'])
                        elif healthy and actor['hp'] / actor['max_hp'] < .30:
                            choice = ('swap', healthy[0]['id'])
                        elif strikers and battle.enemy['hp'] / battle.enemy['max_hp'] < .25:
                            choice = ('assist', strikers[0]['id'])
                        action, _ = battle.resolve(choice)
                        counts[action.kind] += 1
                    else:
                        name, ultimate = ai_move(actor, target, 'aggressive')
                        combat.resolve_move(actor, target, name, ultimate, battle.log)
                        if actor['hp'] > 0 and target['hp'] > 0 and combat.bonus_action(actor):
                            combat.resolve_move(actor, target, actor['moves'][0], False, battle.log, grant_charge=False)
                combat.end_turn(actor, target, battle.log)
                if battle.active['hp'] <= 0 and battle.alive:
                    battle.replace_knockout()
                    counts['forced_swap'] += 1
            if not battle.alive or battle.enemy['hp'] <= 0:
                break
        assert all(0 <= row['hp'] <= row['max_hp'] and 0 <= row['mana'] <= row['max_mana'] for row in battle.party)
        assert 0 <= battle.meter <= 3
        won = battle.enemy['hp'] <= 0 and battle.alive
        return dict(won=won, rounds=turn, actions=dict(counts), standing=sum(row['hp'] > 0 for row in battle.party))
    finally:
        combat.active_states.pop(-12345, None)
        combat.active_parties.pop(-12345, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=24)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'adventure-balance.json')
    args = parser.parse_args()
    catalogue = storage.load_beasts()
    templates = {}
    def state(species, level):
        key = species, level
        if key not in templates:
            templates[key] = build_pve_beast_state(catalogue[species], level)
        return deepcopy(templates[key])
    reports, actions = [], Counter()
    for region, biome in BIOMES.items():
        low, high = biome['level_range']
        level = (low + high) // 2
        outcomes = {size: [] for size in (1, 2, 3)}
        for seed in range(args.seeds):
            random.seed(91000 + seed + low * 100)
            enemy = state(pick_encounter(biome['weights'], region)['id'], random.randint(low, high))
            party = [state(species, level) for species in ('twine', 'fairy', 'imp')]
            for index, member in enumerate(party):
                member['id'] = index + 1
            for size in outcomes:
                difficulty = adventure_party.encounter_difficulty(party[:size], enemy)
                base_xp = encounter_beast_xp(level, enemy['level'], difficulty=difficulty)
                result = fight(party[:size], enemy, 73000 + seed)
                outcomes[size].append(result)
                actions.update(result['actions'])
                xp = [adventure_party.xp_budget(row['level'], level, enemy['level'], base_xp,
                        lead=index == 0, contributed=True) for index, row in enumerate(party[:size])]
                assert sum(xp) <= base_xp * 2.2 + 2
                assert all(value <= base_xp for value in xp)
        reports.append(dict(region=region, level=level, trials_per_size=args.seeds,
            teams={str(size): dict(clean_wins=sum(row['won'] for row in data),
                median_rounds=statistics.median(row['rounds'] for row in data),
                mean_standing=round(statistics.mean(row['standing'] for row in data), 2)) for size, data in outcomes.items()}))
    # A level-one reserve never inherits a level-50 reward budget.
    catchup = adventure_party.xp_budget(1, 50, 50, encounter_beast_xp(50), contributed=True)
    assert catchup <= round(encounter_beast_xp(1) * .60)
    report = dict(status='passed', trials=len(BIOMES) * args.seeds * 3,
        assumptions='Synthetic ungeared Twine/Fairy/Imp teams; production turn rules, 20-round cap, one simple tactical policy; no player telemetry.',
        economy='Trainer XP, encounter levels, catch odds, charges and gold do not multiply with party size. Shared companion XP has per-level reserve ceilings.',
        level_one_catchup_xp=catchup, actions=dict(actions), regions=reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'trials', 'level_one_catchup_xp', 'actions')}))


if __name__ == '__main__':
    main()
