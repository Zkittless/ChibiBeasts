"""Shared combat rules for wild battles, spars, PvP, ranked and raid attacks."""
import random
import asyncio
from utils.type_chart import get_type_multiplier
active_states = {}
state_lock = asyncio.Lock()
STATUS_EFFECTS = {'poison': {'emoji': '☠️', 'verb': 'is poisoned'}, 'burn': {'emoji': '🔥', 'verb': 'is burning'}, 'freeze': {'emoji': '❄️', 'verb': 'is frozen', 'skip_turns': 1}, 'sleep': {'emoji': '💤', 'verb': 'is asleep', 'skip_turns': 2}, 'paralyze': {'emoji': '⚡', 'verb': 'is paralyzed'}, 'slow': {'emoji': '🕸️', 'verb': 'is slowed'}, 'blind': {'emoji': '👁️', 'verb': 'is blinded'}, 'weaken': {'emoji': '🔻', 'verb': 'is weakened'}, 'blight': {'emoji': '💜', 'verb': 'is blighted'}}

def fraction(number):
    return number / 100 if number > 1 else number

def passive(state):
    return (state.get('divine_passive') or {}).get('passive_effect', {})

def pid(state):
    return (state.get('divine_passive') or {}).get('passive_id', '')

def heal(state, amount):
    if state.get('status') == 'blight' or state['hp'] <= 0:
        return 0
    gained = min(amount, state['max_hp'] - state['hp'])
    state['hp'] += gained
    return gained

def build_state(data, row, perks=(), gear=(), buffs=None):
    state = {'id': row.get('id'), 'rarity': data['rarity'], 'name': row.get('nickname') or data['name'], 'level': row.get('level', 1), **{k: row[k] for k in ('hp', 'max_hp', 'attack', 'defense', 'speed', 'mana', 'max_mana')}, 'moves': data['moves'], 'move_effects': data.get('move_effects', {}), 'ultimate': data['ultimate'], 'beast_type': data['type'], 'image_url': data.get('image_url', ''), 'status': None, 'status_turns': 0, 'ultimate_charges': (buffs or {}).get('ultimate_charges', 0), 'divine_passive': data.get('divine_passive') or {}, 'perks': {p['perk_id'] for p in perks if p.get('equipped')}, 'action_count': 0, 'dp_crit_charges': 0}
    happiness = row.get('happiness', 100)
    mult = 0.9 + min(70, max(0, happiness - 30)) / 70 * 0.2
    bond = 1 + min(100, row.get('bond', 0)) * 0.001 if data.get('starter_family') else 1
    for stat in ('attack', 'defense', 'speed'):
        state[stat] = max(1, int(state[stat] * mult * bond))
    for item in gear:
        apply_gear(state, item.get('effect', {}))
    buffs = buffs or {}
    state['defense'] = int(state['defense'] * (1 + buffs.get('next_defense_boost', 0) / 100))
    state['speed'] = int(state['speed'] * (1 + buffs.get('next_speed_boost', 0) / 100))
    state['damage_multiplier'] = buffs.get('damage_multiplier', 1.0) * (1 + row.get('damage_bonus', 0) / 100)
    state['status_ward'] = bool(buffs.get('status_ward_charges', 0))
    state['mana'] = min(state['mana'], max(20, int(state['max_mana'] * 0.4)))
    if 'genesis_spark' in state['perks']:
        state['mana'] = min(state['max_mana'], state['mana'] + 20)
    state['base_speed'] = state['speed']
    state['base_attack'] = state['attack']
    state['shield'] = int(state['max_hp'] * passive(state).get('shield_percent', 0) / 100)
    return state

