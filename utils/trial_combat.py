"""Synchronous, persisted tower combat. No database, Discord I/O or live globals.

Each call consumes local RNG, advances to one player decision or wave boundary,
and returns JSON-safe animation frames. The caller commits before publishing.
"""
from copy import deepcopy
from dataclasses import asdict
import random

from utils import combat, trial_rules
from utils.battle_art import Action, Fighter
from utils.duels import ai_move
from utils.team_combat import PartyBattle, ASSIST_METER


def _json(value):
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items() if k not in {'_party_states', 'closed'}}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json(v) for v in (sorted(value) if isinstance(value, (set, frozenset)) else value)]
    return value


def _tuples(value):
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


class _LocalParty(PartyBattle):
    def __init__(self, payload):
        self.party, self.enemy = deepcopy(payload['party']), deepcopy(payload['enemy'])
        for member in self.party:
            member['perks'] = set(member.get('perks', ()))
        if self.enemy:
            self.enemy['perks'] = set(self.enemy.get('perks', ()))
        self.user_id, self.slot, self.meter = 0, payload['slot'], payload['meter']
        self.entered = set(payload['entered'])
        self.participated = set(payload['participated'])
        self.log = list(payload.get('log', ()))

    def bind(self):
        self.active['_assist_meter'] = round(self.meter * 100 / ASSIST_METER)

    def swap(self, row_id=None, *, forced=False):
        member = next((s for s in self.bench() if row_id is None or s['id'] == row_id), None)
        if member is None:
            return None
        outgoing = self.active
        self.slot = self.party.index(member)
        self.participated.add(member['id'])
        self.bind()
        if member['id'] not in self.entered:
            combat.start_battle_member(member, self.enemy, self.log)
            self.entered.add(member['id'])
        self.log.append(f"{member['name']} {'takes over after the knockout' if forced else 'tags in'} for {outgoing['name']}.")
        return Action(0, f"{outgoing['name']} → {member['name']}", 'swap', journal=tuple(self.log[-2:]),
                      incoming_id=member['id'], outgoing_id=outgoing['id'])


def active(payload):
    return payload['party'][payload['slot']]


def snapshot(payload):
    """Read-only immutable scene for the existing battle renderer."""
    battle = _LocalParty(payload)
    return battle.snapshot() if battle.enemy else ()


def _encode_event(before, action, after):
    return dict(before=_json([asdict(s) for s in before]), action=_json(asdict(action)),
                after=_json([asdict(s) for s in after]))


def decode_event(event):
    def fighter(data):
        return Fighter(**{**data, 'party': tuple(fighter(s) for s in data.get('party', ()))})
    action = {**event['action'], 'journal': tuple(event['action'].get('journal', ()))}
    return tuple(fighter(s) for s in event['before']), Action(**action), tuple(fighter(s) for s in event['after'])


def build_enemy(wave):
    profile = wave.get('profile', trial_rules.PROFILES[wave['kind']])
    state = deepcopy(wave['enemy_template']) if 'enemy_template' in wave else trial_rules.profiled_state(wave['species'], wave['level'], profile)
    state['perks'] = set(state.get('perks', ()))
    ceiling = wave.get('power_cap')
    if ceiling:
        trial_rules.cap_power(state, ceiling)
    state.update(id=-(wave['index']), _trial_heals=0, _trial_controls=0)
    return state


def new_run(party, plan):
    if not 1 <= len(party) <= 3 or len({s['id'] for s in party}) != len(party):
        raise ValueError('Choose one to three different companions.')
    if any(s['hp'] <= 0 for s in party):
        raise ValueError('Every entering companion must be healthy.')
    if not plan.get('waves') or len(plan['waves']) != plan['total']:
        raise ValueError('The saved trial lineup is incomplete.')
    rng = random.Random(f"{plan['seed']}:tower-combat-v1")
    return dict(version=1, plan=deepcopy(plan), party=_json(deepcopy(party)), enemy=None,
                status='between', cleared=0, wave_index=0, slot=0, meter=0, entered=[],
                participated=[], round=0, turn_order=[], cursor=0, phase='idle', awaiting=False,
                rng_state=_json(rng.getstate()), log=[], events=[], reason='')


