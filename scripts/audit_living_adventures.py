"""Audit authored catalog and replay stated exploration combat fixtures."""
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
from utils import adventure_content, combat, journey_combat, trial_combat
from utils.habitats import BIOMES, eligible, encounter_pool


def action(state):
    available = [a for a in trial_combat.legal_actions(state) if a['enabled']]
    active = trial_combat.active(state)
    move = available[0]
    if active['hp'] < active['max_hp'] * .55:
        move = next((a for a in available if a['kind'] == 'move' and combat.move_spec(active, a['choice'][0]).get('heal_percent')), move)
    elif not state['enemy'].get('guard'):
        move = next((a for a in available if a['kind'] == 'ultimate'), move)
    if active['hp'] < active['max_hp'] * .25:
        move = next((a for a in available if a['kind'] == 'swap' and next(s for s in state['party'] if s['id'] == a['choice'][1])['hp'] > active['hp'] * 2), move)
    from utils.team_combat import assist_effect
    if active['hp'] < active['max_hp'] * .55:
        move = next((a for a in available if a['kind'] == 'assist' and assist_effect(next(s for s in state['party'] if s['id'] == a['choice'][1])) == 'heal'), move)
    return move['choice']


def audit(output, trials=10):
    from scripts.audit_progression import companion_state, build_reachability
    from cogs.economy import TRAIN_GAIN
    from utils.economy_balance import TRAIN_HP_MULTIPLIER
    documents = {biome: adventure_content.load(biome) for biome in BIOMES}
    counts = []
    for biome, document in documents.items():
        errors = adventure_content.validate(document, expected_biome=biome)
        assert not errors, errors
        roots = document['encounters']
        counts.append(dict(biome=biome, one_time=sum(not r['repeatable'] for r in roots),
                           repeatable=sum(r['repeatable'] for r in roots), nodes=sum(len(r['nodes']) for r in roots),
                           chains=sum(len(r['nodes']) >= 3 for r in roots)))
    teams = [('twine','fairy','goblin')] * 3 + [('timestitch','unicorn','wyvern'),
        ('timestitch','unicorn','griffin'), ('timestitch','yeti','thunderbird'),
        ('timestitch','hydra','abyssal_eel'), ('timestitch','hydra','dragon'),
        ('chronos','sphinx','kitsune_9'), ('chronos','zodiac','sphinx')]
    summary = dict(kind='Synthetic production combat replays; no live Discord timing or universal win-rate claim.',
                   content=counts, fixtures=[], strategy='Affordable healing below55%, ultimate if unguarded, swap below25%, healing assist below55%, otherwise basic attack.',
                   assumptions=['Three fights per run: single, pack2, pack3, with current HP/MP/status/meter carried.',
                       'First fight uses one seeded base-habitat-weighted capture species; later fillers use ordinary eligible tiers. No capture consumables or story overrides in these fixtures.',
                       'No potions or revives. +10% attack and defense once before combat; 10% surviving-team recovery after fight two.',
                       'Prepared means four attack and four HP training sessions plus trainer-gated existing gear; acquisition time is not simulated.',
                       'Every pack and individual fight pays zero separate gold/XP/material/capture; settlement owns one bundle.'],
                   economy=dict(gold_level_multiple=[6,10], bonus_cap_percent=25, trainer_effort=.8,
                       combat_team_effort=.85, noncombat_team_effort=.2, max_material_or_egg=1, max_catches=1,
                       charges=3, base_regeneration_seconds=1800, max_boon_percent=20, max_recovery_percent=20),
                   data_sha256={key:hashlib.sha256((ROOT/'data'/'adventures'/f'{key}.json').read_bytes()).hexdigest() for key in BIOMES})
    summary['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'utils/journey_combat.py','utils/trial_combat.py','utils/combat.py','utils/team_combat.py',
        'data/beasts.json','data/equipment.json','scripts/audit_living_adventures.py')}
    beasts = __import__('utils.catalog', fromlist=['load_catalog']).load_catalog('beasts.json')['beasts']
    for (biome, region), species in zip(BIOMES.items(), teams):
        low, high = region['level_range']
        trainer = region['min_level']
        gear = ['mossbark_wrapping','iron_dust_chip'] if trainer < 6 else ['emberstone_vest','iron_dust_chip']
        if trainer >= 12:
            gear = ['unicorn_healer_vestments','iron_dust_chip']
        if trainer >= 20:
            gear = ['unicorn_healer_vestments','thunderbird_feathers']
        if trainer >= 30:
            gear = ['dragon_lord_aegis','core_of_the_phoenix']
        reachability = build_reachability(gear, trainer)
        assert reachability['valid']
        for label, level, prepared in [('entry_bare',low,False),('entry_prepared',low,True),('upper_prepared',high,True)]:
            totals = Counter()
            for seed in range(trials):
                party = []
                for index, key in enumerate(species,1):
                    gain = TRAIN_GAIN[beasts[key]['rarity']]
                    extras = dict(attack=4*gain,hp=4*gain*TRAIN_HP_MULTIPLIER) if prepared else {}
                    state = companion_state(key, level, bond=100 if prepared else 0, happiness=100 if prepared else 70,
                                            gear=gear if prepared else (), extras=extras)
                    state.update(id=index, companion_id=index)
                    party.append(state)
                p = {'prepared_party':party}
                journey_combat.effects(p,dict(attack_boon=10,defense_boon=10))
                rng=random.Random(f'{biome}:{seed}:capture-fixture')
                tiers=[r for r,w in region['weights'].items() if w>0 and encounter_pool(r,biome)]
                tier=rng.choices(tiers,weights=[region['weights'][r] for r in tiers])[0]
                ticket=rng.choice(encounter_pool(tier,biome))['id']
                totals['ticket_'+tier]+=1
                for number, kind in enumerate(('single','pack2','pack3')):
                    lineup = journey_combat.plan(biome, f'{seed}:{number}', kind, level,ticket=ticket if number==0 else None)
                    assert lineup == journey_combat.plan(biome,f'{seed}:{number}',kind,level,ticket=ticket if number==0 else None)
                    for wave in lineup['waves']:
                        b=beasts[wave['species']]
                        assert eligible(b) and b['type'] in region['types'] and region['weights'].get(b['rarity'],0)>0
                    journey_combat.start(p,lineup)
                    for _ in range(300):
                        state=p['combat']
                        if state['status']=='between':
                            journey_combat.continue_pack(p)
                        elif state['status']=='fighting':
                            journey_combat.action(p,action(state))
                            totals['actions']+=1
                        else:
                            break
                        p=json.loads(json.dumps(p))
                    else:
                        raise AssertionError('Combat failed to terminate')
                    if p['combat']['status']!='won':
                        break
                    totals['fights_cleared']+=1
                    if number==1:
                        journey_combat.effects(p,{'heal':10})
                totals['complete']+=p['combat']['status']=='won' and number==2
            summary['fixtures'].append(dict(biome=biome,mode=label,level=level,team=species,gear=gear if prepared else [],
                trials=trials,completes=totals['complete'],mean_fights=round(totals['fights_cleared']/trials,2),mean_actions=round(totals['actions']/trials,2),
                capture_tiers={key.removeprefix('ticket_'):value for key,value in totals.items() if key.startswith('ticket_')}))
    summary.update(status='passed', replays=trials*30)
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=summary['status'], replays=summary['replays'], content=counts)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs'/'living-adventure-balance.json')
    parser.add_argument('--trials',type=int,default=10)
    args=parser.parse_args()
    audit(args.output,args.trials)
