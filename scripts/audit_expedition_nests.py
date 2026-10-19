"""Reproducible saved-nest odds and reward-budget audit; no bot or live DB."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import expedition_events as events
from utils.economy_balance import RELEASE_VALUES, item_sell_price
from utils.habitats import BIOMES, biome_key
from utils.nursery_catalog import definition, rarity_pool


def audit(samples):
    regions = []
    perks = ({'perk_id': 'stardust_touch', 'equipped': 1},
             {'perk_id': 'glimmering_fortune', 'equipped': 1})
    for destination, habitat in BIOMES.items():
        region = biome_key(destination)
        level = habitat['min_level']
        base_gold = int(4.5 * (sum(habitat['level_range']) // 2))
        found = Counter()
        chances = {}
        expected_offers = variance = 0.
        for seed in range(samples):
            p = dict(gold=base_gold, exp=20, base_exp=20, level=level,
                     offered=['enchanted_bark'], material='enchanted_bark',
                     bond=0, happiness=0, notes=[])
            events.initialize(p, region, seed, [dict(id=1, name='Scout', type='nature')])
            events.prepare_nest(p, region)
            expected_offers += p['nest_chance']
            variance += p['nest_chance'] * (1 - p['nest_chance'])
            egg_id = events._variant(p, events.REGIONAL_NEST_EGGS[region], 'nest-family')
            chances[egg_id] = p['nest_chance']
            if p['egg_offer']:
                found[p['egg_offer']['id']] += 1
                # The guaranteed discovery route still uses exactly three
                # stops, and a push never offers a second egg.
                events.resolve(p, region, 'shrine')
                events.resolve(p, region, 'study')
                assert 'recover_egg' in {c['value'] for c in p['page']['choices']}
                events.resolve(p, region, 'recover_egg')
                assert p['completed'] == 3
                events.resolve(p, region, 'push')
                assert 'recover_egg' not in {c['value'] for c in p['page']['choices']}
        eggs = []
        for egg_id, chance in chances.items():
            egg = definition(egg_id)
            rates = rarity_pool(egg_id, perks=perks, sanctuary={'celestial_observatory': 1})
            released = sum(RELEASE_VALUES[r] * w for r, w in rates.items())
            merchant = item_sell_price(egg, merchant=True)
            max_liquid = max(released, merchant)
            assert chance <= events.MAX_NEST_CHANCE
            assert chance * max_liquid <= base_gold * events.NEST_RELEASE_BUDGET + 1e-8
            assert chance * egg['price'] <= base_gold * events.NEST_SHOP_BUDGET + 1e-8
            assert egg['min_level'] <= level
            assert egg['grade'] in ('common', 'uncommon', 'rare')
            eggs.append(dict(id=egg_id, family=egg['family'], grade=egg['grade'],
                             min_level=egg['min_level'], price=egg['price'],
                             maximum_bonus_rates=rates, max_bonus_expected_release=released,
                             merchant_resale=merchant, offer_chance=chance,
                             max_expected_liquid_value=chance * max_liquid,
                             observed_offers=found[egg_id]))
        assert abs(sum(found.values()) - expected_offers) <= max(5, 6 * math.sqrt(variance))
        regions.append(dict(destination=destination, trainer_gate=level, base_gold=base_gold,
                            seeds=samples, observed_offers=sum(found.values()),
                            observed_offer_rate=sum(found.values()) / samples,
                            expected_offer_rate=expected_offers / samples,
                            max_expected_liquid_budget=base_gold * events.NEST_RELEASE_BUDGET,
                            eggs=eggs))
    return dict(seed_count=samples * len(BIOMES),
                rules=dict(max_eggs_per_trip=1, replaces='one material bundle on explicit choice',
                           roll='once at departure, independent SHA-256 seed stream; persisted',
                           maximum_offer_probability=events.MAX_NEST_CHANCE,
                           max_added_liquid_value_fraction=events.NEST_RELEASE_BUDGET,
                           max_expected_shop_price_fraction=events.NEST_SHOP_BUDGET,
                           normal_stop_count=3, optional_deep_stop_count=4,
                           old_saved_trips='unchanged; no offer is added'),
                notes=['Liquid budget conservatively uses the greater of merchant resale and hatch release with every eligible bonus.',
                       'The budget ignores the material forfeited, so actual added value is smaller.',
                       'Offers require an eligible clue event; observed rates assume players find a clue.',
                       'Low-region eggs and chances do not improve with trainer level.'],
                destinations=regions, passed=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=10000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f"Validated {result['seed_count']:,} departure seeds across {len(result['destinations'])} destinations.")
    print(args.output.resolve())
