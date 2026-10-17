"""Concurrent raid lifecycle, slow Discord I/O and battle invitation regressions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from utils import combat, db as storage
from utils.duels import active_fighters
from utils.raids import active_raids
from tests.test_gameplay import DatabaseCase, interaction
from tests import test_performance_gameplay as fixtures


class RaidRuntime(DatabaseCase):
    async def make_raid(self, **kwargs):
        return await fixtures.RaidTiming.make_raid(self, **kwargs)

    async def test_actions_during_a_slow_edit_publish_the_latest_state(self):
        raid = await self.make_raid()
        raid.refresh_interval = 0
        entered, release = asyncio.Event(), asyncio.Event()

        async def edit(**kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()

        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=edit))
        raid.last_event = 'First action'
        await raid.refresh()
        await asyncio.wait_for(entered.wait(), 1)
        raid.last_event = 'Latest action'
        await raid.refresh()
        release.set()
        await raid._refresh_task
        self.assertEqual(raid.message.edit.await_count, 2)
        self.assertIn('Latest action', raid.message.edit.call_args.kwargs['embed'].description)

    async def test_raid_stays_reserved_until_reward_settlement_finishes(self):
        raid = await self.make_raid(paid=True)
        raid.damage[42] = 50
        raid.message = SimpleNamespace(edit=AsyncMock())
        raid.channel.send.return_value = raid.message
        active_fighters.add(42)
        active_raids[raid.id] = raid
        combat.active_states[42] = raid.state(42)
        combat.active_parties[42] = raid.party[42]
        entered, release = asyncio.Event(), asyncio.Event()
        settle = raid._settle_rewards

        async def slow_settle(server):
            entered.set()
            await release.wait()
            return await settle(server)

        with patch.object(raid, '_settle_rewards', side_effect=slow_settle), patch('utils.raids.BOSS_INTERVAL', .001):
            runner = asyncio.create_task(raid.run())
            await asyncio.sleep(0)
            finishing = asyncio.create_task(raid.finish(True))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.sleep(.02)
                self.assertFalse(runner.done(), 'Raid runner released its party during settlement')
                self.assertIn(42, active_fighters)
                self.assertIs(combat.active_states[42], raid.state(42))
            finally:
                release.set()
                await asyncio.gather(runner, finishing)
        self.assertNotIn(42, active_fighters)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)

    async def test_cancelling_final_click_does_not_cancel_committed_victory(self):
        raid = await self.make_raid()
        raid.damage[42] = 50
        entered, release = asyncio.Event(), asyncio.Event()
        settle = raid._settle_rewards

        async def slow_settle(server):
            entered.set()
            await release.wait()
            return await settle(server)

        with patch.object(raid, '_settle_rewards', side_effect=slow_settle):
            click = asyncio.create_task(raid.finish(True))
            await asyncio.wait_for(entered.wait(), 1)
            click.cancel()
            await asyncio.gather(click, return_exceptions=True)
            release.set()
            await raid.finish(True)
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')

    async def test_defeated_boss_at_deadline_still_settles_as_victory(self):
        raid = await self.make_raid()
        raid.damage[42] = 50
        elapsed = [0]
        raid.clock = lambda: elapsed[0]
        panel = SimpleNamespace(edit=AsyncMock())

        async def send(*args, **kwargs):
            if raid.message is None:
                elapsed[0] = 601
                raid.enemy['hp'] = 0
            return panel

        raid.channel.send.side_effect = send
        await raid.run()
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)

    async def test_simultaneous_knockouts_use_one_transaction(self):
        raid = await self.make_raid(users=(42, 77))
        for party in raid.party.values():
            party[0]['hp'] = 0
        original, connections = storage.connect, []

        def connect(*args, **kwargs):
            connections.append(1)
            return original(*args, **kwargs)

        with patch.object(storage, 'connect', side_effect=connect):
            await raid.record_knockouts()
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM player_beasts WHERE hp=0 AND knocked_out_until IS NOT NULL')), 2)

    async def test_failed_knockout_save_is_retryable(self):
        raid = await self.make_raid()
        state = raid.state(42)
        state['hp'] = 0
        with patch('aiosqlite.Connection.commit', new=AsyncMock(side_effect=OSError('Synthetic write failure'))):
            with self.assertRaises(OSError):
                await raid.record_knockouts()
        self.assertFalse(state.get('ko_recorded', False))
        await raid.record_knockouts()
        self.assertTrue(state['ko_recorded'])
        self.assertEqual((await self.rows('SELECT hp FROM player_beasts'))[0]['hp'], 0)

    async def test_swap_cycles_through_all_three_healthy_companions(self):
        raid = await self.make_raid()
        for species in ('gloop', 'barkley'):
            rid = await self.beast(species)
            row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (rid,)))[0]
            raid.party[42].append(combat.build_state(storage.get_beast_data(species), row))
        elapsed = [100.0]
        raid.clock = lambda: elapsed[0]
        for expected in (1, 2, 0):
            raid.resolve_action(42, 'swap')
            self.assertEqual(raid.slots[42], expected)
            self.assertIs(combat.active_states[42], raid.state(42))
            elapsed[0] += 10
        raid.party[42][1]['hp'] = 0
        raid.resolve_action(42, 'swap')
        self.assertEqual(raid.slots[42], 2)

    async def test_ten_trainers_and_ultimate_resources_fit_in_the_panel(self):
        users = tuple(range(100000000000000000, 100000000000000010))
        raid = await self.make_raid(users=users)
        for uid in users:
            raid.state(uid)['name'] = 'Long companion nickname ' + 'x' * 16
            raid.state(uid)['ultimate_charges'] = 2
        embed = raid.embed()
        text = '\n'.join(field.value for field in embed.fields)
        self.assertTrue(all(f'<@{uid}>' in text for uid in users))
        self.assertIn('2/3', text)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertLessEqual(len(embed), 6000)

    async def test_pattern_response_window_starts_after_warning_is_published(self):
        raid = await self.make_raid()
        elapsed = [100.0]
        raid.clock = lambda: elapsed[0]
        raid.refresh_interval = 0
        entered, release = asyncio.Event(), asyncio.Event()

        async def edit(**kwargs):
            entered.set()
            await release.wait()

        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=edit))
        raid.enemy['hp'] = int(raid.enemy['max_hp'] * .7)
        raid.resolve_action(42, 'guard')
        await raid.refresh()
        await asyncio.wait_for(entered.wait(), 1)
        elapsed[0] = 200
        raid.resolve_boss_turn()
        self.assertTrue(raid.pending, 'Unseen warning resolved before players could react')
        release.set()
        await raid._refresh_task
        self.assertEqual(raid.pattern_started, 200)
        raid.weaves = raid.weave_required()
        elapsed[0] = 205
        raid.resolve_boss_turn()
        self.assertTrue(raid.pending)
        elapsed[0] = 206
        raid.resolve_boss_turn()
        self.assertFalse(raid.pending)

    async def test_settlement_waits_for_already_resolved_damage_progress(self):
        from utils.progress import track_quest_event
        raid = await self.make_raid()
        raid.enemy['hp'] = 1
        progress_entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_progress(uid, event, **kwargs):
            if event == 'raid_damage':
                progress_entered.set()
                await release.wait()
            return await track_quest_event(uid, event, **kwargs)

        with patch('utils.progress.track_quest_event', side_effect=delayed_progress), patch.object(raid, '_settle_rewards', new=AsyncMock(return_value=[])) as settle:
            action = asyncio.create_task(raid.action(42, 'attack'))
            await asyncio.wait_for(progress_entered.wait(), 1)
            finish = asyncio.create_task(raid.finish(True))
            try:
                await asyncio.sleep(.02)
                self.assertEqual(settle.await_count, 0)
            finally:
                release.set()
                await asyncio.gather(action, finish)
            self.assertEqual(settle.await_count, 1)

    async def test_lost_live_panel_refunds_and_releases_the_party(self):
        import discord
        raid = await self.make_raid(paid=True)
        raid.refresh_interval = 0
        error = discord.NotFound(SimpleNamespace(status=404, reason='Missing'), 'Synthetic deleted panel')
        panel = SimpleNamespace(edit=AsyncMock(side_effect=error))
        raid.channel.send.return_value = panel
        active_fighters.add(42)
        active_raids[raid.id] = raid
        combat.active_states[42] = raid.state(42)
        combat.active_parties[42] = raid.party[42]
        runner = asyncio.create_task(raid.run())
        await asyncio.sleep(0)
        await raid.refresh()
        with self.assertRaisesRegex(ValueError, 'panel is unavailable'):
            await asyncio.wait_for(runner, 1)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(raid.id, active_raids)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_runner_cancelled_after_final_damage_still_awards_the_win(self):
        from utils.progress import track_quest_event
        raid = await self.make_raid(paid=True)
        raid.enemy['hp'] = 1
        raid.channel.send.return_value = SimpleNamespace(edit=AsyncMock())
        active_fighters.add(42)
        active_raids[raid.id] = raid
        combat.active_states[42] = raid.state(42)
        combat.active_parties[42] = raid.party[42]
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_progress(uid, event, **kwargs):
            if event == 'raid_damage':
                entered.set()
                await release.wait()
            return await track_quest_event(uid, event, **kwargs)

        with patch('utils.progress.track_quest_event', side_effect=delayed_progress):
            runner = asyncio.create_task(raid.run())
            await asyncio.sleep(0)
            action = asyncio.create_task(raid.action(42, 'attack'))
            await asyncio.wait_for(entered.wait(), 1)
            runner.cancel()
            try:
                await asyncio.sleep(.02)
                self.assertIn(42, active_fighters)
            finally:
                release.set()
                results = await asyncio.gather(runner, action, return_exceptions=True)
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertIsNone(results[1])
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
        self.assertFalse(await self.rows("SELECT 1 FROM player_inventory WHERE user_id=42 AND item_id='epoch_shard'"))
        self.assertEqual((await self.rows('SELECT refunded FROM raid_payments'))[0]['refunded'], 0)
        self.assertNotIn(42, active_fighters)

    async def test_first_action_has_no_cooldown_and_subsequent_clicks_show_remaining_time(self):
        from utils.raids import RaidCooldownError
        raid = await self.make_raid()
        raid.clock = lambda: 0
        raid.resolve_action(42, 'guard')
        with self.assertRaisesRegex(RaidCooldownError, 'ready.*[0-9]+\\.[0-9]s'):
            raid.resolve_action(42, 'attack')

    async def test_stalled_initial_send_is_bounded_and_refunded(self):
        raid = await self.make_raid(paid=True)

        async def blocked(**kwargs):
            await asyncio.Event().wait()

        raid.channel.send.side_effect = blocked
        with patch('utils.raids.RAID_IO_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await raid.run()
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.assertNotIn(raid.id, active_raids)
        self.assertTrue(raid.state(42)['closed'])

    async def test_stalled_final_edit_cannot_hold_a_completed_raid_open(self):
        raid = await self.make_raid()
        raid.damage[42] = 50

        async def blocked(**kwargs):
            await asyncio.Event().wait()

        raid.message = SimpleNamespace(edit=AsyncMock(side_effect=blocked))
        with patch('utils.raids.RAID_IO_TIMEOUT', .01), self.assertLogs('utils.raids', level='WARNING'):
            await asyncio.wait_for(raid.finish(True), 1)
        self.assertEqual((await self.rows('SELECT status FROM raids'))[0]['status'], 'completed')
        self.assertEqual(len(await self.rows('SELECT * FROM raid_rewards')), 1)

    async def test_repeated_actions_record_participation_once_without_losing_damage(self):
        raid = await self.make_raid()
        elapsed = [100.0]
        raid.clock = lambda: elapsed[0]
        for _ in range(3):
            await raid.action(42, 'attack')
            elapsed[0] += 10
        progress = {row['stat_key']: row['value'] for row in await self.rows('SELECT * FROM player_progress WHERE user_id=42')}
        self.assertEqual(progress['raid_attack'], 1)
        self.assertEqual(progress['raid_damage'], raid.damage[42])
        self.assertEqual((await self.rows('SELECT damage_dealt FROM raid_participants'))[0]['damage_dealt'], raid.damage[42])
        self.assertEqual((await self.rows('SELECT current_hp FROM raids'))[0]['current_hp'], raid.enemy['hp'])

    async def test_lobby_deadline_does_not_depend_on_the_idle_view_timeout(self):
        from utils.raids import open_raid, RaidLobby, RaidSession, open_lobbies
        from cogs.guilds import RAID_BOSSES
        click = interaction()
        click.channel.id = 123
        click.followup.send.return_value = SimpleNamespace(edit=AsyncMock())

        async def blocked_wait():
            await asyncio.Event().wait()

        with patch.object(RaidLobby, 'eligible', new=AsyncMock()), patch.object(RaidLobby, 'wait', side_effect=blocked_wait), patch.object(RaidSession, 'start', new=AsyncMock(side_effect=ValueError('Reached raid start'))) as start, patch('utils.raids.RAID_LOBBY_SECONDS', .01):
            with self.assertRaisesRegex(ValueError, 'Reached raid start'):
                await asyncio.wait_for(open_raid(click, RAID_BOSSES['corrupted'][0], summon_item='epoch_shard'), 1)
            self.assertEqual(start.await_count, 1)
        self.assertNotIn(123, open_lobbies)

    async def test_raid_control_error_is_logged_and_reported_after_acknowledgment(self):
        from utils.raids import RaidView
        raid = await self.make_raid()
        view, click = RaidView(raid), interaction()
        await click.response.defer(ephemeral=True)
        with self.assertLogs('utils.raids', level='ERROR') as logged:
            await view.on_error(click, OSError('Synthetic database failure'), view.children[0])
        self.assertIn('raid=123', logged.output[0])
        self.assertIn('could not finish', click.followup.send.call_args.args[0])
        view.stop()


class BattleInvitations(DatabaseCase):
    async def test_accept_acknowledges_before_any_database_lookup(self):
        from cogs.battle import BattleView, pending_battles
        pending_battles[123] = {'challenger_id': 42, 'opponent_id': 77}
        view, click = BattleView(123, 42, 77), interaction(77)

        async def lookup(uid):
            self.assertTrue(click.response.is_done(), 'Battle accept queried SQLite before acknowledging')
            return None

        try:
            with patch('cogs.battle.get_active_beast', side_effect=lookup):
                await view.accept.callback(click)
        finally:
            view.stop()
            pending_battles.pop(123, None)

    async def test_accepting_while_busy_has_feedback_and_closes_invitation(self):
        from cogs.battle import BattleView, pending_battles
        from utils.duels import FighterBusyError
        pending_battles[123] = {'challenger_id': 42, 'opponent_id': 77}
        view, click = BattleView(123, 42, 77), interaction(77)
        try:
            with patch('cogs.battle.get_active_beast', new=AsyncMock(return_value={'id': 1})), patch('cogs.battle.start_battle', new=AsyncMock(side_effect=FighterBusyError('A trainer is already in a battle.'))):
                await view.accept.callback(click)
            self.assertIn('already in a battle', click.followup.send.call_args.args[0])
            self.assertNotIn(123, pending_battles)
            self.assertTrue(view.is_finished())
        finally:
            view.stop()
            pending_battles.pop(123, None)
