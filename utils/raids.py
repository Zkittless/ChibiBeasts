"""Shared cooperative raid sessions with distinct, readable boss responses."""
import asyncio
import random
import time
import aiosqlite
import discord
from utils import db as storage, combat
from utils.duels import prepare_state, active_fighters, beast_exp
from utils.gameplay import add_stack
from utils.theme import COLORS, hp_bar
active_raids = {}
open_lobbies = set()
MECHANICS = {'corrupted_leviathan': ('Cleanse the current', 'Tainted water is gathering. Use Weave to cleanse it before the next surge.', 'cleanse'), 'corrupted_fenrir': ('Close the rift', 'A rift marks the next strike. Weave it shut or guard against the echo.', 'rift'), 'corrupted_dragon': ('Vent the flame', 'Null-flame is rising. Guard shelters you; Weave vents part of the pressure.', 'flame'), 'ancient_chronos': ('Anchor this moment', 'Chronos is recording an echo of your attacks. Guard before the echo returns; Weave weakens it.', 'echo'), 'ancient_genesis': ('Share the ember', 'An ember is seeking a place to rest. Weave together to turn the coming burst into a shield.', 'ember'), 'ancient_abyss': ('Place a guiding star', "The light is thinning. Weave guiding stars to restore everyone's attack strength.", 'stars')}

async def party_rows(uid):
    await storage.recover_beasts(uid)
    async with aiosqlite.connect(storage.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND raid_slot IN (1,2,3) ORDER BY raid_slot', (uid,)) as c:
            rows = [dict(r) for r in await c.fetchall()]
    if len(rows) != 3:
        raise ValueError('Set three companions with /raidparty first.')
    if any((storage.is_knocked_out(r) for r in rows)):
        raise ValueError('A party companion is recovering. Use Phoenix Elixir or choose another party.')
    return rows

class RaidLobby(discord.ui.View):

    def __init__(self, owner, guild_id):
        super().__init__(timeout=60)
        self.owner = owner
        self.guild_id = guild_id
        self.users = {owner}
        self.cancelled = False

    async def eligible(self, uid):
        player = await storage.get_player(uid)
        if not player or player['level'] < 10:
            raise ValueError('Reach trainer level 10 before joining a raid.')
        if uid in active_fighters:
            raise ValueError('Finish your current battle first.')
        if self.guild_id:
            async with aiosqlite.connect(storage.DB_PATH) as db:
                async with db.execute('SELECT 1 FROM guild_members WHERE user_id=? AND guild_id=?', (uid, self.guild_id)) as c:
                    member = await c.fetchone()
            if not member:
                raise ValueError('This raid is for members of the summoning guild.')
        await party_rows(uid)

    @discord.ui.button(label='Join raid · three beasts', style=discord.ButtonStyle.success)
    async def join(self, bi, button):
        try:
            await self.eligible(bi.user.id)
            if len(self.users) >= 10 and bi.user.id not in self.users:
                raise ValueError('This party is full (10 trainers).')
            self.users.add(bi.user.id)
            await bi.response.send_message(f'You are in. {len(self.users)}/10 trainers; the raid starts when the host is ready or after 60 seconds.', ephemeral=True)
        except ValueError as error:
            await bi.response.send_message(str(error), ephemeral=True)

    @discord.ui.button(label='Start now', style=discord.ButtonStyle.primary)
    async def begin(self, bi, button):
        if bi.user.id != self.owner:
            return await bi.response.send_message('The host starts the raid.', ephemeral=True)
        self.stop()
        await bi.response.edit_message(view=None)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, bi, button):
        if bi.user.id != self.owner:
            return await bi.response.send_message('The host can cancel.', ephemeral=True)
        self.cancelled = True
        self.stop()
        await bi.response.edit_message(content='Raid cancelled. Nothing was spent.', view=None)

async def open_raid(interaction, boss, *, guild_id=None, summon_item=None):
    channel = interaction.channel
    uid = interaction.user.id
    if channel.id in open_lobbies or any((s.channel.id == channel.id for s in active_raids.values())):
        raise ValueError('A raid or lobby is already open here.')
    lobby = RaidLobby(uid, guild_id)
    await lobby.eligible(uid)
    open_lobbies.add(channel.id)
    try:
        message = await interaction.followup.send(embed=discord.Embed(title=f"{boss['name']} · Gather the party", description=f"{boss['description']}\n\nUp to ten trainers. Set three healthy companions with /raidparty.\n{MECHANICS[boss['id']][1]}\n\nThe cost is spent only when the raid begins.", color=COLORS['legendary']), view=lobby)
        await lobby.wait()
        if lobby.cancelled:
            return
        for member in sorted(lobby.users):
            await lobby.eligible(member)
        session = RaidSession(channel, boss, lobby.users, guild_id)
        await session.start(uid, summon_item)
        await message.edit(view=None)
        await session.run()
    finally:
        open_lobbies.discard(channel.id)

