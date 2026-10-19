"""Exercise exploration's return to the journal after authoritative battle results."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, expeditions
from utils.journal import JournalView, open_journal


class JournalBattleReturn(DatabaseCase):
    async def ready_battle(self):
        await self.beast('twine', starter=True)
        trip = await expeditions.begin(42, 'woods', legacy=True)
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks')
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'challenge')
        opened = interaction()
        opened.channel.id = 999
        message = SimpleNamespace(id=100, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        opened.followup.send.return_value = message
        view = await open_journal(opened, page='adventure')
        self.addCleanup(view.stop)
        self.sent, self.sequence = [], []

        async def send(**kwargs):
            if isinstance(kwargs.get('view'), JournalView):
                self.sequence.append('journal')
                self.sent.append(kwargs['view'])
                self.addCleanup(kwargs['view'].stop)
                return SimpleNamespace(id=100 + len(self.sent), attachments=[], edit=AsyncMock(), delete=AsyncMock())
            self.sequence.append('result')
            return SimpleNamespace(id=200, attachments=[], edit=AsyncMock())

        opened.channel.send.side_effect = send
        click = component()
        click.channel, click.message = opened.channel, message
        click.edit_original_response.return_value = message
        return view, click, trip

    async def finish(self, click, row, data, perks, enemy, personality, title, win, loss, **kwargs):
        await win(discord.Embed(), {}, {'hp': 0}, False)
        # The encounter callback runs before the PvE runner's final progress
        # writes and result publication. A journal must wait for both.
        self.sequence.append('settled')
        await storage.update_player(42, gold=4321)
        await click.channel.send(embed=discord.Embed(title='Victory'))

    async def test_fresh_journal_is_below_result_and_contains_settled_state(self):
        view, click, trip = await self.ready_battle()
        with patch('cogs.battle.run_pve_battle', side_effect=self.finish):
            await view.handle(click, 'battle_continue')
        self.assertEqual(self.sequence, ['settled', 'result', 'journal'])
        self.assertEqual(len(self.sent), 1)
        resumed = self.sent[0]
        self.assertEqual((resumed.page, resumed.trip['id'], resumed.trip['status']), ('adventure', trip['id'], 'active'))
        self.assertEqual(resumed.state.player['gold'], 4321)
        self.assertIsNot(resumed, view)
        self.assertTrue(view.closed)
        view.message.delete.assert_awaited_once()
        self.assertTrue(view.is_finished())
        old_click = component()
        old_click.channel, old_click.message = click.channel, view.message
        await view.handle(old_click, 'battle_continue')
        old_click.response.send_message.assert_awaited_once()
        self.assertEqual(len(self.sent), 1)

    async def test_timeout_during_battle_still_returns_to_a_fresh_journal(self):
        view, click, _ = await self.ready_battle()

        async def finish(*args, **kwargs):
            await view.on_timeout()
            await self.finish(*args, **kwargs)

        with patch('cogs.battle.run_pve_battle', side_effect=finish):
            await view.handle(click, 'battle_continue')
        self.assertEqual(self.sequence, ['settled', 'result', 'journal'])
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.sent[0].closed)

    async def test_deleted_previous_journal_does_not_block_a_new_return(self):
        view, click, _ = await self.ready_battle()
        missing = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'),
                                   {'code': 10008, 'message': 'Unknown Message'})

        async def finish(*args, **kwargs):
            view.message.edit.side_effect = missing
            view.message.delete.side_effect = missing
            await self.finish(*args, **kwargs)

        with patch('cogs.battle.run_pve_battle', side_effect=finish):
            await view.handle(click, 'battle_continue')
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.sent[0].closed)
        self.assertEqual((await expeditions.current(42))['status'], 'active')

    async def test_duplicate_encounter_click_cannot_publish_a_second_return(self):
        view, click, _ = await self.ready_battle()
        started, release = asyncio.Event(), asyncio.Event()

        async def finish(*args, **kwargs):
            started.set()
            await release.wait()
            await self.finish(*args, **kwargs)

        duplicate = component()
        duplicate.channel, duplicate.message = click.channel, view.message
        with patch('cogs.battle.run_pve_battle', side_effect=finish) as runner:
            task = asyncio.create_task(view.handle(click, 'battle_continue'))
            try:
                await asyncio.wait_for(started.wait(), 3)
                await view.handle(duplicate, 'battle_continue')
            finally:
                release.set()
                await asyncio.wait_for(task, 6)
        self.assertEqual(runner.await_count, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sequence.count('result'), 1)

    async def test_delete_permission_failure_leaves_previous_controls_retired(self):
        view, click, _ = await self.ready_battle()
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                      {'code': 50013, 'message': 'Missing Permissions'})
        view.message.delete.side_effect = forbidden
        with patch('cogs.battle.run_pve_battle', side_effect=self.finish), self.assertLogs('chibibeasts.journal_sessions', level='WARNING'):
            await view.handle(click, 'battle_continue')
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(view.closed)
        self.assertTrue(view.is_finished())
        retired = view.message.edit.call_args.kwargs['view']
        self.assertFalse(any(isinstance(item, (discord.ui.Button, discord.ui.Select))
                             for item in retired.walk_children()))
        self.assertFalse(self.sent[0].closed)
        self.assertEqual((await expeditions.current(42))['status'], 'active')

    async def test_manual_reopen_during_battle_is_replaced_by_one_settled_return(self):
        view, click, trip = await self.ready_battle()
        manual_message = SimpleNamespace(id=150, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        manual = interaction()
        manual.channel = click.channel
        manual.followup.send.return_value = manual_message
        reopened = []

        async def finish(*args, **kwargs):
            latest = await open_journal(manual, page='activities')
            reopened.append(latest)
            self.addCleanup(latest.stop)
            await self.finish(*args, **kwargs)

        with patch('cogs.battle.run_pve_battle', side_effect=finish):
            await view.handle(click, 'battle_continue')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sequence, ['settled', 'result', 'journal'])
        self.assertEqual((self.sent[0].page, self.sent[0].trip['id']), ('adventure', trip['id']))
        self.assertTrue(reopened[0].closed)
        manual_message.delete.assert_awaited_once()
        view.message.delete.assert_awaited_once()

    async def test_failed_new_send_preserves_old_journal_and_saved_progress(self):
        view, click, _ = await self.ready_battle()
        send = click.channel.send.side_effect
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                      {'code': 50013, 'message': 'Missing Permissions'})

        async def blocked(**kwargs):
            if isinstance(kwargs.get('view'), JournalView):
                raise forbidden
            return await send(**kwargs)

        click.channel.send.side_effect = blocked
        with patch('cogs.battle.run_pve_battle', side_effect=self.finish), self.assertLogs('chibibeasts.journal', level='WARNING'):
            await view.handle(click, 'battle_continue')
        self.assertEqual(self.sent, [])
        self.assertFalse(view.closed)
        self.assertFalse(view.is_finished())
        view.message.delete.assert_not_awaited()
        stored = await self.rows('SELECT message_id FROM journal_panels WHERE user_id=42 AND channel_id=999')
        self.assertEqual(stored, [{'message_id': 100}])
        self.assertEqual((await expeditions.current(42))['status'], 'active')
        refresh = component()
        refresh.channel, refresh.message = click.channel, view.message
        refresh.edit_original_response.return_value = view.message
        await view.handle(refresh, 'refresh')
        self.assertEqual(view.state.player['gold'], 4321)
        self.assertEqual(view.trip['status'], 'active')

    async def test_result_send_failure_after_settlement_still_returns_the_journal(self):
        view, click, _ = await self.ready_battle()
        send = click.channel.send.side_effect
        unavailable = discord.HTTPException(SimpleNamespace(status=503, reason='Unavailable'), 'Discord unavailable')

        async def blocked_result(**kwargs):
            if kwargs.get('embed') is not None:
                self.sequence.append('result rejected')
                raise unavailable
            return await send(**kwargs)

        click.channel.send.side_effect = blocked_result
        with patch('cogs.battle.run_pve_battle', side_effect=self.finish):
            await view.handle(click, 'battle_continue')
        self.assertEqual(self.sequence, ['settled', 'result rejected', 'journal'])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].state.player['gold'], 4321)
        self.assertEqual(self.sent[0].trip['status'], 'active')
        view.message.delete.assert_awaited_once()

    async def test_unowned_duplicate_runner_cannot_interrupt_or_publish_a_return(self):
        view, click, trip = await self.ready_battle()
        started, release = asyncio.Event(), asyncio.Event()
        owner = interaction()
        owner.channel = click.channel

        async def finish(*args, **kwargs):
            started.set()
            await release.wait()
            await self.finish(*args, **kwargs)

        with patch('cogs.battle.run_pve_battle', side_effect=finish) as runner:
            task = asyncio.create_task(expeditions.run_battle(owner, trip))
            try:
                await asyncio.wait_for(started.wait(), 3)
                await view.handle(click, 'battle_continue')
                self.assertEqual(self.sent, [])
                self.assertEqual((await expeditions.current(42))['status'], 'battle')
                self.assertFalse(view.closed)
            finally:
                release.set()
                await asyncio.wait_for(task, 6)
        self.assertEqual(runner.await_count, 1)
        self.assertEqual(self.sent, [])
        self.assertEqual((await expeditions.current(42))['status'], 'active')