def apply_gear(state, effect):
    for key, stat in (('speed', 'speed'), ('attack', 'attack'), ('defense', 'defense'), ('hp_flat', 'max_hp')):
        if key in effect:
            state[stat] += effect[key]
            if stat == 'max_hp':
                state['hp'] += effect[key]
    for stat in ('speed', 'attack', 'defense', 'hp'):
        key = stat + '_percent'
        target = 'max_hp' if stat == 'hp' else stat
        if key in effect:
            bonus = int(state[target] * effect[key] / 100)
            state[target] = max(1, state[target] + bonus)
            if stat == 'hp':
                state['hp'] += bonus
    if state['beast_type'] == 'water':
        state['attack'] += int(state['attack'] * effect.get('water_attack_percent', 0) / 100)
    for key, value in effect.items():
        if key not in {'attack', 'defense', 'speed', 'hp_flat', 'attack_percent', 'defense_percent', 'speed_percent', 'hp_percent', 'water_attack_percent'}:
            state[key] = max(state.get(key, 0), value)

def apply_status(target, status, source=None, log=None):
    log = log if log is not None else []
    if target['hp'] <= 0 or target.get('status') or passive(target).get('status_immune') or passive(target).get('debuff_immune'):
        return False
    if status in {'slow', 'paralyze'} and passive(target).get('immune_to_slow'):
        return False
    if status == 'burn' and target.get('burn_immunity'):
        return False
    if target.pop('status_ward', False):
        log.append(f"✨ {target['name']}'s Aether ward stops {status}.")
        return False
    if 'tether_of_fate' in target.get('perks', set()) and random.random() < 0.25:
        return False
    if source is not None and pid(target) == 'karmic_echo' and (random.random() < passive(target).get('status_reflect_chance', 0.4)):
        return apply_status(source, status, None, log)
    target['status'] = status
    target['status_turns'] = {'freeze': 1, 'sleep': 2}.get(status, 3)
    if status=='blind' and source is not None:
        target['status_turns']=passive(source).get('blind_turns',3)
        target['blind_miss_chance']=passive(source).get('blind_miss_chance',.3)
    log.append(f"{STATUS_EFFECTS[status]['emoji']} {target['name']} {STATUS_EFFECTS[status]['verb']}.")
    return True

def start_battle(a, b, log):
    for state, opp in ((a, b), (b, a)):
        effect = passive(state)
        if effect.get('enemy_speed_reduce_percent'):
            opp['gravity_turns'] = effect.get('turns', 3)
            opp['gravity_multiplier'] = 1 - effect['enemy_speed_reduce_percent'] / 100
        if state.get('speed_debuff_on_battle_start_chance') and random.random() < state['speed_debuff_on_battle_start_chance'] / 100:
            apply_status(opp, 'slow', state, log)
        if state.get('shield'):
            log.append(f"🛡️ {state['name']} begins with {state['shield']} shield.")

def effective_speed(state):
    if passive(state).get('always_first'):
        return float('inf')
    mult = 0.5 if state.get('status') in {'paralyze', 'slow'} else 1
    if state.get('gravity_turns', 0) > 0:
        mult *= state.get('gravity_multiplier', 1)
    return state['speed'] * mult

def begin_turn(state, opp, log):
    state['action_count'] += 1
    effect = passive(state)
    if state.get('gravity_turns', 0) > 0:
        state['gravity_turns'] -= 1
    for stat, key in (('attack', 'attack_stack_percent'), ('speed', 'speed_stack_percent')):
        if effect.get(key):
            state[stat] += max(1, int(state[stat] * effect[key] / 100))
    if effect.get('stat_drain_percent'):
        for stat in ('attack', 'defense', 'speed'):
            if not passive(opp).get('debuff_immune'):
                opp[stat] = max(1, int(opp[stat] * (1 - effect['stat_drain_percent'])))
    regen = effect.get('self_mana_restore_percent', 0)
    state['mana'] = min(state['max_mana'], state['mana'] + int(state['max_mana'] * regen / 100))
    if state.get('hp_regen_percent_per_round'):
        heal(state, max(1, int(state['max_hp'] * state['hp_regen_percent_per_round'] / 100)))
    status = state.get('status')
    if status in {'burn', 'poison', 'blight'}:
        damage = max(1, int(state['max_hp'] * {'burn': 0.08, 'poison': 0.05, 'blight': 0.06}[status]))
        take_damage(state, damage, log)
        log.append(f"{state['name']} takes {damage} {status} damage.")
    if state['hp'] <= 0:
        return False
    if status in {'freeze', 'sleep'}:
        state['status_turns'] -= 1
        if state['status_turns'] <= 0:
            state['status'] = None
        log.append(f"{state['name']} cannot act this turn.")
        return False
    return True

