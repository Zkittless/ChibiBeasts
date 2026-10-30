"""Expired journal webhook tokens never service public message operations."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from tests.test_message_transport import connection, response
from utils import gear_art, journal_sessions as sessions
from utils.journal import JournalView


class RetiredView:
    def __init__(self):
        self.closed = False
        self.render = Mock()
        self.stop = Mock()


@contextmanager
def invalid_token_methods(cls):
    error = discord.HTTPException(SimpleNamespace(status=401, reason='Unauthorized'),
                                  {'code': 50027, 'message': 'Invalid Webhook Token'})
    with patch.object(cls, 'delete', new_callable=AsyncMock) as delete, \
         patch.object(cls, 'edit', new_callable=AsyncMock) as edit:
        delete.side_effect = edit.side_effect = error
        yield delete, edit


def attachment(state, attachment_id, filename):
    return discord.Attachment(state=state, data={
        'id': str(attachment_id), 'filename': filename, 'size': 32,
        'url': f'https://cdn.discordapp.com/attachments/999/{attachment_id}/{filename}',
        'proxy_url': f'https://media.discordapp.net/attachments/999/{attachment_id}/{filename}',
    })


class JournalMessageTransport(unittest.IsolatedAsyncioTestCase):
    def public_message(self, cls=discord.WebhookMessage):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        return response(cls, channel=channel), state, channel

    def journal(self, message):
        # Exercise the production sender and timeout with a real LayoutView;
        # page-state queries and painting are outside this transport contract.
        view = JournalView.__new__(JournalView)
        discord.ui.LayoutView.__init__(view, timeout=None)
        view.add_item(discord.ui.TextDisplay('Your adventure is ready.'))
        view.page, view.realm = 'home', 'woods'
        view.art_enabled, view.portraits = True, {}
        view.message = message
        view.lock = asyncio.Lock()
        view.closed = view.message_repurposed = False
        view.render = Mock()
        self.addCleanup(view.stop)
        return view

    async def test_retirement_uses_bot_delete_after_webhook_token_expired(self):
        for cls in (discord.WebhookMessage, discord.InteractionMessage):
            with self.subTest(message_type=cls.__name__):
                message, state, channel = self.public_message(cls)
                old, replacement = RetiredView(), RetiredView()
                with invalid_token_methods(cls) as (webhook_delete, webhook_edit):
                    await sessions._retire(sessions._panel(old, message), protected=456,
                                           replacement=replacement, channel=channel)
                state.http.delete_message.assert_awaited_once_with(999, 123)
                state.http.edit_message.assert_not_awaited()
                webhook_delete.assert_not_awaited()
                webhook_edit.assert_not_awaited()
                self.assertTrue(old.closed)
                old.stop.assert_called_once()
                self.assertEqual(old._journal_retired_token, 123)
                self.assertFalse(replacement.closed)
                replacement.stop.assert_not_called()

    async def test_forbidden_bot_delete_clears_controls_and_art_with_bot_edit(self):
        message, state, channel = self.public_message()
        state.http.delete_message.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason='Forbidden'), 'Delete denied')
        old, replacement = RetiredView(), RetiredView()
        with invalid_token_methods(discord.WebhookMessage) as (webhook_delete, webhook_edit), \
             patch('discord.message.Message', return_value=object()), \
             self.assertLogs('chibibeasts.journal_sessions', level='WARNING'):
            await sessions._retire(sessions._panel(old, message), replacement=replacement,
                                   protected=456, channel=channel)
        state.http.delete_message.assert_awaited_once_with(999, 123)
        state.http.edit_message.assert_awaited_once()
        call = state.http.edit_message.call_args
        self.assertEqual(call.args, (999, 123))
        payload = call.kwargs['params'].payload
        self.assertEqual(payload['attachments'], [])
        self.assertEqual([item['type'] for item in payload['components']], [10])
        self.assertIn('newest journal', payload['components'][0]['content'])
        self.assertTrue(payload['flags'] & (1 << 15))
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        webhook_delete.assert_not_awaited()
        webhook_edit.assert_not_awaited()
        self.assertTrue(old.closed)
        self.assertFalse(replacement.closed)
        replacement.stop.assert_not_called()

    async def test_publish_uses_bot_edit_and_reuses_existing_attachments(self):
        for cls in (discord.WebhookMessage, discord.InteractionMessage):
            with self.subTest(message_type=cls.__name__):
                message, state, channel = self.public_message(cls)
                view = self.journal(message)
                portrait = gear_art.portrait_filename('twine', 'gnome_iron_plating')
                view.portraits[portrait] = ('twine', 'gnome_iron_plating')
                existing = [attachment(state, 501, view.header.name),
                            attachment(state, 502, portrait)]
                message.attachments = existing
                updated = SimpleNamespace(id=123, attachments=existing)
                interaction = SimpleNamespace(channel=channel, edit_original_response=AsyncMock(),
                                              followup=SimpleNamespace(send=AsyncMock()))
                with invalid_token_methods(cls) as (webhook_delete, webhook_edit), \
                     patch('discord.message.Message', return_value=updated), \
                     patch.object(gear_art, 'render_portrait', new_callable=AsyncMock) as render:
                    await view.publish(interaction)
                state.http.edit_message.assert_awaited_once()
                call = state.http.edit_message.call_args
                self.assertEqual(call.args, (999, 123))
                self.assertEqual(call.kwargs['params'].payload['attachments'],
                                 [item.to_dict() for item in existing])
                self.assertIs(message.attachments, existing)
                self.assertIs(view.message, updated)
                render.assert_not_awaited()
                interaction.edit_original_response.assert_not_awaited()
                interaction.followup.send.assert_not_awaited()
                webhook_delete.assert_not_awaited()
                webhook_edit.assert_not_awaited()

    async def test_timeout_disables_the_public_journal_through_bot_edit(self):
        for cls in (discord.WebhookMessage, discord.InteractionMessage):
            with self.subTest(message_type=cls.__name__):
                message, state, _ = self.public_message(cls)
                view = self.journal(message)
                with invalid_token_methods(cls) as (webhook_delete, webhook_edit), \
                     patch('discord.message.Message', return_value=object()):
                    await view.on_timeout()
                self.assertTrue(view.closed)
                view.render.assert_called_once()
                state.http.edit_message.assert_awaited_once()
                call = state.http.edit_message.call_args
                self.assertEqual(call.args, (999, 123))
                self.assertNotIn('attachments', call.kwargs['params'].payload)
                webhook_delete.assert_not_awaited()
                webhook_edit.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
