"""Three-member adventure combat with one active companion per side.

Swaps and assists replace a normal move. The existing duel engine continues to
own solo battles, trainer duels, ranked and raids.
"""
import asyncio
import logging
from contextlib import nullcontext

import discord

from utils import combat, db as storage, duels
from utils.battle_art import Action, realm_key, snapshot
from utils.theme import COLORS
from utils.type_chart import get_type_multiplier

ASSIST_METER = 3
ASSIST_MANA = 12
MAX_ROUNDS = 20
logger = logging.getLogger('chibibeasts.battle')


def assist_effect(state):
    """A healer mends; earth/ice protect; other companions provide a strike.

    Almost every species has a defensive third move. That universal utility
    must not turn every non-healer into the same guard support.
    """
    specs = [combat.move_spec(state, move) for move in state['moves']]
    if any(spec.get('heal_percent') for spec in specs):
        return 'heal'
    if state.get('beast_type') in {'earth', 'ice'} and any(spec.get('guard_percent') for spec in specs):
        return 'guard'
    return 'attack'


class PartyBattle:
    """Small deterministic coordinator; members remain live combat dictionaries."""

    def __init__(self, party, enemy, user_id):
        if not 1 <= len(party) <= 3 or len({s['id'] for s in party}) != len(party):
            raise ValueError('Adventure teams contain one to three different companions.')
        if any(s['hp'] <= 0 for s in party):
            raise ValueError('Every companion must be healthy before the battle starts.')
        self.party, self.enemy, self.user_id = list(party), enemy, user_id
        self.slot = 0
        self.meter = 0
        self.participated = {party[0]['id']}
        self.entered = {party[0]['id']}
        self.log = []
        for state in party:
            state['companion_id'] = state['id']

    @property
    def active(self):
        return self.party[self.slot]

    @property
    def states(self):
        return [self.active, self.enemy]

    @property
    def alive(self):
        return any(state['hp'] > 0 for state in self.party)

    def bind(self):
        self.active['_assist_meter'] = round(self.meter * 100 / ASSIST_METER)
        combat.active_states[self.user_id] = self.active
        combat.active_parties[self.user_id] = self.party

    def snapshot(self):
        active = self.active
        active['_assist_meter'] = round(self.meter * 100 / ASSIST_METER)
        # This metadata is present only during the synchronous snapshot. Item
        # handling deep-copies plain live states, never a recursive party graph.
        active['_party_states'] = self.party
        active['_party_active_id'] = active['id']
        try:
            return snapshot(self.states)
        finally:
            active.pop('_party_states', None)
            active.pop('_party_active_id', None)

    def bench(self):
        return [state for state in self.party if state is not self.active and state['hp'] > 0]

    def can_assist(self, member):
        if member not in self.bench() or self.meter < ASSIST_METER or member['mana'] < ASSIST_MANA or member.get('status') in {'sleep', 'freeze'}:
            return False
        effect = assist_effect(member)
        if effect == 'heal':
            return self.active['hp'] < self.active['max_hp'] and self.active.get('status') != 'blight'
        if effect == 'guard':
            return self.active.get('guard', 0) < 25
        return self.enemy['hp'] > 0

    def swap(self, row_id=None, *, forced=False):
        candidates = self.bench()
        member = next((state for state in candidates if row_id is None or state['id'] == row_id), None)
        if member is None:
            return None
        outgoing = self.active
        self.slot = self.party.index(member)
        self.participated.add(member['id'])
        self.bind()
        if member['id'] not in self.entered:
            # Opening abilities belong to a creature's first field entry. They
            # neither stack upfront from the bench nor refresh on every swap.
            combat.start_battle(member, self.enemy, self.log)
            self.entered.add(member['id'])
        reason = 'takes over after the knockout' if forced else 'tags in'
        self.log.append(f"{member['name']} {reason} for {outgoing['name']}.")
        return Action(0, f"{outgoing['name']} → {member['name']}", 'swap',
                      journal=tuple(self.log[-2:]), incoming_id=member['id'], outgoing_id=outgoing['id'])

    def replace_knockout(self):
        return self.swap(forced=True) if self.active['hp'] <= 0 else None

    def resolve(self, choice):
        """Resolve exactly one player action, returning its clip and any bonus.

        Invalid/stale team choices become the reliable basic attack, like a
        move whose MP was spent by an item while its prompt was visible.
        """
        name, argument = choice
        if name == 'swap':
            action = self.swap(argument)
            if action is not None:
                return action, None
        elif name == 'assist':
            member = next((s for s in self.bench() if s['id'] == argument), None)
            if member is not None and self.can_assist(member):
                self.meter = 0
                member['mana'] -= ASSIST_MANA
                self.participated.add(member['id'])
                effect = assist_effect(member)
                damage = 0
                if effect == 'heal':
                    amount = max(1, int(min(self.active['max_hp'] * .10, member['max_hp'] * .12)))
                    restored = combat.heal(self.active, amount)
                    self.log.append(f"{member['name']} assists: {self.active['name']} recovers {restored} HP.")
                elif effect == 'guard':
                    self.active['guard'] = max(25, self.active.get('guard', 0))
                    self.log.append(f"{member['name']} assists: guards {self.active['name']}'s next hit by 25%.")
                else:
                    multiplier = max(.75, min(1.5, get_type_multiplier(member['beast_type'], self.enemy['beast_type'])))
                    power = min(member['attack'] * .45, self.active['attack'] * .60)
                    amount = max(1, int(power * 100 / (100 + max(0, self.enemy['defense'])) * multiplier))
                    damage = combat.resolve_hit(member, self.enemy, amount, self.log)
                    self.log.append(f"{member['name']} assists with a strike → {damage} damage.")
                self.bind()
                return Action(0, f"{member['name']} — {effect.title()} assist", 'assist', damage,
                              journal=tuple(self.log[-2:]), support_id=member['id'],
                              support_name=member['name'], support_species=member.get('beast_id', ''),
                              support_armor_id=member.get('armor_id', ''), support_effect=effect), None
        if name in {'swap', 'assist'}:
            name, argument = self.active['moves'][0], False
        state, enemy = self.active, self.enemy
        ultimate = bool(argument)
        if not combat.can_use(state, name, ultimate):
            name, ultimate = state['moves'][0], False
        spec = combat.move_spec(state, name, ultimate)
        before_hp = state['hp']
        damage = combat.resolve_move(state, enemy, name, ultimate, self.log)
        if damage > 0 and not ultimate:
            self.meter = min(ASSIST_METER, self.meter + 1)
        self.participated.add(state['id'])
        self.bind()
        kind = 'guard' if spec.get('guard_percent') else 'heal' if spec.get('heal_percent') or state['hp'] > before_hp and not damage else 'attack' if name == state['moves'][0] else 'skill'
        action = Action(0, name, kind, damage, ultimate, tuple(self.log[-2:]))
        bonus = None
        if state['hp'] > 0 and enemy['hp'] > 0 and combat.bonus_action(state):
            before = self.snapshot()
            extra = combat.resolve_move(state, enemy, state['moves'][0], False, self.log, grant_charge=False)
            bonus = (before, Action(0, state['moves'][0] + ' (bonus)', damage=extra,
                                    journal=tuple(self.log[-2:])), self.snapshot())
        return action, bonus


