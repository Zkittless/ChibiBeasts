"""Long-lived journal handoffs never require an expired public webhook token."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from tests.test_message_transport import connection, response
from utils import journal, nursery_view, roster_view, trial_panel, workshop


def invalid_token():
    return discord.HTTPException(SimpleNamespace(status=401, reason='Unauthorized'),
                                 {'code': 50027, 'message': 'Invalid Webhook Token'})


class TrialMessageTransport(unittest.IsolatedAsyncioTestCase):
    def make_panel(self, *, ephemeral=False):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        panel = trial_panel.TrialPanel(42)
        self.addCleanup(panel.stop)
        panel.channel = channel
        panel.message = response(channel=channel, ephemeral=ephemeral)
        panel.fresh = AsyncMock(return_value=True)
        panel.render = Mock()
        return panel, state

    async def test_background_and_click_trial_refresh_bypass_expired_public_token(self):
        for click in (None, SimpleNamespace()):
            with self.subTest(background=click is None):
                panel, state = self.make_panel()
                original = panel.message
                edited = SimpleNamespace(id=123, attachments=['retained metadata'])
                with patch.object(discord.WebhookMessage, 'edit', autospec=True,
                                  side_effect=invalid_token()) as webhook, \
                     patch.object(discord.PartialMessage, 'edit', autospec=True,
                                  return_value=edited) as bot_edit:
                    self.assertTrue(await panel.deliver(click))
                webhook.assert_not_awaited()
                bot_edit.assert_awaited_once()
                handle = bot_edit.call_args.args[0]
                self.assertIs(handle._state, state)
                self.assertEqual((handle.id, handle.channel.id), (original.id, 999))
                self.assertIs(panel.message, edited, 'Keep the response object and its metadata')

    async def test_normal_bot_message_keeps_its_existing_edit_route(self):
        panel, _ = self.make_panel()
        message = SimpleNamespace(id=123, edit=AsyncMock())
        message.edit.return_value = message
        panel.message = message
        self.assertTrue(await panel.deliver())
        message.edit.assert_awaited_once()
        self.assertIs(panel.message, message)

    async def test_ephemeral_trial_message_cannot_switch_to_public_bot_route(self):
        panel, _ = self.make_panel(ephemeral=True)
        message = panel.message
        with patch.object(discord.WebhookMessage, 'edit', autospec=True,
                          return_value=message) as webhook, \
             patch.object(discord.PartialMessage, 'edit', autospec=True) as bot_edit:
            self.assertTrue(await panel.deliver())
        webhook.assert_awaited_once()
        bot_edit.assert_not_awaited()
        self.assertIs(panel.message, message)

    async def test_invalid_token_does_not_recreate_or_duplicate_a_trial_message(self):
        panel, _ = self.make_panel(ephemeral=True)
        channel = SimpleNamespace(send=AsyncMock())
        panel.channel = channel
        with patch.object(discord.WebhookMessage, 'edit', autospec=True,
                          side_effect=invalid_token()), \
             self.assertLogs('chibibeasts.trials.panel', level='WARNING'):
            self.assertFalse(await panel.deliver())
        channel.send.assert_not_awaited()
        self.assertFalse(panel.recreated)

    async def test_adjacent_panel_timeouts_use_bot_auth_for_public_messages(self):
        for view_class in (workshop.WorkshopView, nursery_view.NurseryView):
            with self.subTest(panel=view_class.__name__):
                state = connection()
                channel = discord.PartialMessageable(state=state, id=999)
                message = response(channel=channel)
                view = SimpleNamespace(closed=False, lock=asyncio.Lock(), render=Mock(), message=message)
                with patch.object(discord.WebhookMessage, 'edit', autospec=True,
                                  side_effect=invalid_token()) as webhook, \
                     patch.object(discord.PartialMessage, 'edit', autospec=True) as bot_edit:
                    await view_class.on_timeout(view)
                self.assertTrue(view.closed)
                webhook.assert_not_awaited()
                bot_edit.assert_awaited_once()
                self.assertIs(bot_edit.call_args.args[0]._state, state)

    async def test_roster_return_deletes_old_public_panel_through_bot_auth(self):
        state = connection()
        channel = discord.PartialMessageable(state=state, id=999)
        view = roster_view.OwnedRosterView(42, 'Trainer')
        self.addCleanup(view.stop)
        view.message = response(channel=channel)
        click = SimpleNamespace(user=SimpleNamespace(id=42), channel=channel)
        replacement = SimpleNamespace()
        with patch.object(journal, 'open_journal', new=AsyncMock(return_value=replacement)), \
             patch.object(discord.WebhookMessage, 'delete', autospec=True,
                          side_effect=invalid_token()) as webhook:
            self.assertIs(await view.return_journal(click), replacement)
        webhook.assert_not_awaited()
        state.http.delete_message.assert_awaited_once_with(999, 123)
        self.assertTrue(view.is_finished())

    async def test_reopening_trials_retires_expired_panel_in_its_original_channel(self):
        old_state, new_state = connection(), connection()
        old_channel = discord.PartialMessageable(state=old_state, id=999)
        new_channel = discord.PartialMessageable(state=new_state, id=888)
        previous = trial_panel.TrialPanel(973)
        self.addCleanup(previous.stop)
        previous.channel, previous.message = old_channel, response(channel=old_channel)
        trial_panel._panels[previous.key] = previous
        self.addCleanup(trial_panel._panels.pop, previous.key, None)
        click = SimpleNamespace(user=SimpleNamespace(id=973), channel=new_channel,
                                response=SimpleNamespace(defer=AsyncMock()))
        replacement = response(channel=new_channel, message_id=456)

        async def delivered(view, *_args, **_kwargs):
            view.message = replacement
            return True

        with patch.object(trial_panel.storage, 'get_or_create_player', new=AsyncMock()), \
             patch.object(trial_panel.TrialPanel, 'load', new=AsyncMock()), \
             patch.object(trial_panel.TrialPanel, 'prepare_media', new=AsyncMock()), \
             patch.object(trial_panel.TrialPanel, 'arm', new=AsyncMock()), \
             patch.object(trial_panel.TrialPanel, 'deliver', autospec=True, side_effect=delivered), \
             patch.object(discord.WebhookMessage, 'delete', autospec=True,
                          side_effect=invalid_token()) as webhook:
            current = await trial_panel.open_trials(click, destination='woods')
            self.addCleanup(current.stop)
        self.assertIs(current.message, replacement)
        self.assertTrue(previous.closed)
        webhook.assert_not_awaited()
        old_state.http.delete_message.assert_awaited_once_with(999, 123)
        new_state.http.delete_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
