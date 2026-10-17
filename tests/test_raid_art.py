"""Shared raid pixels, bounded batching and real component dispatch under load."""
import asyncio
from dataclasses import FrozenInstanceError, replace
import io
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ui.view import ViewStore
from PIL import Image, ImageChops, ImageSequence

from scripts.preview_raid_arena import make_raid, clash, capture, SPECIES
from cogs.guilds import RAID_BOSSES
from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from tests import test_performance_gameplay as fixtures
from utils import battle_art, combat, raid_art as art, db as storage
from utils.raids import RaidView


class ArenaImages(unittest.TestCase):
    def test_all_bosses_and_one_to_ten_trainers_have_safe_geometry(self):
        for count in range(1, 11):
            scene = capture(make_raid(RAID_BOSSES['corrupted'][2], count))
            self.assertEqual(len(art.placements(count)), count)
            boxes = []
            for member, place in zip(scene.members, art.placements(count)):
                image = art.fitted(member.fighter.species, place.size)
                boxes.append((place.x - image.width / 2, place.feet - image.height,
                              place.x + image.width / 2, place.feet))
            for marker in art.marker_boxes(scene):
                x0, y0, x1, y1 = marker
                self.assertTrue(0 <= x0 < x1 <= 640 and 0 <= y0 < y1 <= 464)
                for a, b, c, d in boxes:
                    self.assertFalse(x0 < c and x1 > a and y0 < d and y1 > b,
                                     ('Marker covers a companion', count, marker, (a, b, c, d)))
        for group in RAID_BOSSES.values():
            for boss in group:
                with self.subTest(boss=boss['id']):
                    scene = capture(make_raid(boss, 10))
                    self.assertEqual(scene.realm, art.REALMS[boss['id']])
                    self.assertIsNotNone(art.boss_portrait(boss['id']))
                    self.assertEqual(art.render(scene, scene).extension, 'png')

    def test_snapshot_is_immutable_and_keeps_species_after_a_nickname(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2])
        raid.state(0)['name'] = '雪🌸' * 15
        saved = capture(raid)
        raid.state(0)['hp'] = 1
        self.assertEqual(saved.members[0].fighter.species, 'timestitch')
        self.assertNotEqual(saved.members[0].fighter.hp, 1)
        with self.assertRaises(FrozenInstanceError):
            saved.phase = 99
        self.assertTrue(art.render(saved, saved).data)

    def test_real_ten_trainer_hits_share_a_one_shot_clip_and_hp_moves_at_impact(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2], 10)
        before, after, events = clash(raid)
        first = art.frame(before, after, events, 0)
        windup = art.frame(before, after, events, .2)
        hit = art.frame(before, after, events, .8)
        health = (10, 54, 632, 90)
        self.assertIsNone(ImageChops.difference(first.crop(health), windup.crop(health)).getbbox())
        self.assertIsNotNone(ImageChops.difference(first.crop(health), hit.crop(health)).getbbox())
        self.assertIsNotNone(ImageChops.difference(first, windup).getbbox())
        self.assertEqual(len(events), 10)
        clip = art.render(before, after, events)
        self.assertLess(len(clip.data), 2_000_000)
        with Image.open(io.BytesIO(clip.data)) as gif:
            self.assertGreater(gif.n_frames, 12)
            self.assertEqual(gif.size, (640, 596))
            self.assertNotIn('loop', gif.info)
            self.assertEqual(sum(frame.info['duration'] for frame in ImageSequence.Iterator(gif)), 1200)

    def test_warning_is_visible_from_first_frame_and_knockout_stays_collapsed(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2])
        before, after, events = clash(raid)
        warning = replace(after, pending=True, warning='Vent the flame', weaves=1)
        self.assertIsNone(ImageChops.difference(art.frame(before, warning, events, 0).crop((0, 95, 640, 132)),
                                              art.frame(before, warning, events, .8).crop((0, 95, 640, 132))).getbbox())
        final = replace(after, boss=replace(after.boss, hp=0), pending=False, outcome='victory')
        still = art.frame(final, final)
        subsequent = art.frame(final, final, (art.Event(99, None, 'status'),), .2)
        self.assertIsNone(ImageChops.difference(still, subsequent).getbbox())

    def test_healing_swap_and_boss_sweep_are_visible_without_recomputing_rules(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2])
        before = capture(raid)
        raid.resolve_action(0, 'tend')
        raid.resolve_action(1, 'swap')
        raid.resolve_boss_turn()
        after = capture(raid)
        events = tuple(raid._art_events)
        self.assertEqual(after.members[1].slot, 1)
        self.assertNotEqual(after.members[1].fighter.species, before.members[1].fighter.species)
        self.assertTrue({'tend', 'swap', 'boss'}.issubset({event.kind for event in events}))
        self.assertIsNotNone(ImageChops.difference(art.frame(before, after, events, 0), art.frame(before, after, events, .8)).getbbox())

    def test_event_buffer_and_per_trainer_aggregates_are_bounded(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2], 10)
        for i in range(200):
            raid._record_art(i % 10, 'attack', 3)
        self.assertEqual(len(raid._art_events), art.MAX_EVENTS)
        self.assertEqual(raid._art_sequence, 200)
        groups = art.events_by_member(tuple(raid._art_events))
        self.assertEqual(len(groups), 10)
        self.assertEqual(sum(group['damage'] for group in groups.values()), art.MAX_EVENTS * 3)


