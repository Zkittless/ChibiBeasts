"""Real GIFs plus Discord dispatcher tests at the new playback boundary."""
import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ui.view import ViewStore
from PIL import Image, ImageChops, ImageSequence

from cogs.battle import build_pve_beast_state
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import battle_art as art, combat, db as storage
from utils.duels import MoveView, active_fighters, duel


def fighters(left='twine', right='gloop'):
    return [build_pve_beast_state(storage.get_beast_data(k), 5) for k in (left, right)]


class BattleImages(unittest.TestCase):
    def test_all_86_canonical_portraits_and_five_realms_are_packaged(self):
        roster = storage.load_beasts()
        self.assertEqual(len(roster), 86)
        for species in roster:
            with self.subTest(species=species):
                sprite = art.sprite(species)
                self.assertIsNotNone(sprite)
                self.assertLessEqual(sprite.width, 180)
                self.assertLessEqual(sprite.height, 176)
        manifest = json.loads((art.PROJECT_ROOT / 'data' / 'battle-artwork.json').read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['realms']), set(art.REALMS))
        for realm, record in manifest['realms'].items():
            path = art.PROJECT_ROOT / 'data' / record['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'])
            self.assertEqual(art.background(realm).size, (640, 360))

    def test_snapshot_keeps_species_independent_of_nickname_and_collection_id(self):
        states = fighters()
        states[0].update(id=999, name='A renamed companion')
        saved = art.snapshot(states)
        states[0]['hp'] = 1
        self.assertEqual(saved[0].species, 'twine')
        self.assertEqual(saved[0].name, 'A renamed companion')
        self.assertNotEqual(saved[0].hp, 1)
        with self.assertRaises(FrozenInstanceError):
            saved[0].hp = 1

    def test_realm_follows_destination_npc_home_and_pvp_host_element(self):
        for value, expected in [('ember', 'ember'), ('🔥 The Ember Wastes', 'ember'),
                                ('Celestial Loom', 'loom'), ('Whispering Woods', 'woods'),
                                ('The Glacial Hollows', 'glacial'), ('Sunken Abyssal Trenches', 'trenches')]:
            self.assertEqual(art.realm_key(value), expected)
        self.assertEqual(art.realm_key(states=[{'beast_type': 'water'}]), 'trenches')
        self.assertEqual(art.realm_key(states=[{'beast_type': 'nature'}]), 'woods')
        self.assertEqual(art.realm_key('woods', [{'beast_type': 'cosmic'}]), 'woods')

    def test_real_combat_hit_changes_pixels_and_hp_only_at_impact(self):
        states = fighters()
        before = art.snapshot(states)
        with patch('utils.combat.random.random', return_value=.99):
            damage = combat.resolve_move(*states, states[0]['moves'][0], False, [])
        after = art.snapshot(states)
        action = art.Action(0, states[0]['moves'][0], damage=damage)
        first = art.frame(before, after, 'woods', action, 0)
        windup = art.frame(before, after, 'woods', action, .2)
        impact = art.frame(before, after, 'woods', action, .65)
        hud = (320, 0, 640, art.HUD)
        self.assertIsNone(ImageChops.difference(first.crop(hud), windup.crop(hud)).getbbox())
        self.assertIsNotNone(ImageChops.difference(first.crop(hud), impact.crop(hud)).getbbox())
        self.assertIsNotNone(ImageChops.difference(first, windup).getbbox())
        self.assertEqual(art.interpolate(before[1].hp, after[1].hp, .47), before[1].hp)
        self.assertEqual(art.interpolate(before[1].hp, after[1].hp, 1), states[1]['hp'])
        clip = art.render(before, after, 'woods', action)
        with Image.open(io.BytesIO(clip.data)) as gif:
            self.assertGreater(gif.n_frames, 15)
            self.assertNotIn('loop', gif.info)
            self.assertEqual(sum(f.info['duration'] for f in ImageSequence.Iterator(gif)), action.milliseconds)
            self.assertEqual(gif.size, (640, 448))
        self.assertLess(len(clip.data), art.MAX_BYTES)

    def test_heal_guard_ultimate_and_knockout_render_distinct_motion(self):
        for kind, ultimate in [('heal', False), ('guard', False), ('skill', False), ('attack', True)]:
            with self.subTest(kind=kind, ultimate=ultimate):
                states = fighters()
                states[0].update(hp=35, mana=70, ultimate_charges=3)
                before = art.snapshot(states)
                if kind == 'heal':
                    states[0]['hp'] = 80
                elif kind == 'guard':
                    states[0]['guard'] = 55
                else:
                    states[1]['hp'] = 0
                    states[0].update(mana=20, ultimate_charges=0)
                after = art.snapshot(states)
                clip = art.render(before, after, 'loom', art.Action(0, kind, kind, 126 if ultimate else 0, ultimate))
                with Image.open(io.BytesIO(clip.data)) as gif:
                    # Guard holds a small set of deliberate brace/shield poses;
                    # GIF compression combines identical held frames.
                    self.assertGreaterEqual(gif.n_frames, 4 if kind == 'guard' else 11)
                    gif.seek(gif.n_frames - 1)
                    last = gif.convert('RGB')
                    gif.seek(0)
                    self.assertIsNotNone(ImageChops.difference(last, gif.convert('RGB')).getbbox())
                self.assertLess(len(clip.data), art.MAX_BYTES)
        # The still retains a collapsed KO, rather than standing it up again.
        final = art.frame(after, after, 'loom')
        self.assertIsNone(ImageChops.difference(final.crop((390, 190, 570, 300)), art.background('loom').convert('RGB').crop((390, 190 - art.HUD, 570, 300 - art.HUD))).getbbox())
        followup = art.frame(after, after, 'loom', art.Action(0, 'End-of-turn effects', 'status'), .1)
        self.assertIsNone(ImageChops.difference(final.crop((390, 190, 570, 440)), followup.crop((390, 190, 570, 440))).getbbox())

    def test_long_unicode_names_and_missing_species_have_safe_stills(self):
        states = fighters()
        states[0].update(name='🌸雪' * 20, beast_id='missing_species')
        saved = art.snapshot(states)
        image = art.render(saved, saved, 'glacial')
        with Image.open(io.BytesIO(image.data)) as png:
            self.assertEqual(png.size, (640, 448))


