"""Offline economy ledger; exploration simulations retain the historical quick model.

Its acquisition, income and reset-loop estimates are dated audit evidence, not
forecasts for the saved Field Journal expeditions or their gathering choices.
"""
import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import itertools
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils import db as storage
from utils.habitats import BIOMES, encounter_pool, material_pool
from utils.catalog import load_catalog
from utils.sanctuary import apply_craft_discount
from cogs.hatch import HATCH_EGGS, get_egg_pool, _BASE_EGG_POOLS
from cogs.world import EGGS
from cogs.utilities import SHARD_SHOP
from utils.shops import GUILD_ITEMS
from cogs.economy import TRAIN_COST, TRAIN_GAIN

ITEMS = storage.load_items()
GEAR = load_catalog('equipment.json')
MATERIALS = load_catalog('materials.json')['materials']
RECIPES = {**GEAR['equipment'], **GEAR['runes'], **{k:v for k,v in ITEMS.items() if v.get('recipe')}}


def literal(file, name):
    for node in ast.walk(ast.parse((ROOT/file).read_text(encoding='utf-8'))):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id==name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def settings():
    try:
        from utils.economy_balance import INCUBATION_PRICES, RELEASE_VALUES, MATERIAL_SELL_VALUES, recipe_level, item_sell_price, shop_level, gold_reward_level
        return INCUBATION_PRICES, RELEASE_VALUES, MATERIAL_SELL_VALUES, recipe_level, item_sell_price, shop_level, gold_reward_level
    except ModuleNotFoundError:
        prices = literal('cogs/profile.py', 'RARITY_PRICE')
        release = literal('cogs/utilities.py', 'REFUND')
        materials = literal('cogs/utilities.py', 'MATERIAL_PRICES')
        return prices, release, materials, lambda item:1, lambda item,merchant=False:int(max(5,int(item.get('price',0)*.35))*(1.2 if merchant else 1)), lambda item:1, lambda level:level


INCUBATION_PRICES, RELEASE, MATERIAL_SELL, recipe_level, item_sell_price, shop_level, reward_level = settings()


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def percentile(values, p):
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*p))]


def material_distribution(biome, win_rate=.75):
    """Historical quick model: 5% quiet, then species-weighted encounter loot."""
    weights = BIOMES[biome]['weights']
    pools = {r:encounter_pool(r, biome) for r in weights}
    total = sum(weights[r] for r in weights if pools[r])
    result = defaultdict(float)
    try:
        from utils.economy_balance import GUARANTEED_EXPLORE_MATERIAL
    except ModuleNotFoundError:
        GUARANTEED_EXPLORE_MATERIAL = False
    for rarity, beasts in pools.items():
        for beast in beasts:
            affinity = .15*beast['catch_rate']
            success = 1 if GUARANTEED_EXPLORE_MATERIAL else affinity+(1-affinity)*win_rate
            p = .95*weights[rarity]/total/len(beasts)*success
            candidates = material_pool(beast, biome)
            for mid, count in Counter(candidates).items():
                result[mid] += p*count/len(candidates)
    if GUARANTEED_EXPLORE_MATERIAL:
        candidates = material_pool({'id':'','type':''},biome)
        for mid,count in Counter(candidates).items():
            result[mid] += .05*count/len(candidates)
    return dict(result)


def egg_values():
    rows = []
    for eid, egg in HATCH_EGGS.items():
        for star, merchant, observatory, fortune in itertools.product((False,True), repeat=4):
            perks = [{'perk_id':'stardust_touch','equipped':1}] if star else []
            pool = get_egg_pool(eid, perks, {'celestial_observatory':int(observatory)})
            altered = _BASE_EGG_POOLS[eid].get('altered_chance',0)
            refund = sum(p*RELEASE.get(r,50) for r,p in pool.items())
            refund = refund*(1-altered)+RELEASE['altered_divine']*altered
            if fortune:
                refund = refund*.9995+RELEASE['divine']*.0005
            price = int(egg['price']*.95) if merchant else egg['price']
            rows.append({'egg':eid,'stardust':star,'merchant':merchant,'observatory':observatory,'fortune':fortune,
                         'price':price,'expected_release_gold':round(refund,2),'expected_net_gold':round(refund-price,2),
                         'effective_pool':json.dumps(pool,sort_keys=True)})
    for eid, egg in EGGS.items():
        if eid=='world_tree_seed':
            continue
        pool = {k:v for k,v in egg['pool'].items() if k!='altered_chance'}
        total = sum(pool.values())
        altered = egg['pool'].get('altered_chance',0)
        refund = sum(p/total*RELEASE.get(r,50) for r,p in pool.items())*(1-altered)+RELEASE['altered_divine']*altered
        price = INCUBATION_PRICES[egg['rarity']]
        rows.append({'egg':eid,'stardust':False,'merchant':False,'observatory':False,'fortune':False,
                     'price':price,'expected_release_gold':round(refund,2),'expected_net_gold':round(refund-price,2),
                     'effective_pool':json.dumps(pool,sort_keys=True)})
    return rows