class RaidSession:

    def __init__(self, channel, boss, users, guild_id):
        self.channel = channel
        self.boss = boss
        self.users = sorted(users)
        self.guild_id = guild_id
        self.id = None
        self.party = {}
        self.slots = {uid: 0 for uid in users}
        self.damage = {uid: 0 for uid in users}
        self.support = {uid: 0 for uid in users}
        self.last = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self.pending = None
        self.weaves = 0
        self.phase = 0
        self.message = None
        self.tasks = []
        self.totems = {}
        self.started = time.monotonic()
        self.last_event = 'The party makes room for an unfinished creature.'

    def state(self, uid):
        return self.party[uid][self.slots[uid]]

    async def start(self, owner, item):
        for uid in self.users:
            if uid in active_fighters:
                raise ValueError('A trainer has entered another battle; nothing was spent.')
        active_fighters.update(self.users)
        try:
            rows = {uid: await party_rows(uid) for uid in self.users}
            async with aiosqlite.connect(storage.DB_PATH) as db:
                await db.execute('BEGIN IMMEDIATE')
                if self.guild_id:
                    cur = await db.execute('UPDATE guilds SET guild_tokens=guild_tokens-50 WHERE id=? AND level>=5 AND guild_tokens>=50', (self.guild_id,))
                    if not cur.rowcount:
                        raise ValueError('The guild needs level 5 and 50 tokens.')
                else:
                    cur = await db.execute('UPDATE player_inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=? AND quantity>0', (owner, item))
                    if not cur.rowcount:
                        raise ValueError('The summon item is no longer available.')
                    await db.execute('DELETE FROM player_inventory WHERE user_id=? AND item_id=? AND quantity<=0', (owner, item))
                cur = await db.execute('INSERT INTO raids(boss_id,boss_name,boss_type,max_hp,current_hp,guild_id,channel_id) VALUES(?,?,?,?,?,?,?)', (self.boss['id'], self.boss['name'], 'corrupted' if self.guild_id else 'ancient', 1, 1, self.guild_id, self.channel.id))
                self.id = cur.lastrowid
                await db.execute('INSERT INTO raid_payments(raid_id,user_id,guild_id,item_id) VALUES(?,?,?,?)', (self.id, owner, self.guild_id, item))
                await db.commit()
            for uid in self.users:
                async with aiosqlite.connect(storage.DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute('SELECT * FROM player_perks WHERE user_id=? AND equipped=1', (uid,)) as c:
                        perks = [dict(r) for r in await c.fetchall()]
                from utils.sanctuary import get_user_sanctuary
                sanctuary = await get_user_sanctuary(uid)
                self.party[uid] = []
                for index, row in enumerate(rows[uid]):
                    state = await prepare_state(uid, row, storage.get_beast_data(row['beast_id']), perks, consume=index == 0)
                    state['mana'] = min(25 + (20 if 'genesis_spark' in state['perks'] else 0), state['max_mana'])
                    state['ultimate_charges'] = 3
                    if sanctuary.get('raid_altar'):
                        state['damage_multiplier'] *= 1.1
                        state['damage_reduction_flat_percent'] = state.get('damage_reduction_flat_percent', 0) + 5
                    self.party[uid].append(state)
                async with aiosqlite.connect(storage.DB_PATH) as db:
                    async with db.execute('SELECT raid_totem_charges FROM players WHERE user_id=?', (uid,)) as c:
                        totem = (await c.fetchone())[0]
                    self.totems[uid] = totem > 0
                    if totem:
                        await db.execute('UPDATE players SET raid_totem_charges=raid_totem_charges-1 WHERE user_id=?', (uid,))
                    await db.commit()
                combat.active_states[uid] = self.state(uid)
            data = storage.get_beast_data(self.boss['id'])
            avg_def = sum((self.state(uid)['defense'] for uid in self.users)) / len(self.users)
            defense = max(20, int(avg_def * 0.55))
            dps = sum((self.state(uid)['attack'] * 100 / (100 + defense) for uid in self.users))
            hp = max(500, int(dps * (35 if self.guild_id else 45) / len(self.users) ** 0.2))
            avg_hp = sum((self.state(uid)['max_hp'] for uid in self.users)) / len(self.users)
            row = {'hp': hp, 'max_hp': hp, 'attack': int(avg_hp * 0.22), 'defense': defense, 'speed': 1, 'mana': 100, 'max_mana': 100, 'happiness': 70}
            self.enemy = combat.build_state(data, row)
            self.enemy['divine_passive'] = {}
            for uid in self.users:
                combat.start_battle(self.state(uid), self.enemy, [])
            active_raids[self.id] = self
            async with aiosqlite.connect(storage.DB_PATH) as db:
                await db.execute('UPDATE raids SET max_hp=?,current_hp=? WHERE id=?', (hp, hp, self.id))
                await db.commit()
        except Exception:
            active_fighters.difference_update(self.users)
            for uid in self.users:
                combat.active_states.pop(uid, None)
            if self.id:
                await refund_raid(self.id)
            raise

    def embed(self):
        hp = self.enemy['hp']
        description = f"{hp_bar(hp, self.enemy['max_hp'])}\n\n{self.last_event}"
        if self.pending:
            description += f"\n\n⚠️ **{MECHANICS[self.boss['id']][0]}** — {MECHANICS[self.boss['id']][1]}\nWeave: {self.weaves}/{min(2, len(self.users))}"
        embed = discord.Embed(title=f"⚔️ {self.boss['name']}", description=description, color=COLORS['legendary'])
        lines = []
        for uid in self.users:
            state = self.state(uid)
            lines.append(f"<@{uid}> · {state['name']} · {state['hp']}/{state['max_hp']} HP · {state['mana']} MP · {self.damage[uid]} damage / {self.support[uid]} support")
        embed.add_field(name='Companions', value='\n'.join(lines)[:1024], inline=False)
        embed.set_image(url=self.boss['image_url'])
        embed.set_footer(text='Attack / skill / guard / ultimate · Weave responds to the boss · Tend helps an ally · Swap preserves your bench')
        return embed

    async def refresh(self):
        if not self.closed and self.message:
            try:
                await self.message.edit(embed=self.embed(), view=self.view)
            except discord.HTTPException:
                pass

    async def run(self):
        self.view = RaidView(self)
        self.message = await self.channel.send(embed=self.embed(), view=self.view)
        self.started = time.monotonic()
        try:
            while not self.closed and time.monotonic() - self.started < 600:
                await asyncio.sleep(8)
                async with self.lock:
                    if self.closed:
                        break
                    async with combat.state_lock:
                        await self.boss_turn()
                if self.enemy['hp'] <= 0:
                    await self.finish(True)
                    break
                await self.refresh()
                if not any((s['hp'] > 0 for party in self.party.values() for s in party)):
                    await self.finish(False)
            if not self.closed:
                await self.finish(False)
        except Exception:
            if not self.closed:
                await refund_raid(self.id)
            raise
        finally:
            self.closed = True
            active_raids.pop(self.id, None)
            active_fighters.difference_update(self.users)
            for uid in self.users:
                combat.active_states.pop(uid, None)
            self.view.stop()

    async def boss_turn(self):
        alive = [uid for uid in self.users if self.state(uid)['hp'] > 0]
        if not alive:
            return
        log = []
        if not combat.begin_turn(self.enemy, self.state(alive[0]), log):
            combat.end_turn(self.enemy, self.state(alive[0]), log)
            self.last_event = ' · '.join(log[-2:]) or 'The creature pauses.'
            return
        if self.pending:
            kind = MECHANICS[self.boss['id']][2]
            required = min(2, len(self.users))
            solved = self.weaves >= required
            for uid in alive:
                state = self.state(uid)
                log = []
                if solved and kind in {'cleanse', 'rift', 'ember', 'stars'}:
                    if kind == 'ember':
                        state['shield'] = state.get('shield', 0) + int(state['max_hp'] * 0.2)
                    else:
                        combat.heal(state, int(state['max_hp'] * 0.1))
                        state['status'] = None
                else:
                    base = int(state['max_hp'] * (0.12 if solved else 0.22) * 100 / (100 + state['defense']) * 1.5)
                    if kind == 'echo':
                        base += int(self.damage.get(uid, 0) * 0.01)
                    combat.resolve_hit(self.enemy, state, base, log)
                    if not solved and kind in {'cleanse', 'flame', 'rift'}:
                        combat.apply_status(state, {'cleanse': 'poison', 'flame': 'burn', 'rift': 'slow'}[kind], self.enemy, log)
            self.last_event = 'The party loosened the pattern together.' if solved else 'The pattern releases. Guarding sheltered companions from its force.'
            self.pending = None
            self.weaves = 0
        else:
            uid = random.choice(alive)
            state = self.state(uid)
            log = []
            damage = max(1, int(self.enemy['attack'] * 100 / (100 + state['defense'])))
            combat.resolve_hit(self.enemy, state, damage, log)
            self.last_event = f"{self.boss['name']} meets {state['name']}: " + (' · '.join(log[-1:]) if log else f'{damage} pressure.')
        for uid in self.users:
            for state in self.party[uid]:
                if state['hp'] <= 0 and (not state.get('ko_recorded')):
                    state['ko_recorded'] = True
                    async with aiosqlite.connect(storage.DB_PATH) as db:
                        async with db.execute('SELECT rarity FROM player_beasts WHERE id=?', (state['id'],)) as c:
                            row = await c.fetchone()
                    if row:
                        await storage.knockout_beast(state['id'], row[0])
        combat.end_turn(self.enemy, self.state(alive[0]), [])

    async def action(self, uid, kind):
        if uid not in self.party:
            raise ValueError('Join the lobby before the raid begins.')
        async with self.lock:
            if self.closed:
                raise ValueError('This raid is complete.')
            state = self.state(uid)
            if state['hp'] <= 0 and kind != 'swap':
                raise ValueError('Swap to a healthy bench companion.')
            cooldown = 8 if kind in {'weave', 'tend'} else 2
            if time.monotonic() - self.last.get((uid, kind if kind in {'weave', 'tend'} else 'action'), 0) < cooldown:
                raise ValueError('Give your companion a moment before that action.')
            key = (uid, kind if kind in {'weave', 'tend'} else 'action')
            log = []
            damage = 0
            if kind == 'swap':
                options = [i for i, s in enumerate(self.party[uid]) if i != self.slots[uid] and s['hp'] > 0]
                if not options:
                    raise ValueError('No healthy companion remains on the bench.')
                self.slots[uid] = options[0]
                combat.active_states[uid] = self.state(uid)
                self.last_event = f"<@{uid}> brings in {self.state(uid)['name']}."
            elif kind == 'weave':
                if not self.pending:
                    raise ValueError('The weave is steady. Watch for the next announced pattern.')
                self.weaves += 1
                self.support[uid] += max(1, int(state['max_hp'] * 0.1))
                self.last_event = f"<@{uid}> helps {MECHANICS[self.boss['id']][0].lower()}."
            elif kind == 'tend':
                targets = [(other, self.state(other)) for other in self.users if 0 < self.state(other)['hp'] < self.state(other)['max_hp']]
                if not targets:
                    raise ValueError('Everyone standing is healthy; keep the supplies.')
                if state['mana'] < 15:
                    raise ValueError('Tending an ally needs 15 mana.')
                other, target = min(targets, key=lambda pair: pair[1]['hp'] / pair[1]['max_hp'])
                state['mana'] -= 15
                restored = combat.heal(target, max(1, int(target['max_hp'] * 0.18)))
                target['status'] = None
                self.support[uid] += restored
                self.last_event = f"<@{uid}> tends <@{other}>'s {target['name']}: +{restored} HP."
            else:
                index = {'attack': 0, 'skill': 1, 'guard': 2}.get(kind, 0)
                ult = kind == 'ultimate'
                move = state['ultimate'] if ult else state['moves'][index]
                if not combat.can_use(state, move, ult):
                    raise ValueError('Not enough mana or ultimate charge. Attack or guard to build resources.')
                if combat.begin_turn(state, self.enemy, log):
                    old_hp = state['hp']
                    old = self.enemy['hp']
                    if self.totems[uid]:
                        state['damage_multiplier'] *= 1.2
                    combat.resolve_move(state, self.enemy, move, ult, log)
                    if state['hp'] > 0 and self.enemy['hp'] > 0 and combat.bonus_action(state):
                        combat.resolve_move(state, self.enemy, state['moves'][0], False, log)
                    if self.totems[uid]:
                        state['damage_multiplier'] /= 1.2
                    self.support[uid] += max(0, state['hp'] - old_hp)
                    if self.pending and MECHANICS[self.boss['id']][2] == 'stars' and (self.weaves < min(2, len(self.users))):
                        loss = int((old - self.enemy['hp']) * 0.3)
                        self.enemy['hp'] += loss
                    damage = max(0, old - self.enemy['hp'])
                    self.damage[uid] += damage
                    if not ult:
                        state['ultimate_charges'] = min(3, state.get('ultimate_charges', 0) + 1)
                    if kind == 'guard' and self.pending:
                        self.support[uid] += max(1, int(state['max_hp'] * 0.04))
                    combat.end_turn(state, self.enemy, log)
                self.last_event = ' · '.join(log[-2:])[-500:]
            self.last[key] = time.monotonic()
            pct = self.enemy['hp'] / self.enemy['max_hp']
            if self.phase < 3 and pct < (0.75, 0.45, 0.2)[self.phase]:
                self.phase += 1
                self.pending = True
                self.weaves = 0
                self.last_event = MECHANICS[self.boss['id']][1]
            defeated = self.enemy['hp'] <= 0
            if state['hp'] <= 0 and (not state.get('ko_recorded')):
                state['ko_recorded'] = True
                await storage.knockout_beast(state['id'], state['rarity'])
        from utils.progress import track_quest_event
        if kind not in {'swap'}:
            await track_quest_event(uid, 'raid_attack', event_key=f'raid-join:{self.id}:{uid}')
        if damage:
            await track_quest_event(uid, 'raid_damage', amount=damage)
        async with aiosqlite.connect(storage.DB_PATH) as db:
            await db.execute('INSERT INTO raid_participants VALUES(?,?,?) ON CONFLICT(raid_id,user_id) DO UPDATE SET damage_dealt=excluded.damage_dealt', (self.id, uid, self.damage[uid]))
            await db.execute('UPDATE raids SET current_hp=? WHERE id=?', (self.enemy['hp'], self.id))
            await db.commit()
        if defeated:
            await self.finish(True)
        else:
            await self.refresh()

    async def finish(self, victory):
        async with self.lock:
            if self.closed:
                return
            self.closed = True
        async with aiosqlite.connect(storage.DB_PATH) as db:
            await db.execute('UPDATE raids SET status=?,ended_at=CURRENT_TIMESTAMP WHERE id=?', ('completed' if victory else 'failed', self.id))
            await db.commit()
        self.view.stop()
        if self.message:
            await self.message.edit(embed=self.embed(), view=None)
        if not victory:
            await self.channel.send('The companions step back. The stitch can wait while they recover. No catch progress was lost.')
            return
        from cogs.guilds import BOSS_KILL_SCENES
        scene = BOSS_KILL_SCENES.get(self.boss['id'])
        if scene:
            await self.channel.send(embed=discord.Embed(title=scene['title'], description='\n\n'.join(scene['lines']), color=COLORS['legendary']))
        lines = []
        for uid in self.users:
            if self.damage[uid] + self.support[uid] <= 0:
                continue
            caught, clears = await self.reward(uid)
            from utils.progress import track_quest_event, record_bestiary_sighting
            await track_quest_event(uid, 'raid_complete', raid_type='corrupted' if self.guild_id else 'ancient', event_key=f'raid-clear:{self.id}:{uid}')
            for state in self.party[uid]:
                await beast_exp(uid, state['id'], 80)
            if caught:
                beast = storage.get_beast_data(self.boss['id'])
                await storage.add_beast_to_player(uid, {**beast, 'caught_from': 'raid'}, grant_key=f'raid-catch:{self.id}:{uid}')
                await record_bestiary_sighting(self.channel.guild.id, beast['id'], uid)
            lines.append(f"<@{uid}> · {self.damage[uid]} damage / {self.support[uid]} support · forging materials + gold + shards · {('a companion joins!' if caught else f'catch progress {clears}/{(8 if self.guild_id else 12)}')}")
        if self.guild_id:
            async with aiosqlite.connect(storage.DB_PATH) as db:
                await db.execute('UPDATE guilds SET guild_tokens=guild_tokens+50 WHERE id=?', (self.guild_id,))
                await db.commit()
        async with aiosqlite.connect(storage.DB_PATH) as db:
            for uid in self.users:
                for state in self.party[uid]:
                    if state['hp'] > 0:
                        await db.execute('UPDATE player_beasts SET hp=max_hp WHERE id=?', (state['id'],))
            await db.commit()
        await self.channel.send(embed=discord.Embed(title='🌱 The weave settles', description='\n'.join(lines)[:4000] + '\n\nEvery active contributor earns rewards, including support. Catch progress guarantees a companion after enough successful rescues.', color=COLORS['success']))

    async def reward(self, uid):
        ancient = not self.guild_id
        limit = 12 if ancient else 8
        boss_id = self.boss['id']
        materials = {'chronos_gear': 2, 'tear_of_the_creator': 1} if boss_id == 'ancient_chronos' else {'blighted_world_core': 2, 'tear_of_the_creator': 1} if ancient else {'kraken_ink': 2} if boss_id == 'corrupted_leviathan' else {'fenrir_fur': 2} if boss_id == 'corrupted_fenrir' else {'dragon_scale': 2, 'sunforge_residue': 2}
        async with aiosqlite.connect(storage.DB_PATH) as db:
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT caught FROM raid_rewards WHERE raid_id=? AND user_id=?', (self.id, uid)) as c:
                old = await c.fetchone()
            if old:
                return (bool(old[0]), 0)
            await db.execute('INSERT INTO raid_catch_progress VALUES(?,?,1) ON CONFLICT(user_id,beast_id) DO UPDATE SET clears=clears+1', (uid, boss_id))
            async with db.execute('SELECT clears FROM raid_catch_progress WHERE user_id=? AND beast_id=?', (uid, boss_id)) as c:
                clears = (await c.fetchone())[0]
            caught = clears >= limit or random.random() < (0.08 if ancient else 0.05)
            if caught:
                await db.execute('UPDATE raid_catch_progress SET clears=0 WHERE user_id=? AND beast_id=?', (uid, boss_id))
            await db.execute('INSERT INTO raid_rewards VALUES(?,?,?)', (self.id, uid, int(caught)))
            from utils.rewards import shards
            await shards(db, uid, 8 if ancient else 3)
            await db.execute('UPDATE players SET gold=gold+?,guild_tokens=guild_tokens+10 WHERE user_id=?', (500 if ancient else 300, uid))
            for mid, amount in materials.items():
                await add_stack(db, 'player_materials', uid, mid, amount)
            from utils.artwork import PROJECT_ROOT
            import json
            known_materials = json.loads((PROJECT_ROOT / 'data/materials.json').read_text(encoding='utf-8'))['materials']
            loot = random.choice(self.boss['loot_table'])
            await add_stack(db, 'player_materials' if loot in known_materials else 'player_inventory', uid, loot)
            await db.commit()
        from cogs.battle import award_player_exp
        await award_player_exp(uid, 250 if ancient else 150)
        return (caught, 0 if caught else clears)

class RaidView(discord.ui.View):

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
        for index, (kind, label) in enumerate([('attack', 'Attack'), ('skill', 'Skill'), ('guard', 'Guard'), ('ultimate', 'Ultimate'), ('weave', MECHANICS[session.boss['id']][0]), ('tend', 'Tend an ally'), ('swap', 'Swap companion')]):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.success if kind in {'weave', 'tend'} else discord.ButtonStyle.primary, row=index // 4)

            async def action(bi, key=kind):
                await bi.response.defer(ephemeral=True)
                try:
                    await self.session.action(bi.user.id, key)
                except ValueError as error:
                    await bi.followup.send(str(error), ephemeral=True)
            button.callback = action
            self.add_item(button)

async def refund_raid(raid_id):
    async with aiosqlite.connect(storage.DB_PATH) as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT user_id,guild_id,item_id,refunded FROM raid_payments WHERE raid_id=?', (raid_id,)) as c:
            payment = await c.fetchone()
        if payment and (not payment[3]):
            uid, guild, item, _ = payment
            if guild:
                await db.execute('UPDATE guilds SET guild_tokens=guild_tokens+50 WHERE id=?', (guild,))
            elif item:
                await add_stack(db, 'player_inventory', uid, item)
            await db.execute('UPDATE raid_payments SET refunded=1 WHERE raid_id=?', (raid_id,))
        await db.execute("UPDATE raids SET status='abandoned',ended_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (raid_id,))
        await db.commit()

async def recover_raids():
    async with aiosqlite.connect(storage.DB_PATH) as db:
        async with db.execute("SELECT id FROM raids WHERE status='active'") as c:
            ids = [r[0] for r in await c.fetchall()]
    for id in ids:
        if id not in active_raids:
            await refund_raid(id)
