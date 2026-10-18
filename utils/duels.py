"""Interactive duel runner. Every duel mode uses the same states and rules."""
from contextlib import contextmanager, nullcontext
from functools import wraps
import asyncio
import logging
import aiosqlite
import discord
from utils import db as storage
from utils import combat
from utils.battle_art import Action, BattlePresentation, realm_key, snapshot
from utils.theme import COLORS
active_fighters = set()
DUEL_IO_TIMEOUT = 15


class FighterBusyError(ValueError):
    """An expected refusal to overlap a battle or collection transfer."""

@contextmanager
def reserve_fighters(users):
    """Reserve before the first await, including preparation and settlement."""
    ids = {uid for uid in users if uid is not None}
    if ids.intersection(active_fighters):
        raise FighterBusyError('A trainer is already in a battle or transfer. Finish it first. In a duel, an inactive turn closes after 45 seconds.')
    active_fighters.update(ids)
    try:
        yield
    finally:
        active_fighters.difference_update(ids)

def exclusive_player(function):
    """Keep a short collection mutation from overlapping battle preparation."""
    @wraps(function)
    async def protected(user_id, *args, **kwargs):
        async with combat.lock_states([user_id]):
            with reserve_fighters([user_id]):
                return await function(user_id, *args, **kwargs)
    return protected

async def prepare_state(user_id, row, data, perks, *, consume=True):
    from utils.catalog import load_catalog
    from utils.economy_balance import recipe_level
    equipment=load_catalog('equipment.json')
    gear = {**equipment['equipment'], **equipment['runes']}
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        if consume:
            await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND id=?', (user_id, row['id'])) as c:
            fresh = await c.fetchone()
        if not fresh or fresh['beast_id'] != data['id']:
            raise ValueError('Your companion changed while the battle was preparing. Try again.')
        row = dict(fresh)
        if storage.is_knocked_out(row) or row['hp'] <= 0:
            raise ValueError('Your companion needs recovery before it can battle.')
        async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
            player = dict(await c.fetchone())
        async with db.execute('SELECT equipment_id FROM player_equipment WHERE user_id=? AND beast_row_id=? ORDER BY id', (user_id, row['id'])) as c:
            ids = [r[0] for r in await c.fetchall()]
        if row.get('rune_id') and row['rune_id'] not in ids:
            ids.append(row['rune_id'])
        if consume:
            await db.execute('UPDATE players SET next_defense_boost=0,next_speed_boost=0,status_ward_charges=MAX(0,status_ward_charges-1) WHERE user_id=?', (user_id,))
            await db.commit()
    # Legacy saves retain their bound pieces and appearance. Their effects
    # remain dormant until the trainer meets the same gate as a new equip.
    equipped = [gear[id] if player['level'] >= recipe_level(gear[id]) else {**gear[id], 'effect': {}}
                for id in ids if id in gear]
    return combat.build_state(data, row, perks, equipped, player)