def move_spec(state, name, ultimate=False):
    if ultimate:
        return {'power': 2.5, 'mana_cost': 50, 'description': 'A charged signature attack.'}
    return state.get('move_effects', {}).get(name, {'power': 1, 'mana_cost': 0, 'mana_restore': 10, 'description': 'Reliable attack.'})

def can_use(state, name, ultimate=False):
    return state['mana'] >= move_spec(state, name, ultimate).get('mana_cost', 0) and (not ultimate or state.get('ultimate_charges', 0) >= 3)

def calc_damage(attacker, defender, move, is_ultimate=False, perks=None):
    spec = move_spec(attacker, move, is_ultimate)
    power = spec.get('power', 1)
    if power <= 0:
        return (0, False, 1.0, 0)
    if attacker.get('status') == 'blind' and random.random() < attacker.get('blind_miss_chance',.3):
        return (0, False, 1.0, 0)
    effect = passive(attacker)
    defense = defender['defense']
    if effect.get('true_damage') or random.random() < effect.get('defense_pierce_chance', 0):
        defense = 0
    else:
        defense *= 1 - effect.get('defense_pierce_percent', 0)
    mult = get_type_multiplier(attacker.get('beast_type', ''), defender.get('beast_type', ''))
    if effect.get('ignore_type_disadvantage') or effect.get('fire_resistance_ignore') or (pid(attacker) == 'boundary_break' and attacker['speed'] >= attacker.get('base_speed', attacker['speed']) * effect.get('resistance_ignore_speed_threshold',200)/100):
        mult = max(1, mult)
    perk_ids = attacker.get('perks', set()) | {p['perk_id'] for p in perks or [] if p.get('equipped')}
    crit = random.random() < 0.1 + (0.1 if 'spellbound_focus' in perk_ids else 0) + attacker.get('crit_chance', 0) / 100
    if defender.get('crit_immunity'):
        crit = False
    charge = 0
    crit_multiplier = 1.5 if crit else 1
    if pid(attacker) == 'critical_mass':
        charge = int(not crit and effect.get('crit_charge_on_miss',True))
        if attacker.get('dp_crit_charges', 0) + charge >= effect.get('charges_needed', 3):
            crit = True
            crit_multiplier = effect.get('charged_crit_multiplier', 3)
            charge = -3
    if defender.get('crit_immunity'):
        crit = False
        crit_multiplier = 1
    if is_ultimate:
        power = effect.get('ultimate_damage_mod', 2.5)
        if 'genesis_spark' in perk_ids:
            power *= 1.1
    damage = attacker['attack'] * 100 / (100 + max(0, defense)) * mult * power * crit_multiplier
    damage *= attacker.get('damage_multiplier', 1) * attacker.get('focus', 1)
    if attacker.get('status') == 'weaken':
        damage *= 0.75
    if attacker.get('attack_double_below_25hp') and attacker['hp'] <= attacker['max_hp'] * 0.25:
        damage *= 2
    if effect.get('void_damage_percent'):
        damage += defender['max_hp'] * effect['void_damage_percent']
    return (max(1, int(damage * random.uniform(0.85, 1.15))), crit, mult, charge)

