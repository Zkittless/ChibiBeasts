"""Discord input ordering and spar settlement, without a live bot or player DB."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from utils import combat, db as storage
from utils.duels import MoveView, active_fighters, duel


def component(*, section=None, edit=None, uid=42):
    click = interaction(uid)
    click.id = 123456
    click.data = {'values': [section]} if section else {}
    click.response.is_done = lambda: bool(
        click.response.defer.await_count or click.response.edit_message.await_count
    )
    click.response.edit_message = AsyncMock(side_effect=edit)
    click.edit_original_response = AsyncMock(side_effect=edit)
    return click


class ShopInteractions(DatabaseCase):
    async def open_shop(self):
        from cogs.profile import Shop
        command = interaction()
        message = SimpleNamespace(id=123, edit=AsyncMock())
        command.followup.send.return_value = message
        command.channel.get_partial_message = lambda _: message
        await Shop.shop.callback(Shop(None), command)
        view = command.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        return view, message

    async def test_fast_tab_switches_cannot_publish_old_controls_after_new_ones(self):
        view, _ = await self.open_shop()
        first_started, release = asyncio.Event(), asyncio.Event()
        visible = {}

        async def edit_first(**kwargs):
            payload = (kwargs['embed'].title, kwargs['view'].to_components())
            first_started.set()
            await release.wait()
            visible['payload'] = payload

        async def edit_second(**kwargs):
            visible['payload'] = (kwargs['embed'].title, kwargs['view'].to_components())

        # Both clicks can be queued from the same menu while Discord is still
        # returning the first HTTP request. Capture its actual serialized UI.
        select = view.children[0]
        first = component(section='incubation', edit=edit_first)
        second = component(section='potions', edit=edit_second)
        tasks = []
        try:
            tasks.append(asyncio.create_task(select.callback(first)))
            await asyncio.wait_for(first_started.wait(), 1)
            tasks.append(asyncio.create_task(select.callback(second)))
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIn('Revive & Heal', visible['payload'][0])
        self.assertEqual(visible['payload'][1], view.to_components())
        first.response.defer.assert_awaited_once()
        second.response.defer.assert_awaited_once()
        self.assertIs(view.children[0], select)

    async def test_every_tab_and_page_remains_usable_across_repeated_switches(self):
        view, _ = await self.open_shop()
        select = view.children[0]
        for _ in range(2):
            for section in ('incubation', 'potions', 'consumables', 'items', 'shards', 'instant'):
                click = component(section=section)
                await select.callback(click)
                self.assertEqual(view.section, section)
                self.assertIs(view.children[0], select)
                while True:
                    next_button = next((b for b in view.children if getattr(b, 'label', '') == '▶'), None)
                    if next_button is None or next_button.disabled:
                        break
                    click = component()
                    await next_button.callback(click)
                    click.response.defer.assert_awaited_once()
                    self.assertLessEqual(len(view.children), 25)
                    self.assertTrue(view.to_components())

    async def test_discord_dispatcher_keeps_routing_the_menu_after_tab_edits(self):
        from discord.ui.view import ViewStore
        view, message = await self.open_shop()
        store = ViewStore(None)
        store.add_view(view, message.id)
        select_id = view.children[0].custom_id
        for section in ('incubation', 'potions', 'consumables', 'shards', 'instant'):
            edited = asyncio.Event()

            async def edit(**kwargs):
                store.add_view(kwargs['view'], message.id)
                edited.set()

            click = component(section=section, edit=edit)
            click.message = message
            store.dispatch_view(3, select_id, click)
            await asyncio.wait_for(edited.wait(), 1)
            self.assertEqual(view.section, section)
            click.response.defer.assert_awaited_once()

    async def test_purchase_refreshes_balance_and_shard_week_limits(self):
        await storage.update_player(42, level=25, celestial_shards=100)
        view, _ = await self.open_shop()
        with patch('utils.progress.track_quest_event', AsyncMock(return_value=[])):
            await view.children[1].callback(component())
        self.assertIn('300g', view._current_embed().description)
        await view.children[0].callback(component(section='shards'))
        button = next(b for b in view.children if getattr(b, 'label', '').startswith('🧭'))
        await button.callback(component())
        self.assertIn('75', view._current_embed().description)
        self.assertEqual(view.week_data.get('divine_compass'), 1)

    async def test_expired_shop_visibly_closes_and_callback_errors_get_feedback(self):
        view, message = await self.open_shop()
        click = component()
        with self.assertLogs('chibibeasts.shop', level='ERROR'):
            await view.on_error(click, RuntimeError('Synthetic shop failure'), view.children[0])
        click.response.send_message.assert_awaited_once()
        view.stop()
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        self.assertIn('/shop', message.edit.call_args.kwargs['content'])


class SparInteractions(DatabaseCase):
    async def states(self):
        from cogs.battle import build_pve_beast_state
        await self.beast(starter=True)
        row = (await storage.get_player_beasts(42))[0]
        player = combat.build_state(storage.get_beast_data('prismite'), row)
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        enemy.update(hp=100000, max_hp=100000, attack=1)
        return [player, enemy]

    async def test_delayed_old_move_callback_cannot_replace_the_next_turn(self):
        states = await self.states()
        first_ready, next_ready, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        views, visible = [], {}

        class ObservedView(MoveView):
            def __init__(self, *args):
                super().__init__(*args)
                views.append(self)

        async def display(**kwargs):
            visible['view'] = kwargs.get('view')
            if views and kwargs.get('view') is views[0] and views[0].turn_id > 1:
                next_ready.set()

        message = SimpleNamespace(edit=AsyncMock(side_effect=display))

        async def send(**kwargs):
            await display(**kwargs)
            first_ready.set()
            return message

        async def delayed_old_edit(**kwargs):
            await release.wait()
            await display(**kwargs)

        fake = interaction()
        fake.channel.send = AsyncMock(side_effect=send)
        task = asyncio.create_task(duel(fake, states, [42, None], 'Ordering test', view_class=ObservedView))
        callback = None
        try:
            await asyncio.wait_for(first_ready.wait(), 1)
            click = component(edit=delayed_old_edit)
            callback = asyncio.create_task(views[0].children[0].callback(click))
            await asyncio.wait_for(next_ready.wait(), 1)
            release.set()
            await callback
            self.assertIs(visible['view'], views[0])
            self.assertEqual(len(views), 1)
            click.response.defer.assert_awaited_once()
        finally:
            release.set()
            if callback:
                await callback
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_turn_deadline_releases_battle_even_if_view_timer_is_not_running(self):
        states = await self.states()

        class UnregisteredView(MoveView):
            def __init__(self, *args):
                super().__init__(*args)
                self.timeout = .01

        fake = interaction()
        fake.channel.send.return_value = SimpleNamespace(edit=AsyncMock())
        result = await asyncio.wait_for(duel(fake, states, [42, None], 'No dispatcher', view_class=UnregisteredView), 1)
        self.assertEqual((result[0], result[3]), (1, True))
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_duplicate_and_stale_move_inputs_are_acknowledged_once_per_click(self):
        state = (await self.states())[0]
        view = MoveView(state['moves'], state['ultimate'], 42, state)
        view.open_turn()
        clicks = [component(), component()]
        await asyncio.gather(*(view.children[i].callback(click) for i, click in enumerate(clicks)))
        self.assertEqual(view.chosen_move, (state['moves'][0], False))
        for click in clicks:
            click.response.defer.assert_awaited_once()
            click.response.edit_message.assert_not_awaited()
        self.assertEqual(sum(click.followup.send.await_count for click in clicks), 1)
        self.assertFalse(view.is_finished())
        view.stop()

    async def test_stalled_discord_send_releases_reservation(self):
        states = await self.states()

        async def blocked(**kwargs):
            await asyncio.Event().wait()

        fake = interaction()
        fake.channel.send.side_effect = blocked
        with patch('utils.duels.DUEL_IO_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(duel(fake, states, [42, None], 'Send failure'), 1)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_callback_error_stops_duel_and_reports_the_original_failure(self):
        states = await self.states()
        ready = asyncio.Event()
        views = []

        async def send(**kwargs):
            views.append(kwargs['view'])
            ready.set()
            return SimpleNamespace(edit=AsyncMock())

        fake = interaction()
        fake.channel.send.side_effect = send
        task = asyncio.create_task(duel(fake, states, [42, None], 'Callback failure'))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            click = component()
            with self.assertLogs('chibibeasts.battle', level='ERROR'):
                await views[0].on_error(click, RuntimeError('Synthetic input failure'), views[0].children[0])
            with self.assertRaisesRegex(RuntimeError, 'Synthetic input failure'):
                await asyncio.wait_for(task, 1)
            click.response.send_message.assert_awaited_once()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(42, active_fighters)
        self.assertNotIn(42, combat.active_states)

    async def test_cancelled_unpaid_spar_releases_its_daily_attempt(self):
        from cogs.battle import Battle
        await self.beast(starter=True)
        ready = asyncio.Event()

        async def blocked(*args, **kwargs):
            ready.set()
            await asyncio.Event().wait()

        with patch('cogs.battle.run_pve_battle', side_effect=blocked):
            task = asyncio.create_task(Battle.sparr.callback(Battle(None), interaction(), 'maren'))
            try:
                await asyncio.wait_for(ready.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(await self.rows("SELECT * FROM daily_quests WHERE quest_id='sparr_maren'"))
        self.assertNotIn(42, active_fighters)

    async def test_error_after_reward_commit_cannot_pay_the_same_spar_again(self):
        from cogs.battle import Battle
        await self.beast(starter=True)

        async def win(*args, **kwargs):
            await kwargs['on_win'](discord.Embed(), {}, {}, False)

        with patch('cogs.battle.run_pve_battle', side_effect=win) as run, patch('cogs.battle.award_player_exp', side_effect=RuntimeError('Synthetic notification failure')):
            with self.assertRaises(RuntimeError):
                await Battle.sparr.callback(Battle(None), interaction(), 'maren')
            paid = await storage.get_player(42)
            await Battle.sparr.callback(Battle(None), interaction(), 'maren')
            self.assertEqual(run.await_count, 1)
        self.assertEqual((await storage.get_player(42))['gold'], paid['gold'])
        self.assertEqual((await storage.get_player(42))['celestial_shards'], paid['celestial_shards'])
        self.assertEqual((await self.rows("SELECT progress FROM daily_quests WHERE quest_id='sparr_maren'"))[0]['progress'], 2)
        self.assertNotIn(42, active_fighters)

    async def test_spar_win_advances_an_existing_relationship_and_preserves_other_npcs(self):
        from cogs.battle import Battle
        from utils.story import get_quest_state, save_quest_state
        await self.beast(starter=True)
        qs = await get_quest_state(42)
        qs['npc_relationships'] = {'maren': 'known', 'sable': 'trusted'}
        await save_quest_state(qs)
        before = await storage.get_player(42)

        async def win(*args, **kwargs):
            await kwargs['on_win'](discord.Embed(), {}, {}, False)

        with patch('cogs.battle.run_pve_battle', side_effect=win), patch('cogs.battle.check_achievements', AsyncMock(return_value=[])):
            await Battle.sparr.callback(Battle(None), interaction(), 'maren')
        self.assertEqual((await get_quest_state(42))['npc_relationships'], {'maren': 'trusted', 'sable': 'trusted'})
        after = await storage.get_player(42)
        self.assertGreater(after['gold'], before['gold'])
        self.assertEqual(after['celestial_shards'], before['celestial_shards'] + 1)
        self.assertNotIn(42, active_fighters)