def _rng(payload):
    result = random.Random()
    result.setstate(_tuples(payload['rng_state']))
    return result


def _flush(payload, battle, rng, events):
    payload.update(party=_json(battle.party), enemy=_json(battle.enemy), slot=battle.slot,
                   meter=battle.meter, entered=sorted(battle.entered),
                   participated=sorted(battle.participated), log=battle.log[-300:],
                   rng_state=_json(rng.getstate()), events=events)
    return events


def _event(events, before, battle, action):
    events.append(_encode_event(before, action, battle.snapshot()))


def _terminal(payload, battle):
    if not battle.alive:
        payload.update(status='lost', reason='Your team needs recovery.', awaiting=False, phase='idle')
        return True
    if battle.enemy['hp'] <= 0:
        payload['cleared'] += 1
        payload.update(wave_index=payload['cleared'], awaiting=False, phase='idle',
                       status='won' if payload['cleared'] == payload['plan']['total'] else 'between')
        return True
    return False


def _enemy_action(battle, events):
    state, opp = battle.enemy, battle.active
    name, ultimate = ai_move(state, opp, 'optimal')
    spec = combat.move_spec(state, name, ultimate)
    if spec.get('heal_percent') and state['_trial_heals'] >= 1 or spec.get('status') in {'freeze', 'sleep'} and state['_trial_controls'] >= 1:
        name, ultimate = state['moves'][0], False
        spec = combat.move_spec(state, name)
    before = battle.snapshot()
    if spec.get('heal_percent'):
        state['_trial_heals'] += 1
    if spec.get('status') in {'freeze', 'sleep'}:
        state['_trial_controls'] += 1
    damage = combat.resolve_move(state, opp, name, ultimate, battle.log)
    kind = 'guard' if spec.get('guard_percent') else 'heal' if spec.get('heal_percent') else 'attack' if name == state['moves'][0] else 'skill'
    _event(events, before, battle, Action(1, name, kind, damage, ultimate, tuple(battle.log[-2:])))
    if state['hp'] > 0 and opp['hp'] > 0 and combat.bonus_action(state):
        before = battle.snapshot()
        damage = combat.resolve_move(state, opp, state['moves'][0], False, battle.log, grant_charge=False)
        _event(events, before, battle, Action(1, state['moves'][0] + ' (bonus)', damage=damage, journal=tuple(battle.log[-2:])))


def _advance(payload, battle, events):
    """An explicit begin/action/end cursor prevents replaying turn effects."""
    limit = payload['plan']['waves'][payload['wave_index']]['round_limit']
    for _ in range(300):
        if payload['status'] != 'fighting':
            return
        if not payload['turn_order']:
            payload['turn_order'] = sorted((0, 1), key=lambda i: combat.effective_speed(battle.states[i]), reverse=True)
            payload.update(cursor=0, phase='begin')
        actor = payload['turn_order'][payload['cursor']]
        if payload['phase'] == 'begin':
            state, opp = (battle.active, battle.enemy) if actor == 0 else (battle.enemy, battle.active)
            payload['turn_member'] = state['id']
            before = battle.snapshot()
            can_act = combat.begin_turn(state, opp, battle.log)
            after = battle.snapshot()
            if before != after or not can_act:
                events.append(_encode_event(before, Action(actor, 'Turn effects' if can_act else 'Turn skipped', 'status', journal=tuple(battle.log[-2:])), after))
            payload['phase'] = 'action' if can_act else 'end'
        if payload['phase'] == 'action':
            if actor == 0 and battle.active['hp'] > 0 and battle.enemy['hp'] > 0:
                payload['awaiting'] = True
                return
            if actor == 1 and battle.enemy['hp'] > 0 and battle.active['hp'] > 0:
                _enemy_action(battle, events)
            payload['phase'] = 'end'
        if payload['phase'] == 'end':
            state = next(s for s in [*battle.party, battle.enemy] if s['id'] == payload['turn_member'])
            opp = battle.enemy if actor == 0 else battle.active
            before = battle.snapshot()
            combat.end_turn(state, opp, battle.log)
            if before != battle.snapshot():
                _event(events, before, battle, Action(actor, 'End-of-turn effects', 'status', journal=tuple(battle.log[-2:])))
            if battle.active['hp'] <= 0 and battle.alive:
                before = battle.snapshot()
                action = battle.replace_knockout()
                if action:
                    _event(events, before, battle, action)
            if _terminal(payload, battle):
                return
            payload['cursor'] += 1
            payload['phase'] = 'begin'
            if payload['cursor'] == 2:
                if payload['round'] >= limit:
                    payload.update(status='lost', awaiting=False, phase='idle', reason='The wave reached its round limit.')
                    return
                payload.update(round=payload['round'] + 1, turn_order=[])
    raise RuntimeError('Trial turn cursor failed to reach a decision.')