def take_damage(target, damage, log, source=None):
    """The only HP mutation for a resolved hit; survival always sees final damage."""
    amount = min(target['hp'], max(0, damage))
    fatal = damage >= target['hp'] and damage > 0
    effect = passive(target)
    if fatal and (not target.get('revive_used')):
        revive = effect.get('revive_heal_percent', 0) or effect.get('revive_hp_percent', 0)
        if revive:
            target['revive_used'] = True
            target['hp'] = max(1, int(target['max_hp'] * fraction(revive)))
            target['status'] = None
            if effect.get('revive_mana_full'):
                target['mana'] = target['max_mana']
            target['attack'] += int(target['attack'] * effect.get('revive_attack_boost', 0) / 100)
            log.append(f"🔥 {target['name']} rises again with {target['hp']} HP.")
            return amount
        if target.get('time_rewind_on_fatal_once'):
            target['revive_used'] = True
            target['hp'] = max(1, int(target['max_hp'] * 0.3))
            log.append(f"⏳ {target['name']} rewinds the fatal hit.")
            return amount
        if effect.get('survive_one_shot') or 'aura_of_phoenix' in target.get('perks', set()):
            target['revive_used'] = True
            target['hp'] = 1
            log.append(f"🔥 {target['name']} holds on at 1 HP.")
            return amount
    target['hp'] = max(0, target['hp'] - damage)
    if fatal and target.get('death_explosion_fire') and (source is not None):
        take_damage(source, int(target['max_hp'] * 0.3), log)
    return amount

def resolve_hit(attacker, defender, damage, log, *, allow_evasion=True):
    effect = passive(defender)
    dodge = max(defender.get('evasion_percent', 0) / 100, effect.get('dodge_chance', 0))
    if allow_evasion and dodge and (random.random() < dodge):
        log.append(f"💨 {defender['name']} evades — 0 damage.")
        return 0
    if defender.get('guard'):
        damage = int(damage * (1 - defender.pop('guard') / 100))
    damage = int(damage * (1 - defender.get('damage_reduction_flat_percent', 0) / 100))
    damage = int(damage * (1 - effect.get('damage_reduction_flat', 0) / 100))
    if attacker.get('beast_type') == 'water':
        damage = int(damage * (1 - defender.get('water_resist_percent', 0) / 100))
    absorbed = min(damage, defender.get('shield', 0))
    defender['shield'] = defender.get('shield', 0) - absorbed
    damage -= absorbed
    actual = take_damage(defender, max(0, damage), log, attacker)
    if actual:
        if effect.get('absorb_to_attack_percent'):
            defender['attack'] += int(actual * effect['absorb_to_attack_percent'] / 100)
        if pid(defender) == 'bifrost_surge' and defender['hp'] <= defender['max_hp'] * effect.get('threshold_percent',50)/100 and (not defender.get('surge_used')):
            defender['surge_used'] = True
            defender['attack'] = int(defender['attack'] * (1+effect.get('attack_bonus',30)/100))
            defender['speed'] = int(defender['speed'] * (1+effect.get('speed_bonus',20)/100))
        reflect = defender.get('reflect_damage_percent', 0) / 100
        if random.random() < effect.get('reflect_chance', 0):
            reflect += effect.get('reflect_percent', 0) / 100
        if reflect:
            take_damage(attacker, int(actual * reflect), log)
        if defender.get('thorns_poison_on_hit_chance') and random.random() < defender['thorns_poison_on_hit_chance'] / 100:
            apply_status(attacker, 'poison', defender, log)
    return actual

