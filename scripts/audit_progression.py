"""Seeded, database-free progression audit using production combat and XP rules.

This is an action-budget model, not a measured human play session. Battles use
the production engine; calendar days, route choice and between-battle recovery
are explicit assumptions. No connection to a player database is ever opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import combat
from utils.catalog import load_catalog
from utils.duels import ai_move
from utils.economy_balance import recipe_level
from utils.gameplay import STARTER_FORMS
from utils.habitats import (BIOMES, encounter_difficulty, encounter_level, material_pool,
                            pick_encounter, starter_training_destination)
from utils.progression import (activity_beast_xp, advance_beast_xp, advance_trainer_xp,
                               companion_growth, encounter_beast_xp, encounter_trainer_xp,
                               evolution_blockers)
from cogs.battle import LEVEL_MILESTONES, NPC_TIERS, build_pve_beast_state

BEASTS = load_catalog('beasts.json')['beasts']
EQUIPMENT = load_catalog('equipment.json')
GEAR = {**EQUIPMENT['equipment'], **EQUIPMENT['runes']}
CARROT = load_catalog('items.json')['items']['gnome_grown_carrots']


@lru_cache(maxsize=None)
def _enemy_template(species, level):
    return build_pve_beast_state(BEASTS[species], level)


def enemy_state(species, level):
    # Production's builder loads a private copy of the entire roster. Reuse only
    # its pristine output; every fight receives fresh, independent mutable state.
    return deepcopy(_enemy_template(species, level))


@dataclass(frozen=True)
class Profile:
    explores: int
    challenges: int
    plays: int = 1
    npc: bool = False
    trains: int = 0
    feeds: int = 0


PROFILES = {
    'casual': Profile(6, 2),
    'active': Profile(10, 4, plays=2, npc=True),
    'casual_care': Profile(6, 2, plays=2, npc=True, trains=2, feeds=1),
}


def quantiles(values):
    if not values:
        return {'p10': None, 'median': None, 'p90': None}
    ordered = sorted(values)
    return {'p10': ordered[int((len(ordered)-1)*.1)],
            'median': statistics.median(ordered),
            'p90': ordered[int((len(ordered)-1)*.9)]}


def companion_state(species, level, *, bond=0, happiness=100, charges=0, gear=(), extras=None, perks=()):
    """Build the same row arithmetic as storage, including ancestral growth."""
    data = BEASTS[species]
    growth = companion_growth(species, level, BEASTS)
    stats = {stat: value + growth[stat] + (extras or {}).get(stat, 0)
             for stat, value in data['base_stats'].items()}
    row = {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'],
           'level': level, 'happiness': happiness, 'bond': bond}
    return combat.build_state(data, row, perks=perks,
                              gear=[GEAR[item] for item in gear], buffs={'ultimate_charges': charges})


def resolve_battle(states, personality='wild', *, player_personality='optimal'):
    """Synchronous copy of the production duel's combat loop, with no UI/I/O.

    The parity test compares it to utils.duels._duel under identical seeds.
    No outcome or damage rolls are replaced with a stub.
    """
    log = []
    metrics = Counter()
    combat.start_battle(*states, log)
    for round_number in range(1, 21):
        order = sorted(range(2), key=lambda i: combat.effective_speed(states[i]), reverse=True)
        for index in order:
            state, enemy = states[index], states[1-index]
            if min(s['hp'] for s in states) <= 0:
                break
            if combat.begin_turn(state, enemy, log):
                move, ultimate = ai_move(state, enemy, player_personality if index == 0 else personality)
                spec = combat.move_spec(state, move, ultimate)
                metrics['player_actions' if index == 0 else 'enemy_actions'] += 1
                metrics['ultimates'] += ultimate
                metrics['guards'] += bool(spec.get('guard_percent'))
                metrics['heals'] += bool(spec.get('heal_percent'))
                combat.resolve_move(state, enemy, move, ultimate, log)
                if state['hp'] > 0 and enemy['hp'] > 0 and combat.bonus_action(state):
                    combat.resolve_move(state, enemy, state['moves'][0], False, log, grant_charge=False)
                    metrics['bonus_actions'] += 1
            else:
                metrics['skipped_turns'] += 1
            combat.end_turn(state, enemy, log)
            for checked in states:
                assert 0 <= checked['hp'] <= checked['max_hp'], checked
                assert 0 <= checked['mana'] <= checked['max_mana'], checked
        if min(s['hp'] for s in states) <= 0:
            break
    timed_out = min(s['hp'] for s in states) > 0
    if states[0]['hp'] <= 0 and states[1]['hp'] <= 0:
        winner = None
    elif states[0]['hp'] <= 0:
        winner = 1
    elif states[1]['hp'] <= 0:
        winner = 0
    else:
        ratios = [s['hp']/s['max_hp'] for s in states]
        winner = None if abs(ratios[0]-ratios[1]) < 1e-6 else int(ratios[1] > ratios[0])
    clean = winner == 0 and not timed_out and states[0]['hp'] > 0 and states[1]['hp'] <= 0
    return {'winner': winner, 'clean_win': clean, 'timed_out': timed_out,
            'rounds': round_number, 'metrics': dict(metrics), 'log': log}


def battle_rewards(level, trainer_level, enemy_level, difficulty, result, *, expedition=False, destination=None):
    """Production full rewards require a real, surviving knockout victory."""
    effort = 1 if result['clean_win'] else .25
    beast = encounter_beast_xp(level, enemy_level, difficulty=difficulty, effort=effort)
    if expedition and not result['clean_win']:
        midpoint = sum(BIOMES[destination]['level_range']) // 2
        trainer = encounter_trainer_xp(trainer_level, midpoint, effort=.55)
    else:
        trainer = encounter_trainer_xp(trainer_level, enemy_level, difficulty=difficulty, effort=effort)
    return beast, trainer


@dataclass
class Journey:
    starter: str
    profile: str
    seed: int
    species: str = ''
    level: int = 1
    exp: int = 0
    trainer: int = 1
    trainer_exp: int = 0
    bond: int = 0
    charges: int = 0
    gold: int = 500
    materials: Counter = field(default_factory=Counter)
    gear: list = field(default_factory=list)
    extras: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)
    fights: list = field(default_factory=list)
    first_ten: dict | None = None
    awakening: dict | None = None

    def __post_init__(self):
        self.species = self.starter

    def state(self):
        return companion_state(self.species, self.level, bond=self.bond, charges=self.charges,
                               gear=self.gear, extras=self.extras)

    def trainer_xp(self, amount):
        old = self.trainer
        self.trainer, self.trainer_exp = advance_trainer_xp(self.trainer, self.trainer_exp, amount)
        self.gold += sum(LEVEL_MILESTONES[n]['reward_gold'] for n in range(old+1, self.trainer+1)
                         if n in LEVEL_MILESTONES)

    def beast_xp(self, amount):
        self.level, self.exp = advance_beast_xp(self.level, self.exp, amount)

    def craft(self):
        # Consume real explored materials; no gifted entry gear in journeys.
        for item in ('iron_dust_chip', 'gnome_iron_plating'):
            recipe = GEAR[item]
            if item in self.gear or self.trainer < recipe_level(recipe):
                continue
            if all(self.materials[key] >= amount for key, amount in recipe['recipe'].items()):
                self.materials.subtract(recipe['recipe'])
                self.gear.append(item)
                self.counts['crafts'] += 1

    def try_awakening(self, day):
        if self.awakening:
            return
        row = {'level': self.level, 'bond': self.bond, 'happiness': 100, 'hp': 1}
        evolution = BEASTS[self.species].get('evolution')
        if evolution and not evolution_blockers(row, self.trainer, evolution):
            self.species = evolution['evolves_to']
            self.awakening = {'day': day, 'level': self.level, 'trainer_level': self.trainer,
                              'bond': self.bond, 'max_hp': self.state()['max_hp'],
                              'action_counts': dict(self.counts)}


def destination_for(level, trainer):
    """Use the deepest unlocked band whose lower edge is within one level."""
    possible = [key for key, region in BIOMES.items()
                if region['min_level'] <= trainer and region['level_range'][0] <= level+1]
    return max(possible, key=lambda key: BIOMES[key]['level_range'][0])


def training_destination(model):
    """Follow the recommended starter training grounds, then try new regions."""
    recommendation = starter_training_destination(BEASTS[model.species], model.level, model.trainer)
    return recommendation or destination_for(model.level, model.trainer)


def journey(starter, profile_name, seed, *, max_days=20, destination_policy=None):
    random.seed(f'progression-v1:{starter}:{profile_name}:{seed}')
    model = Journey(starter, profile_name, seed)
    profile = PROFILES[profile_name]
    for day in range(1, max_days+1):
        # Spaced across the day; this represents successful recovery between
        # fights and does not claim that all actions fit in one short session.
        actions = ['explore'] * profile.explores + ['challenge'] * profile.challenges
        for kind in actions:
            destination = (destination_policy(model) if destination_policy else
                           destination_for(model.level, model.trainer))
            beast = pick_encounter(BIOMES[destination]['weights'], destination)
            enemy_level = encounter_level(destination, model.level)
            enemy = enemy_state(beast['id'], enemy_level)
            actor = model.state()
            difficulty = encounter_difficulty(actor, enemy)
            pre_level, pre_trainer = model.level, model.trainer
            result = resolve_battle([actor, enemy], 'aggressive' if kind == 'explore' else 'wild')
            beast_xp, trainer_xp = battle_rewards(pre_level, pre_trainer, enemy_level, difficulty, result,
                                                 expedition=kind == 'explore', destination=destination)
            model.beast_xp(beast_xp)
            model.trainer_xp(trainer_xp)
            model.charges = actor['ultimate_charges']
            # Challenge and spar callbacks add one charge after duel settlement.
            if kind == 'challenge':
                model.charges = min(3, model.charges+1)
            if result['clean_win']:
                model.bond = min(100, model.bond+2)
            model.counts[kind] += 1
            model.counts['clean_wins'] += result['clean_win']
            model.counts['battles'] += 1
            if kind == 'explore':
                model.materials[random.choice(material_pool({'id': '', 'type': ''}, destination))] += 1
                gold_level = sum(BIOMES[destination]['level_range']) // 2
                gold = random.randint(gold_level*3, gold_level*6)
                model.gold += int(gold*1.25) if result['clean_win'] else gold
            else:
                model.gold += random.randint(enemy_level*8, enemy_level*15) if result['clean_win'] else random.randint(10, 25)
            model.fights.append({'day': day, 'action': kind, 'destination': destination,
                                  'level': pre_level, 'trainer_level': pre_trainer,
                                  'species': model.species, 'enemy': beast['id'], 'enemy_level': enemy_level,
                                  'difficulty': difficulty, 'clean_win': result['clean_win'],
                                  'timed_out': result['timed_out'], 'rounds': result['rounds'],
                                  'beast_xp': beast_xp, 'trainer_xp': trainer_xp,
                                  'metrics': result['metrics']})
            model.craft()
            model.try_awakening(day)
            if kind == 'explore' and model.counts['explore'] == 10:
                model.first_ten = {'day': day, 'level': model.level, 'trainer_level': model.trainer,
                                   'species': model.species, 'hp': model.state()['max_hp'],
                                   'bond': model.bond, 'action_counts': dict(model.counts)}
        for _ in range(profile.plays):
            model.counts['play'] += 1
            model.bond = min(100, model.bond+3)
            model.beast_xp({'common':8, 'rare':13}.get(BEASTS[model.species]['rarity'], 8))
            model.try_awakening(day)
        if profile.npc:
            options = [tier for tier in NPC_TIERS.values()
                       if tier['trainer'] <= model.trainer and tier['level'] <= model.level+2]
            if options:
                tier = max(options, key=lambda value: value['level'])
                actor = model.state()
                enemy = enemy_state(tier['companion'], tier['level'])
                difficulty = encounter_difficulty(actor, enemy)
                result = resolve_battle([actor, enemy], tier['personality'])
                pre_level, pre_trainer, pre_species = model.level, model.trainer, model.species
                bx, tx = battle_rewards(pre_level, pre_trainer, tier['level'], difficulty, result)
                model.beast_xp(bx)
                model.trainer_xp(tx)
                model.charges = min(3, actor['ultimate_charges']+1)
                model.bond = min(100, model.bond+2*result['clean_win'])
                model.gold += random.randint(100, 300) if result['clean_win'] else 50
                model.counts['npc'] += 1
                model.counts['battles'] += 1
                model.counts['clean_wins'] += result['clean_win']
                model.fights.append({'day':day, 'action':'npc', 'destination':'npc', 'level':pre_level,
                                      'trainer_level':pre_trainer, 'species':pre_species,
                                      'enemy':tier['companion'], 'enemy_level':tier['level'],
                                      'clean_win':result['clean_win'], 'rounds':result['rounds'],
                                      'timed_out':result['timed_out'], 'beast_xp':bx, 'trainer_xp':tx,
                                      'metrics':result['metrics']})
        earned = 0
        for _ in range(profile.trains):
            from utils.economy_balance import TRAIN_COSTS
            cost = TRAIN_COSTS[BEASTS[model.species]['rarity']]
            if model.gold >= cost and model.counts['train'] < 20:
                model.gold -= cost
                model.extras['attack'] += 2 if BEASTS[model.species]['rarity'] == 'rare' else 1
                amount = activity_beast_xp('training', model.level, earned)
                earned += amount
                model.beast_xp(amount)
                model.counts['train'] += 1
                model.bond = min(100, model.bond+1)
        earned = 0
        for _ in range(profile.feeds):
            if model.gold >= CARROT['price']:
                model.gold -= CARROT['price']
                # Match utils.items.use_item's requested EXP, rather than the
                # legacy scaling description retained in older item catalogs.
                requested = CARROT['effect'].get('exp', 0) or 60
                amount = min(requested, max(0, 60-earned))
                earned += amount
                model.beast_xp(amount)
                model.counts['feed'] += 1
        model.try_awakening(day)
        if model.awakening and model.first_ten:
            break
    return model


def fight_summary(rows):
    total = len(rows)
    metrics = Counter()
    for row in rows:
        metrics.update(row.get('metrics', {}))
    return {'battles':total, 'clean_win_rate':round(sum(row['clean_win'] for row in rows)/total, 4),
            'timeout_rate':round(sum(row['timed_out'] for row in rows)/total, 4),
            'rounds':quantiles([row['rounds'] for row in rows]), 'engine_action_counts':dict(metrics)} if total else {}


def build_reachability(gear, trainer):
    sources = {material for key, region in BIOMES.items() if region['min_level'] <= trainer
               for material in material_pool({'id':'','type':''}, key)}
    errors = []
    for item_id in gear:
        item = GEAR[item_id]
        if recipe_level(item) > trainer:
            errors.append(f'{item_id}: requires trainer {recipe_level(item)}, fixture has {trainer}')
        missing = set(item.get('recipe', {})) - sources
        if missing:
            errors.append(f'{item_id}: ingredients lack an accessible wild habitat: {sorted(missing)}')
    return {'valid':not errors, 'errors':errors,
            'scope':'Recipe level and wild ingredient access; separate prepared fixture, not a simulated crafting timeline.'}


def summarize_journeys(models):
    rows = []
    for profile in PROFILES:
        for starter in STARTER_FORMS:
            selected = [model for model in models if model.profile == profile and model.starter == starter]
            completed = [model for model in selected if model.awakening]
            first = [model.first_ten for model in selected if model.first_ten]
            fights = [fight for model in selected for fight in model.fights]
            rows.append({'profile':profile, 'starter':starter, 'runs':len(selected),
                         'awakened':len(completed), 'awakening_day':quantiles([m.awakening['day'] for m in completed]),
                         'awakening_beast_level':quantiles([m.awakening['level'] for m in completed]),
                         'awakening_trainer_level':quantiles([m.awakening['trainer_level'] for m in completed]),
                         'awakening_actions':{key:quantiles([m.awakening['action_counts'].get(key, 0) for m in completed])
                                               for key in ('explore','challenge','npc','play','train','feed','battles','clean_wins')},
                         'first_ten_explorations':{'level':quantiles([s['level'] for s in first]),
                                                  'trainer_level':quantiles([s['trainer_level'] for s in first]),
                                                  'max_hp':max(s['hp'] for s in first),
                                                  'evolved_runs':sum(s['species'] != starter for s in first)},
                         'combat':fight_summary(fights),
                         'combat_before_awakening':fight_summary([r for r in fights if r.get('species') == starter]),
                         'combat_by_destination':{destination:fight_summary([r for r in fights if r['destination'] == destination])
                                                  for destination in sorted({r['destination'] for r in fights})}})
    return rows


def band_matrix(seeds):
    """Checkpoint tests are prepared build fixtures, not simulated lifetime runs."""
    rows = []
    for destination, region in BIOMES.items():
        level = region['level_range'][1]
        trainer = max(region['min_level'], 8 if level >= 15 else 1)
        gear = ['gnome_iron_plating', 'iron_dust_chip']
        if trainer >= 12:
            gear = ['unicorn_healer_vestments', 'iron_dust_chip']
        if trainer >= 30:
            gear = ['dragon_lord_aegis', 'iron_dust_chip']
        reachability = build_reachability(gear, trainer)
        if not reachability['valid']:
            rows.append({'destination':destination, 'level':level, 'trainer_level':trainer,
                         'gear':gear, 'build_reachability':reachability,
                         'combat':{}, 'skipped':'Invalid prepared build; no combat result claimed.'})
            continue
        for starter, form in STARTER_FORMS.items():
            species = form if level >= 15 else starter
            trials = []
            for seed in seeds:
                random.seed(f'band-v1:{destination}:{species}:{seed}')
                beast = pick_encounter(region['weights'], destination)
                enemy = enemy_state(beast['id'], encounter_level(destination, level))
                perks = [{'perk_id':'genesis_spark','equipped':True}] if trainer >= 25 else []
                actor = companion_state(species, level, bond=100, charges=seed%4, gear=gear, perks=perks)
                result = resolve_battle([actor, enemy])
                trials.append(result)
            rows.append({'destination':destination, 'companion':species, 'level':level,
                         'trainer_level':trainer, 'gear':gear,
                         'build_reachability':reachability,
                         'starting_charges':'seed modulo 4 (0,1,2,3 evenly when seeds is a multiple of 4)',
                         'combat':fight_summary(trials)})
    return rows


def source_hashes():
    files = ['scripts/audit_progression.py', 'tests/test_progression_simulation.py',
             'utils/progression.py', 'utils/habitats.py',
             'utils/combat.py','utils/type_chart.py','utils/duels.py','utils/gameplay.py','utils/events.py',
             'utils/expeditions.py','utils/economy_balance.py','utils/db.py',
             'cogs/battle.py','cogs/utilities.py','cogs/economy.py','data/beasts.json',
             'data/equipment.json','data/items.json','data/perks.json','data/materials.json']
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}


def run(seeds, max_days, policy='training'):
    before = source_hashes()
    destination_policy = training_destination if policy == 'training' else None
    models = [journey(starter, profile, seed, max_days=max_days, destination_policy=destination_policy)
              for profile in PROFILES for starter in STARTER_FORMS for seed in seeds]
    matrix = band_matrix(seeds)
    after = source_hashes()
    if before != after:
        raise RuntimeError('Game sources changed during the audit; rerun on the final tree.')
    report = {'schema_version':1, 'generated_at_utc':datetime.now(timezone.utc).isoformat(),
              'git_head':subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
              'source_sha256':after, 'seed_ids':list(seeds), 'max_days':max_days,
              'journey_policy':policy,
              'profiles':{name:vars(profile) for name, profile in PROFILES.items()},
              'assumptions':[
                  'Database-free action-budget simulation. No real or disposable player DB is accessed.',
                  'Every exploration follows tracks -> challenge -> return. Noncombat trips award no companion battle XP and are not represented.',
                  ('Training policy: Woods through companion level 7, Woods Depths from level 8 until first awakening; then the deepest unlocked band with floor at most companion level +1.'
                   if policy == 'training' else 'Aggressive sensitivity policy: always take the deepest unlocked band with floor at most companion level +1.'),
                  'Fights resolve real production move arithmetic, type chart, AI, passives, charges, statuses, speed ordering, guard, healing, and bonus actions, to the production 20-round limit.',
                  'Full XP and +2 battle bond require a surviving clean KO. Losses and HP-judged wins receive production reduced XP. Expedition failure retains its base trainer reward.',
                  'All scheduled actions complete across one calendar day; exploration recharge, challenge cooldown, KO recovery, human input time and healing expense are abstracted. Stored HP/MP fully recover before each fight, then production build_state applies its opening mana cap (normally 40% of maximum, with a 20 MP floor). Ultimate charges carry between fights.',
                  'Happiness stays 100 with daily care. No disposition (starters have none), guild bonuses, paid boosts, quests, achievements, story rewards, daily XP rewards, or recruitment switching.',
                  f'Journeys craft entry armor/rune only after actual random regional materials have been collected and recipe level gates pass. Gold starts at onboarding 500 and includes battle gold and level milestone gold; optional care is affordable-only. Carrots use current catalog effect {CARROT["effect"]["exp"]} XP for {CARROT["price"]} gold, sharing the 60/day feed budget.',
                  'Band checkpoints are separate prepared combat fixtures, not claims of reaching levels 20-50 on the simulated early calendar. Gear respects recipe gates; required materials have reachable wild habitats. No raid gear or catalysts are gifted.',
                  'Checkpoint bond100 and charges cycling0-3 show practiced starters. Genesis Spark is equipped only at trainer25+. Rare starters are compared to actual stronger late-region populations; defeat can indicate a need to recruit/build, not a pacing result.',
              ], 'journey_summary':summarize_journeys(models), 'band_checkpoints':matrix,
              'runs':[{'starter':m.starter,'profile':m.profile,'seed':m.seed,
                       'first_ten':m.first_ten,'awakening':m.awakening,
                       'final_level':m.level,'final_trainer':m.trainer,'action_counts':dict(m.counts),
                       'combat':fight_summary(m.fights)} for m in models],
              'checks':{'first_ten_samples':sum(m.first_ten is not None for m in models),
                        'first_ten_evolved':sum(m.first_ten is not None and m.first_ten['species'] != m.starter for m in models),
                        'first_ten_max_hp':max(m.first_ten['hp'] for m in models if m.first_ten),
                        'awakening_within_two_days':sum(m.awakening is not None and m.awakening['day'] <= 2 for m in models)},
              'example_journeys':[{'starter':m.starter,'profile':m.profile,'seed':m.seed,'fights':m.fights}
                                  for m in models if m.seed == seeds[0]]}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=80, help='Seeds per starter per profile (80 gives 320 runs/profile).')
    parser.add_argument('--seed-start', type=int, default=1000)
    parser.add_argument('--max-days', type=int, default=20)
    parser.add_argument('--policy', choices=('training','deepest'), default='training')
    parser.add_argument('--output', type=Path, default=ROOT.parent/'output/progression-rebalance/progression.json')
    args = parser.parse_args()
    report = run(range(args.seed_start, args.seed_start+args.seeds), args.max_days, args.policy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output':str(args.output.resolve()), 'checks':report['checks'],
                      'summaries':[{'profile':r['profile'],'starter':r['starter'],
                                    'awakening_day':r['awakening_day'],
                                    'clean_win_rate':r['combat']['clean_win_rate']}
                                   for r in report['journey_summary']]}, indent=2))
