"""Shared cooperative raid sessions with distinct, readable boss responses."""
import asyncio
import random
import time
import json
import logging
import math
from contextlib import AsyncExitStack, suppress
from collections import deque
import aiosqlite
import discord
from utils import db as storage, combat
from utils.duels import prepare_state, active_fighters
from utils.gameplay import add_stack
from utils.theme import COLORS, hp_bar
from utils.raid_balance import BOSS_INTERVAL
from utils.raid_art import RaidPresentation, Event, MAX_EVENTS, capture
active_raids = {}
open_lobbies = set()
RAID_IO_TIMEOUT = 15
RAID_LOBBY_SECONDS = 60
MECHANICS = {'corrupted_leviathan': ('Cleanse the current', 'Tainted water is gathering. Use Weave to cleanse it before the next surge.', 'cleanse'), 'corrupted_fenrir': ('Close the rift', 'A rift marks the next strike. Weave it shut or guard against the echo.', 'rift'), 'corrupted_dragon': ('Vent the flame', 'Null-flame is rising. Guard shelters you; Weave vents part of the pressure.', 'flame'), 'ancient_chronos': ('Anchor this moment', 'Chronos is recording an echo of your attacks. Guard before the echo returns; Weave weakens it.', 'echo'), 'ancient_genesis': ('Share the ember', 'An ember is seeking a place to rest. Weave together to turn the coming burst into a shield.', 'ember'), 'ancient_abyss': ('Place a guiding star', "The light is thinning. Weave guiding stars to restore everyone's attack strength.", 'stars')}

async def party_rows(uid):
    rows = await storage.get_raid_party(uid)
    if any(row is None for row in rows):
        raise ValueError('Set three companions with /raidparty first.')
    if any((storage.is_knocked_out(r) for r in rows)):
        raise ValueError('A party companion is recovering. Use Phoenix Elixir or choose another party.')
    return rows

class RaidCooldownError(ValueError):
    """A valid raid input that is still recovering, rather than an invalid action."""


class RaidControls(discord.ui.View):
    async def on_error(self, interaction, error, item):
        logging.getLogger(__name__).error(
            'Raid control failed: raid=%s trainer=%s control=%s',
            getattr(getattr(self, 'session', None), 'id', None), interaction.user.id,
            getattr(item, 'custom_id', None), exc_info=error,
        )
        try:
            async with asyncio.timeout(RAID_IO_TIMEOUT):
                message = 'That raid action could not finish. Check the raid panel before trying again.'
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
        except (discord.HTTPException, TimeoutError):
            pass


class RaidLobby(RaidControls):

    def __init__(self, owner, guild_id):
        super().__init__(timeout=RAID_LOBBY_SECONDS)
        self.owner = owner
        self.guild_id = guild_id
        self.users = {owner}
        self.cancelled = False
        self.closed = False

    async def eligible(self, uid):
        player = await storage.get_player(uid)
        if not player or player['level'] < 10:
            raise ValueError('Reach trainer level 10 before joining a raid.')
        if uid in active_fighters:
            raise ValueError('Finish your current battle first.')
        if self.guild_id:
            async with storage.connect() as db:
                async with db.execute('SELECT 1 FROM guild_members WHERE user_id=? AND guild_id=?', (uid, self.guild_id)) as c:
                    member = await c.fetchone()
            if not member:
                raise ValueError('This raid is for members of the summoning guild.')
        await party_rows(uid)

    @discord.ui.button(label='Join raid · three beasts', style=discord.ButtonStyle.success)
    async def join(self, bi, button):
        await bi.response.defer(ephemeral=True)
        try:
            if self.closed:
                raise ValueError('This lobby has closed.')
            await self.eligible(bi.user.id)
            if self.closed:
                raise ValueError('This lobby has closed.')
            if len(self.users) >= 10 and bi.user.id not in self.users:
                raise ValueError('This party is full (10 trainers).')
            self.users.add(bi.user.id)
            await bi.followup.send(f'You are in. {len(self.users)}/10 trainers; the raid starts when the host is ready or after 60 seconds.', ephemeral=True)
        except ValueError as error:
            await bi.followup.send(str(error), ephemeral=True)

    async def on_timeout(self):
        self.closed = True

    @discord.ui.button(label='Start now', style=discord.ButtonStyle.primary)
    async def begin(self, bi, button):
        if bi.user.id != self.owner:
            return await bi.response.send_message('The host starts the raid.', ephemeral=True)
        if self.closed:
            return await bi.response.send_message('This lobby has closed.', ephemeral=True)
        self.closed = True
        self.stop()
        await bi.response.edit_message(view=None)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, bi, button):
        if bi.user.id != self.owner:
            return await bi.response.send_message('The host can cancel.', ephemeral=True)
        if self.closed:
            return await bi.response.send_message('This lobby has closed.', ephemeral=True)
        self.closed = True
        self.cancelled = True
        self.stop()
        await bi.response.edit_message(content='Raid cancelled. Nothing was spent.', view=None)