class BoundedRendering(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_jobs_hold_worker_slots_without_blocking_event_loop(self):
        gate = threading.Event()
        entered = threading.Event()
        count = 0
        lock = threading.Lock()

        def blocked(*_):
            nonlocal count
            with lock:
                count += 1
                if count == 2:
                    entered.set()
            gate.wait(3)
            return art.Artwork(b'image', 'png')

        first = second = None
        try:
            with patch.object(art, 'render', side_effect=blocked):
                first = asyncio.create_task(art.render_async((), (), 'woods'))
                second = asyncio.create_task(art.render_async((), (), 'woods'))
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                self.assertIsNone(await art.render_async((), (), 'woods'))
                self.assertEqual(count, 2)
                # A coroutine can still acquire a live player's state lock
                # while both CPU workers are busy.
                async with asyncio.timeout(.2):
                    async with combat.lock_states([42]):
                        await asyncio.sleep(0)
                gate.set()
                await second
        finally:
            gate.set()
            await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
        for _ in range(100):
            if art._slots.acquire(blocking=False):
                art._slots.release()
                break
            await asyncio.sleep(.005)
        self.assertEqual(count, 2)

    async def test_timeout_returns_text_fallback_and_recovers_when_worker_finishes(self):
        gate = threading.Event()

        def slow(*_):
            gate.wait(2)
            return art.Artwork(b'image', 'png')

        try:
            with patch.object(art, 'render', side_effect=slow), patch.object(art, 'RENDER_TIMEOUT', .01):
                with self.assertLogs('chibibeasts.battle.art', level='WARNING'):
                    self.assertIsNone(await art.render_async((), (), 'woods'))
        finally:
            gate.set()
        # Wait for the bounded job to release its slot before another test.
        await asyncio.sleep(.03)


class AnimatedDuels(DatabaseCase):
    battle_art_enabled = True

    async def states(self):
        await self.beast('twine', starter=True)
        row = (await storage.get_player_beasts(42))[0]
        left = combat.build_state(storage.get_beast_data('twine'), row)
        right = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        return [left, right]

    async def test_real_gif_upload_playback_blocks_moves_but_acknowledges_stale_clicks(self):
        states = await self.states()
        states[1].update(hp=1, attack=1, speed=1)
        store = ViewStore(None)
        prompt, playing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        records, files = [], []
        view = None
        message = SimpleNamespace(id=900, edit=None)

        def capture(kwargs):
            nonlocal view
            current = kwargs.get('view')
            if current:
                view = current
                store.add_view(view, message.id)
            file = kwargs.get('file') or next(iter(kwargs.get('attachments', [])), None)
            if file:
                files.append(file)
                records.append((file.filename, file.fp.getvalue(), current.to_components()))
            if current and any('choose a move' in f.name for f in kwargs['embed'].fields):
                prompt.set()

        async def send(**kwargs):
            capture(kwargs)
            return message

        async def edit(**kwargs):
            if kwargs.get('embed'):
                capture(kwargs)

        async def playback(seconds):
            self.assertGreaterEqual(seconds, 1.4)
            self.assertFalse(view._accepting)
            self.assertTrue(all(b.disabled for b in view.children))
            # No combat lock is held across either render, upload or sleep.
            async with asyncio.timeout(.2):
                async with combat.lock_states([42]):
                    pass
            playing.set()
            await release.wait()

        fake = interaction()
        fake.channel.send.side_effect = send
        message.edit = AsyncMock(side_effect=edit)
        with patch('utils.duels.BattlePresentation', side_effect=lambda realm: art.BattlePresentation(realm, sleep=playback)):
            task = asyncio.create_task(duel(fake, states, [42, None], 'Animated test', realm='ember'))
            try:
                await asyncio.wait_for(prompt.wait(), 3)
                original_view = view
                click = component()
                click.message = message
                store.dispatch_view(2, view.children[0].custom_id, click)
                await asyncio.wait_for(playing.wait(), 5)
                self.assertTrue(all(s.get('closed') for s in states))
                click.response.defer.assert_awaited_once()
                stale = component()
                stale.message = message
                store.dispatch_view(2, view.children[0].custom_id, stale)
                for _ in range(20):
                    if stale.followup.send.await_count:
                        break
                    await asyncio.sleep(.005)
                stale.response.defer.assert_awaited_once()
                self.assertIn('next move', stale.followup.send.call_args.args[0])
                self.assertEqual(view.chosen_move, (states[0]['moves'][0], False))
                release.set()
                result = await asyncio.wait_for(task, 3)
                self.assertEqual(result[0], 0)
                self.assertIs(view, original_view)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([Path(r[0]).suffix for r in records], ['.png', '.gif', '.png'])
        self.assertEqual(len({r[0] for r in records}), len(records))
        with Image.open(io.BytesIO(records[1][1])) as gif:
            self.assertGreater(gif.n_frames, 15)
        self.assertTrue(all(file.fp.closed for file in files))
        self.assertFalse(store._views)
        self.assertNotIn(42, combat.active_states)
        self.assertNotIn(42, active_fighters)

    async def test_playback_does_not_spend_next_turn_deadline_and_cancel_releases_state(self):
        states = await self.states()
        states[1].update(hp=10000, max_hp=10000, speed=1, attack=1)
        first, second = asyncio.Event(), asyncio.Event()
        views = []
        prompts = 0

        class ShortDeadline(MoveView):
            def __init__(self, *args):
                super().__init__(*args)
                self.timeout = .05
                views.append(self)

        async def display(**kwargs):
            nonlocal prompts
            if kwargs.get('embed') and any('choose a move' in f.name for f in kwargs['embed'].fields):
                prompts += 1
                (first if prompts == 1 else second).set()

        message = SimpleNamespace(edit=AsyncMock(side_effect=display))
        fake = interaction()

        async def send(**kwargs):
            await display(**kwargs)
            return message

        fake.channel.send.side_effect = send
        async def playback(_):
            await asyncio.sleep(.08)  # Longer than the next choice deadline.

        saved = art.Artwork(b'fake media', 'png', 1.4)
        with patch.object(art, 'render_async', new=AsyncMock(return_value=saved)), patch('utils.duels.BattlePresentation', side_effect=lambda realm: art.BattlePresentation(realm, sleep=playback)):
            task = asyncio.create_task(duel(fake, states, [42, None], 'Pacing', view_class=ShortDeadline))
            try:
                await asyncio.wait_for(first.wait(), 1)
                await views[0].children[2].callback(component())
                await asyncio.wait_for(second.wait(), 1)
                self.assertFalse(task.done())
                self.assertTrue(views[0]._accepting)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_pvp_keeps_one_dispatcher_and_changes_owner_after_each_clip(self):
        states = await self.states()
        states[1] = build_pve_beast_state(storage.get_beast_data('prismite'), 5)
        for state in states:
            state.update(hp=10000, max_hp=10000, attack=1)
        await storage.get_or_create_player(77, 'Other trainer')
        store, prompts = ViewStore(None), asyncio.Queue()
        message = SimpleNamespace(id=901, edit=None)

        def capture(kwargs):
            view = kwargs.get('view')
            if view:
                store.add_view(view, message.id)
                if any('choose a move' in f.name for f in kwargs['embed'].fields):
                    prompts.put_nowait((view, view.current_player_id))
                else:
                    self.assertTrue(all(b.disabled for b in view.children))

        async def send(**kwargs):
            capture(kwargs)
            return message

        async def edit(**kwargs):
            if kwargs.get('embed'):
                capture(kwargs)

        async def rendering(before, after, realm, action=None):
            return art.Artwork(b'image', 'gif' if action else 'png', 1.4 if action else 0)

        fake = interaction()
        fake.channel.send.side_effect = send
        message.edit = AsyncMock(side_effect=edit)
        with patch.object(art, 'render_async', side_effect=rendering), patch('utils.duels.BattlePresentation', side_effect=lambda realm: art.BattlePresentation(realm, sleep=AsyncMock())):
            task = asyncio.create_task(duel(fake, states, [42, 77], 'PvP animation'))
            try:
                original = None
                for owner in (42, 77, 42):
                    view, actual_owner = await asyncio.wait_for(prompts.get(), 1)
                    self.assertEqual(actual_owner, owner)
                    if original is None:
                        original = view
                    self.assertIs(view, original)
                    wrong = component(uid=77 if owner == 42 else 42)
                    wrong.message = message
                    store.dispatch_view(2, view.children[0].custom_id, wrong)
                    right = component(uid=owner)
                    right.message = message
                    store.dispatch_view(2, view.children[0].custom_id, right)
                    for _ in range(20):
                        if right.response.defer.await_count and wrong.response.send_message.await_count:
                            break
                        await asyncio.sleep(.005)
                    right.response.defer.assert_awaited_once()
                    wrong.response.send_message.assert_awaited_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(store._views)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(77, active_fighters)

    async def test_renderer_failure_keeps_text_battle_playable(self):
        states = await self.states()
        states[1].update(hp=1, speed=1)

        class AutoMove(MoveView):
            async def wait(self):
                self.chosen_move = (self.moves[0], False)

        fake = interaction()
        fake.channel.send.return_value = SimpleNamespace(edit=AsyncMock())
        with patch.object(art, 'render_async', new=AsyncMock(return_value=None)) as render:
            result = await duel(fake, states, [42, None], 'Fallback', view_class=AutoMove)
        self.assertEqual(result[0], 0)
        self.assertEqual(render.await_count, 1)
        self.assertNotIn('file', fake.channel.send.call_args.kwargs)

    async def test_cancellation_during_playback_closes_panel_and_releases_reservation(self):
        states = await self.states()
        # AI opens the match, so the outer runner has not received the first
        # message from publish() yet when cancellation lands during playback.
        states[1].update(hp=10000, max_hp=10000, speed=10000, attack=1)
        playing = asyncio.Event()

        class AutoMove(MoveView):
            async def wait(self):
                self.chosen_move = (self.moves[0], False)

        async def playback(_):
            playing.set()
            await asyncio.Event().wait()

        fake = interaction()
        message = SimpleNamespace(edit=AsyncMock())
        fake.channel.send.return_value = message
        with patch.object(art, 'render_async', new=AsyncMock(return_value=art.Artwork(b'image', 'gif', 1.4))), patch('utils.duels.BattlePresentation', side_effect=lambda realm: art.BattlePresentation(realm, sleep=playback)):
            task = asyncio.create_task(duel(fake, states, [42, None], 'Cancel', view_class=AutoMove))
            await asyncio.wait_for(playing.wait(), 1)
            view = fake.channel.send.call_args.kwargs['view']
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(view.is_finished())
        self.assertEqual(message.edit.call_args.kwargs, {'view': None})
        self.assertTrue(states[0]['closed'])
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_bonus_strike_and_status_ticks_publish_separate_real_results(self):
        states = await self.states()
        states[0].update(attack=1, status='poison', status_turns=3,
                         divine_passive={'passive_effect': {'double_turn_chance': 1}})
        states[1].update(hp=1000, max_hp=1000, speed=1, attack=1)
        published = []
        choices = 0

        class OneTurn(MoveView):
            async def wait(self):
                nonlocal choices
                choices += 1
                self.chosen_move = (self.moves[0], False) if choices == 1 else None

        async def rendering(before, after, realm, action=None):
            if action:
                published.append((before, after, action))
            return None

        # Keep this observation renderer enabled even though no file is needed.
        class Observe(art.BattlePresentation):
            async def art(self, before, after, action=None):
                return await rendering(before, after, self.realm, action)

        fake = interaction()
        fake.channel.send.return_value = SimpleNamespace(edit=AsyncMock())
        with patch('utils.duels.BattlePresentation', side_effect=Observe), patch('utils.combat.random.random', return_value=.5):
            result = await duel(fake, states, [42, None], 'Passives', view_class=OneTurn)
        self.assertEqual(result[3], True)
        ticks = [(b, a, move) for b, a, move in published if move.kind == 'status']
        self.assertTrue(any(a[0].hp < b[0].hp for b, a, _ in ticks))
        bonus = [(b, a, move) for b, a, move in published if '(bonus)' in move.name]
        self.assertEqual(len(bonus), 1)
        before, after, _ = bonus[0]
        self.assertLess(after[1].hp, before[1].hp)
        self.assertEqual(before[0].charges, after[0].charges)

    async def test_slow_action_upload_does_not_undo_a_winning_move(self):
        states = await self.states()
        states[1].update(hp=1, speed=1)

        class AutoMove(MoveView):
            async def wait(self):
                self.chosen_move = (self.moves[0], False)

        async def edit(**kwargs):
            if kwargs.get('embed') and 'choose' not in kwargs['embed'].footer.text.lower():
                await asyncio.Event().wait()

        fake = interaction()
        fake.channel.send.return_value = SimpleNamespace(edit=AsyncMock(side_effect=edit))
        with patch.object(art, 'render_async', new=AsyncMock(return_value=art.Artwork(b'image', 'gif', 1.4))), patch.object(art, 'IO_TIMEOUT', .01):
            with self.assertLogs('chibibeasts.battle', level='WARNING'):
                result = await asyncio.wait_for(duel(fake, states, [42, None], 'Slow upload', view_class=AutoMove), 1)
        self.assertEqual(result[0], 0)
        self.assertEqual(states[1]['hp'], 0)
        self.assertNotIn(42, active_fighters)

    async def test_deleted_prompt_is_recreated_without_leaking_old_router_entries(self):
        states = await self.states()
        states[1].update(hp=10000, max_hp=10000, speed=1, attack=1)
        store, views = ViewStore(None), []
        choices = 0

        class OneTurn(MoveView):
            async def wait(self):
                nonlocal choices
                choices += 1
                self.chosen_move = (self.moves[2], False) if choices == 1 else None

        def message(mid):
            panel = SimpleNamespace(id=mid, edit=None)

            async def edit(**kwargs):
                if not kwargs.get('view'):
                    return
                if mid == 902 and any('choose a move' in f.name for f in kwargs['embed'].fields):
                    raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), {'code': 10008, 'message': 'Unknown Message'})
                store.add_view(kwargs['view'], mid)

            panel.edit = AsyncMock(side_effect=edit)
            return panel

        async def send(**kwargs):
            mid = 902 + len(views)
            view = kwargs['view']
            views.append(view)
            store.add_view(view, mid)
            return message(mid)

        fake = interaction()
        fake.channel.send.side_effect = send
        with patch.object(art, 'render_async', new=AsyncMock(return_value=art.Artwork(b'image', 'png', 1.4))), patch('utils.duels.BattlePresentation', side_effect=lambda realm: art.BattlePresentation(realm, sleep=AsyncMock())):
            result = await duel(fake, states, [42, None], 'Deleted prompt', view_class=OneTurn)
        self.assertTrue(result[3])
        self.assertEqual(len(views), 2)
        self.assertIsNot(views[0], views[1])
        self.assertTrue(all(view.is_finished() for view in views))
        self.assertFalse(store._views)
        self.assertNotIn(42, active_fighters)

    async def test_rejected_attachment_retries_text_and_final_redraw_failure_preserves_win(self):
        states = await self.states()
        states[1].update(hp=1, speed=1)
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})

        class AutoMove(MoveView):
            async def wait(self):
                self.chosen_move = (self.moves[0], False)

        fake = interaction()
        message = SimpleNamespace(edit=AsyncMock())
        calls = []

        async def send(**kwargs):
            calls.append(kwargs)
            if kwargs.get('file'):
                raise forbidden
            return message

        async def edit(**kwargs):
            if kwargs.get('embed') and any(f.name == 'Battle complete' for f in kwargs['embed'].fields):
                raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), {'code': 10008, 'message': 'Unknown Message'})

        message.edit.side_effect = edit
        fake.channel.send.side_effect = send
        with patch.object(art, 'render_async', new=AsyncMock(return_value=art.Artwork(b'image', 'png'))):
            with self.assertLogs('chibibeasts.battle.art', level='WARNING'):
                result = await duel(fake, states, [42, None], 'Upload failure', view_class=AutoMove)
        self.assertEqual(result[0], 0)
        self.assertEqual(len(calls), 2)
        self.assertIn('file', calls[0])
        self.assertNotIn('file', calls[1])
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)