class MoveView(discord.ui.View):

    def __init__(self, moves, ultimate, current_player_id, battle_state):
        # The runner owns the 45-second turn deadline. Keep this view registered
        # across turns, HTTP redraws and final cleanup so every visible button
        # still reaches a handler, even when the next prompt is in flight.
        super().__init__(timeout=None)
        self._input_ready = asyncio.Event()
        self._accepting = False
        self._closed = False
        self.turn_id = 0
        self.message = None
        for i in range(3):
            button = discord.ui.Button(style=discord.ButtonStyle.primary, row=0)
            button.callback = self.make_callback(i)
            self.add_item(button)
        button = discord.ui.Button(style=discord.ButtonStyle.danger, row=1)
        button.callback = self.make_callback(0, True)
        self.add_item(button)
        self.prepare_turn(moves, ultimate, current_player_id, battle_state)

    def prepare_turn(self, moves, ultimate, current_player_id, battle_state):
        self._accepting = False
        self._input_ready.clear()
        self.chosen_move = None
        self.error = None
        self.turn_id += 1
        self.current_player_id = current_player_id
        self.moves = tuple(moves[:3])
        self.ultimate = ultimate
        for i, button in enumerate(self.children[:3]):
            name = self.moves[i] if i < len(self.moves) else 'Unavailable'
            spec = combat.move_spec(battle_state, name)
            button.label = f"{name} · {spec.get('mana_cost', 0)} MP"[:80]
            button.disabled = i >= len(self.moves) or not combat.can_use(battle_state, name)
        charges = battle_state.get('ultimate_charges', 0)
        self.children[3].label = f'⚡ {ultimate} · {charges}/3 · 50 MP'[:80]
        self.children[3].disabled = not combat.can_use(battle_state, ultimate, True)

    def open_turn(self):
        self._accepting = not self._closed and not self.is_finished()

    def pause_input(self):
        self._accepting = False
        for child in self.children:
            child.disabled = True

    def close_input(self):
        self._closed = True
        self._accepting = False
        self._input_ready.set()
        for child in self.children:
            child.disabled = True

    async def wait(self):
        await self._input_ready.wait()
        return self.chosen_move is None

    def stop(self):
        self.close_input()
        super().stop()

    def make_callback(self, index, is_ultimate=False):

        async def callback(interaction):
            if interaction.user.id != self.current_player_id:
                return await interaction.response.send_message("It is your fellow trainer's turn.", ephemeral=True)
            turn_id, accepting = self.turn_id, self._accepting
            await interaction.response.defer()
            if self._closed or self.is_finished():
                return await interaction.followup.send('This battle has ended.', ephemeral=True)
            if not accepting or not self._accepting or turn_id != self.turn_id:
                return await interaction.followup.send('Your move is being resolved. Wait for the next move prompt.', ephemeral=True)
            name = self.ultimate if is_ultimate else self.moves[index]
            self.chosen_move = (name, is_ultimate)
            self._accepting = False
            for child in self.children:
                child.disabled = True
            # Wake the runner without stopping/unregistering the UI. A stopped
            # view drops subsequent gateway clicks before our callback can ack.
            self._input_ready.set()
        return callback

    async def on_timeout(self):
        self.close_input()

    async def on_error(self, interaction, error, item):
        logging.getLogger('chibibeasts.battle').error(
            'Move input failed: trainer=%s message=%s turn=%s control=%s',
            self.current_player_id, getattr(self.message, 'id', None), self.turn_id,
            getattr(item, 'custom_id', None), exc_info=error,
        )
        self.error = error
        self.close_input()
        try:
            message = 'The battle input failed. This match will close; please try again.'
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

def ai_move(state, opp, personality):
    if state['hp'] < state['max_hp'] * 0.55 and combat.can_use(state, state['moves'][1]) and combat.move_spec(state, state['moves'][1]).get('heal_percent'):
        return (state['moves'][1], False)
    if combat.can_use(state, state['ultimate'], True) and (not opp.get('guard') or personality == 'chaotic'):
        return (state['ultimate'], True)
    if opp.get('focus', 1) > 1 and not state.get('guard') and personality in {'defensive', 'patient', 'optimal'}:
        return (state['moves'][2], False)
    condition = combat.move_spec(state, state['moves'][1]).get('status')
    if condition and combat.can_receive_status(opp, condition) and combat.can_use(state, state['moves'][1]):
        return (state['moves'][1], False)
    return (state['moves'][0], False)

async def duel(interaction, states, users, title, personality='wild', view_class=MoveView, *, reserved=False, realm=None):
    with nullcontext() if reserved else reserve_fighters(users):
        return await _duel(interaction, states, users, title, personality, view_class, realm)

