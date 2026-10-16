"""Interactive duel runner. Every duel mode uses the same states and rules."""
import asyncio
import json
import aiosqlite
import discord
from utils import db as storage
from utils import combat
from utils.artwork import PROJECT_ROOT
from utils.theme import hp_bar, COLORS
active_fighters = set()

async def prepare_state(user_id, row, data, perks, *, consume=True):
    equipment = json.loads((PROJECT_ROOT / 'data/equipment.json').read_text(encoding='utf-8'))
    gear = {**equipment['equipment'], **equipment['runes']}
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if consume:
            await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
            player = dict(await c.fetchone())
        async with db.execute('SELECT equipment_id FROM player_equipment WHERE user_id=? AND beast_row_id=?', (user_id, row['id'])) as c:
            ids = [r[0] for r in await c.fetchall()]
        if row.get('rune_id') and row['rune_id'] not in ids:
            ids.append(row['rune_id'])
        if consume:
            await db.execute('UPDATE players SET next_defense_boost=0,next_speed_boost=0,status_ward_charges=MAX(0,status_ward_charges-1) WHERE user_id=?', (user_id,))
            await db.commit()
    return combat.build_state(data, row, perks, [gear[id] for id in ids if id in gear], player)

class MoveView(discord.ui.View):

    def __init__(self, moves, ultimate, current_player_id, battle_state):
        super().__init__(timeout=45)
        self.chosen_move = None
        self.current_player_id = current_player_id
        self.message = None
        for i, name in enumerate(moves[:3]):
            spec = combat.move_spec(battle_state, name)
            label = f"{name} · {spec.get('mana_cost', 0)} MP"
            button = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary, disabled=not combat.can_use(battle_state, name), row=0)
            button.callback = self.make_callback(name)
            self.add_item(button)
        charges = battle_state.get('ultimate_charges', 0)
        button = discord.ui.Button(label=f'⚡ {ultimate} · {charges}/3 · 50 MP'[:80], style=discord.ButtonStyle.danger, disabled=not combat.can_use(battle_state, ultimate, True), row=1)
        button.callback = self.make_callback(ultimate, True)
        self.add_item(button)

    def make_callback(self, name, is_ultimate=False):

        async def callback(interaction):
            if interaction.user.id != self.current_player_id:
                return await interaction.response.send_message("It is your fellow trainer's turn.", ephemeral=True)
            if self.chosen_move is not None:
                return
            self.chosen_move = (name, is_ultimate)
            for child in self.children:
                child.disabled = True
            self.stop()
            await interaction.response.edit_message(view=self)
        return callback

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

def ai_move(state, opp, personality):
    if state['hp'] < state['max_hp'] * 0.55 and combat.can_use(state, state['moves'][1]) and combat.move_spec(state, state['moves'][1]).get('heal_percent'):
        return (state['moves'][1], False)
    if combat.can_use(state, state['ultimate'], True) and (not opp.get('guard') or personality == 'chaotic'):
        return (state['ultimate'], True)
    if opp.get('focus', 1) > 1 and personality in {'defensive', 'patient', 'optimal'}:
        return (state['moves'][2], False)
    if not opp.get('status') and combat.can_use(state, state['moves'][1]) and combat.move_spec(state, state['moves'][1]).get('status'):
        return (state['moves'][1], False)
    return (state['moves'][0], False)

async def duel(interaction, states, users, title, personality='wild', view_class=MoveView):
    ids = [u for u in users if u is not None]
    if any((uid in active_fighters for uid in ids)):
        raise ValueError('A trainer is already in a battle. Finish it first.')
    for state in states:
        if state['hp'] <= 0:
            raise ValueError('Your beast needs recovery before it can battle.')
    active_fighters.update(ids)
    for uid, state in zip(users, states):
        if uid is not None:
            combat.active_states[uid] = state
        else:
            state['ultimate_charges'] = 3
    log = []
    message = None
    timed_out = False
    winner = None
    try:
        combat.start_battle(*states, log)
        for round_number in range(1, 21):
            order = sorted(range(2), key=lambda i: combat.effective_speed(states[i]), reverse=True)
            for index in order:
                state, opp = (states[index], states[1 - index])
                if min((s['hp'] for s in states)) <= 0:
                    break
                if combat.begin_turn(state, opp, log):
                    uid = users[index]
                    if uid is None:
                        if state['mana'] >= 50:
                            state['ultimate_charges'] = 3
                        name, ult = ai_move(state, opp, personality)
                    else:
                        embed = discord.Embed(title=f'⚔️ {title} · Round {round_number}', description='\n\n'.join((f"**{s['name']}** Lv.{s.get('level', 1)}\n{hp_bar(s['hp'], s['max_hp'])}\n{s['mana']}/{s['max_mana']} MP · {s.get('status') or 'ready'}" for s in states)), color=COLORS['epic'])
                        embed.add_field(name=f'<@{uid}> — choose a move', value='\n'.join((f"**{m}** — {combat.move_spec(state, m)['description']}" for m in state['moves'])), inline=False)
                        if log:
                            embed.add_field(name='Last actions', value='\n'.join(log[-3:])[-1000:], inline=False)
                        if opp.get('image_url'):
                            embed.set_image(url=opp['image_url'])
                        if state.get('image_url'):
                            embed.set_thumbnail(url=state['image_url'])
                        view = view_class(state['moves'], state['ultimate'], uid, state)
                        if message:
                            await message.edit(embed=embed, view=view)
                        else:
                            message = await interaction.channel.send(embed=embed, view=view)
                        view.message = message
                        await view.wait()
                        if view.chosen_move is None:
                            timed_out = True
                            winner = 1 - index
                            log.append(f"{state['name']}'s trainer did not choose a move in time.")
                            break
                        name, ult = view.chosen_move
                    async with combat.state_lock:
                        combat.resolve_move(state, opp, name, ult, log)
                        if state['hp'] > 0 and opp['hp'] > 0 and combat.bonus_action(state):
                            combat.resolve_move(state, opp, state['moves'][0], False, log)
                combat.end_turn(state, opp, log)
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
        if message:
            await message.edit(view=None)
        return (winner, states, log, timed_out)
    finally:
        async with aiosqlite.connect(storage.DB_PATH) as db:
            for uid, state in zip(users, states):
                if uid is not None:
                    await db.execute('UPDATE players SET ultimate_charges=? WHERE user_id=?', (state['ultimate_charges'], uid))
            await db.commit()
        for uid in ids:
            active_fighters.discard(uid)
            combat.active_states.pop(uid, None)

async def beast_exp(user_id, row_id, amount):
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        from utils.rewards import has_perk
        if await has_perk(db, user_id, 'fable_scholar'):
            amount = round(amount * 1.1)
        async with db.execute('SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON s.guild_id=m.guild_id WHERE m.user_id=? AND s.arcane_library=1', (user_id,)) as c:
            if await c.fetchone():
                amount = round(amount * 1.15)
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND id=?', (user_id, row_id)) as c:
            row = await c.fetchone()
        if not row:
            return
        row = dict(row)
        level = row['level']
        exp = row['exp'] + amount
        while exp >= storage.get_beast_exp_for_level(row, level):
            exp -= storage.get_beast_exp_for_level(row, level)
            level += 1
        await storage.apply_beast_levelup(db, row, level, exp)
        await db.commit()
    if level > row['level']:
        from utils.progress import track_quest_event
        await track_quest_event(user_id, 'beast_level_up')
    return level