def offers():
    rows = []
    for eid, item in HATCH_EGGS.items():
        rows.append({'id':eid,'name':item['name'],'shop':'gold/instant','currency':'gold','cost':item['price'],'level':shop_level(item),'limit':0})
    for eid, item in EGGS.items():
        if eid!='world_tree_seed':
            rows.append({'id':eid,'name':item['name'],'shop':'gold/incubation','currency':'gold','cost':INCUBATION_PRICES[item['rarity']],'level':shop_level(item),'limit':0})
    for iid, item in ITEMS.items():
        if item.get('price',0)>0:
            rows.append({'id':iid,'name':item['name'],'shop':'gold/items','currency':'gold','cost':item['price'],'level':shop_level(item),'limit':0})
    for iid, item in SHARD_SHOP.items():
        rows.append({'id':iid,'name':item['name'],'shop':'shards','currency':'shards','cost':item['cost'],'level':item.get('min_level',1),'limit':item['weekly_limit']})
    for iid,(cost,level,_) in GUILD_ITEMS.items():
        rows.append({'id':iid,'name':ITEMS[iid]['name'],'shop':'guild','currency':'tickets','cost':cost,'level':level,'limit':0})
    return rows


def recipe_trials(distributions, samples=200):
    rows = []
    # Route planner uses level-10 guaranteed raid rewards, no random loot.
    guaranteed = {'chronos_gear':('ancient_chronos',2),'tear_of_the_creator':('ancient_chronos',1),
                  'blighted_world_core':('ancient_genesis',2)}
    for iid,item in RECIPES.items():
        for forge in (False,True):
            recipe = apply_craft_discount(item['recipe'], {'gnome_forge':int(forge)})
            raids = defaultdict(int)
            remaining = dict(recipe)
            for mid,qty in list(remaining.items()):
                if mid in guaranteed:
                    boss, amount = guaranteed[mid]
                    raids[boss] = max(raids[boss], (qty+amount-1)//amount)
                    del remaining[mid]
            # Genesis awards its own tear, so do not ask for redundant Chronos clears.
            if 'blighted_world_core' in recipe and 'tear_of_the_creator' in recipe:
                raids['ancient_genesis'] = max(raids['ancient_genesis'], recipe['tear_of_the_creator'])
                raids.pop('ancient_chronos',None)
            impossible = [mid for mid in remaining if not any(d.get(mid,0) for d in distributions.values())]
            trials = []
            if not impossible:
                for seed in range(samples):
                    rng = random.Random(f'{iid}:{forge}:{seed}')
                    have = Counter()
                    for turn in range(10001):
                        missing = {mid:qty-have[mid] for mid,qty in remaining.items() if have[mid]<qty}
                        if not missing:
                            trials.append(turn)
                            break
                        # Address the slowest outstanding ingredient at its best destination.
                        mid = max(missing,key=lambda m:missing[m]/max(d.get(m,0) for d in distributions.values()))
                        biome = max(distributions,key=lambda b:distributions[b].get(mid,0))
                        keys = list(distributions[biome])
                        weights = list(distributions[biome].values())
                        drop = rng.choices(keys+[None],weights=weights+[1-sum(weights)])[0]
                        if drop:
                            have[drop] += 1
            gate = max([recipe_level(item),*[min(BIOMES[b]['min_level'] for b,d in distributions.items() if d.get(mid,0)) for mid in remaining if mid not in impossible],10 if raids else 1])
            resale = sum(qty*MATERIAL_SELL[MATERIALS[mid]['rarity']] for mid,qty in recipe.items())
            rows.append({'item':iid,'rarity':item['rarity'],'forge':forge,'unlock_level':recipe_level(item),
                         'earliest_source_level':gate,'recipe':json.dumps(recipe,sort_keys=True),'ingredient_sell_value':resale,
                         'mean_explorations':round(statistics.mean(trials),1) if trials else '',
                         'p90_explorations':percentile(trials,.9) if trials else '',
                         'days_at_8_explores':round(statistics.mean(trials)/8,2) if trials else '',
                         'guaranteed_ancient_clears':sum(raids.values()),'ancient_plan':json.dumps(dict(raids),sort_keys=True),
                         'unreachable_materials':','.join(impossible)})
            rows[-1].update(raid_shortcut(recipe))
    return rows


def raid_shortcut(recipe):
    routes={'dragon_scale':('corrupted_dragon',2),'sunforge_residue':('corrupted_dragon',2),
            'kraken_ink':('corrupted_leviathan',2),'fenrir_fur':('corrupted_fenrir',2),
            'chronos_gear':('ancient_chronos',2),'blighted_world_core':('ancient_genesis',2),
            'tear_of_the_creator':('ancient_genesis' if 'blighted_world_core' in recipe else 'ancient_chronos',1)}
    plan=defaultdict(int)
    for mid,qty in recipe.items():
        if mid in routes:
            boss,amount=routes[mid]
            plan[boss]=max(plan[boss],(qty+amount-1)//amount)
    return {'guaranteed_raid_shortcut':json.dumps(dict(plan),sort_keys=True),
            'forage_after_shortcut':json.dumps({mid:qty for mid,qty in recipe.items() if mid not in routes},sort_keys=True)}


def incomes(distributions):
    rows = []
    for level in (1,3,5,10,15,25,50):
        best = max((b for b in BIOMES if BIOMES[b]['min_level']<=level), key=lambda b:sum(p*MATERIAL_SELL[MATERIALS[m]['rarity']] for m,p in distributions[b].items()))
        for label,explores,challenges,spars,clears in (('casual',4,2,1,0),('active',8,4,3,0),('raider',8,4,3,2)):
            clears = clears if level>=10 else 0
            daily = 100+level*15
            # Explicit planning assumptions: 75% battle wins; three completed quests
            # worth 200g average on active/raider days, two on casual days.
            explore = explores*(.95*.75*5.5*reward_level(level)*1.25+.05*30+.95*.25*3.5*reward_level(level))
            challenge = challenges*(.75*11.5+.25*3.5)*reward_level(level)
            spar = spars*(.75*200+.25*50)
            quests = 400 if label=='casual' else 600
            raids = clears*300*(1+(max(10,min(50,level))-10)/40)
            income = daily+explore+challenge+spar+quests+raids
            materials = explores*sum(p*MATERIAL_SELL[MATERIALS[m]['rarity']] for m,p in distributions[best].items())
            rows.append({'trainer_and_companion_level':level,'profile':label,'explores':explores,'challenges':challenges,'spars':spars,'corrupted_clears':clears,
                         'daily_gold_before_spending':round(income,1),'optional_explore_salvage_gold':round(materials,1),
                         'modeled_daily_shards_without_quests':2+spars*.75+clears*round(3*(1+(max(10,min(50,level))-10)/40)),
                         'days_for_8000_gold':round(8000/income,2),'days_for_75000_gold':round(75000/income,2)})
    return rows


def reset_loops():
    """Optimistic repeatable loop: win/release every encounter; no finite charges."""
    from utils.hatching import promote_rates
    from utils.habitats import challenge_weights
    rows=[]
    for level in (1,10,30,50,100):
        for bonuses in (False,True):
            trials=[]
            for biome,definition in BIOMES.items():
                if definition['min_level']>level:continue
                def species(weights):
                    pools={r:encounter_pool(r,biome) for r in weights}
                    total=sum(w for r,w in weights.items() if pools[r])
                    return [(beast,weights[r]/total/len(pool)) for r,pool in pools.items() for beast in pool]
                rates=definition['weights']
                if bonuses:
                    rates=promote_rates(rates,{'rare':.10,'epic':.10})
                    rates=promote_rates(rates,{'epic':.02,'legendary':.02})
                explore_species=species(rates)
                explore_gold=.05*30+.95*sum(p*5.5*reward_level(level)*(1+.25*(1-min(.4,.15*b['catch_rate']+(.05 if bonuses else 0))))*(1.1 if bonuses else 1) for b,p in explore_species)
                release_gold=.95*sum(p*RELEASE.get(b['rarity'],50) for b,p in explore_species)
                # Deliberately generous: assume the most valuable available material every time.
                materials=material_distribution(biome,win_rate=1)
                material_gold=max(MATERIAL_SELL[MATERIALS[mid]['rarity']] for mid in materials)*(1.2 if bonuses else 1)
                challenge_release=max(sum(p*RELEASE.get(b['rarity'],50)*({'common':.5,'uncommon':.35,'rare':.2,'epic':.1,'legendary':.05,'divine':.02}[b['rarity']]+(.05 if bonuses else 0)) for b,p in species(challenge_weights(definition['weights'],rarity))) for rarity in ('common','uncommon','rare','epic','legendary','divine'))
                trials.append((biome,explore_gold+release_gold+material_gold,11.5*reward_level(level)+challenge_release))
            explore=max(trials,key=lambda x:x[1]); challenge=max(trials,key=lambda x:x[2])
            gross=explore[1]+challenge[2]
            cost=int(ITEMS['chrono_biscuit']['price']*(.95 if bonuses else 1))
            rows.append({'level':level,'all_repeatable_bonuses':bonuses,'explore_biome':explore[0],'challenge_biome':challenge[0],
                         'optimistic_expected_gross_gold':round(gross,2),'reset_price':cost,'expected_net_gold':round(gross-cost,2)})
    return rows


def raid_values():
    from cogs.guilds import RAID_BOSSES
    from cogs.ancient import ANCIENT_BOSSES
    rows=[]
    for boss in [*RAID_BOSSES['corrupted'],*ANCIENT_BOSSES]:
        ancient=boss['id'].startswith('ancient_')
        materials={'chronos_gear':2,'tear_of_the_creator':1} if boss['id']=='ancient_chronos' else {'blighted_world_core':2,'tear_of_the_creator':1} if ancient else {'kraken_ink':2} if boss['id']=='corrupted_leviathan' else {'fenrir_fur':2} if boss['id']=='corrupted_fenrir' else {'dragon_scale':2,'sunforge_residue':2}
        bonus=sum(MATERIAL_SELL[MATERIALS[key]['rarity']] if key in MATERIALS else item_sell_price(ITEMS[key]) for key in boss['loot_table'])/len(boss['loot_table'])
        for level in (10,30,50):
            scale=1+(level-10)/40
            direct=round((500 if ancient else 300)*scale)
            salvage=sum(round(qty*scale)*MATERIAL_SELL[MATERIALS[mid]['rarity']] for mid,qty in materials.items())
            rows.append({'boss':boss['id'],'level':level,'gold':direct,'shards':round((8 if ancient else 3)*scale),
                         'personal_tickets':round(10*scale),'guaranteed_material_sell_value':salvage,
                         'mean_random_loot_sell_value':round(bonus,2),'gross_if_all_loot_sold':round(direct+salvage+bonus,2)})
    return rows


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    distributions = {b:material_distribution(b) for b in BIOMES}
    material_rows = []
    for mid,item in MATERIALS.items():
        rates = {b:round(d.get(mid,0),5) for b,d in distributions.items()}
        best = max(rates,key=rates.get)
        material_rows.append({'material':mid,'rarity':item['rarity'],'sell_gold':MATERIAL_SELL[item['rarity']],
                              'used_by_recipes':sum(mid in x['recipe'] for x in RECIPES.values()),
                              **{b+'_per_explore':p for b,p in rates.items()},'best_biome':best if rates[best] else 'ancient raid',
                              'mean_attempts_per_unit':round(1/rates[best],1) if rates[best] else ''})
    write_csv(output/'materials.csv',material_rows)
    recipes = recipe_trials(distributions)
    write_csv(output/'recipes.csv',recipes)
    eggs = egg_values()
    write_csv(output/'egg-values.csv',eggs)
    shop = offers()
    write_csv(output/'shop-offers.csv',shop)
    income=incomes(distributions)
    write_csv(output/'income-model.csv',income)
    loops=reset_loops()
    write_csv(output/'reset-loops.csv',loops)
    write_csv(output/'raid-rewards.csv',raid_values())
    try:
        from utils.economy_balance import GUILD_EXP_PER_LEVEL
    except ModuleNotFoundError:
        GUILD_EXP_PER_LEVEL=500
    guilds=[{'members':members,'target_guild_level':level,'mean_contributions_needed':round(GUILD_EXP_PER_LEVEL*level*(level-1)/2/115,2),
             'days_with_one_contribution_per_member':round(GUILD_EXP_PER_LEVEL*level*(level-1)/2/115/members,2)} for members in (1,3,10) for level in (5,10,15)]
    write_csv(output/'guild-progression.csv',guilds)
    affordability=[]
    for offer in shop:
        level=max(10,offer['level']) if offer['shop']!='guild' else 10
        model=next(r for r in income if r['profile']=='active' and r['trainer_and_companion_level']>=level)
        rate=model['daily_gold_before_spending'] if offer['currency']=='gold' else model['modeled_daily_shards_without_quests'] if offer['currency']=='shards' else 7.5
        affordability.append({**offer,'gate_type':'guild level' if offer['shop']=='guild' else 'trainer level',
                              'model_trainer_level':model['trainer_and_companion_level'],'modeled_days_before_spending':round(offer['cost']/rate,2),
                              'level_10_raid_clears_for_tickets':round(offer['cost']/10,1) if offer['currency']=='tickets' else ''})
    write_csv(output/'shop-affordability.csv',affordability)
    try:
        from utils.economy_balance import TRAIN_HP_MULTIPLIER
    except ModuleNotFoundError:
        TRAIN_HP_MULTIPLIER=1
    training = [{'rarity':r,'gold_per_session':c,'stat_gain':TRAIN_GAIN[r],'hp_gain':TRAIN_GAIN[r]*TRAIN_HP_MULTIPLIER,
                 'gold_per_point':round(c/TRAIN_GAIN[r],1)} for r,c in TRAIN_COST.items() if r!='dev']
    write_csv(output/'training.csv',training)
    carrots = ITEMS['gnome_grown_carrots']
    growth = [{'trainer_level':level,'carrot_price':carrots['price'],
               'exp_per_carrot':carrots['effect']['exp']+carrots['effect'].get('exp_per_level',0)*min(level,carrots['effect'].get('exp_level_cap',level)),
               'companion_next_level_exp':storage.calc_exp_for_level(level)} for level in (1,5,10,15,30,50,100)]
    write_csv(output/'growth.csv',growth)
    report = {'generated_at_utc':datetime.now(timezone.utc).isoformat(),
              'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'source_hash_format':'SHA-256 of UTF-8 text with LF line endings',
              'source_sha256':{str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() for path in sorted([*ROOT.glob('cogs/*.py'),*ROOT.glob('utils/*.py'),*ROOT.glob('data/*.json'),Path(__file__)])},
              'shop_offers':len(shop),'craftable_items':len(RECIPES),'materials':len(MATERIALS),
              'free_shop_offers':[r['id'] for r in shop if r['cost']<=0],
              'positive_expected_egg_resale':[{k:r[k] for k in ('egg','price','expected_release_gold','expected_net_gold','stardust','observatory','fortune')} for r in eggs if r['expected_net_gold']>0],
              'unreachable_recipes':[r['item'] for r in recipes if r['unreachable_materials']],
              'profitable_repeatable_reset_loops':[r for r in loops if r['expected_net_gold']>0],
              'common_recipe_mean_explorations':{r['item']:r['mean_explorations'] for r in recipes if r['rarity']=='common' and not r['forge']},
              'scope':'Offline catalog checks with historical quick-exploration simulations. The acquisition, income and reset-loop estimates do not model current saved expeditions or their gathering choices. Crafting uses 200 seeded adaptive-foraging trials per recipe, all exploration destinations available, base 30-minute cooldown, no perks or consumables. The historical baseline requires affinity/75% modeled encounter victories; the later quick rules guarantee one material. Earliest source levels are route availability, not forecasts of account readiness. Income scenarios are planning assumptions, not live telemetry; they exclude selling catches, raid loot sales, one-time story/achievement/milestone rewards, fees and spending.'}
    (output/'summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('positive_expected_egg_resale','source_sha256')},indent=2))
    print(f"Positive expected hatch resale cases: {len(report['positive_expected_egg_resale'])}")


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output.resolve())
