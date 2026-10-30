"""Public responses use bot auth while ephemeral responses retain their route."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from utils.message_transport import durable_message


def connection():
    return SimpleNamespace(
        http=SimpleNamespace(delete_message=AsyncMock(), edit_message=AsyncMock()),
        _get_guild=lambda _: None,
        allowed_mentions=None,
        prevent_view_updates_for=Mock(),
        store_view=Mock(),
    )


def response(cls=discord.WebhookMessage, *, channel=None, ephemeral=False, message_id=123):
    message = object.__new__(cls)
    message.id = message_id
    message.channel = channel
    message.flags = discord.MessageFlags(ephemeral=ephemeral)
    return message


class MessageTransport(unittest.IsolatedAsyncioTestCase):
    async def test_public_followup_and_original_response_use_bot_http_without_fetch(self):
        for cls in (discord.WebhookMessage, discord.InteractionMessage):
            with self.subTest(message_type=cls.__name__):
                state = connection()
                channel = discord.PartialMessageable(state=state, id=999)
                message = response(cls, channel=channel)
                handle = durable_message(message)
                self.assertIsInstance(handle, discord.PartialMessage)
                self.assertIs(handle._state, state)
                self.assertEqual((handle.id, handle.channel.id), (123, 999))
                state.http.delete_message.assert_not_awaited()
                state.http.edit_message.assert_not_awaited()
                await handle.delete()
                state.http.delete_message.assert_awaited_once_with(999, 123)

    async def test_ephemeral_messages_keep_their_interaction_endpoint(self):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        for cls in (discord.WebhookMessage, discord.InteractionMessage):
            with self.subTest(message_type=cls.__name__):
                message = response(cls, channel=channel, ephemeral=True)
                self.assertIs(durable_message(message, channel=channel), message)
        state.http.delete_message.assert_not_awaited()
        state.http.edit_message.assert_not_awaited()

    async def test_components_v2_retirement_edit_uses_bot_http(self):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        message = response(channel=channel)
        handle = durable_message(message)
        retired = discord.ui.LayoutView(timeout=None)
        self.addCleanup(retired.stop)
        retired.add_item(discord.ui.TextDisplay('This journal has moved.'))
        updated = object()
        with patch('discord.message.Message', return_value=updated):
            result = await handle.edit(view=retired, attachments=[],
                                       allowed_mentions=discord.AllowedMentions.none())
        self.assertIs(result, updated)
        state.http.edit_message.assert_awaited_once()
        call = state.http.edit_message.call_args
        self.assertEqual(call.args, (999, 123))
        self.assertEqual(call.kwargs['params'].payload['attachments'], [])
        self.assertEqual(call.kwargs['params'].payload['components'], retired.to_components())
        self.assertTrue(call.kwargs['params'].payload['flags'] & (1 << 15))

    async def test_existing_bot_messages_and_metadata_are_preserved(self):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        partial = channel.get_partial_message(123)
        regular = object.__new__(discord.Message)
        fake = SimpleNamespace(id=123, edit=AsyncMock(), delete=AsyncMock())
        for message in (None, regular, partial, fake):
            self.assertIs(durable_message(message, channel=channel), message)

    async def test_fresh_channel_can_replace_an_unmanaged_channel_with_the_same_id(self):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        old_channel = SimpleNamespace(id=999, get_partial_message=Mock())
        message = response(channel=old_channel)
        handle = durable_message(message, channel=channel)
        self.assertIs(handle._state, state)
        await handle.delete()
        state.http.delete_message.assert_awaited_once_with(999, 123)
        old_channel.get_partial_message.assert_not_called()

    async def test_wrong_channel_override_cannot_target_a_different_channel(self):
        original_state, wrong_state = connection(), connection()
        original = discord.PartialMessageable(state=original_state, id=999)
        wrong = discord.PartialMessageable(state=wrong_state, id=888)
        message = response(channel=original)
        handle = durable_message(message, channel=wrong)
        await handle.delete()
        original_state.http.delete_message.assert_awaited_once_with(999, 123)
        wrong_state.http.delete_message.assert_not_awaited()

    async def test_unavailable_bot_connection_and_invalid_ids_keep_original_route(self):
        unmanaged = SimpleNamespace(id=999, get_partial_message=Mock())
        message = response(channel=unmanaged)
        self.assertIs(durable_message(message), message)
        unmanaged.get_partial_message.assert_not_called()
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        for invalid_id in (None, 0, -1, True, '123'):
            message = response(channel=channel, message_id=invalid_id)
            self.assertIs(durable_message(message), message)
        state.http.delete_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