class TeamMoveView(duels.MoveView):
    """The same long-lived router as a duel, plus two swap/assist pairs."""

    def __init__(self, battle):
        self.battle = battle
        self.team_buttons = []
        active = battle.active
        super().__init__(active['moves'], active['ultimate'], battle.user_id, active)
        for index in range(2):
            for kind in ('swap', 'assist'):
                button = discord.ui.Button(style=discord.ButtonStyle.secondary, row=2 + index)
                button.callback = self.team_callback(index, kind)
                self.team_buttons.append(button)
                self.add_item(button)
        self.prepare_team_turn()

    def prepare_team_turn(self):
        active = self.battle.active
        self.prepare_turn(active['moves'], active['ultimate'], self.battle.user_id, active)
        self.reserves = tuple(self.battle.bench())
        for index in range(2):
            member = self.reserves[index] if index < len(self.reserves) else None
            swap, assist = self.team_buttons[index * 2:index * 2 + 2]
            swap.label = (f"Swap: {member['name']}" if member else 'No reserve')[:80]
            assist.label = (f"{assist_effect(member).title()}: {member['name']} · 12 MP" if member else 'No assist')[:80]
            swap.disabled = member is None
            assist.disabled = member is None or not self.battle.can_assist(member)

    def team_callback(self, index, kind):
        async def callback(interaction):
            if interaction.user.id != self.current_player_id:
                return await interaction.response.send_message("It is your fellow trainer's turn.", ephemeral=True)
            turn_id, accepting = self.turn_id, self._accepting
            member = self.reserves[index] if index < len(self.reserves) else None
            await interaction.response.defer()
            if self._closed or self.is_finished():
                return await interaction.followup.send('This battle has ended.', ephemeral=True)
            if not accepting or not self._accepting or turn_id != self.turn_id:
                return await interaction.followup.send('Your move is being resolved. Wait for the next move prompt.', ephemeral=True)
            if member is None or member['hp'] <= 0 or kind == 'assist' and not self.battle.can_assist(member):
                return await interaction.followup.send('That teammate is not ready. Choose an available action.', ephemeral=True)
            self.chosen_move = kind, member['id']
            self.pause_input()
            self._input_ready.set()
        return callback