def resolve_move(attacker, defender, name, ultimate, log):
    spec = move_spec(attacker, name, ultimate)
    if not can_use(attacker, name, ultimate):
        name = attacker['moves'][0]
        ultimate = False
        spec = move_spec(attacker, name)
    attacker['mana'] -= spec.get('mana_cost', 0)
    if ultimate:
        attacker['ultimate_charges'] = 0
    attacker['mana'] = min(attacker['max_mana'], attacker['mana'] + spec.get('mana_restore', 0))
    if spec.get('guard_percent'):
        attacker['guard'] = spec['guard_percent']
        attacker['focus'] = spec.get('focus_multiplier', 1)
        if pid(attacker) == 'living_roots':
            heal(attacker, int(attacker['max_hp'] * 0.08))
        log.append(f"🛡️ {attacker['name']} uses {name}: protects its next hit and prepares a stronger strike.")
        return 0
    if spec.get('heal_percent'):
        if spec.get('cleanse'):
            attacker['status'] = None
        amount = spec['heal_percent'] + (8 if pid(attacker) == 'gentle_change' else 0)
        restored = heal(attacker, int(attacker['max_hp'] * amount / 100))
        log.append(f"🌿 {attacker['name']} uses {name}: +{restored} HP and status cleared.")
        return 0
    if pid(attacker) == 'sacred_mending':
        attacker['mending_hits'] = attacker.get('mending_hits', 0) + 1
        if attacker['mending_hits'] % 3 == 0:
            restored = heal(attacker, int(attacker['max_hp'] * passive(attacker).get('third_hit_heal_percent',30)/100))
            log.append(f'✨ Sacred Mending restores {restored} HP instead of attacking.')
            return 0
    damage, crit, mult, charge = calc_damage(attacker, defender, name, ultimate)
    damage=int(damage*attacker.pop('bonus_power',1))
    attacker['focus'] = 1
    attacker['dp_crit_charges'] = max(0, attacker.get('dp_crit_charges', 0) + charge)
    actual = resolve_hit(attacker, defender, damage, log) if damage > 0 else 0
    log.append(f"{('⭐ ' if crit else '')}{attacker['name']} uses {name} → {actual} damage{(' · effective' if mult >= 2 else ' · resisted' if mult < 1 else '')}.")
    if not actual:
        return 0
    effect = passive(attacker)
    lifesteal = fraction(effect.get('lifesteal_percent', 0)) + attacker.get('lifesteal_percent', 0) / 100
    if lifesteal:
        heal(attacker, int(actual * lifesteal))
    if spec.get('status'):
        apply_status(defender, spec['status'], attacker, log)
    if ultimate and effect.get('ultimate_status_guaranteed'):
        apply_status(defender, random.choice(['burn', 'poison', 'weaken']), attacker, log)
    if random.random() < effect.get('blind_chance', 0):
        apply_status(defender, 'blind', attacker, log)
    for key, status in (('burn_on_hit_chance', 'burn'), ('stun_on_hit_chance', 'freeze'), ('blight_on_hit_chance', 'blight')):
        if attacker.get(key) and random.random() < attacker[key] / 100:
            apply_status(defender, status, attacker, log)
    if crit and attacker.get('petrify_on_crit_chance') and (random.random() < attacker['petrify_on_crit_chance'] / 100):
        apply_status(defender, 'freeze', attacker, log)
    attacker['mana'] = min(attacker['max_mana'], attacker['mana'] + attacker.get('mana_regen_on_hit', 0))
    if effect.get('speed_drain_per_hit') and (not passive(defender).get('debuff_immune')):
        defender['speed'] = max(int(defender.get('base_speed', defender['speed']) * (1 - effect.get('speed_drain_max', 0.5))), int(defender['speed'] * (1 - effect['speed_drain_per_hit'])))
    return actual

def end_turn(state, opp, log):
    effect = passive(state)
    if effect.get('corruption_drain_percent') and opp['hp'] > 1:
        opp['hp'] = max(1, opp['hp'] - int(opp['max_hp'] * effect['corruption_drain_percent']))
    if effect.get('regen_percent'):
        heal(state, int(state['max_hp'] * effect['regen_percent'] / 100))
    if state.get('status') not in {None, 'freeze', 'sleep'}:
        state['status_turns'] -= 1
        if state['status_turns'] <= 0:
            state['status'] = None

def bonus_action(state):
    effect = passive(state)
    if pid(state) == 'stitch_step' and state['action_count'] % effect.get('interval',3) == 0:
        state['mana'] = min(state['max_mana'], state['mana'] + 15)
        return True
    if pid(state) == 'fox_sovereign' and state['action_count'] % effect.get('nine_tail_strike_interval', 3) == 0:
        state['bonus_power']=effect.get('nine_tail_multiplier',9)
        return True
    return random.random() < effect.get('double_turn_chance', 0)
