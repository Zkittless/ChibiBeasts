"""Expired clicks and a busy journal cannot silently kill usable controls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import battle_art, expeditions, journal_adventure, journal_state, trial_panel
from utils.journal import JournalView


def missing(code, text):
    return discord.NotFound(SimpleNamespace(status=404, reason='Not Found'),
                            {'code': code, 'message': text})


class JournalStartResponsiveness(DatabaseCase):
    async def view(self):
        await self.beast('twine', starter=True)
        view = JournalView(42, await journal_state.snapshot(42), page='adventure')
        view.art_enabled = False
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.render()
        self.addCleanup(view.stop)
        return view

    async def test_expired_click_ack_leaves_journal_usable_for_next_click(self):
        view = await self.view()
        expired = component()
        expired.response.defer.side_effect = missing(10062, 'Unknown interaction')
        expired.message = view.message
        with patch.object(view, 'publish', new=AsyncMock()) as publish:
            await view.handle(expired, 'begin')
            self.assertFalse(view.closed)
            self.assertFalse(view.is_finished())
            self.assertFalse(view.mutating)
            self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
            publish.assert_not_awaited()
            retry = component()
            retry.message = view.message
            await view.handle(retry, 'refresh')
            retry.response.defer.assert_awaited_once()
            publish.assert_awaited_once()
            self.assertFalse(view.closed)

    async def test_actual_deleted_journal_still_stops_its_controls(self):
        view = await self.view()
        click = component()
        click.message = view.message
        with patch.object(view, 'publish', new=AsyncMock(side_effect=missing(10008, 'Unknown Message'))):
            await view.handle(click, 'refresh')
        self.assertTrue(view.closed)
        self.assertTrue(view.is_finished())
        self.assertIn('removed', click.followup.send.call_args.args[0])

    async def test_challenge_acknowledges_before_waiting_for_busy_journal(self):
        view = await self.view()
        click = component()
        click.message = view.message
        await view.lock.acquire()
        with patch.object(trial_panel, 'open_trials', new=AsyncMock()) as open_trials:
            task = asyncio.create_task(view.handle(click, 'launch:challenge'))
            try:
                await asyncio.sleep(0)
                click.response.defer.assert_awaited_once()
                open_trials.assert_not_awaited()
            finally:
                view.lock.release()
                await asyncio.wait_for(task, 2)
            open_trials.assert_awaited_once_with(click, bot=view.bot, destination=view.realm,
                                                source=view, already_deferred=True)

    async def test_first_start_waits_for_refresh_and_duplicate_does_not_spend_again(self):
        view = await self.view()
        first, duplicate = component(), component()
        first.message = duplicate.message = view.message
        await view.lock.acquire()
        with patch('utils.journal_adventure.present', new=AsyncMock()) as present:
            task = asyncio.create_task(view.handle(first, 'begin'))
            try:
                await asyncio.sleep(0)
                first.response.defer.assert_awaited_once()
                self.assertFalse(task.done(), 'First Start should queue behind a refresh')
                self.assertTrue(view.mutating)
                await view.handle(duplicate, 'begin')
                self.assertIn('updating', duplicate.followup.send.call_args.args[0])
                self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])
            finally:
                view.lock.release()
                await asyncio.wait_for(task, 10)
            self.assertEqual(len(await self.rows('SELECT id FROM expeditions')), 1)
            present.assert_awaited_once()
            self.assertFalse(view.mutating)

    async def test_start_shows_committed_trip_before_slow_art_and_spends_only_once(self):
        view = await self.view()
        view.art_enabled = True
        energy = (await expeditions.energy(42)).charges
        click = component()
        click.message = view.message
        first_publish, rendering, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        visible = []

        async def publish(*args, **kwargs):
            visible.append((view.journey_playing, view.journey_media,
                            view.trip['id'] if view.trip else None, view.art_enabled))
            first_publish.set()

        async def render(*args, **kwargs):
            rendering.set()
            await release.wait()
            # A failed or unavailable renderer must not retry the committed
            # departure or charge the player again when restoring controls.
            return None

        with patch.object(view, 'publish', new=AsyncMock(side_effect=publish)), \
             patch.object(battle_art.BattlePresentation, 'art', new=AsyncMock(side_effect=render)):
            task = asyncio.create_task(view.handle(click, 'begin'))
            try:
                await asyncio.wait_for(rendering.wait(), 10)
                await asyncio.wait_for(first_publish.wait(), 1)
                self.assertTrue(visible[0][0], 'The saved departure must visibly be preparing playback')
                self.assertIsNone(visible[0][1], 'Feedback must not wait for a media attachment')
                self.assertFalse(visible[0][3], 'Preparing feedback must not upload a header or portrait')
                self.assertIsNotNone(visible[0][2])
                saved = await self.rows('SELECT id FROM expeditions WHERE user_id=42')
                self.assertEqual(saved, [{'id': visible[0][2]}])
                self.assertEqual((await expeditions.energy(42)).charges, energy - 1)
                duplicate = component()
                duplicate.message = view.message
                await asyncio.wait_for(view.handle(duplicate, 'begin'), 1)
                duplicate.response.defer.assert_awaited_once()
                self.assertIn('updating', duplicate.followup.send.call_args.args[0])
            finally:
                release.set()
                await asyncio.wait_for(task, 10)
        self.assertEqual(len(await self.rows('SELECT id FROM expeditions WHERE user_id=42')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, energy - 1)
        self.assertFalse(view.mutating)
        self.assertFalse(view.journey_playing)
        self.assertTrue(view.art_enabled)

    async def test_changed_trip_during_first_render_restores_current_controls(self):
        view = await self.view()
        view.art_enabled = True
        view.trip = await expeditions.begin(42, 'woods', adventure_version=3)
        original_version = view.trip['version']
        view.state = await journal_state.snapshot(42)
        visible = []

        async def publish(*args, **kwargs):
            visible.append((view.trip['version'], view.journey_playing, view.art_enabled))

        async def another_journal_updates(*args, **kwargs):
            await self.execute('UPDATE expeditions SET version=version+1 WHERE id=?', (view.trip['id'],))
            return None

        with patch.object(view, 'publish', new=AsyncMock(side_effect=publish)), \
             patch.object(battle_art.BattlePresentation, 'art', new=AsyncMock(side_effect=another_journal_updates)):
            await journal_adventure.present(view, component(), animate=True)
        self.assertEqual(visible[0], (original_version, True, False))
        self.assertEqual(visible[-1], (original_version + 1, False, False))
        self.assertEqual(view.trip['version'], original_version + 1)
        self.assertFalse(view.closed)
        self.assertFalse(view.journey_playing)
        self.assertTrue(view.art_enabled)
        self.assertTrue(any(isinstance(item, discord.ui.Button) and item.label == 'Refresh' and not item.disabled
                            for item in view.walk_children()))
        self.assertEqual(len(await self.rows('SELECT id FROM expeditions')), 1)
