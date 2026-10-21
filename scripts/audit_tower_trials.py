"""Reproduce seeded-plan invariants and stated, production-core tower fixtures.

No Discord connection or simulated acquisition timeline. Success means invariant
checks passed; per-build clear rates are measurements, never universal win claims.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import combat, trial_combat as tower, trial_rules as rules
from utils.catalog import load_catalog
from utils.habitats import BIOMES, eligible


def configuration():
    from cogs.economy import TRAIN_GAIN
    from utils.economy_balance import TRAIN_HP_MULTIPLIER
    return dict(version=rules.VERSION, wave_counts=rules.WAVE_COUNTS,
                profiles=rules.PROFILES, guardian_pools=rules.GUARDIANS,
                miniboss_pools=rules.MINIBOSSES, ordinary_divines=sorted(rules.ORDINARY_DIVINES),
                power_ceiling=dict(normal=1.35, miniboss=1.25, guardian=1.25),
                camp=dict(claims=1, hp_fraction=.25, mana_fraction=.25, clear_condition=True, revive=False),
                item_policy=dict(maximum=3, fixture_item='pixie_dust_potion', hp_fraction=.20),
                training=dict(attack_sessions=4, hp_sessions=4, minimum_days=2, max_sessions_per_day=4,
                              gains=TRAIN_GAIN, hp_multiplier=TRAIN_HP_MULTIPLIER),
                fixtures=dict(bare_happiness=70, bare_bond=0, prepared_happiness=100, prepared_bond=100,
                              initial_ultimate_charges=0, initial_health='full',
                              initial_mana='production build_state entry cap, not a refill'),
                strategy='Basic attack; heal below55% when available, otherwise use an affordable ultimate if enemy is unguarded; swap below25% to a reserve with more than twice current HP; an available healing Assist below55% takes precedence. Three20%HP potions between waves, one25%HP/MP midpoint camp. No optimal-play claim.',
                rewards=[dict(waves=[5,7], multiplier=1.5, cooldown_seconds=1800),
                         dict(waves=[8,11], multiplier=2, cooldown_seconds=2400),
                         dict(waves=[12,15], multiplier=3, cooldown_seconds=3600)],
                data_sha256={name: hashlib.sha256((ROOT/'data'/name).read_bytes()).hexdigest()
                             for name in ('beasts.json','equipment.json','items.json')},
                destination_pools={key: dict(types=sorted(value['types']), weights=value['weights'],
                                              levels=list(value['level_range']))
                                   for key,value in BIOMES.items()})


def metadata(seeds=1000, trials=30):
    cfg=configuration()
    for total in rules.WAVE_COUNTS.values():
        ratio=rules.reward_multiplier(total)*3600/rules.cooldown_seconds(total)
        assert math.isclose(ratio,3.)
        assert math.isclose(ratio/.8,3.75)
    return dict(status='passed', scope='Seed/serialization/rate invariants and the stated strategy fixtures; not live pacing.',
                plan_count=seeds*len(BIOMES), combat_trials=trials*3*len(BIOMES),
                plan_seeds_per_biome=seeds, strategy_seeds_per_mode=trials,
                config=cfg, config_fingerprint=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest())


def verify_plan(plan):
    catalog=load_catalog('beasts.json')['beasts']
    waves=plan['waves']
    assert len(waves)==plan['total']
    assert max(Counter(w['species'] for w in waves).values())<=2
    assert sum(w['categories']['control'] for w in waves)<=(plan['total']+3)//4
    region=BIOMES[plan['destination']]
    low,high=region['level_range']
    for i,w in enumerate(waves):
        b=catalog[w['species']]
        assert eligible(b) and b['type'] in region['types'] and region['weights'].get(b['rarity'],0)>0
        assert w['level']==low+(high-low)*i//(len(waves)-1)
        assert rules.power(tower.build_enemy(w))==w['power_after']<=w['power_cap']
        # Before enemy-template freezing the same profile builder and cap were
        # applied at wave entry. This comparison also protects frozen replay parity.
        original=rules.profiled_state(w['species'],w['level'],w['profile'])
        rules.cap_power(original,w['power_cap'])
        original.update(id=-w['index'],_trial_heals=0,_trial_controls=0)
        assert tower.build_enemy(w)==original
    for a,b in zip(waves,waves[1:]):
        assert a['species']!=b['species']
        assert not any(a['categories'][kind] and b['categories'][kind] for kind in ('heal','control'))
    if len(waves)>=8:
        assert waves[plan['camp_wave']-1]['species']!=waves[-1]['species']
        assert waves[plan['camp_wave']-1]['species'] in rules.MINIBOSSES[plan['destination']]
    assert waves[-1]['species'] in rules.GUARDIANS[plan['destination']]
    assert json.loads(json.dumps(plan))==plan


def audit(output, *, seeds=1000, trials=30):
    """Synthetic strategy replays with production effects; no claimed live pacing."""
    from pathlib import Path
    from scripts.audit_progression import companion_state, build_reachability
    from cogs.economy import TRAIN_GAIN
    from utils.economy_balance import TRAIN_HP_MULTIPLIER
    from utils.team_combat import assist_effect
    catalog = load_catalog('beasts.json')['beasts']
    summary = {'kind': 'synthetic production-core replay; no Discord/network timing',
               'assumptions': ['Entry bare and prepared fixtures are separate, not simulated acquisition timelines.',
                               'Prepared gear obeys trainer gates and checked wild-material reachability.',
                               'Prepared training: 4 attack + 4 HP sessions per companion, at most 4/day over 2 days.',
                               'Prepared care: at most 3 existing Pixie Dust Potions (20% HP), between waves only.',
                               'No other perks, consumables, permanent-stat items or passive overrides.'], 'biomes': []}
    teams = [
        ('twine', 'fairy', 'goblin'), ('twine', 'fairy', 'goblin'), ('twine', 'fairy', 'goblin'),
        ('timestitch', 'unicorn', 'wyvern'), ('timestitch', 'unicorn', 'griffin'),
        ('timestitch', 'yeti', 'thunderbird'), ('timestitch', 'hydra', 'abyssal_eel'),
        ('timestitch', 'hydra', 'dragon'), ('chronos', 'sphinx', 'kitsune_9'), ('chronos', 'zodiac', 'sphinx'),
    ]
    for (destination, total), species in zip(rules.WAVE_COUNTS.items(), teams):
        for seed in range(seeds):
            verify_plan(rules.generate_plan(destination, seed))
        low, high = BIOMES[destination]['level_range']
        trainer = BIOMES[destination]['min_level']
        gear = ['mossbark_wrapping', 'iron_dust_chip']
        if trainer >= 6:
            gear = ['emberstone_vest', 'iron_dust_chip']
        if trainer >= 12:
            gear = ['unicorn_healer_vestments', 'iron_dust_chip']
        if trainer >= 20:
            gear = ['unicorn_healer_vestments', 'thunderbird_feathers']
        if trainer >= 30:
            gear = ['dragon_lord_aegis', 'core_of_the_phoenix']
        reachability = build_reachability(gear, trainer)
        assert reachability['valid'], (destination, reachability)
        modes = []
        for label, level, prepared in [('entry_bare', low, False), ('entry_prepared', low, True), ('upper_prepared', high, True)]:
            won = cleared = actions = 0
            for seed in range(trials):
                party = []
                for i, k in enumerate(species, 1):
                    gain = TRAIN_GAIN[catalog[k]['rarity']]
                    extras = {'attack': 4 * gain, 'hp': 4 * gain * TRAIN_HP_MULTIPLIER} if prepared else {}
                    state = companion_state(k, level, bond=100 if prepared else 0, happiness=100 if prepared else 70,
                                            gear=gear if prepared else (), extras=extras)
                    state.update(id=i, companion_id=i)
                    party.append(state)
                p = tower.new_run(party, rules.generate_plan(destination, seed))
                camped = False
                items = 0
                for _ in range(1000):
                    if p['status'] == 'between':
                        if p['cleared'] == p['plan']['camp_wave'] and not camped:
                            for member in p['party']:
                                if member['hp'] > 0:
                                    member['hp'] = min(member['max_hp'], member['hp'] + int(member['max_hp'] * .25))
                                    member['mana'] = min(member['max_mana'], member['mana'] + int(member['max_mana'] * .25))
                                    member.update(status=None, status_turns=0)
                            camped = True
                        if prepared and items < 3:
                            injured = [s for s in p['party'] if 0 < s['hp'] < s['max_hp'] * .75]
                            if injured:
                                member = min(injured, key=lambda s: s['hp'] / s['max_hp'])
                                member['hp'] = min(member['max_hp'], member['hp'] + int(member['max_hp'] * .20))
                                items += 1
                        tower.start_wave(p)
                    elif p['status'] == 'fighting':
                        state = tower.active(p)
                        available = [a for a in tower.legal_actions(p) if a['enabled']]
                        move = available[0]
                        if state['hp'] < state['max_hp'] * .55:
                            move = next((a for a in available if a['kind'] == 'move' and combat.move_spec(state, a['choice'][0]).get('heal_percent')), move)
                        elif not p['enemy'].get('guard'):
                            move = next((a for a in available if a['kind'] == 'ultimate'), move)
                        if state['hp'] < state['max_hp'] * .25:
                            move = next((a for a in available if a['kind'] == 'swap' and next(s for s in p['party'] if s['id'] == a['choice'][1])['hp'] > state['hp'] * 2), move)
                        helpers = [a for a in available if a['kind'] == 'assist']
                        if state['hp'] < state['max_hp'] * .55:
                            move = next((a for a in helpers if assist_effect(next(s for s in p['party'] if s['id'] == a['choice'][1])) == 'heal'), move)
                        tower.act(p, move['choice'])
                        actions += 1
                    else:
                        break
                    p = json.loads(json.dumps(p))
                else:
                    raise AssertionError('Run did not terminate')
                won += p['status'] == 'won'
                cleared += p['cleared']
            modes.append(dict(label=label, level=level, trials=trials, wins=won,
                              mean_cleared=round(cleared / trials, 2), mean_actions=round(actions / trials, 2)))
        summary['biomes'].append(dict(destination=destination, total=total, trainer=trainer, species=list(species),
                                     prepared_gear=gear, gear_reachability=reachability, modes=modes))
    summary.update(metadata(seeds, trials))
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('status', 'plan_count', 'combat_trials', 'config_fingerprint')}))
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs'/'tower-balance.json')
    parser.add_argument('--seeds',type=int,default=1000)
    parser.add_argument('--trials',type=int,default=30)
    args=parser.parse_args()
    if args.seeds<1 or args.trials<1: parser.error('seeds and trials must be positive')
    audit(args.output,seeds=args.seeds,trials=args.trials)
