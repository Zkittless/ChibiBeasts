"""Reproduce the 6,400-duel Veilmoth comparison with explicit frozen stat builds."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import audit_progression
from utils import combat, db as storage, progression
from utils.duels import ai_move

# These are counterfactual fixtures, not aliases for the current catalog stats.
# Keep the original report labels so regenerated rows can be compared directly.
VEILMOTH_BUILDS = {
    'current525': dict(hp=135, attack=85, defense=75, speed=105, mana=125),
    'candidate560': dict(hp=150, attack=95, defense=85, speed=105, mana=125),
}
PEERS = ('dragon', 'kraken', 'hydra', 'fenrir', 'charybdis', 'leviathan',
         'jormungandr', 'simurgh')
LEVELS = (41, 50)
SEEDS = 50
POLICIES = {
    'production_optimal': 'Unchanged production optimal AI both sides.',
    'legal_care_aware': 'Same production AI, with legal third-slot heals also visible to care policy at under55%HP, for both sides.',
}


def companion_state(species, level, variant, beasts):
    beast = variant if species == 'veilmoth' else beasts[species]
    growth = progression.companion_growth(species, level, beasts)
    stats = {key: value + growth[key] for key, value in beast['base_stats'].items()}
    row = {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'], 'level': level,
           'happiness': 100, 'bond': 0}
    result = combat.build_state(beast, row, buffs={'ultimate_charges': 0})
    result['hp'], result['mana'] = result['max_hp'], result['max_mana']
    return result


def care_aware(state, opponent, personality):
    """Apply the same legal third-slot healing policy to either participant."""
    if state['hp'] < state['max_hp'] * .55:
        for move in state['moves']:
            if combat.can_use(state, move) and combat.move_spec(state, move).get('heal_percent'):
                return move, False
    preferred = ai_move(state, opponent, personality)
    return preferred if combat.can_use(state, *preferred) else (state['moves'][0], False)


def run_audit():
    beasts = storage.load_beasts()
    assert all(beasts[peer]['rarity'] == 'legendary' for peer in PEERS)
    paths = ('scripts/audit_hollowlight_peers.py', 'scripts/audit_progression.py',
             'utils/combat.py', 'utils/duels.py', 'utils/progression.py',
             'utils/type_chart.py', 'data/beasts.json')
    hashes = {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
    rows = []
    previous_ai = audit_progression.ai_move
    try:
        for label, base_stats in VEILMOTH_BUILDS.items():
            variant = deepcopy(beasts['veilmoth'])
            variant['base_stats'] = deepcopy(base_stats)
            for policy in POLICIES:
                audit_progression.ai_move = ai_move if policy == 'production_optimal' else care_aware
                for level in LEVELS:
                    for peer in PEERS:
                        counts, hp, rounds = Counter(), 0., 0
                        for seed in range(SEEDS):
                            for side in (0, 1):
                                states = [companion_state('veilmoth', level, variant, beasts),
                                          companion_state(peer, level, variant, beasts)]
                                if side:
                                    states.reverse()
                                with combat.using_rng(random.Random(f'veilmoth-peers:{level}:{peer}:{seed}:{side}')):
                                    result = audit_progression.resolve_battle(states, 'optimal')
                                counts['win' if result['winner'] == side else 'draw' if result['winner'] is None else 'loss'] += 1
                                counts['timeout'] += result['timed_out']
                                counts['ko_win'] += result['winner'] == side and states[1-side]['hp'] <= 0
                                hp += states[side]['hp'] / states[side]['max_hp']
                                rounds += result['rounds']
                        trials = SEEDS * 2
                        rows.append(dict(variant=label, base_stats=deepcopy(base_stats), policy=policy,
                                         level=level, peer=peer, trials=trials, **counts,
                                         mean_remaining_hp=round(hp/trials, 3), mean_rounds=round(rounds/trials, 2)))
    finally:
        audit_progression.ai_move = previous_ai
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == expected
               for path, expected in hashes.items()), 'Audited sources changed during replay.'
    summary = [dict(variant=variant, policy=policy,
                    trials=sum(row['trials'] for row in rows if row['variant'] == variant and row['policy'] == policy),
                    wins=sum(row.get('win', 0) for row in rows if row['variant'] == variant and row['policy'] == policy))
               for variant in VEILMOTH_BUILDS for policy in POLICIES]
    assert sum(row['trials'] for row in rows) == 6400
    return dict(beasts_sha256=hashes['data/beasts.json'], source_sha256=hashes,
                scope='Equal level41/50, full HP/MP, happiness100, bond0, no gear/perks/training, zero opening ultimate charges; both starting sides; 20-round production duel-parity resolver.',
                policies=POLICIES, rows=rows, summary=summary,
                decision='Adopt candidate560: 150 HP / 95 ATK / 85 DEF / 105 SPD / 125 MP. The current525 label is the pre-buff baseline.',
                fingerprint_context='Hashes describe the current checkout. Both Veilmoth base-stat builds are explicitly frozen in the audit, independent of the current catalog; peers, moves and engine use the checkout sources.',
                sampling='50 deterministic seeds per level and peer, each repeated with Veilmoth on either starting side; seed string veilmoth-peers:{level}:{peer}:{seed}:{side}. Total 6400 duels.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/hollowlight-veilmoth-balance.json')
    args = parser.parse_args()
    report = run_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    for row in report['summary']:
        print(json.dumps(row))
    print(json.dumps(dict(duels=sum(row['trials'] for row in report['rows']), output=str(args.output))))


if __name__ == '__main__':
    main()