class SharedWorkerBudget(unittest.IsolatedAsyncioTestCase):
    async def test_raids_and_duels_share_the_same_two_cancel_safe_workers(self):
        gate, entered = threading.Event(), threading.Event()
        count, mutex = [0], threading.Lock()

        def blocked(*_):
            with mutex:
                count[0] += 1
                if count[0] == 2:
                    entered.set()
            gate.wait(2)
            return battle_art.Artwork(b'image', 'png')

        tasks = []
        try:
            with patch.object(art, 'render', side_effect=blocked), patch.object(battle_art, 'render', side_effect=blocked):
                presentation = art.RaidPresentation()
                tasks = [asyncio.create_task(presentation.prepare(None)),
                         asyncio.create_task(battle_art.render_async((), (), 'woods'))]
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                tasks[0].cancel()
                await asyncio.gather(tasks[0], return_exceptions=True)
                self.assertIsNone(await presentation.prepare(None))
                self.assertEqual(count[0], 2)
                gate.set()
                await tasks[1]
        finally:
            gate.set()
            await asyncio.gather(*tasks, return_exceptions=True)


class RaidPresentationRuntime(DatabaseCase):
    raid_art_enabled = True

    async def raid(self, users=(42, 77), *, paid=False):
        raid = await fixtures.RaidTiming.make_raid(self, users=users, paid=paid)
        raid.view = RaidView(raid)
        raid.presentation.sleep = AsyncMock()
        self.addCleanup(raid.view.stop)
        self.addAsyncCleanup(raid._stop_refresh)
        return raid

    async def test_real_router_acknowledges_other_trainers_during_slow_render_and_upload(self):
        raid = await self.raid()
        raid.refresh_interval = 0
        store, files, records = ViewStore(None), [], []
        message = SimpleNamespace(id=910, edit=None)

        def register(kwargs):
            if kwargs.get('view'):
                store.add_view(kwargs['view'], message.id)
            file = kwargs.get('file') or next(iter(kwargs.get('attachments', [])), None)
            if file:
                files.append(file)
                records.append((file.filename, file.fp.getvalue()))

        async def send(**kwargs):
            register(kwargs)
            return message

        rendering, upload, release_render, release_upload = (asyncio.Event() for _ in range(4))
        blocked = [False]

        async def edit(**kwargs):
            register(kwargs)
            if kwargs.get('attachments') and kwargs['attachments'][0].filename.endswith('.gif') and not blocked[0]:
                blocked[0] = True
                upload.set()
                await release_upload.wait()

        message.edit = AsyncMock(side_effect=edit)
        raid.channel.send.side_effect = send
        await raid._publish_panel(initial=True)
        original = raid.presentation.prepare
        for button in raid.view.children:
            callback = button.callback

            async def observed(click, callback=callback):
                try:
                    await callback(click)
                finally:
                    click.completed.set()

            button.callback = observed

        async def slow(scene, events=()):
            if not rendering.is_set():
                rendering.set()
                await release_render.wait()
            return await original(scene, events)

        clicks = []

        async def dispatch(uid, index):
            click = component(uid=uid)
            click.message = message
            click.completed = asyncio.Event()
            click.data = {'component_type': 2, 'custom_id': raid.view.children[index].custom_id}
            acknowledged = asyncio.Event()
            click.response.defer.side_effect = lambda **_: acknowledged.set()
            store.dispatch_view(2, click.data['custom_id'], click)
            await asyncio.wait_for(acknowledged.wait(), .25)
            clicks.append(click)

        try:
            with patch.object(raid.presentation, 'prepare', side_effect=slow):
                await dispatch(42, 0)
                await asyncio.wait_for(rendering.wait(), 1)
                await dispatch(77, 0)
                async with asyncio.timeout(.25):
                    async with raid.lock, combat.lock_states(raid.users):
                        pass
                release_render.set()
                await asyncio.wait_for(upload.wait(), 3)
                await dispatch(77, 0)  # Individual cooldown rejection still gets an acknowledgement.
                self.assertFalse(any(button.disabled for button in raid.view.children))
                # Acknowledgement precedes DB/progress completion. Wait for all
                # callbacks before inspecting the final displayed snapshot.
                await asyncio.wait_for(asyncio.gather(*(click.completed.wait() for click in clicks)), 2)
                release_upload.set()
                await asyncio.wait_for(raid._refresh_task, 3)
            self.assertEqual(raid.presentation.previous.boss.hp, raid.enemy['hp'])
            self.assertEqual(len(raid._art_events), 0)
            self.assertIs(store._synced_message_views[message.id], raid.view)
            with Image.open(io.BytesIO(next(data for name, data in records if name.endswith('.gif')))) as gif:
                self.assertGreater(gif.n_frames, 10)
            self.assertEqual(len({name for name, _ in records}), len(records))
            self.assertTrue(all(file.fp.closed for file in files))
        finally:
            release_render.set()
            release_upload.set()
            await asyncio.wait_for(asyncio.gather(*(click.completed.wait() for click in clicks)), 2)
            await raid._stop_refresh()
            raid.view.stop()
        self.assertFalse(store._views)

    async def test_new_warning_interrupts_old_render_and_starts_window_after_native_publish(self):
        raid = await self.raid(users=(42,))
        raid.refresh_interval = 0
        now = [100.0]
        raid.clock = lambda: now[0]
        raid.started = 0
        rendered, cancelled, warning, release = (asyncio.Event() for _ in range(4))
        calls = []

        async def prepare(scene, events=()):
            if not rendered.is_set():
                rendered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return battle_art.Artwork(b'image', 'png')

        async def edit(**kwargs):
            calls.append(kwargs)
            if 'Weave:' in kwargs['embed'].description and 'attachments' not in kwargs:
                warning.set()
                await release.wait()

        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=edit))
        raid.presentation.filename = 'old-scene.png'
        with patch.object(raid.presentation, 'prepare', side_effect=prepare):
            try:
                await raid.refresh()
                await asyncio.wait_for(rendered.wait(), 1)
                raid.enemy['hp'] = int(raid.enemy['max_hp'] * .7)
                await raid.action(42, 'guard')
                await asyncio.wait_for(warning.wait(), .5)
                self.assertTrue(cancelled.is_set())
                self.assertTrue(raid._pattern_needs_publish)
                now[0] = 200
                raid.resolve_boss_turn()
                self.assertTrue(raid.pending)
                release.set()
                await asyncio.wait_for(raid._refresh_task, 1)
            finally:
                release.set()
                await raid._stop_refresh()
        self.assertEqual(raid.pattern_started, 200)
        self.assertTrue(any('attachments' not in call for call in calls))
        raid.weaves = raid.weave_required()
        now[0] = 205
        raid.resolve_boss_turn()
        self.assertTrue(raid.pending)
        now[0] = 206
        raid.resolve_boss_turn()
        self.assertFalse(raid.pending)

    async def test_ten_trainer_burst_is_one_clip_with_no_playback_wait_in_actions(self):
        raid = await self.raid(users=tuple(range(100, 110)))
        raid.refresh_interval = 0
        raid.message = SimpleNamespace(edit=AsyncMock())
        raid.presentation.previous = (await raid._capture_art())[0]
        for uid in raid.users:
            raid.resolve_action(uid, 'attack')
        with patch.object(raid.presentation, 'prepare', new=AsyncMock(return_value=battle_art.Artwork(b'clip', 'gif', 1.2))) as prepare:
            await raid.refresh()
            await raid._refresh_task
        self.assertEqual(prepare.await_count, 1)
        self.assertEqual(len(prepare.call_args.args[1]), 10)
        self.assertEqual(raid.message.edit.await_count, 1)
        raid.presentation.sleep.assert_not_awaited()

    async def test_final_clip_follows_saved_rewards_then_a_static_knockout(self):
        raid = await self.raid(users=(42,), paid=True)
        raid.damage[42] = 50
        raid.enemy['hp'] = 0
        raid._record_art(42, 'ultimate', 50)
        files, views, counts = [], [], []

        async def edit(**kwargs):
            files.extend(kwargs.get('attachments', []))
            views.append(kwargs.get('view'))
            counts.append(len(await self.rows('SELECT * FROM raid_rewards')))

        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=edit))
        raid.channel.send.return_value = raid.message
        clips = [battle_art.Artwork(b'gif', 'gif', 1.2), battle_art.Artwork(b'png', 'png')]
        with patch.object(raid.presentation, 'prepare', new=AsyncMock(side_effect=clips)):
            await raid.finish(True)
        self.assertEqual(counts, [1, 1])
        self.assertEqual(views, [None, None])
        self.assertEqual([file.filename.rsplit('.', 1)[1] for file in files], ['gif', 'png'])
        self.assertTrue(all(file.fp.closed for file in files))
        raid.presentation.sleep.assert_awaited_once_with(1.4)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
        self.assertTrue(raid.view.is_finished())

    async def test_rejected_files_and_render_outages_leave_a_usable_text_panel(self):
        raid = await self.raid(users=(42,))
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        calls, files = [], []

        async def send(**kwargs):
            calls.append(kwargs)
            if kwargs.get('file'):
                files.append(kwargs['file'])
                raise forbidden
            return SimpleNamespace(edit=AsyncMock())

        raid.channel.send.side_effect = send
        with patch.object(raid.presentation, 'prepare', new=AsyncMock(return_value=battle_art.Artwork(b'image', 'png'))):
            with self.assertLogs('chibibeasts.raid.art', level='WARNING'):
                await raid._publish_panel(initial=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(raid.presentation.enabled)
        self.assertIs(calls[-1]['view'], raid.view)
        self.assertTrue(all(file.fp.closed for file in files))
        with patch.object(battle_art, 'render_job', new=AsyncMock(return_value=None)):
            other = art.RaidPresentation()
            scene = (await raid._capture_art())[0]
            for _ in range(3):
                self.assertIsNone(await other.prepare(scene))
            self.assertFalse(other.enabled)

    async def test_cancelled_or_deleted_final_art_cannot_lose_rewards(self):
        raid = await self.raid(users=(42,), paid=True)
        raid.damage[42] = 50
        entered, release = asyncio.Event(), asyncio.Event()

        async def prepare(*_):
            entered.set()
            await release.wait()
            return battle_art.Artwork(b'image', 'png')

        missing = discord.NotFound(SimpleNamespace(status=404, reason='Missing'), {'code': 10008, 'message': 'Unknown Message'})
        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=missing))
        with patch.object(raid.presentation, 'prepare', side_effect=prepare):
            finishing = asyncio.create_task(raid.finish(True))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)
                finishing.cancel()
                await asyncio.gather(finishing, return_exceptions=True)
                release.set()
                with self.assertLogs('utils.raids', level='INFO'):
                    await raid.finish(True)
            finally:
                release.set()
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
