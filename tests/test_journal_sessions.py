"""Journal replacement preserves a usable panel and retires only its owner."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from tests.test_gameplay import DatabaseCase
from utils import journal_sessions as sessions


class View:
    def __init__(self, uid=42):
        self.user_id = uid
        self.prefix = 'journal:test'
        self.message = None
        self.closed = False
        self.stop = Mock()


def message(message_id):
    return SimpleNamespace(id=message_id, delete=AsyncMock(), edit=AsyncMock())


class JournalSessions(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        sessions._panels.clear()
        sessions._locks.clear()
        self.messages = {}
        self.channel = SimpleNamespace(id=999, get_partial_message=lambda mid: self.messages[mid])
        self.interaction = SimpleNamespace(channel=self.channel, channel_id=999)

    async def asyncTearDown(self):
        sessions._panels.clear()
        sessions._locks.clear()
        await super().asyncTearDown()

    async def send(self, mid, *, view=None, interaction=None, source=None, replace=False):
        view = view or View()
        msg = self.messages.setdefault(mid, message(mid))
        await sessions.publish(view, interaction or self.interaction, AsyncMock(return_value=msg),
                               source=source, replace=replace)
        return view

    async def test_send_new_before_retiring_current_and_record_its_id(self):
        old = await self.send(101)
        new, replacement = View(), message(102)

        async def send():
            self.assertFalse(old.closed)
            old.message.delete.assert_not_awaited()
            return replacement

        result = await sessions.publish(new, self.interaction, send)
        self.assertIs(result, replacement)
        self.assertIs(new.message, replacement)
        old.message.delete.assert_awaited_once()
        self.assertTrue(old.closed)
        self.assertFalse(new.closed)
        self.assertEqual(await self.rows('SELECT * FROM journal_panels'),
                         [{'user_id': 42, 'channel_id': 999, 'message_id': 102}])

    async def test_failed_new_send_leaves_old_journal_usable(self):
        old = await self.send(101)
        with self.assertRaises(TimeoutError):
            await sessions.publish(View(), self.interaction, AsyncMock(side_effect=TimeoutError))
        self.assertFalse(old.closed)
        old.stop.assert_not_called()
        old.message.delete.assert_not_awaited()
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 101)

    async def test_concurrent_opens_serialize_send_and_retirement(self):
        old = await self.send(101)
        entered, release = asyncio.Event(), asyncio.Event()
        first, second = View(), View()
        first_msg, second_msg = message(102), message(103)

        async def first_send():
            entered.set()
            await release.wait()
            return first_msg

        second_send = AsyncMock(return_value=second_msg)
        first_task = asyncio.create_task(sessions.publish(first, self.interaction, first_send))
        await asyncio.wait_for(entered.wait(), 2)
        second_task = asyncio.create_task(sessions.publish(second, self.interaction, second_send))
        try:
            await asyncio.sleep(0)
            second_send.assert_not_awaited()
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), 3)
        old.message.delete.assert_awaited_once()
        first_msg.delete.assert_awaited_once()
        second_msg.delete.assert_not_awaited()
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 103)

    async def test_restart_recovers_prior_id_and_deletes_only_that_message(self):
        old = await self.send(101)
        unrelated = self.messages[500] = message(500)
        sessions._panels.clear()
        await self.send(102)
        old.message.delete.assert_awaited_once()
        unrelated.delete.assert_not_awaited()

    async def test_owner_and_channel_each_have_their_own_current_journal(self):
        first = await self.send(101)
        second = await self.send(102, view=View(43))
        another_channel = SimpleNamespace(channel=SimpleNamespace(id=1000), channel_id=1000)
        third = await self.send(103, interaction=another_channel)
        for view in (first, second, third):
            view.message.delete.assert_not_awaited()
            self.assertFalse(view.closed)
        self.assertEqual(len(await self.rows('SELECT * FROM journal_panels')), 3)

    async def test_forbidden_delete_clears_controls_and_artwork(self):
        old = await self.send(101)
        old.message.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        with self.assertLogs('chibibeasts.journal_sessions', level='WARNING'):
            await self.send(102)
        self.assertTrue(old.closed)
        old.stop.assert_called_once()
        edit = old.message.edit.call_args.kwargs
        self.assertEqual(edit['attachments'], [])
        self.assertTrue(all(isinstance(item, discord.ui.TextDisplay) for item in edit['view'].walk_children()))
        self.assertIn('newest journal', edit['view'].children[0].content)

    async def test_missing_prior_message_does_not_need_an_edit(self):
        old = await self.send(101)
        old.message.delete.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'gone')
        await self.send(102)
        old.message.edit.assert_not_awaited()
        self.assertTrue(old.closed)

    async def test_completed_retirement_is_not_repeated_by_battle_source(self):
        origin = await self.send(101)
        manual = await self.send(102)
        await self.send(103, source=origin)
        origin.message.delete.assert_awaited_once()
        manual.message.delete.assert_awaited_once()

    async def test_same_message_handoff_stops_former_view_without_deleting_panel(self):
        old = await self.send(101)
        new = await self.send(101, replace=True)
        self.assertTrue(old.closed)
        self.assertFalse(new.closed)
        new.message.delete.assert_not_awaited()
        new.message.edit.assert_not_awaited()

    async def test_forget_is_conditional_and_cannot_unregister_a_newer_journal(self):
        old = await self.send(101)
        current = await self.send(102)
        await sessions.forget(old, self.interaction)
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 102)
        await sessions.forget(current, self.interaction)
        self.assertEqual(await self.rows('SELECT * FROM journal_panels'), [])

    async def test_timeout_does_not_prevent_next_open_deleting_old_message(self):
        old = await self.send(101)
        old.closed = True
        await self.send(102)
        old.message.delete.assert_awaited_once()

    async def test_repurposed_workshop_source_is_never_deleted(self):
        old = await self.send(101)
        old.message_repurposed = True
        await sessions.forget(old, self.interaction)
        await self.send(102, source=old)
        old.message.delete.assert_not_awaited()
        old.message.edit.assert_not_awaited()

    async def test_database_failure_retires_new_send_and_preserves_former_journal(self):
        old = await self.send(101)
        new, sent = View(), message(102)
        with patch.object(sessions, '_remember', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                await sessions.publish(new, self.interaction, AsyncMock(return_value=sent))
        sent.delete.assert_awaited_once()
        self.assertTrue(new.closed)
        self.assertFalse(old.closed)
        old.message.delete.assert_not_awaited()
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 101)

    async def test_discard_only_targets_old_view_and_keeps_newer_registry_entry(self):
        old = await self.send(101)
        current = await self.send(102)
        await sessions.discard(old, self.interaction)
        old.message.delete.assert_awaited_once()
        current.message.delete.assert_not_awaited()
        self.assertFalse(current.closed)
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 102)

    async def test_discard_removes_old_journal_without_registering_nursery(self):
        old = await self.send(101)
        await sessions.discard(old, self.interaction)
        old.message.delete.assert_awaited_once()
        self.assertTrue(old.closed)
        self.assertEqual(await self.rows('SELECT * FROM journal_panels'), [])

    async def test_failed_replacement_registration_preserves_existing_workshop_without_registry(self):
        view, workshop_message = View(), message(501)
        with patch.object(sessions, '_remember', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                await sessions.publish(view, self.interaction, AsyncMock(return_value=workshop_message), replace=True)
        workshop_message.delete.assert_not_awaited()
        workshop_message.edit.assert_not_awaited()
        self.assertTrue(view.closed)
        view.stop.assert_called_once()
        self.assertEqual(await self.rows('SELECT * FROM journal_panels'), [])

    async def test_failed_replacement_registration_keeps_distinct_tracked_journal_and_workshop(self):
        current = await self.send(101)
        view, workshop_message = View(), message(501)
        with patch.object(sessions, '_remember', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                await sessions.publish(view, self.interaction, AsyncMock(return_value=workshop_message), replace=True)
        workshop_message.delete.assert_not_awaited()
        workshop_message.edit.assert_not_awaited()
        current.message.delete.assert_not_awaited()
        self.assertFalse(current.closed)
        self.assertTrue(view.closed)
        view.stop.assert_called_once()
        self.assertEqual((await self.rows('SELECT message_id FROM journal_panels'))[0]['message_id'], 101)