async def team_duel(interaction, party, enemy, title, personality='wild', *, reserved=False, realm=None,
                    view_class=TeamMoveView):
    with nullcontext() if reserved else duels.reserve_fighters([interaction.user.id]):
        battle = PartyBattle(party, enemy, interaction.user.id)
        return await _team_duel(interaction, battle, title, personality, realm, view_class)


async def _team_duel(interaction, battle, title, personality, realm, view_class):
    uid = battle.user_id
    presentation = duels.BattlePresentation(realm_key(realm, battle.states))
    message = view = None
    winner = None
    timed_out = False
    round_number = 0

    def embed(*, action=None, finished=False):
        panel = discord.Embed(title=f'⚔️ {title} · Round {round_number}', color=COLORS['epic'])
        panel.description = '\n'.join(f"**{s['name']}** · {s['hp']:,}/{s['max_hp']:,} HP · {s['mana']}/{s['max_mana']} MP · {s.get('status') or ('KO' if s['hp'] <= 0 else 'ready')}" for s in battle.states)
        if action:
            panel.description = f'**{action.name}**\n' + panel.description
        panel.add_field(name=f'Team · Assist {battle.meter}/{ASSIST_METER}', value='\n'.join(
            f"{'▶' if member is battle.active else '○'} **{member['name']}** · {member['hp']}/{member['max_hp']} HP · {member['mana']} MP"
            for member in battle.party), inline=False)
        if battle.log:
            panel.add_field(name='Last actions', value='\n'.join(battle.log[-3:])[-1000:], inline=False)
        if finished:
            result = 'Your team wins.' if winner == 0 else 'Your team retreats.' if winner == 1 else 'A draw.'
            panel.add_field(name='Battle complete', value=result + (' Match decided on time.' if timed_out else ''), inline=False)
        elif action is None:
            panel.add_field(name='Choose a move, swap, or assist', value='Swapping or assisting uses your turn. Three damaging moves charge an assist; reserves spend 12 MP. Benched companions retain their resources and conditions.', inline=False)
        return panel

    async def publish(before=None, action=None, after=None, *, prompt=False, finished=False):
        nonlocal message, view
        if view is None:
            view = view_class(battle)
        if prompt:
            view.prepare_team_turn()
        else:
            view.pause_input()
        current = battle.snapshot()
        had_message = message is not None
        try:
            if presentation is not None:
                message = await presentation.publish(interaction, message, embed(action=action, finished=finished), view,
                                                     before or current, after or current, action)
            else:
                async with asyncio.timeout(duels.DUEL_IO_TIMEOUT):
                    if message:
                        await message.edit(embed=embed(action=action, finished=finished), view=view)
                    else:
                        message = await interaction.channel.send(embed=embed(action=action, finished=finished), view=view)
        except discord.NotFound:
            if not had_message:
                raise
            # Recreate a removed panel while retaining one registered router.
            message = None
            view.stop()
            view = None
            if prompt:
                await publish(prompt=True)
        except (discord.HTTPException, TimeoutError):
            if not prompt:
                if presentation is not None:
                    presentation.enabled = False
                logger.warning('Team battle playback unavailable; continuing with text controls', exc_info=True)
            else:
                raise
        if view is not None:
            view.message = message
        return view

    async def record_knockouts():
        defeated = [member for member in battle.party if member['hp'] <= 0 and not member.get('ko_recorded')]
        if defeated:
            await storage.knockout_beasts((member['id'], member['rarity']) for member in defeated)
            for member in defeated:
                member['ko_recorded'] = True
        if not battle.alive or battle.enemy['hp'] <= 0:
            for member in [*battle.party, battle.enemy]:
                member['closed'] = True

    async def replace():
        async with combat.lock_states([uid]):
            await record_knockouts()
            before = battle.snapshot()
            action = battle.replace_knockout()
            after = battle.snapshot()
        if action is not None:
            await publish(before, action, after)

    try:
        async with combat.lock_states([uid]):
            battle.bind()
            combat.start_battle(battle.active, battle.enemy, battle.log)
        for round_number in range(1, MAX_ROUNDS + 1):
            order = sorted(range(2), key=lambda index: combat.effective_speed(battle.states[index]), reverse=True)
            for index in order:
                if not battle.alive or battle.enemy['hp'] <= 0:
                    break
                state, opp = (battle.active, battle.enemy) if index == 0 else (battle.enemy, battle.active)
                async with combat.lock_states([uid]):
                    before = battle.snapshot()
                    can_act = combat.begin_turn(state, opp, battle.log)
                    after = battle.snapshot()
                    await record_knockouts()
                if before != after or not can_act:
                    await publish(before, Action(index, 'Turn effects' if can_act else 'Turn skipped', 'status', journal=tuple(battle.log[-2:])), after)
                if can_act and battle.alive and battle.enemy['hp'] > 0:
                    if index == 0:
                        await publish(prompt=True)
                        view.open_turn()
                        try:
                            async with asyncio.timeout(view.timeout or 45):
                                await view.wait()
                        except TimeoutError:
                            view.close_input()
                        if view.error is not None:
                            raise view.error
                        if view.chosen_move is None:
                            winner, timed_out = 1, True
                            battle.log.append('The team retreats after its trainer misses a turn.')
                            break
                        async with combat.lock_states([uid]):
                            before = battle.snapshot()
                            action, bonus = battle.resolve(view.chosen_move)
                            # resolve() records a bonus separately; preserve the
                            # primary frame rather than jumping past that hit.
                            after = bonus[0] if bonus is not None else battle.snapshot()
                            await record_knockouts()
                        await publish(before, action, after)
                        if bonus is not None:
                            await publish(*bonus)
                    else:
                        async with combat.lock_states([uid]):
                            before = battle.snapshot()
                            name, ultimate = duels.ai_move(state, opp, personality)
                            spec = combat.move_spec(state, name, ultimate)
                            damage = combat.resolve_move(state, opp, name, ultimate, battle.log)
                            after = battle.snapshot()
                            kind = 'guard' if spec.get('guard_percent') else 'heal' if spec.get('heal_percent') else 'attack' if name == state['moves'][0] else 'skill'
                            action = Action(1, name, kind, damage, ultimate, tuple(battle.log[-2:]))
                            bonus = None
                            if state['hp'] > 0 and opp['hp'] > 0 and combat.bonus_action(state):
                                extra_before = battle.snapshot()
                                extra_damage = combat.resolve_move(state, opp, state['moves'][0], False, battle.log, grant_charge=False)
                                bonus = (extra_before, Action(1, state['moves'][0] + ' (bonus)', damage=extra_damage, journal=tuple(battle.log[-2:])), battle.snapshot())
                            await record_knockouts()
                        await publish(before, action, after)
                        if bonus is not None:
                            await publish(*bonus)
                # A swap uses the outgoing member's turn; the incoming member
                # gets no free regeneration, charge, passive or bonus action.
                async with combat.lock_states([uid]):
                    before = battle.snapshot()
                    combat.end_turn(state, opp, battle.log)
                    after = battle.snapshot()
                    await record_knockouts()
                if before != after:
                    await publish(before, Action(index, 'End-of-turn effects', 'status', journal=tuple(battle.log[-2:])), after)
                await replace()
            if winner is not None or not battle.alive or battle.enemy['hp'] <= 0:
                break
        await replace()
        if winner is None:
            if not battle.alive and battle.enemy['hp'] <= 0:
                winner = None
            elif not battle.alive:
                winner = 1
            elif battle.enemy['hp'] <= 0:
                winner = 0
            else:
                timed_out = True
                # Bench HP cannot win a stalling contest: compare the fighter
                # who is actually exposed against the opponent, as in a duel.
                player_ratio = battle.active['hp'] / battle.active['max_hp']
                enemy_ratio = battle.enemy['hp'] / battle.enemy['max_hp']
                winner = None if abs(player_ratio - enemy_ratio) < 1e-6 else int(enemy_ratio > player_ratio)
        async with combat.lock_states([uid]):
            for state in [*battle.party, battle.enemy]:
                state['closed'] = True
            battle.active['_participated'] = tuple(battle.participated)
        if message:
            if view is not None:
                view.close_input()
            await publish(finished=True)
        return winner, battle.states, battle.log, timed_out
    finally:
        if view is not None:
            view.close_input()
            message = message or view.message
        try:
            async with combat.lock_states([uid]):
                for member in [*battle.party, battle.enemy]:
                    member['closed'] = True
                await duels.persist_pve_states(uid, battle.party, battle.active)
        finally:
            combat.active_states.pop(uid, None)
            combat.active_parties.pop(uid, None)
            try:
                if message:
                    async with asyncio.timeout(duels.DUEL_IO_TIMEOUT):
                        await message.edit(view=None)
            except (discord.HTTPException, TimeoutError):
                logger.info('Closed team controls could not be removed', exc_info=True)
            finally:
                if view is not None:
                    view.stop()