async def open_raid(interaction, boss, *, guild_id=None, summon_item=None):
    channel = interaction.channel
    uid = interaction.user.id
    if channel.id in open_lobbies or any((s.channel.id == channel.id for s in active_raids.values())):
        raise ValueError('A raid or lobby is already open here.')
    lobby = RaidLobby(uid, guild_id)
    open_lobbies.add(channel.id)
    message = None
    try:
        await lobby.eligible(uid)
        async with asyncio.timeout(RAID_IO_TIMEOUT):
            message = await interaction.followup.send(embed=discord.Embed(title=f"{boss['name']} · Gather the party", description=f"{boss['description']}\n\nUp to ten trainers. Set three healthy companions with /raidparty. Aim for a prepared party and a 5–8 minute fight; bring healing and answer the patterns.\n{MECHANICS[boss['id']][1]}\n\nThe cost is spent only when the raid begins.", color=COLORS['legendary']), view=lobby)
        try:
            # View's idle timeout resets on clicks; the advertised lobby deadline does not.
            async with asyncio.timeout(RAID_LOBBY_SECONDS):
                await lobby.wait()
        except TimeoutError:
            pass
        lobby.closed = True
        lobby.stop()
        async with asyncio.timeout(RAID_IO_TIMEOUT):
            await message.edit(view=None)
        message = None
        if lobby.cancelled:
            return
        for member in sorted(lobby.users):
            await lobby.eligible(member)
        session = RaidSession(channel, boss, lobby.users, guild_id)
        await session.start(uid, summon_item)
        await session.run()
    finally:
        lobby.closed = True
        lobby.stop()
        open_lobbies.discard(channel.id)
        if message is not None:
            with suppress(discord.HTTPException, TimeoutError):
                async with asyncio.timeout(RAID_IO_TIMEOUT):
                    await message.edit(view=None)

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
        self.weavers = set()
        self.pattern_started = 0
        self.pattern_grace = 6
        self.phase = 0
        self.message = None
        self._refresh_task = None
        self._refresh_revision = 0
        self._render_lock = asyncio.Lock()
        self.presentation = RaidPresentation()
        self._art_events = deque(maxlen=MAX_EVENTS)
        self._art_sequence = 0
        self._art_urgent = asyncio.Event()
        self._finish_task = None
        self._finished = asyncio.Event()
        self._display_error = None
        self._pattern_needs_publish = False
        self._participated = set()
        self._progress_locks = {uid: asyncio.Lock() for uid in self.users}
        self.refresh_interval = 1.5
        self.totems = {}
        self.clock = time.monotonic
        self.started = self.clock()
        self.last_event = 'The party makes room for an unfinished creature.'

    def state(self, uid):
        return self.party[uid][self.slots[uid]]

    def weave_required(self):
        standing = sum(any(state['hp'] > 0 for state in party) for party in self.party.values())
        return max(1, min(2, standing))

    async def start(self, owner, item):
        for uid in self.users:
            if uid in active_fighters:
                raise ValueError('A trainer has entered another battle; nothing was spent.')
        active_fighters.update(self.users)
        try:
            rows = {uid: await party_rows(uid) for uid in self.users}
            async with storage.connect() as db:
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
                async with storage.connect() as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute('SELECT * FROM player_perks WHERE user_id=? AND equipped=1', (uid,)) as c:
                        perks = [dict(r) for r in await c.fetchall()]
                from utils.sanctuary import get_user_sanctuary
                sanctuary = await get_user_sanctuary(uid)
                self.party[uid] = []
                for index, row in enumerate(rows[uid]):
                    state = await prepare_state(uid, row, storage.get_beast_data(row['beast_id']), perks, consume=index == 0)
                    state['ultimate_charges'] = 0
                    if sanctuary.get('raid_altar'):
                        state['damage_multiplier'] *= 1.1
                        state['damage_reduction_flat_percent'] = state.get('damage_reduction_flat_percent', 0) + 5
                    self.party[uid].append(state)
                async with storage.connect() as db:
                    async with db.execute('SELECT raid_totem_charges FROM players WHERE user_id=?', (uid,)) as c:
                        totem = (await c.fetchone())[0]
                    self.totems[uid] = totem > 0
                    if totem:
                        await db.execute('UPDATE players SET raid_totem_charges=raid_totem_charges-1 WHERE user_id=?', (uid,))
                    await db.commit()
                combat.active_states[uid] = self.state(uid)
                combat.active_parties[uid] = self.party[uid]
            self.build_enemy()
            for uid in self.users:
                combat.start_battle(self.state(uid), self.enemy, [])
            active_raids[self.id] = self
            async with storage.connect() as db:
                await db.execute('UPDATE raids SET max_hp=?,current_hp=? WHERE id=?', (self.enemy['max_hp'], self.enemy['hp'], self.id))
                await db.commit()
        except BaseException:
            active_raids.pop(self.id, None)
            active_fighters.difference_update(self.users)
            for uid in self.users:
                combat.active_states.pop(uid, None)
                combat.active_parties.pop(uid, None)
            if self.id:
                await refund_raid(self.id)
            raise

    def build_enemy(self):
        from utils.raid_balance import budget
        data = storage.get_beast_data(self.boss['id'])
        row = budget(self.boss['id'], self.party)
        self.reference_speed = row['reference_speed']
        self.raid_level = row['level']
        row['max_hp'] = row['hp']
        self.enemy = combat.build_state(data, row)
        self.enemy['divine_passive'] = {}
        self.enemy['raid_boss'] = True
        self.enemy['reference_attack'] = row['reference_attack']

    def action_cooldown(self, uid):
        from utils.raid_balance import action_interval
        state = self.state(uid)
        return action_interval(state, getattr(self, 'reference_speed', state['speed']))

    def embed(self):
        hp = self.enemy['hp']
        remaining = max(0, min(600, math.ceil(600 - (self.clock() - self.started))))
        description = f"{hp_bar(hp, self.enemy['max_hp'])}\n{remaining // 60}:{remaining % 60:02} remaining · {len(self.users)}/10 trainers\n\n{self.last_event}"
        if self.pending:
            description += f"\n\n⚠️ **{MECHANICS[self.boss['id']][0]}** — {MECHANICS[self.boss['id']][1]}\nWeave: {self.weaves}/{self.weave_required()}"
        embed = discord.Embed(title=f"⚔️ {self.boss['name']} · Lv.{self.enemy.get('level', 1)}", description=description, color=COLORS['legendary'])
        lines = []
        for position, uid in enumerate(self.users, 1):
            state = self.state(uid)
            name = discord.utils.escape_markdown(state['name'])
            lines.append(f"**{position:02}** <@{uid}> · **{name}**\n{state['hp']}/{state['max_hp']} HP · {state['mana']}/{state['max_mana']} MP · ⚡ {state.get('ultimate_charges', 0)}/3 · {self.action_cooldown(uid):.1f}s actions · {self.damage[uid]} damage / {self.support[uid]} support")
        chunk = ''
        for line in lines:
            if len(chunk) + len(line) + 2 > 1024:
                embed.add_field(name='Companions' if not embed.fields else 'Companions (continued)', value=chunk, inline=False)
                chunk = ''
            chunk += ('\n\n' if chunk else '') + line
        if chunk:
            embed.add_field(name='Companions' if not embed.fields else 'Companions (continued)', value=chunk, inline=False)
        embed.set_image(url=self.boss['image_url'])
        embed.set_footer(text='Weave answers patterns · Tend heals · Speed controls action recovery · Bosses resist repeated stuns')
        return embed

    def _record_art(self, uid, kind, damage=0, target=None, *, move='', species='', slot=None):
        if self.presentation is not None and self.presentation.enabled:
            self._art_sequence += 1
            self._art_events.append(Event(self._art_sequence, uid, kind, damage, target, move, species, slot))

    async def _capture_art(self, outcome=''):
        async with self.lock, combat.lock_states(self.users):
            return (capture(self, MECHANICS[self.boss['id']][0], outcome),
                    tuple(self._art_events), self._art_sequence, self.embed())

    async def _mark_warning_visible(self, phase):
        async with self.lock:
            if self.pending and self.phase == phase and self._pattern_needs_publish:
                self.pattern_started = self.clock()
                self._pattern_needs_publish = False

    async def _prepare_art(self, scene, events, *, final=False):
        if final:
            return await self.presentation.prepare(scene, events)
        rendering = asyncio.create_task(self.presentation.prepare(scene, events))
        urgent = asyncio.create_task(self._art_urgent.wait())
        try:
            done, _ = await asyncio.wait((rendering, urgent), return_when=asyncio.FIRST_COMPLETED)
            if urgent in done:
                return None
            return await rendering
        finally:
            for task in (rendering, urgent):
                if not task.done():
                    task.cancel()
            await asyncio.gather(rendering, urgent, return_exceptions=True)

    async def _publish_panel(self, *, initial=False, outcome='', final=False):
        """Caller owns _render_lock. Never hold combat locks during media/I/O."""
        if self.presentation is None:
            async with asyncio.timeout(RAID_IO_TIMEOUT):
                if initial:
                    self.message = await self.channel.send(embed=self.embed(), view=self.view)
                else:
                    await self.message.edit(embed=self.embed(), view=None if final else self.view)
            return True
        scene, events, sequence, embed = await self._capture_art(outcome)
        view = None if final else self.view
        if scene.pending and self._pattern_needs_publish and not initial and not final:
            # Native warning first. Retain the last attachment so controls do not
            # jump while a renderer is busy, then refresh the arena separately.
            async with asyncio.timeout(RAID_IO_TIMEOUT):
                self.message = await self.presentation.publish(self.channel, self.message, embed,
                                                               view, keep=True)
            await self._mark_warning_visible(scene.phase)
        artwork = await self._prepare_art(scene, events, final=final or initial)
        if not final and not initial:
            async with self.lock:
                if self.closed or self.enemy['hp'] <= 0:
                    return False
                if self._art_urgent.is_set() or (self.phase, bool(self.pending)) != (scene.phase, scene.pending):
                    return False
        async with asyncio.timeout(RAID_IO_TIMEOUT):
            self.message = await self.presentation.publish(self.channel, self.message, embed, view,
                                                           artwork=artwork, keep=artwork is None and self.presentation.enabled and not final)
        if initial and self.presentation.previous is None:
            self.presentation.previous = scene
        if artwork and self.presentation.filename:
            self.presentation.accepted(scene, sequence)
            while self._art_events and self._art_events[0].sequence <= sequence:
                self._art_events.popleft()
        elif not self.presentation.enabled:
            self._art_events.clear()
        await self._mark_warning_visible(scene.phase)
        if final and artwork and artwork.seconds and self.presentation.filename:
            # Rewards are already saved and controls removed. Only the closing
            # scene waits for playback; active player callbacks never wait.
            await self.presentation.sleep(artwork.seconds + .2)
            still = await self.presentation.prepare(scene)
            if still:
                async with asyncio.timeout(RAID_IO_TIMEOUT):
                    self.message = await self.presentation.publish(self.channel, self.message, embed, None, artwork=still)
        return True

    async def refresh(self):
        if not self.closed and self.message:
            self._refresh_revision += 1
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._flush_refresh())

    async def _flush_refresh(self):
        while not self.closed and self.message:
            if self.refresh_interval:
                with suppress(TimeoutError):
                    async with asyncio.timeout(self.refresh_interval):
                        await self._art_urgent.wait()
            else:
                await asyncio.sleep(0)
            self._art_urgent.clear()
            async with self._render_lock:
                if self.closed or not self.message:
                    return
                revision, phase = self._refresh_revision, self.phase
                try:
                    published = await self._publish_panel()
                    if self.presentation is None:
                        await self._mark_warning_visible(phase)
                except (discord.NotFound, discord.Forbidden, TimeoutError):
                    self._display_error = ValueError('The raid panel is unavailable. This raid has closed; check /profile before starting another.')
                    self._finished.set()
                    return
                except discord.HTTPException:
                    logging.getLogger(__name__).warning('Raid display update failed', exc_info=True)
                    published = True
                if published and revision == self._refresh_revision:
                    return

    async def _stop_refresh(self):
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

    async def run(self):
        self.view = RaidView(self)
        self.started = self.clock()
        try:
            async with self._render_lock:
                await self._publish_panel(initial=True)
            while not self.closed and self.clock() - self.started < 600:
                try:
                    async with asyncio.timeout(min(BOSS_INTERVAL, max(0, 600 - (self.clock() - self.started)))):
                        await self._finished.wait()
                    if self._display_error:
                        raise self._display_error
                    break
                except TimeoutError:
                    pass
                async with self.lock, combat.lock_states(self.users):
                    if self.closed:
                        break
                    if self.enemy['hp'] > 0:
                        await self.boss_turn()
                if self.enemy['hp'] <= 0:
                    await self.finish(True)
                    break
                await self.refresh()
                if not any(s['hp'] > 0 for party in self.party.values() for s in party):
                    await self.finish(False)
            if not self.closed:
                await self.finish(self.enemy['hp'] <= 0)
            if self._finish_task is not None:
                await asyncio.shield(self._finish_task)
        except BaseException:
            # A recorded victory is resumed on restart; an unstarted/failed session is refunded.
            async with self.lock, combat.lock_states(self.users):
                won = self.enemy['hp'] <= 0
                if not won and self._finish_task is None:
                    self.closed = True
                    for party in self.party.values():
                        for state in party:
                            state['closed'] = True
            if won and self._finish_task is None:
                with suppress(Exception):
                    await self.finish(True)
            if self._finish_task is not None:
                with suppress(Exception):
                    await asyncio.shield(self._finish_task)
            await self._wait_for_progress()
            await refund_raid(self.id)
            raise
        finally:
            self.closed = True
            for party in self.party.values():
                for state in party:
                    state['closed'] = True
            try:
                await self._stop_refresh()
            finally:
                active_raids.pop(self.id, None)
                active_fighters.difference_update(self.users)
                for uid in self.users:
                    combat.active_states.pop(uid, None)
                    combat.active_parties.pop(uid, None)
                self.view.stop()

    async def boss_turn(self):
        self.resolve_boss_turn()
        await self.record_knockouts()

    def resolve_boss_turn(self):
        alive = [uid for uid in self.users if self.state(uid)['hp'] > 0]
        if not alive:
            return
        log = []
        if not combat.begin_turn(self.enemy, self.state(alive[0]), log):
            combat.end_turn(self.enemy, self.state(alive[0]), log)
            self.last_event = ' · '.join(log[-2:]) or 'The creature pauses.'
            self._record_art(None, 'status')
            return
        ready_pattern = self.pending and not self._pattern_needs_publish and self.clock() - self.pattern_started >= self.pattern_grace
        if ready_pattern:
            kind = MECHANICS[self.boss['id']][2]
            required = self.weave_required()
            solved = self.weaves >= required
            for uid in alive:
                state = self.state(uid)
                log = []
                if solved and kind in {'cleanse', 'rift', 'ember', 'stars'}:
                    if kind == 'ember':
                        state['shield'] = state.get('shield', 0) + int(state['max_hp'] * 0.2)
                    else:
                        state['status'] = None
                        state['status_turns'] = 0
                        combat.heal(state, int(state['max_hp'] * 0.1))
                else:
                    base = int(self.enemy['attack'] * (0.65 if solved else 1.8) * 100 / (100 + state['defense']))
                    if kind == 'echo':
                        base += min(int(self.damage.get(uid, 0) * 0.01), int(state['max_hp'] * .12))
                    combat.resolve_hit(self.enemy, state, base, log)
                    if not solved and kind in {'cleanse', 'flame', 'rift'}:
                        combat.apply_status(state, {'cleanse': 'poison', 'flame': 'burn', 'rift': 'slow'}[kind], self.enemy, log)
            self.last_event = 'The party loosened the pattern together.' if solved else 'The pattern releases. Guarding sheltered companions from its force.'
            self.pending = None
            self.weaves = 0
            self.weavers.clear()
            self._art_urgent.set()
        else:
            for uid in alive:
                state = self.state(uid)
                log = []
                damage, _, _, _ = combat.calc_damage(self.enemy, state, self.enemy['moves'][0])
                combat.resolve_hit(self.enemy, state, damage, log)
            self.last_event = f"{self.boss['name']} sweeps across the party. Guard, tend an ally, or swap a wounded companion."
        combat.end_turn(self.enemy, self.state(alive[0]), [])
        harmless = ready_pattern and solved and kind in {'cleanse', 'rift', 'ember', 'stars'}
        self._record_art(None, 'boss', move='' if harmless else self.enemy['moves'][0], species=self.enemy['beast_id'])

    async def record_knockouts(self):
        knocked_out = [state for uid in self.users for state in self.party[uid]
                       if state['hp'] <= 0 and not state.get('ko_recorded')]
        if knocked_out:
            await storage.knockout_beasts((state['id'], state['rarity']) for state in knocked_out)
            # A failed commit leaves these eligible for the next tick's retry.
            for state in knocked_out:
                state['ko_recorded'] = True

    async def action(self, uid, kind):
        if uid not in self.party:
            raise ValueError('Join the lobby before the raid begins.')
        async with self.lock, combat.lock_states(self.users):
            damage, defeated = self.resolve_action(uid, kind)
            await self.record_knockouts()
            # Keep snapshots ordered with combat. A delayed older callback must
            # never overwrite the final boss HP or a newer contribution total.
            async with storage.connect() as db:
                await db.execute('INSERT INTO raid_participants VALUES(?,?,?) ON CONFLICT(raid_id,user_id) DO UPDATE SET damage_dealt=excluded.damage_dealt', (self.id, uid, self.damage[uid]))
                await db.execute('UPDATE raids SET current_hp=? WHERE id=?', (self.enemy['hp'], self.id))
                await db.commit()
        from utils.progress import track_quest_event
        try:
            async with self._progress_locks[uid]:
                if kind != 'swap' and uid not in self._participated:
                    await track_quest_event(uid, 'raid_attack', event_key=f'raid-join:{self.id}:{uid}')
                    self._participated.add(uid)
                if damage:
                    await track_quest_event(uid, 'raid_damage', amount=damage)
        finally:
            if defeated:
                await self.finish(True)
            else:
                await self.refresh()

    def resolve_action(self, uid, kind):
        """Resolve one validated input; caller owns locks and persistence."""
        if uid not in self.party:
            raise ValueError('Join the lobby before the raid begins.')
        if self.closed or (hasattr(self, 'enemy') and self.enemy['hp'] <= 0):
            raise ValueError('This raid is complete.')
        if kind not in {'attack', 'skill', 'guard', 'ultimate', 'swap', 'weave', 'tend'}:
            raise ValueError('Choose an action from the raid controls.')
        state = self.state(uid)
        if state['hp'] <= 0 and kind != 'swap':
            raise ValueError('Swap to a healthy bench companion.')
        key = (uid, kind if kind in {'weave', 'tend'} else 'action')
        cooldown = 8 if kind in {'weave', 'tend'} else self.action_cooldown(uid)
        last = self.last.get(key)
        if last is not None and (remaining := cooldown - (self.clock() - last)) > 0:
            raise RaidCooldownError(f'Your companion is ready for that action in {max(.1, remaining):.1f}s.')
        log = []
        damage = 0
        target_uid = None
        animation_kind = kind
        animation_move = ''
        source_species = state['beast_id']
        source_slot = self.slots[uid]
        if kind == 'swap':
            count = len(self.party[uid])
            options = [(self.slots[uid] + offset) % count for offset in range(1, count)
                       if self.party[uid][(self.slots[uid] + offset) % count]['hp'] > 0]
            if not options:
                raise ValueError('No healthy companion remains on the bench.')
            self.slots[uid] = options[0]
            combat.active_states[uid] = self.state(uid)
            self.last_event = f"<@{uid}> brings in {self.state(uid)['name']}."
        elif kind == 'weave':
            if not self.pending:
                raise ValueError('The weave is steady. Watch for the next announced pattern.')
            if uid in self.weavers:
                raise ValueError('You have already helped with this pattern. Another trainer can complete the weave.')
            self.weavers.add(uid)
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
            target_uid = other
            state['mana'] -= 15
            target['status'] = None
            target['status_turns'] = 0
            restored = combat.heal(target, max(1, int(target['max_hp'] * 0.18)))
            self.support[uid] += restored
            self.last_event = f"<@{uid}> tends <@{other}>'s {target['name']}: +{restored} HP."
        else:
            index = {'attack': 0, 'skill': 1, 'guard': 2}.get(kind, 0)
            ult = kind == 'ultimate'
            move = state['ultimate'] if ult else state['moves'][index]
            if not combat.can_use(state, move, ult):
                raise ValueError('Not enough mana or ultimate charge. Attack or guard to build resources.')
            if combat.move_spec(state, move, ult).get('heal_percent'):
                animation_kind = 'heal'
            if combat.begin_turn(state, self.enemy, log):
                animation_move = move
                old_hp = state['hp']
                old = self.enemy['hp']
                if self.totems[uid]:
                    state['damage_multiplier'] *= 1.2
                combat.resolve_move(state, self.enemy, move, ult, log)
                if state['hp'] > 0 and self.enemy['hp'] > 0 and combat.bonus_action(state):
                    combat.resolve_move(state, self.enemy, state['moves'][0], False, log, grant_charge=False)
                if self.totems[uid]:
                    state['damage_multiplier'] /= 1.2
                combat.end_turn(state, self.enemy, log)
                self.support[uid] += max(0, state['hp'] - old_hp)
                if self.pending and MECHANICS[self.boss['id']][2] == 'stars' and (self.weaves < self.weave_required()):
                    loss = int((old - self.enemy['hp']) * 0.3)
                    self.enemy['hp'] += loss
                damage = max(0, old - self.enemy['hp'])
                self.damage[uid] += damage
                if kind == 'guard' and self.pending:
                    self.support[uid] += max(1, int(state['max_hp'] * 0.04))
            else:
                animation_kind = 'status'
            self.last_event = ' · '.join(log[-2:])[-500:]
        self.last[key] = self.clock()
        pct = self.enemy['hp'] / self.enemy['max_hp']
        if not self.pending and self.enemy['hp'] > 0 and self.phase < 3 and pct < (0.75, 0.45, 0.2)[self.phase]:
            self.phase += 1
            self.pending = True
            self.weaves = 0
            self.weavers.clear()
            self.pattern_started = self.clock()
            self._pattern_needs_publish = bool(self.message)
            self._art_urgent.set()
            self.last_event = MECHANICS[self.boss['id']][1]
        defeated = self.enemy['hp'] <= 0
        self._record_art(uid, animation_kind, damage, target_uid,
                         move=animation_move, species=source_species, slot=source_slot)
        return damage, defeated

    async def finish(self, victory):
        # A callback can be cancelled independently of the command's raid runner.
        # One shielded settlement task owns the result; every caller waits for it.
        if self._finish_task is None:
            self._finish_task = asyncio.create_task(self._finish_and_signal(victory))
        return await asyncio.shield(self._finish_task)

    async def _finish_and_signal(self, victory):
        try:
            await self._finish(victory)
        finally:
            self._finished.set()

    async def _wait_for_progress(self):
        async with AsyncExitStack() as stack:
            for uid in self.users:
                await stack.enter_async_context(self._progress_locks[uid])

    async def _finish(self, victory):
        async with self.lock, combat.lock_states(self.users):
            if self.closed:
                return
            self.closed = True
            await self.record_knockouts()
            for party in self.party.values():
                for state in party:
                    state['closed'] = True
        await self._stop_refresh()
        if victory:
            payload = {
                'boss': self.boss, 'users': self.users, 'guild_id': self.guild_id,
                'server_id': getattr(getattr(self.channel, 'guild', None), 'id', None),
                'party': {str(uid): [{'id': state['id'], 'hp': state['hp']} for state in self.party[uid]] for uid in self.users},
                'damage': self.damage, 'support': self.support,
                'raid_level': getattr(self, 'raid_level', 10),
            }
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                await db.execute('INSERT OR IGNORE INTO raid_settlements(raid_id,payload) VALUES(?,?)', (self.id, json.dumps(payload)))
                await db.execute("UPDATE raids SET status='settling' WHERE id=?", (self.id,))
                await db.commit()
            # Save the victory first so restart recovery can resume it. Then let
            # resolved damage events finish before raid_complete advances story.
            await self._wait_for_progress()
            lines = await self._settle_rewards(payload['server_id'])
        else:
            await self._wait_for_progress()
            async with storage.connect() as db:
                await db.execute("UPDATE raids SET status='failed',ended_at=CURRENT_TIMESTAMP WHERE id=?", (self.id,))
                await db.commit()
            lines = []
        # Reward settlement precedes every Discord edit/send; deleted messages cannot eat rewards.
        try:
            async with asyncio.timeout(RAID_IO_TIMEOUT):
                async with self._render_lock:
                    if self.message:
                        try:
                            await self._publish_panel(final=True, outcome='victory' if victory else 'defeat')
                        except discord.NotFound:
                            logging.getLogger(__name__).info('Raid panel %s was already removed; rewards are saved', getattr(self.message, 'id', None))
                        except discord.HTTPException:
                            logging.getLogger(__name__).warning('Raid rewards saved, but closed controls could not be removed', exc_info=True)
                if not victory:
                    await self.channel.send('The companions step back. The stitch can wait while they recover. No catch progress was lost.')
                    return
                from cogs.guilds import BOSS_KILL_SCENES
                scene = BOSS_KILL_SCENES.get(self.boss['id'])
                if scene:
                    await self.channel.send(embed=discord.Embed(title=scene['title'], description='\n\n'.join(scene['lines']), color=COLORS['legendary']))
                await self.channel.send(embed=discord.Embed(title='🌱 The weave settles', description='\n'.join(lines)[:3700] + '\n\nEvery active contributor earns rewards, including support. Catch progress guarantees a companion after enough successful rescues.', color=COLORS['success']))
        except (discord.HTTPException, TimeoutError):
            logging.getLogger(__name__).warning('Raid rewards saved, but the result message could not be sent', exc_info=True)
        finally:
            if getattr(self, 'view', None):
                self.view.stop()

    async def _settle_rewards(self, server_id):
        from utils.progress import track_quest_event, record_bestiary_sighting
        lines = []
        for uid in self.users:
            if self.damage[uid] + self.support[uid] <= 0:
                continue
            caught, clears = await self.reward(uid)
            await track_quest_event(uid, 'raid_complete', raid_type='corrupted' if self.guild_id else 'ancient', event_key=f'raid-clear:{self.id}:{uid}')
            if caught and server_id:
                await record_bestiary_sighting(server_id, self.boss['id'], uid)
            lines.append(f"<@{uid}> · {self.damage[uid]} damage / {self.support[uid]} support · forging materials + gold + shards · " + ('a companion joins!' if caught else f'catch progress {clears}/{8 if self.guild_id else 12}'))
        async with storage.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            completed = await db.execute('UPDATE raid_settlements SET completed=1 WHERE raid_id=? AND completed=0', (self.id,))
            if completed.rowcount and self.guild_id:
                await db.execute('UPDATE guilds SET guild_tokens=guild_tokens+50 WHERE id=?', (self.guild_id,))
            for uid in self.users:
                for state in self.party[uid]:
                    if state['hp'] > 0:
                        await db.execute('UPDATE player_beasts SET hp=max_hp WHERE user_id=? AND id=?', (uid, state['id']))
            await db.execute("UPDATE raids SET status='completed',ended_at=CURRENT_TIMESTAMP WHERE id=?", (self.id,))
            await db.commit()
        return lines

    async def reward(self, uid):
        from utils.rewards import shards, player_exp
        from utils.duels import beast_exp_in_transaction
        from utils.catalog import load_catalog
        from utils.raid_balance import reward_scale
        scale = reward_scale(getattr(self, 'raid_level', 10))
        ancient = not self.guild_id
        limit = 12 if ancient else 8
        boss_id = self.boss['id']
        materials = {'chronos_gear': 2, 'tear_of_the_creator': 1} if boss_id == 'ancient_chronos' else {'blighted_world_core': 2, 'tear_of_the_creator': 1} if ancient else {'kraken_ink': 2} if boss_id == 'corrupted_leviathan' else {'fenrir_fur': 2} if boss_id == 'corrupted_fenrir' else {'dragon_scale': 2, 'sunforge_residue': 2}
        beast = {**storage.get_beast_data(boss_id), 'caught_from': 'raid'}
        catch_id = None
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT caught FROM raid_rewards WHERE raid_id=? AND user_id=?', (self.id, uid)) as c:
                old = await c.fetchone()
            if old:
                caught = bool(old[0])
                async with db.execute('SELECT clears FROM raid_catch_progress WHERE user_id=? AND beast_id=?', (uid, boss_id)) as c:
                    progress = await c.fetchone()
                clears = progress[0] if progress else 0
            else:
                await db.execute('INSERT INTO raid_catch_progress VALUES(?,?,1) ON CONFLICT(user_id,beast_id) DO UPDATE SET clears=clears+1', (uid, boss_id))
                async with db.execute('SELECT clears FROM raid_catch_progress WHERE user_id=? AND beast_id=?', (uid, boss_id)) as c:
                    clears = (await c.fetchone())[0]
                caught = clears >= limit or random.random() < (0.08 if ancient else 0.05)
                if caught:
                    await db.execute('UPDATE raid_catch_progress SET clears=0 WHERE user_id=? AND beast_id=?', (uid, boss_id))
                await db.execute('INSERT INTO raid_rewards(raid_id,user_id,caught) VALUES(?,?,?)', (self.id, uid, int(caught)))
                await shards(db, uid, round((8 if ancient else 3) * scale))
                await db.execute('UPDATE players SET gold=gold+?,guild_tickets=guild_tickets+? WHERE user_id=?', (round((500 if ancient else 300) * scale), round(10 * scale), uid))
                for mid, amount in materials.items():
                    await add_stack(db, 'player_materials', uid, mid, round(amount * scale))
                known_materials = load_catalog('materials.json')['materials']
                loot = random.choice(self.boss['loot_table'])
                await add_stack(db, 'player_materials' if loot in known_materials else 'player_inventory', uid, loot)
                exp_gain = round((250 if ancient else 150) * scale)
                async with db.execute('SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON s.guild_id=m.guild_id WHERE m.user_id=? AND s.arcane_library=1', (uid,)) as c:
                    if await c.fetchone():
                        exp_gain = round(exp_gain * 1.15)
                level, _, leveled = await player_exp(db, uid, exp_gain)
                levels = []
                for state in self.party.get(uid, []):
                    _, beast_leveled = await beast_exp_in_transaction(db, uid, state['id'], round(80 * scale))
                    if beast_leveled:
                        levels.append(state['id'])
                await db.execute('INSERT INTO raid_reward_details VALUES(?,?,?,?)', (self.id, uid, level if leveled else None, json.dumps(levels)))
            if caught:
                catch_id = await storage.grant_beast(db, uid, beast, grant_key=f'raid-catch:{self.id}:{uid}')
            async with db.execute('SELECT player_level,beast_levels FROM raid_reward_details WHERE raid_id=? AND user_id=?', (self.id, uid)) as c:
                details = await c.fetchone()
            await db.commit()
        if catch_id:
            from utils.events import acquired_beast
            await acquired_beast(uid, catch_id, beast, 'raid')
        if details:
            from utils.progress import track_quest_event
            if details['player_level']:
                from cogs.questline import advance_quest_step
                await advance_quest_step(uid, 'level_up', level=details['player_level'])
            if json.loads(details['beast_levels']):
                await track_quest_event(uid, 'beast_level_up', amount=len(json.loads(details['beast_levels'])), event_key=f'raid-levels:{self.id}:{uid}')
        return (caught, 0 if caught else clears)

class RaidView(RaidControls):

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
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute("SELECT status FROM raids WHERE id=?", (raid_id,)) as c:
            status = await c.fetchone()
        if not status or status[0] not in {'active', 'abandoned'}:
            return
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
    # Replay recorded victories before refunding interrupted active sessions.
    async with storage.connect() as db:
        async with db.execute('SELECT raid_id,payload FROM raid_settlements WHERE completed=0') as c:
            pending = await c.fetchall()
    for raid_id, serialized in pending:
        if raid_id in active_raids:
            continue
        payload = json.loads(serialized)
        session = RaidSession(None, payload['boss'], payload['users'], payload['guild_id'])
        session.id = raid_id
        session.raid_level = payload.get('raid_level', 10)
        session.party = {int(uid): party for uid, party in payload['party'].items()}
        session.damage = {int(uid): amount for uid, amount in payload['damage'].items()}
        session.support = {int(uid): amount for uid, amount in payload['support'].items()}
        await session._settle_rewards(payload['server_id'])
    async with storage.connect() as db:
        async with db.execute("SELECT id FROM raids WHERE status='active'") as c:
            ids = [r[0] for r in await c.fetchall()]
    for id in ids:
        if id not in active_raids:
            await refund_raid(id)
