"""Exact, offline Nursery probability and release-economy audit.

Run from any directory. This deliberately uses the production catalog/odds
functions instead of a second model of the hatch algorithm.
"""
import argparse
from datetime import datetime, timezone
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils.economy_balance import RELEASE_VALUES
from utils.nursery_catalog import (RARITIES, RESEARCH_COST, RESEARCH_MAX,
                                  definitions, rarity_pool, research_key,
                                  research_pool, species_pool)


def egg_values():
    rows = []
    for egg_id, egg in definitions().items():
        if not egg['shop']:
            continue
        for star, fortune, observatory, merchant in product((False, True), repeat=4):
            perks = [{'perk_id': perk, 'equipped': 1}
                     for perk, active in (('stardust_touch', star), ('glimmering_fortune', fortune))
                     if active]
            rates = rarity_pool(egg_id, perks, {'celestial_observatory': observatory})
            expected_release = sum(chance * RELEASE_VALUES[rarity] for rarity, chance in rates.items())
            price = max(1, int(egg['price'] * .95)) if merchant else egg['price']
            floor_release = RELEASE_VALUES[egg['grade']]
            # Give a research credit to EVERY random roll: this overstates real
            # duplicate progress. The selection replaces a subsequent paid roll.
            cycle_release = RESEARCH_COST * expected_release + floor_release
            cycle_cost = (RESEARCH_COST + 1) * price
            rows.append({
                'egg': egg_id, 'grade': egg['grade'], 'family': egg['family'],
                'stardust': star, 'fortune': fortune, 'observatory': observatory, 'merchant': merchant,
                'price': price, 'effective_pool': rates,
                'expected_release_gold': round(expected_release, 6),
                'expected_net_gold': round(expected_release - price, 6),
                'research_floor_release_gold': floor_release,
                'research_cycle_paid_hatches': RESEARCH_COST + 1,
                'research_cycle_expected_net_gold_upper_bound': round(cycle_release - cycle_cost, 6),
                # Stronger bound: even unlimited floor research cannot improve
                # this maximum of the two paid outcomes into a positive cycle.
                'research_net_per_paid_hatch_upper_bound': round(max(expected_release, floor_release) - price, 6),
            })
    return rows


def build_report():
    rows = egg_values()
    catalog = definitions()
    eggs = []
    for egg_id, egg in catalog.items():
        family_pools = {r: [beast['id'] for beast in species_pool(egg_id, r)]
                        for r in RARITIES if species_pool(egg_id, r)}
        eggs.append({**egg, 'candidate_species': family_pools,
                     'research_key': research_key(egg_id),
                     'research_candidates': [beast['id'] for beast in research_pool(egg_id)]})
    failures = []
    for row in rows:
        if abs(sum(row['effective_pool'].values()) - 1) > 1e-10:
            failures.append({'egg': row['egg'], 'reason': 'Probability does not sum to one.'})
        if row['expected_net_gold'] >= 0 or row['research_net_per_paid_hatch_upper_bound'] >= 0:
            failures.append({'egg': row['egg'], 'reason': 'Nonnegative expected hatch/research release loop.'})
        if any(not species_pool(row['egg'], rarity) for rarity in row['effective_pool']):
            failures.append({'egg': row['egg'], 'reason': 'An advertised rarity has no eligible species.'})
    # Regional level floors come from habitats.py. Keep the acquisition comparison
    # explicit: a purchase should fill a team gap, not replace regional progression.
    from utils.habitats import BIOMES
    regional_comparisons = []
    for grade, egg_id, region in (
        ('common', 'common_egg', 'woods'), ('uncommon', 'wildheart_egg', 'ember'),
        ('rare', 'rare_egg', 'glacial'), ('epic', 'resonant_egg', 'trenches'),
        ('legendary', 'celestial_egg', 'loom'), ('divine', 'abyssal_egg', 'loom_deep'),
    ):
        egg = catalog[egg_id]
        region_def = BIOMES[region]
        row = {'grade': grade, 'egg': egg_id, 'trainer_unlock': egg['min_level'],
               'hatch_level_range': egg['level_range'], 'comparison_region': region,
               'regional_trainer_unlock': region_def['min_level'],
               'regional_wild_level_range': region_def['level_range'],
               'hatch_max_below_regional_wild_max': egg['level_range'][1] < region_def['level_range'][1]}
        regional_comparisons.append(row)
        if not row['hatch_max_below_regional_wild_max']:
            failures.append({'egg': egg_id, 'reason': 'Hatch levels erase the regional wild level advantage.'})
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'catalog_count': len(catalog), 'paid_egg_count': sum(e['shop'] for e in catalog.values()),
        'bonus_scenarios': len(rows), 'research_duplicate_cost': RESEARCH_COST,
        'research_credit_cap': RESEARCH_MAX,
        'research_assumption': 'Selection replaces a paid hatch within the same grade/family at the grade floor. No free companion and no jackpot selection.',
        'incubation_choice_assumption': 'One rarity roll, then up to three distinct species of exactly that rarity. No best-of-three rarity rolls.',
        'fortune_assumption': '0.05% of lower-than-Divine probability redirects to eligible family Divine species; Altered probability is unchanged.',
        'release_assumption': 'Release uses the fixed rarity table; Merchant discounts egg purchases by 5% and does not boost companion release.',
        'maximum_expected_net_gold_per_paid_hatch': max(row['expected_net_gold'] for row in rows),
        'maximum_research_net_per_paid_hatch_upper_bound': max(row['research_net_per_paid_hatch_upper_bound'] for row in rows),
        'failures': failures, 'passed': not failures,
        'level_comparisons': regional_comparisons, 'catalog': eggs, 'scenarios': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'output' / 'nursery-rework' / 'catalog-balance.json')
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'eggs': report['catalog_count'],
                      'scenarios': report['bonus_scenarios'], 'output': str(args.output),
                      'maximum_expected_net_gold': report['maximum_expected_net_gold_per_paid_hatch']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