def start_wave(payload):
    if payload['status'] != 'between' or payload['cleared'] >= payload['plan']['total']:
        raise ValueError('This trial is not waiting for a new wave.')
    battle, rng, events = _LocalParty(payload), _rng(payload), []
    battle.enemy = build_enemy(payload['plan']['waves'][payload['cleared']])
    if not battle.alive:
        raise ValueError('Your remaining team needs recovery.')
    if battle.active['hp'] <= 0:
        battle.slot = next(i for i, s in enumerate(battle.party) if s['hp'] > 0)
    payload.update(status='fighting', wave_index=payload['cleared'], round=1, cursor=0,
                   turn_order=[], phase='begin', awaiting=False)
    with combat.using_rng(rng):
        before = battle.snapshot()
        combat.start_battle_member(battle.enemy, battle.active, battle.log)
        if battle.active['id'] not in battle.entered:
            combat.start_battle_member(battle.active, battle.enemy, battle.log)
            battle.entered.add(battle.active['id'])
            battle.participated.add(battle.active['id'])
        _event(events, before, battle, Action(1, f"Wave {payload['cleared'] + 1}: {battle.enemy['name']} enters", 'enter'))
        _advance(payload, battle, events)
    return _flush(payload, battle, rng, events)


def legal_actions(payload):
    if payload['status'] != 'fighting' or not payload.get('awaiting'):
        return []
    battle, result = _LocalParty(payload), []
    state = battle.active
    for name in state['moves']:
        result.append(dict(choice=[name, False], label=name, kind='move', enabled=combat.can_use(state, name)))
    result.append(dict(choice=[state['ultimate'], True], label=state['ultimate'], kind='ultimate',
                       enabled=combat.can_use(state, state['ultimate'], True)))
    for member in battle.bench():
        result.append(dict(choice=['swap', member['id']], label=f"Swap: {member['name']}", kind='swap', enabled=True))
        result.append(dict(choice=['assist', member['id']], label=f"Assist: {member['name']}", kind='assist',
                           enabled=battle.can_assist(member)))
    return result


def act(payload, choice):
    if not any(action['enabled'] and action['choice'] == list(choice) for action in legal_actions(payload)):
        raise ValueError('Choose an available action from this turn.')
    battle, rng, events = _LocalParty(payload), _rng(payload), []
    payload['awaiting'] = False
    with combat.using_rng(rng):
        before = battle.snapshot()
        action, bonus = battle.resolve(choice)
        events.append(_encode_event(before, action, bonus[0] if bonus else battle.snapshot()))
        if bonus:
            events.append(_encode_event(*bonus))
        payload['phase'] = 'end'
        _advance(payload, battle, events)
    return _flush(payload, battle, rng, events)
