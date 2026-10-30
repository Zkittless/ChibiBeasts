"""One versioned Discord panel for a saved tower, including its battle clips."""
import asyncio
import hashlib
import io
import logging
import time
from pathlib import Path
from uuid import uuid4

import discord

from utils import adventure_party, battle_art, crafting, db as storage, journal_sessions, journal_state, journal_team_art
from utils.habitats import BIOMES
from utils.message_transport import durable_message
from utils.trial_rules import config

logger = logging.getLogger('chibibeasts.trials.panel')
_locks, _panels = {}, {}
IO_TIMEOUT = 15
TURN_SECONDS = 45


def _identity(user_id):
    return str(Path(storage.DB_PATH).resolve()), user_id


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def wait_text(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f'{minutes}m {seconds}s' if minutes else f'{seconds}s'


class TrialPanel(discord.ui.LayoutView):
    def __init__(self, user_id, *, bot=None, destination=None):
        super().__init__(timeout=300)
        self.user_id, self.bot = user_id, bot
        self.key = _identity(user_id)
        self.lock = _locks.setdefault(self.key, asyncio.Lock())
        self.prefix = f'trials:{user_id}:{uuid4().hex[:10]}:'
        self.message, self.channel = None, None
        self.run, self.board, self.rows = None, {}, []
        self.destination = destination if destination in BIOMES else 'woods'
        self.mode, self.notice, self.media = 'selector', '', None
        self.presentation = battle_art.BattlePresentation(self.destination)
        self.closed, self.playing, self.recreated = False, False, False
        self.playback_wave = None
        self.generation, self.serial = 0, 0
        self.item_id, self.item_target = None, None
        self.timer = None

    def current(self):
        return not self.closed and not self.is_finished() and _panels.get(self.key) is self

    @property
    def payload(self):
        return self.run['payload'] if self.run else {}

    @property
    def version(self):
        return (self.run['id'], self.run['version']) if self.run else None

    async def load(self):
        from utils import trials
        self.board = await trials.board(self.user_id)
        if self.run:
            self.run = await trials.read(self.user_id, self.run['id'])
        else:
            self.run = self.board.get('run')
        if self.run:
            self.destination = self.run['destination'] if self.mode == 'run' else self.destination
            self.rows = self.payload.get('party', [])
        else:
            self.rows = await adventure_party.rows(self.user_id)
            appearances = {row['id']: row for row in (await crafting.workshop_state(self.user_id)).companions}
            self.rows = [{**row, 'armor_id': appearances.get(row['id'], {}).get('armor_id')} for row in self.rows]

    def button(self, label, action, *, style=discord.ButtonStyle.secondary, disabled=False):
        self.serial += 1
        control = discord.ui.Button(label=label[:80], style=style, disabled=disabled or self.playing,
                                    custom_id=self.prefix + str(self.serial))
        version, generation = self.version, self.generation
        control.callback = lambda click: self.handle(click, action, version, generation)
        return control

    def select(self, placeholder, options, action):
        self.serial += 1
        control = discord.ui.Select(placeholder=placeholder, options=options,
                                    custom_id=self.prefix + str(self.serial), disabled=self.playing)
        version, generation = self.version, self.generation
        control.callback = lambda click: self.handle(click, action, version, generation)
        return control

    def text(self, value):
        self.container.add_item(discord.ui.TextDisplay(value[:3500]))

    def row(self, *items):
        self.container.add_item(discord.ui.ActionRow(*items))

    def team_text(self):
        rows = self.payload.get('party', self.rows)
        slot = self.payload.get('slot', 0)
        return '\n'.join(f"{'Lead' if index == slot else 'Reserve'} · **{safe(row.get('name') or row.get('nickname') or storage.get_beast_data(row['beast_id'])['name'])}** · "
                         f"HP {max(0, row['hp'])}/{row['max_hp']} · MP {max(0, row['mana'])}/{row['max_mana']}"
                         + (' · KO' if row['hp'] <= 0 else ' · ' + safe(row['status']) if row.get('status') else '')
                         for index, row in enumerate(rows)) or 'Choose a healthy exploration team in the Field Journal.'

    def render(self):
        self.clear_items()
        self.generation += 1
        self.container = discord.ui.Container(accent_colour=0x789F88)
        self.add_item(self.container)
        if self.notice:
            self.text(self.notice[:700])
        if self.mode == 'selector':
            self.render_selector()
        else:
            self.render_run()
        if self.content_length() > 3900 or self.total_children_count > 40:
            raise ValueError('Trials panel exceeds Discord message limits.')

    def artwork(self):
        if self.media:
            self.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
                'attachment://' + self.media[0], description=battle_art.REALMS[self.destination] + ' · Tower Trials')))

    def render_selector(self):
        region, rules = BIOMES[self.destination], config(self.destination)
        player = self.board.get('player', {})
        destination = next((row for row in self.board.get('destinations', ()) if row['key'] == self.destination), {})
        cooldown = destination.get('effective_cooldown_seconds', rules['cooldown_seconds'])
        ready_in = max(0, self.board.get('ready_at', 0) - self.board.get('now', time.time()))
        self.text('## Tower Trials\nBring your exploration team through a saved climb. One opponent per wave; no capture rolls.')
        self.artwork()
        self.row(self.select('Choose a trial biome', [discord.SelectOption(
            label=(data['name'] + (f" · Locked Lv.{data['min_level']}" if player.get('level', 1) < data['min_level'] else ''))[:100],
            value=key, default=key == self.destination) for key, data in BIOMES.items()], 'destination'))
        low, high = region['level_range']
        self.text(f"**{safe(battle_art.REALMS[self.destination])}** · {rules['total']} waves\n"
                  f"Companion Lv.{low}–{high} · Trainer Lv.{region['min_level']}+\n"
                  f"Camp after wave {rules['camp_wave']}: surviving teammates recover 25% HP/MP · 3 care items per climb\n"
                  f"Full clear: {rules['multiplier']:g}× one challenge reward · {cooldown//60} minute cooldown")
        stamp = self.board.get('stamps', {}).get(self.destination)
        if stamp:
            self.text(f"**Cleared** · Best {stamp['best_actions']} actions")
        self.text(self.team_text())
        if len(self.rows) < 3:
            self.text('A full team of three is recommended. A smaller team faces the same tower.')
        if self.run and self.run['status'] != 'complete':
            self.text(f"**Saved climb** · {safe(battle_art.REALMS[self.run['destination']])} · {self.payload.get('cleared', 0)}/{self.payload['plan']['total']} cleared")
            self.row(self.button('Resume saved climb', 'show_run', style=discord.ButtonStyle.success))
        elif ready_in:
            self.text('Next attempt in **' + wait_text(ready_in) + '**.')
        self.row(self.button('Begin tower', 'begin', style=discord.ButtonStyle.success,
                             disabled=bool(ready_in or self.run and self.run['status'] != 'complete' or not self.rows or player.get('level', 1) < region['min_level'])),
                 self.button('Refresh', 'refresh'), self.button('Back to journal', 'journal'))

    def render_run(self):
        from utils import trial_combat
        if not self.run:
            self.mode = 'selector'
            return self.render_selector()
        p, status = self.payload, self.run['status']
        plan, cleared = p['plan'], p.get('cleared', 0)
        index = self.playback_wave if self.playing and self.playback_wave is not None else p.get('wave_index', 0)
        wave = plan['waves'][min(index, len(plan['waves']) - 1)]
        fighting = status == 'fighting' or self.playing
        self.text(f"## {safe(battle_art.REALMS[self.run['destination']])}\n"
                  f"**Wave {min(index + 1, plan['total'])}/{plan['total']}** · {cleared} cleared"
                  + (f" · Round {p.get('round', 1)}/{wave['round_limit']}" if fighting else ''))
        self.artwork()
        self.text(self.team_text())
        if status == 'complete' and not self.playing:
            outcome = p.get('outcome', p.get('status', 'complete'))
            label = 'Tower cleared' if outcome == 'won' else 'Climb finished'
            self.text('### ' + label + '\n' + self.settlement_text())
            self.row(self.button('Back to trials', 'trials', style=discord.ButtonStyle.primary), self.button('Back to journal', 'journal'))
            return
        if status == 'paused':
            self.text('**Climb paused.** Your team and earned progress are saved. The team remains reserved until the climb ends.')
            self.row(self.button('Resume climb', 'resume', style=discord.ButtonStyle.success),
                     self.button('Retreat & bank', 'retreat'), self.button('Back to trials', 'trials'), self.button('Back to journal', 'journal'))
            return
        self.text(f"**{'Opponent' if fighting else 'Next opponent'} · {safe(wave.get('name') or storage.get_beast_data(wave['species'])['name'])}** · Lv.{wave['level']} · {safe(wave.get('type') or storage.get_beast_data(wave['species'])['type']).title()}\n"
                  + safe(wave['hint']))
        if fighting:
            enemy = p.get('enemy') or {}
            self.text(f"Enemy HP {max(0, enemy.get('hp', 0))}/{enemy.get('max_hp', 1)} · Assist {p.get('meter', 0)}/3\n"
                      + ('Playing action…' if self.playing else 'Choose a move · 45 seconds. Swap or Assist uses your turn.'))
            active = trial_combat.active(p)
            actions = trial_combat.legal_actions(p)
            controls = [self.button(('Ultimate · ' if action['kind'] == 'ultimate' else '') + action['label'],
                                   action['kind'] + ':' + action['choice'][0], disabled=not action['enabled'])
                        for action in actions if action['kind'] in {'move', 'ultimate'}]
            if controls:
                self.row(*controls)
            available = {tuple(action['choice']) for action in actions if action['enabled']}
            for member in p['party']:
                if member is active or member.get('id') == active.get('id'):
                    continue
                self.row(self.button('Swap: ' + member['name'], 'swap:' + str(member['id']), disabled=('swap', member['id']) not in available),
                         self.button('Assist: ' + member['name'], 'assist:' + str(member['id']), disabled=('assist', member['id']) not in available))
            return
        self.text(f"Camp {'used' if p.get('camp_used') else 'automatic at midpoint'} · Care items {p.get('items_used', 0)}/3 used\n"
                  'Continue, pause safely, or finish and bank your earned progress.')
        bank, full = p.get('bank_quote'), p.get('reward_preview')
        if bank and full:
            self.text(f"**Bank now** · +{bank['gold']:,} gold · +{bank['trainer_xp']:,} trainer EXP\n"
                      f"Full clear · +{full['gold']:,} gold · +{full['trainer_xp']:,} trainer EXP")
        if cleared >= plan['camp_wave'] and index < plan['total'] - 1:
            guardian = plan['waves'][-1]
            self.text(f"**Final guardian · {safe(guardian.get('name') or storage.get_beast_data(guardian['species'])['name'])}** · Lv.{guardian['level']}")
        self.render_care()
        self.row(self.button('Next wave', 'next', style=discord.ButtonStyle.success),
                 self.button('Pause', 'pause'), self.button('Retreat & bank', 'retreat'))
        self.row(self.button('Back to trials', 'trials'), self.button('Back to journal', 'journal'))

    def settlement_text(self):
        settlement = self.payload.get('settlement') or {}
        reward = settlement.get('reward', settlement)
        if not isinstance(reward, dict):
            return f"{self.payload.get('cleared', 0)} waves cleared. Earned progress has been banked."
        values = []
        for key, label in (('gold', 'gold'), ('trainer_xp', 'trainer EXP')):
            if key in reward:
                values.append(f"+{reward[key]:,} {label}")
        if reward.get('party_xp'):
            values.append('Companion EXP: ' + ' · '.join(f"{safe(row['name'])} +{row['amount']}" for row in reward['party_xp']))
        if settlement.get('stamp'):
            values.append('First-clear stamp earned')
        return ' · '.join(values) or f"{self.payload.get('cleared', 0)} waves cleared. Earned progress has been banked."

    def render_care(self):
        items = [item for item in self.board.get('care_items', ()) if item['quantity'] > 0]
        if not items or self.payload.get('items_used', 0) >= 3:
            return
        self.row(self.select('Care item · between waves only', [discord.SelectOption(label=f"{item['name']} ×{item['quantity']}"[:100], value=item['id'], default=item['id'] == self.item_id) for item in items[:25]], 'care_item'))
        if self.item_id:
            selected = next((item for item in items if item['id'] == self.item_id), None)
            if selected:
                effect = selected.get('effect', {})
                detail = (f"Recover {effect['heal_percent']}% max HP" if effect.get('heal_percent') else 'Cleanse a condition and prepare a status ward')
                self.text(f"**{safe(selected['name'])}** · {detail} · uses 1 of 3 care items")
            living = [row for row in self.payload['party'] if row['hp'] > 0]
            self.row(self.select('Apply to a companion', [discord.SelectOption(label=row['name'][:100], value=str(row['id']), default=row['id'] == self.item_target) for row in living], 'care_target'))
            self.row(self.button('Use care item', 'care_use', disabled=not self.item_target))

    async def fresh(self):
        from utils import trials
        if not self.current():
            return False
        if self.run:
            latest = await trials.read(self.user_id, self.run['id'])
            if not latest or latest['version'] != self.run['version']:
                return False
        return True

    async def deliver(self, interaction=None, *, opening=False):
        """Publish one current revision; only a confirmed deletion creates a replacement."""
        if not await self.fresh():
            return False
        self.render()
        file, buffer = None, None
        if self.media:
            buffer = io.BytesIO(self.media[1])
            file = discord.File(buffer, filename=self.media[0])
        async def send(with_art):
            async with asyncio.timeout(IO_TIMEOUT):
                if self.recreated and self.message is None:
                    if file:
                        file.fp.seek(0)
                    return await self.channel.send(view=self, **({'file': file} if with_art else {}), allowed_mentions=discord.AllowedMentions.none())
                if opening:
                    return await interaction.followup.send(view=self, files=[file] if with_art else [], wait=True,
                                                           allowed_mentions=discord.AllowedMentions.none())
                return await durable_message(self.message, channel=self.channel).edit(
                    view=self, attachments=[file] if with_art else [], allowed_mentions=discord.AllowedMentions.none())
        try:
            try:
                result = await send(bool(file))
            except discord.NotFound:
                if self.recreated or self.channel is None:
                    return False
                self.recreated = True
                self.message = None
                try:
                    result = await send(bool(file))
                except discord.HTTPException:
                    if not file:
                        raise
                    self.media = None
                    self.presentation.enabled = False
                    self.render()
                    result = await send(False)
            except discord.HTTPException:
                if not file:
                    raise
                self.media = None
                self.presentation.enabled = False
                self.render()
                result = await send(False)
            self.message = result or self.message
            return True
        except (discord.HTTPException, TimeoutError):
            logger.warning('Trials revision saved but panel upload failed: trainer=%s', self.user_id, exc_info=True)
            return False
        finally:
            if file:
                file.close()
            if buffer:
                buffer.close()

    async def prepare_media(self):
        from utils import trial_combat
        destination = self.run['destination'] if self.run and self.mode == 'run' else self.destination
        self.destination = destination
        if self.presentation.realm != destination:
            self.presentation = battle_art.BattlePresentation(destination)
        if self.mode == 'run' and self.payload.get('enemy'):
            states = trial_combat.snapshot(self.payload)
            art = await self.presentation.art(states, states)
            self.media = self.artifact(art) if art else None
        else:
            team = await journal_team_art.render_team(self.payload.get('party', self.rows), destination)
            self.media = (team.name, team.data) if team else None

    def artifact(self, art):
        return (f'trial-{hashlib.sha256(art.data).hexdigest()[:24]}.{art.extension}', art.data)

    async def play(self, interaction):
        from utils import trial_combat
        self.cancel_timer()
        self.playing = True
        try:
            for event in self.payload.get('events', ()):
                if not await self.fresh():
                    return
                before, action, after = trial_combat.decode_event(event)
                enemy_id = before[1].member_id
                self.playback_wave = -enemy_id - 1 if isinstance(enemy_id, int) and enemy_id < 0 else self.payload.get('wave_index', 0)
                art = await self.presentation.art(before, after, action)
                if art:
                    self.media = self.artifact(art)
                if not await self.deliver(interaction):
                    return
                if art and action:
                    await self.presentation.sleep(art.seconds + .2)
        finally:
            self.playing = False
            self.playback_wave = None

    def cancel_timer(self):
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = None

    async def arm(self):
        from utils import trials
        self.cancel_timer()
        if not self.current() or not self.run or self.run['status'] != 'fighting':
            return
        self.run = await trials.arm_turn(self.user_id, *self.version)
        deadline = self.run.get('turn_deadline') or self.payload.get('turn_deadline')
        if deadline:
            version = self.version
            self.timer = asyncio.create_task(self.deadline(version, deadline))

    async def deadline(self, version, deadline):
        from utils import trials
        try:
            await asyncio.sleep(max(0, deadline - time.time()))
            async with self.lock:
                if not self.current() or self.version != version:
                    return
                self.run = await trials.timeout(self.user_id, *version)
                if self.run['status'] == 'complete':
                    self.notice = 'The move window closed. Your earned progress has been banked.'
                    await self.prepare_media()
                    await self.deliver()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception('Trials deadline could not settle trainer=%s', self.user_id)

    async def handle(self, click, action, version, generation):
        from utils import trials
        if click.user.id != self.user_id:
            return await click.response.send_message('Open /challenge for your own Trials team.', ephemeral=True)
        await click.response.defer()
        async with self.lock:
            if not self.current() or self.playing or version != self.version or generation != self.generation:
                return await click.followup.send('Use the current Trials controls.', ephemeral=True)
            self.notice = ''
            try:
                if action == 'journal':
                    return await self.back_journal(click)
                if action in {'trials', 'pause'}:
                    if self.run and self.run['status'] == 'between':
                        self.run = await trials.pause(self.user_id, *self.version)
                    if action == 'trials':
                        self.mode = 'selector'
                        if self.run and self.run['status'] == 'complete':
                            self.run = None
                elif action == 'destination':
                    destination = click.data['values'][0]
                    if destination not in BIOMES:
                        raise ValueError('Choose a destination from the current panel.')
                    self.destination = destination
                elif action == 'begin':
                    self.run = await trials.begin(self.user_id, self.destination)
                    self.mode = 'run'
                elif action in {'resume', 'show_run'}:
                    if self.run and self.run['status'] == 'paused':
                        self.run = await trials.resume(self.user_id, *self.version)
                    self.mode = 'run'
                elif action == 'next':
                    self.run = await trials.next_wave(self.user_id, *self.version)
                    await self.play(click)
                elif action.startswith(('move:', 'ultimate:', 'swap:', 'assist:')):
                    kind, value = action.split(':', 1)
                    choice = (value, kind == 'ultimate') if kind in {'move', 'ultimate'} else (kind, int(value))
                    self.run = await trials.act(self.user_id, *self.version, choice)
                    await self.play(click)
                elif action == 'retreat':
                    self.run = await trials.retreat(self.user_id, *self.version)
                elif action == 'care_item':
                    self.item_id, self.item_target = click.data['values'][0], None
                elif action == 'care_target':
                    self.item_target = int(click.data['values'][0])
                elif action == 'care_use':
                    self.run = await trials.use_item(self.user_id, *self.version, self.item_id, self.item_target)
                    self.item_id, self.item_target = None, None
                await self.load()
            except ValueError as error:
                self.notice = safe(error)
                await self.load()
            await self.prepare_media()
            if await self.deliver(click):
                await self.arm()

    async def back_journal(self, click):
        from utils import trials
        if self.run and self.run['status'] == 'fighting':
            raise ValueError('Finish this wave before returning to the journal.')
        if self.run and self.run['status'] == 'between':
            self.run = await trials.pause(self.user_id, *self.version)
        from utils.journal import open_journal
        try:
            await open_journal(click, bot=self.bot, page='adventure', realm=self.destination,
                               already_deferred=True, replace_message=True)
        except (discord.HTTPException, TimeoutError):
            self.notice = 'Your climb is saved. The journal could not open; use Back to journal to try again.'
            await self.prepare_media()
            await self.deliver(click)
            return
        self.closed = True
        self.stop()
        self.cancel_timer()
        if _panels.get(self.key) is self:
            _panels.pop(self.key, None)

    async def on_timeout(self):
        async with self.lock:
            if self.current() and (not self.run or self.run['status'] != 'fighting'):
                self.closed = True
                self.cancel_timer()

    async def on_error(self, interaction, error, item):
        logger.error('Trials control failed: trainer=%s', self.user_id, exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send('Your climb is saved. Open /challenge to resume its current state.', ephemeral=True)
        else:
            await interaction.response.send_message('Your climb is saved. Open /challenge to resume its current state.', ephemeral=True)


async def open_trials(interaction, *, bot=None, destination=None, source=None, already_deferred=False):
    if not already_deferred:
        await interaction.response.defer()
    await storage.get_or_create_player(interaction.user.id, str(interaction.user))
    view = TrialPanel(interaction.user.id, bot=bot or getattr(interaction, 'client', None), destination=destination)
    view.channel = getattr(interaction, 'channel', None)
    async with view.lock:
        previous = _panels.get(view.key)
        await view.load()
        if view.run:
            view.mode, view.destination = 'run', view.run['destination']
        elif destination is None:
            view.destination = journal_state.explore_destination(await journal_state.snapshot(view.user_id))[0]
        if source:
            view.message = source.message
        _panels[view.key] = view
        published = False
        try:
            await view.prepare_media()
            published = await view.deliver(interaction, opening=not bool(view.message))
            if not published:
                _panels[view.key] = previous
                view.stop()
                return None
            if previous and previous is not view:
                previous.closed = True
                previous.cancel_timer()
                previous.stop()
                if previous.message and previous.message.id != view.message.id:
                    try:
                        async with asyncio.timeout(IO_TIMEOUT):
                            await durable_message(previous.message, channel=previous.channel).delete()
                    except (discord.HTTPException, TimeoutError):
                        pass
            if source:
                source.message_repurposed = True
                source.closed = True
                source.stop()
                await journal_sessions.forget(source, interaction)
            await view.arm()
        except BaseException:
            if not published:
                _panels[view.key] = previous
                view.stop()
            raise
    return view
