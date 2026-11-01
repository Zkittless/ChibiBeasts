"""Bounded PvE decisions from visible combat state, without consuming battle RNG.

Only versioned party encounters opt in. PvP, raids and old saves keep their AI.
"""
from utils import combat


class _AverageRoll:
    def random(self):
        return .5

    def uniform(self, low, high):
        return (low + high) / 2


def damage_estimate(attacker, defender, name, ultimate=False):
    """A normal hit estimate, not a prediction of the next random roll."""
    attacker = {**attacker, 'perks': set(attacker.get('perks', ()))}
    with combat.using_rng(_AverageRoll()):
        damage = combat.calc_damage(attacker, defender, name, ultimate)[0]
    damage *= 1 - defender.get('guard', 0) / 100
    damage *= 1 - defender.get('damage_reduction_flat_percent', 0) / 100
    damage *= 1 - combat.passive(defender).get('damage_reduction_flat', 0) / 100
    if attacker.get('beast_type') == 'water':
        damage *= 1 - defender.get('water_resist_percent', 0) / 100
    return max(0, int(damage) - defender.get('shield', 0))


def choose(state, opponent):
    """Finish safe KOs, protect against charged attacks, and spend skills wisely.

    Healing/hard control remain once per foe. Two defensive actions per foe and
    requiring unused focus prevent guard loops; enemies cannot inspect a queued
    player action, reserves, future waves or RNG.
    """
    moves = [(name, False) for name in state['moves']]
    moves.append((state['ultimate'], True))
    available = []
    for name, ult in moves:
        spec = combat.move_spec(state, name, ult)
        if not combat.can_use(state, name, ult):
            continue
        if spec.get('heal_percent') and state.get('_trial_heals', 0) >= 1:
            continue
        if spec.get('status') in {'freeze', 'sleep'} and state.get('_trial_controls', 0) >= 1:
            continue
        available.append((name, ult))
    attacks = [(name, ult) for name, ult in available
               if combat.move_spec(state, name, ult).get('power', 1) > 0]
    basic = (state['moves'][0], False)
    for name, ult in attacks:
        if damage_estimate(state, opponent, name, ult) * .85 >= opponent['hp']:
            return name, ult
    if state.get('beast_id') == 'hexpurr' and ('Lanterns Out', False) in available:
        return 'Lanterns Out', False
    healing = next(((name, ult) for name, ult in available
                    if combat.move_spec(state, name, ult).get('heal_percent')), None)
    if (healing and state.get('_trial_heals', 0) < 1
            and state['hp'] < state['max_hp'] * .5
            and (state.get('status') != 'blight' or combat.move_spec(state, *healing).get('cleanse'))):
        return healing
    guard = next(((name, ult) for name, ult in available
                  if combat.move_spec(state, name, ult).get('guard_percent')), None)
    charged = combat.can_use(opponent, opponent['ultimate'], True)
    if (guard and state.get('_pve_guards', 0) < 2 and not state.get('guard')
            and state.get('focus', 1) <= 1
            and (charged or opponent.get('focus', 1) > 1)):
        return guard
    if combat.can_use(state, state['ultimate'], True) and not opponent.get('guard'):
        return state['ultimate'], True
    for name, ult in attacks:
        spec = combat.move_spec(state, name, ult)
        status = spec.get('status')
        if (status and combat.can_receive_status(opponent, status)
                and (status not in {'freeze', 'sleep'} or state.get('_trial_controls', 0) < 1)
                and not opponent.get('guard')):
            return name, ult
    return basic
