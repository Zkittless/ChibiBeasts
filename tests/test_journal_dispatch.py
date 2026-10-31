"""Exercise the SDK dispatcher, not just direct button callback calls."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ui.view import ViewStore

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import battle_art, expeditions, journal_diagnostics, journal_state, trial_locks
from utils.journal import JournalView


def click_for(view, action, *, value=None, uid=42):
    item = next(item for item in view.walk_children()
                if getattr(item, 'custom_id', '').startswith(view.prefix + ':' + action))
    click = component(uid=uid)
    click.type = discord.InteractionType.component
    click.extras = {}
    click.message = view.message
    click.channel_id = 999
    click.data = dict(custom_id=item.custom_id, component_type=item.type.value)
    if value is not None:
        click.data['values'] = [value]
    return click


class JournalDispatch(DatabaseCase):
    async def journal(self, *, page='home'):
        await self.beast('twine', starter=True)
        view = JournalView(42, await journal_state.snapshot(42), page=page)
        self.addCleanup(view.stop)
        view.art_enabled = False
        view.message = SimpleNamespace(id=123, attachments=[])
        view.render()
        return view

    async def dispatch(self, store, click):
        tasks = []
        # Keep the SDK's actual lookup, checks, callbacks and error dispatch.
        # Track its tasks explicitly so the temporary DB outlives every callback.
        with patch.object(store, 'add_task', side_effect=tasks.append):
            store.dispatch_view(click.data['component_type'], click.data['custom_id'], click)
            tasks.append(asyncio.create_task(journal_diagnostics.received(click)))
            await asyncio.wait_for(asyncio.gather(*tasks), 15)

    async def test_fresh_journal_navigation_and_start_dispatch_once(self):
        view = await self.journal()
        store = ViewStore(SimpleNamespace())
        store.add_view(view, 123)

        async def publish(*args, **kwargs):
            # A successful Discord send/edit registers the rebuilt controls.
            store.add_view(view, 123)

        before = (await expeditions.energy(42)).charges
        with patch.object(view, 'publish', new=AsyncMock(side_effect=publish)), \
             patch.object(battle_art.BattlePresentation, 'art', new=AsyncMock(return_value=None)), \
             self.assertLogs('chibibeasts.journal.input', level='INFO') as logs:
            for action, value in [('page:adventure', None), ('realm', 'woods'), ('refresh', None), ('begin', None)]:
                click = click_for(view, action, value=value)
                await self.dispatch(store, click)
                click.response.defer.assert_awaited_once()
                click.response.send_message.assert_not_awaited()
            self.assertEqual(view.trip['status'], 'battle')
        self.assertEqual(len(await self.rows('SELECT id FROM expeditions')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, before - 1)
        output = '\n'.join(logs.output)
        self.assertIn('stage=received', output)
        self.assertIn('stage=adventure_saved', output)
        self.assertNotIn('stage=unhandled', output)

    async def test_unregistered_controls_get_recovery_notice_and_log_without_spend(self):
        view = await self.journal(page='adventure')
        store = ViewStore(SimpleNamespace())
        store.add_view(view, 123)
        click = click_for(view, 'begin')
        view.stop()  # Same missing callback state as a restart/expired panel.
        before = (await expeditions.energy(42)).charges
        with self.assertLogs('chibibeasts.journal.input', level='INFO') as logs:
            await self.dispatch(store, click)
        click.response.send_message.assert_awaited_once()
        self.assertIn('/journal', click.response.send_message.call_args.args[0])
        self.assertTrue(click.response.send_message.call_args.kwargs['ephemeral'])
        self.assertIn('stage=unhandled', '\n'.join(logs.output))
        click.response.defer.assert_not_awaited()
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, before)

    async def test_owner_rejection_has_one_response_and_no_fallback(self):
        view = await self.journal(page='adventure')
        store = ViewStore(SimpleNamespace())
        store.add_view(view, 123)
        click = click_for(view, 'begin', uid=99)
        await self.dispatch(store, click)
        click.response.send_message.assert_awaited_once()
        self.assertIn('own adventure', click.response.send_message.call_args.args[0])
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])

    async def test_live_callback_waiting_for_lock_is_not_replied_to_twice(self):
        view = await self.journal(page='adventure')
        store = ViewStore(SimpleNamespace())
        store.add_view(view, 123)
        click = click_for(view, 'begin')
        acknowledged = asyncio.Event()
        click.response.defer.side_effect = lambda: acknowledged.set()
        await view.lock.acquire()
        task = asyncio.create_task(self.dispatch(store, click))
        try:
            await asyncio.wait_for(acknowledged.wait(), 2)
            await asyncio.sleep(0)
            click.response.send_message.assert_not_awaited()
            # Replacing the journal while it waited must explain the move.
            view.closed = True
        finally:
            view.lock.release()
            await task
        click.response.defer.assert_awaited_once()
        click.followup.send.assert_awaited_once()
        self.assertIn('moved', click.followup.send.call_args.args[0])
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])

    async def test_stale_completed_trip_cannot_hide_start_rejection(self):
        view = await self.journal(page='adventure')
        trip = await expeditions.begin(42, 'woods', adventure_version=3)
        # The page still shows its cached completed trip and one charge.
        trip['status'] = 'complete'
        view.trip = trip
        await self.execute("UPDATE expeditions SET status='complete',version=version+1,reported=1 WHERE id=?", (trip['id'],))
        trial_locks.unlock(42)
        view.render()
        start = next(item for item in view.walk_children() if getattr(item, 'label', '').startswith('Start adventure'))
        self.assertFalse(start.disabled)
        click = click_for(view, 'begin')
        visible = []

        async def publish(*args, **kwargs):
            visible.append('\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)))

        with patch.object(view, 'publish', new=AsyncMock(side_effect=publish)):
            await view.handle(click, 'begin')
        self.assertEqual(len(visible), 1)
        self.assertIn('next exploration charge', visible[0])
        self.assertEqual(view.trip['version'], trip['version'] + 1)
        self.assertEqual(len(await self.rows('SELECT id FROM expeditions')), 1)
        self.assertFalse(view.mutating)

    async def test_timeout_explains_disabled_controls_in_the_existing_message(self):
        view = await self.journal(page='adventure')
        view.message.edit = AsyncMock()
        await view.on_timeout()
        view.message.edit.assert_awaited_once()
        text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn('timed out', text)
        self.assertIn('/journal', text)
        self.assertTrue(all(item.disabled for item in view.walk_children()
                            if isinstance(item, (discord.ui.Button, discord.ui.Select))))


class ReceiptFiltering(unittest.IsolatedAsyncioTestCase):
    async def test_other_components_commands_and_modals_are_untouched(self):
        for kind, custom_id in [(discord.InteractionType.application_command, 'journal:abc:begin:1'),
                                (discord.InteractionType.modal_submit, 'journal:abc:begin:1'),
                                (discord.InteractionType.component, 'shop:buy')]:
            click = component()
            click.type = kind
            click.data = {'custom_id': custom_id}
            await journal_diagnostics.received(click)
            click.response.send_message.assert_not_awaited()

    async def test_expired_unhandled_click_is_logged_without_killing_gateway_listener(self):
        click = component()
        click.type = discord.InteractionType.component
        click.data = {'custom_id': 'journal:abc:begin:1', 'values': ['private-choice']}
        click.token = 'never-log-this-token'
        click.response.send_message.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason='Not Found'), {'code': 10062, 'message': 'Unknown interaction'})
        with self.assertLogs('chibibeasts.journal.input', level='INFO') as logs:
            await journal_diagnostics.received(click)
        output = '\n'.join(logs.output)
        self.assertIn('code=10062', output)
        self.assertNotIn(click.token, output)
        self.assertNotIn('private-choice', output)