async def _duel(interaction, states, users, title, personality, view_class, realm):
    ids = [u for u in users if u is not None]
    for state in states:
        if state['hp'] <= 0:
            raise ValueError('Your beast needs recovery before it can battle.')
    presentation = BattlePresentation(realm_key(realm, states))
    for uid, state in zip(users, states):
        if uid is not None:
            combat.active_states[uid] = state
    log = []
    message = None
    view = None
    timed_out = False
    winner = None

    def battle_embed(round_number, *, action=None, uid=None, finished=False):
        embed = discord.Embed(title=f'⚔️ {title} · Round {round_number}', color=COLORS['epic'])
        if action:
            embed.description = f"**{states[action.actor]['name']}** — {action.name}"
        else:
            # Compact text remains readable with GIF autoplay disabled and by
            # screen readers; the large bars live above the shared battlefield.
            embed.description = '\n'.join(
                f"**{s['name']}** · {s['hp']:,}/{s['max_hp']:,} HP · "
                f"{s['mana']}/{s['max_mana']} MP · {s.get('status') or ('KO' if s['hp'] <= 0 else 'ready')}"
                for s in states)
            if uid is not None:
                state = states[users.index(uid)]
                embed.add_field(name=f'<@{uid}> — choose a move', value='\n'.join(
                    f"**{m}** — {combat.move_spec(state, m)['description']}" for m in state['moves']), inline=False)
            if log:
                embed.add_field(name='Last actions', value='\n'.join(log[-3:])[-1000:], inline=False)
            if finished:
                result = f"**{states[winner]['name']}** wins." if winner is not None else 'Both companions finish together. A draw.'
                if timed_out:
                    result += ' Match decided on time.'
                embed.add_field(name='Battle complete', value=result, inline=False)
        return embed

    def close_defeated():
        # The result is already authoritative while its clip is playing. Item
        # use must not resurrect a resolved KO just because rendering adds time.
        if any(s['hp'] <= 0 for s in states):
            for state in states:
                state['closed'] = True

    async def animate(before, action, round_number, after=None):
        nonlocal message, view
        if presentation is None:  # Injectable text-only presentation for engine/router tests.
            return
        if view is None:
            first = next((i for i, uid in enumerate(users) if uid is not None), 0)
            state = states[first]
            view = view_class(state['moves'], state['ultimate'], users[first], state)
        view.pause_input()
        try:
            message = await presentation.publish(interaction, message, battle_embed(round_number, action=action),
                                                 view, before, after if after is not None else snapshot(states), action)
        except discord.NotFound:
            logging.getLogger('chibibeasts.battle').info('Battle panel removed during playback; next prompt will recreate it')
            message = None
            view.stop()
            view = None
        except (discord.HTTPException, TimeoutError):
            presentation.enabled = False
            logging.getLogger('chibibeasts.battle').warning('Battle playback unavailable; continuing with text controls', exc_info=True)
        if view is not None:
            view.message = message

    try:
        async with combat.lock_states(users):
            combat.start_battle(*states, log)
        for round_number in range(1, 21):
            order = sorted(range(2), key=lambda i: combat.effective_speed(states[i]), reverse=True)
            for index in order:
                state, opp = (states[index], states[1 - index])
                if min((s['hp'] for s in states)) <= 0:
                    break
                async with combat.lock_states(users):
                    before = snapshot(states)
                    can_act = combat.begin_turn(state, opp, log)
                    close_defeated()
                    changed = before != snapshot(states)
                if changed or not can_act:
                    effect_name = 'Turn effects' if can_act else f"{before[index].status or 'Recovery'} — turn skipped"
                    await animate(before, Action(index, effect_name, 'status', journal=tuple(log[-2:])), round_number)
                if can_act:
                    uid = users[index]
                    if uid is None:
                        name, ult = ai_move(state, opp, personality)
                    else:
                        embed = battle_embed(round_number, uid=uid)
                        if view is None:
                            view = view_class(state['moves'], state['ultimate'], uid, state)
                        else:
                            view.prepare_turn(state['moves'], state['ultimate'], uid, state)
                        if presentation is not None:
                            current = snapshot(states)
                            try:
                                message = await presentation.publish(interaction, message, embed, view, current, current)
                            except discord.NotFound:
                                # Deleting a battle message must not strand the
                                # reserved trainers at the next decision point.
                                # Retire the old message's router entries too.
                                view.stop()
                                view = view_class(state['moves'], state['ultimate'], uid, state)
                                message = await presentation.publish(interaction, None, embed, view, current, current)
                        else:
                            async with asyncio.timeout(DUEL_IO_TIMEOUT):
                                if message:
                                    await message.edit(embed=embed, view=view)
                                else:
                                    message = await interaction.channel.send(embed=embed, view=view)
                        view.message = message
                        view.open_turn()
                        # Limit this prompt independently of the long-lived UI;
                        # unrelated clicks cannot extend the decision deadline.
                        try:
                            async with asyncio.timeout(view.timeout or 45):
                                await view.wait()
                        except TimeoutError:
                            view.close_input()
                        if view.error is not None:
                            raise view.error
                        if view.chosen_move is None:
                            timed_out = True
                            winner = 1 - index
                            log.append(f"{state['name']}'s trainer did not choose a move in time.")
                            break
                        name, ult = view.chosen_move
                    async with combat.lock_states(users):
                        # Item use can change MP while the trainer chooses. Match
                        # resolve_move's fallback so the clip names the real move.
                        if not combat.can_use(state, name, ult):
                            name, ult = state['moves'][0], False
                        spec = combat.move_spec(state, name, ult)
                        before = snapshot(states)
                        damage = combat.resolve_move(state, opp, name, ult, log)
                        after = snapshot(states)
                        primary_log = tuple(log[-2:])
                        extra = None
                        # Preserve the original atomic primary+bonus resolution;
                        # playback adds no new item-use window between the rolls.
                        if state['hp'] > 0 and opp['hp'] > 0 and combat.bonus_action(state):
                            extra_before = snapshot(states)
                            extra_damage = combat.resolve_move(state, opp, state['moves'][0], False, log, grant_charge=False)
                            extra = (extra_before, snapshot(states), extra_damage)
                        close_defeated()
                    if spec.get('guard_percent'):
                        kind = 'guard'
                    elif spec.get('heal_percent') or after[index].hp > before[index].hp and not damage:
                        kind = 'heal'
                    else:
                        kind = 'skill' if name != state['moves'][0] else 'attack'
                    await animate(before, Action(index, name, kind, damage, ult, primary_log), round_number, after)
                    if extra is not None:
                        extra_before, extra_after, extra_damage = extra
                        await animate(extra_before, Action(index, state['moves'][0] + ' (bonus)', damage=extra_damage, journal=tuple(log[-2:])), round_number, extra_after)
                async with combat.lock_states(users):
                    before = snapshot(states)
                    combat.end_turn(state, opp, log)
                    close_defeated()
                    changed = before != snapshot(states)
                if changed:
                    await animate(before, Action(index, 'End-of-turn effects', 'status', journal=tuple(log[-2:])), round_number)
            if winner is not None or min((s['hp'] for s in states)) <= 0:
                break
        if winner is None:
            if states[0]['hp'] <= 0 and states[1]['hp'] <= 0:
                winner = None
            elif states[0]['hp'] <= 0:
                winner = 1
            elif states[1]['hp'] <= 0:
                winner = 0
            else:
                timed_out = True
                ratios = [s['hp'] / s['max_hp'] for s in states]
                winner = None if abs(ratios[0] - ratios[1]) < 1e-06 else int(ratios[1] > ratios[0])
        async with combat.lock_states(users):
            for state in states:
                state['closed'] = True
        if presentation is not None and message:
            if view is not None:
                view.close_input()
            current = snapshot(states)
            try:
                message = await presentation.publish(interaction, message, battle_embed(round_number, finished=True),
                                                     view, current, current)
            except (discord.HTTPException, TimeoutError):
                # A cosmetic final redraw cannot cancel an already resolved
                # battle, its rewards or the caller's daily spar settlement.
                logging.getLogger('chibibeasts.battle').info('Final battle artwork could not be displayed', exc_info=True)
        return (winner, states, log, timed_out)
    finally:
        if view is not None:
            view.close_input()
            if message is None:
                message = view.message
        try:
            async with combat.lock_states(users):
                for state in states:
                    state['closed'] = True
                async with storage.connect() as db:
                    for uid, state in zip(users, states):
                        if uid is not None:
                            await db.execute('UPDATE players SET ultimate_charges=? WHERE user_id=?', (state['ultimate_charges'], uid))
                    await db.commit()
        finally:
            for uid in ids:
                combat.active_states.pop(uid, None)
            try:
                if message:
                    async with asyncio.timeout(DUEL_IO_TIMEOUT):
                        await message.edit(view=None)
            except discord.NotFound:
                # A trainer/moderator may have removed the panel. Cleanup is
                # already complete locally; this is not another battle failure.
                logging.getLogger('chibibeasts.battle').info('Battle message %s was already removed at cleanup', getattr(message, 'id', None))
            except (discord.HTTPException, TimeoutError):
                logging.getLogger('chibibeasts.battle').warning('Could not remove closed battle controls', exc_info=True)
            finally:
                if view is not None:
                    view.stop()

async def beast_exp(user_id, row_id, amount):
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        level, leveled = await beast_exp_in_transaction(db, user_id, row_id, amount)
        await db.commit()
    if leveled:
        from utils.progress import track_quest_event
        await track_quest_event(user_id, 'beast_level_up')
    return level

async def beast_exp_in_transaction(db, user_id, row_id, amount, *, bonuses=True):
    from utils.rewards import has_perk
    if bonuses and await has_perk(db, user_id, 'fable_scholar'):
        amount = round(amount * 1.1)
    if bonuses:
        async with db.execute('SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON s.guild_id=m.guild_id WHERE m.user_id=? AND s.arcane_library=1', (user_id,)) as c:
            if await c.fetchone():
                amount = round(amount * 1.15)
    async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND id=?', (user_id, row_id)) as c:
        row = await c.fetchone()
    if not row:
        return (None, False)
    row = dict(row)
    from utils.progression import advance_beast_xp
    level, exp = advance_beast_xp(row['level'], row['exp'], amount)
    await storage.apply_beast_levelup(db, row, level, exp)
    return (level, level > row["level"])
