"""A modal-only response still acknowledges promptly when storage is slow."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from utils import journal_adventure


class TeamMenuAcknowledgement(unittest.IsolatedAsyncioTestCase):
    async def test_slow_read_returns_retry_notice_and_releases_journal(self):
        view = SimpleNamespace(page='adventure', closed=False, is_finished=lambda: False,
            message_repurposed=False, journey_playing=False, lock=asyncio.Lock(),
            mutating=False, user_id=42)
        response = SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock())
        cancelled = asyncio.Event()

        async def slow_read(*args):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with patch.object(journal_adventure, 'TEAM_MENU_READ_TIMEOUT', .01), \
             patch.object(journal_adventure.expeditions, 'read', side_effect=slow_read):
            await asyncio.wait_for(journal_adventure.open_team_menu(
                view, SimpleNamespace(response=response), 'trip', 1), .5)
        self.assertTrue(cancelled.is_set())
        response.send_message.assert_awaited_once()
        self.assertTrue(response.send_message.call_args.kwargs['ephemeral'])
        self.assertIn('again', response.send_message.call_args.args[0])
        response.send_modal.assert_not_awaited()
        self.assertFalse(view.lock.locked())
        self.assertFalse(view.closed)
