"""Battle clicks through Discord.py's real router, including turn redraws."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui.view import ViewStore

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import combat, db as storage
from utils.duels import MoveView, active_fighters, duel


class BattleDispatch(DatabaseCase):
    async def states(self):
        from cogs.battle import build_pve_beast_state
        await self.beast('twine', starter=True)
        row = (await storage.get_player_beasts(42))[0]
        player = combat.build_state(storage.get_beast_data('twine'), row)
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        enemy.update(hp=100000, max_hp=100000, attack=1)
        return [player, enemy]

    def dispatch(self, store, message, custom_id, uid=42):
        click = component(uid=uid)
        click.message = message
        click.data = {'component_type': 2, 'custom_id': custom_id}
        click.acknowledged = asyncio.Event()
        click.response.defer.side_effect = lambda **_: click.acknowledged.set()
        click.response.send_message.side_effect = lambda *_, **__: click.acknowledged.set()
        store.dispatch_view(2, custom_id, click)
        return click

    async def test_rewind_nibble_and_clicks_during_redraw_keep_a_live_handler(self):
        states = await self.states()
        store = ViewStore(None)
        shown, updating, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        payload = {}
        block_next_edit = True
        message = SimpleNamespace(id=700, edit=None)

        def register(view):
            payload['view'] = view
            payload['controls'] = view.to_components()
            store.add_view(view, message.id)
            shown.set()

        async def edit(**kwargs):
            nonlocal block_next_edit
            view = kwargs.get('view')
            if view is None:
                return
            if block_next_edit:
                block_next_edit = False
                updating.set()
                await release.wait()
            register(view)

        message.edit = AsyncMock(side_effect=edit)

        async def send(**kwargs):
            register(kwargs['view'])
            return message

        fake = interaction()
        fake.channel.send.side_effect = send
        task = asyncio.create_task(duel(fake, states, [42, None], 'Wild Slime', 'aggressive'))
        try:
            await asyncio.wait_for(shown.wait(), 1)
            original_view = payload['view']
            rewind_id = payload['controls'][0]['components'][2]['custom_id']
            first = self.dispatch(store, message, rewind_id)
            await asyncio.wait_for(first.acknowledged.wait(), 1)
            await asyncio.wait_for(updating.wait(), 1)

            # The client still shows the previous controls while the HTTP edit
            # is in flight. This click MUST reach a handler and be acknowledged.
            during_redraw = self.dispatch(store, message, rewind_id)
            await asyncio.wait_for(during_redraw.acknowledged.wait(), .25)
            self.assertEqual(states[0]['ultimate_charges'], 1)
            self.assertEqual(states[0].get('focus'), 1.4)
            self.assertIn('next', during_redraw.followup.send.call_args.args[0].lower())

            shown.clear()
            release.set()
            await asyncio.wait_for(shown.wait(), 1)
            self.assertIs(payload['view'], original_view)
            self.assertEqual(payload['controls'][0]['components'][2]['custom_id'], rewind_id)

            # Continue through several real dispatches, including repeated
            # Rewind Nibble, rather than calling a button callback directly.
            for move_index in (0, 2, 0):
                shown.clear()
                control_id = payload['controls'][0]['components'][move_index]['custom_id']
                click = self.dispatch(store, message, control_id)
                await asyncio.wait_for(click.acknowledged.wait(), 1)
                await asyncio.wait_for(shown.wait(), 1)
                self.assertIs(payload['view'], original_view)
                self.assertFalse(task.done())
            self.assertGreater(states[0]['mana'], 0)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(42, combat.active_states)
        self.assertNotIn(42, active_fighters)
        self.assertFalse(store._views)

    async def test_one_panel_routes_different_moves_and_owners_in_pvp(self):
        from cogs.battle import build_pve_beast_state
        states = await self.states()
        states[1] = build_pve_beast_state(storage.get_beast_data('prismite'), 1)
        for state in states:
            state.update(hp=10000, max_hp=10000, attack=1)
        await storage.get_or_create_player(77, 'Other trainer')
        store, panels = ViewStore(None), asyncio.Queue()
        message = SimpleNamespace(id=702, edit=None)

        def register(view):
            store.add_view(view, message.id)
            panels.put_nowait((view, view.current_player_id, [b.label for b in view.children]))

        async def edit(**kwargs):
            if kwargs.get('view'):
                register(kwargs['view'])

        async def send(**kwargs):
            register(kwargs['view'])
            return message

        message.edit = AsyncMock(side_effect=edit)
        fake = interaction()
        fake.channel.send.side_effect = send
        task = asyncio.create_task(duel(fake, states, [42, 77], 'PvP'))
        try:
            first, owner, labels = await asyncio.wait_for(panels.get(), 1)
            self.assertEqual(owner, 42)
            self.assertIn('Tick Bite', labels[0])
            first_click = self.dispatch(store, message, first.children[2].custom_id)
            await asyncio.wait_for(first_click.acknowledged.wait(), 1)
            second, owner, labels = await asyncio.wait_for(panels.get(), 1)
            self.assertIs(second, first)
            self.assertEqual(owner, 77)
            self.assertIn(states[1]['moves'][0], labels[0])

            wrong_owner = self.dispatch(store, message, second.children[0].custom_id)
            await asyncio.wait_for(wrong_owner.acknowledged.wait(), 1)
            wrong_owner.response.send_message.assert_awaited_once()
            self.assertEqual(states[1]['ultimate_charges'], 0)

            second_click = self.dispatch(store, message, second.children[0].custom_id, uid=77)
            await asyncio.wait_for(second_click.acknowledged.wait(), 1)
            third, owner, labels = await asyncio.wait_for(panels.get(), 1)
            self.assertIs(third, first)
            self.assertEqual(owner, 42)
            self.assertIn('Tick Bite', labels[0])
            self.assertEqual([s['ultimate_charges'] for s in states], [1, 1])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(store._views)

    async def test_click_ack_started_in_an_old_turn_cannot_select_the_next_turn(self):
        states = await self.states()
        store, panels = ViewStore(None), asyncio.Queue()
        started, release = asyncio.Event(), asyncio.Event()
        message = SimpleNamespace(id=703, edit=None)

        def register(view):
            store.add_view(view, message.id)
            panels.put_nowait(view)

        async def edit(**kwargs):
            if kwargs.get('view'):
                register(kwargs['view'])

        async def send(**kwargs):
            register(kwargs['view'])
            return message

        async def slow_ack(**kwargs):
            started.set()
            await release.wait()

        message.edit = AsyncMock(side_effect=edit)
        fake = interaction()
        fake.channel.send.side_effect = send
        task = asyncio.create_task(duel(fake, states, [42, None], 'Delayed click'))
        slow = None
        try:
            view = await asyncio.wait_for(panels.get(), 1)
            slow = self.dispatch(store, message, view.children[2].custom_id)
            slow.response.defer.side_effect = slow_ack
            await asyncio.wait_for(started.wait(), 1)
            fast = self.dispatch(store, message, view.children[2].custom_id)
            await asyncio.wait_for(fast.acknowledged.wait(), 1)
            await asyncio.wait_for(panels.get(), 1)
            release.set()
            # The callback's followup is the deterministic completion signal.
            finished = asyncio.Event()
            slow.followup.send.side_effect = lambda *_, **__: finished.set()
            await asyncio.wait_for(finished.wait(), 1)
            self.assertEqual(states[0]['ultimate_charges'], 1)
            self.assertIsNone(view.chosen_move)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_duplicate_click_during_final_cleanup_is_acknowledged(self):
        states = await self.states()
        states[1]['hp'] = 1
        store = ViewStore(None)
        shown, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        message = SimpleNamespace(id=704, edit=None)
        active_view = None

        async def edit(**kwargs):
            if kwargs.get('view') is None:
                closing.set()
                await release.wait()

        async def send(**kwargs):
            nonlocal active_view
            active_view = kwargs['view']
            store.add_view(active_view, message.id)
            shown.set()
            return message

        message.edit = AsyncMock(side_effect=edit)
        fake = interaction()
        fake.channel.send.side_effect = send
        task = asyncio.create_task(duel(fake, states, [42, None], 'Final click'))
        try:
            await asyncio.wait_for(shown.wait(), 1)
            custom_id = active_view.children[0].custom_id
            first = self.dispatch(store, message, custom_id)
            await asyncio.wait_for(first.acknowledged.wait(), 1)
            await asyncio.wait_for(closing.wait(), 1)
            last = self.dispatch(store, message, custom_id)
            await asyncio.wait_for(last.acknowledged.wait(), 1)
            self.assertIn('ended', last.followup.send.call_args.args[0])
            release.set()
            result = await asyncio.wait_for(task, 1)
            self.assertEqual(result[0], 0)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(store._views)

    async def test_real_explore_resumes_legacy_rewind_turns_and_catches_once(self):
        from cogs.hatch import Hatch
        from utils import expeditions
        from utils.journal import JournalView
        await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET attack=1000 WHERE user_id=42')
        store, pending, prompts = ViewStore(None), [], []
        loading = SimpleNamespace(id=705, edit=AsyncMock(), delete=AsyncMock())
        message = SimpleNamespace(id=706, edit=None)
        continued_message = SimpleNamespace(id=707, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        continued, sent = [], []
        choices = iter((2, 2, 0))

        async def choose(view, index):
            await asyncio.sleep(0)
            click = self.dispatch(store, message, view.children[index].custom_id)
            await asyncio.wait_for(click.acknowledged.wait(), 1)

        def register(view):
            store.add_view(view, message.id)
            prompts.append(view)
            pending.append(asyncio.create_task(choose(view, next(choices))))

        async def edit(**kwargs):
            if isinstance(kwargs.get('view'), MoveView):
                register(kwargs['view'])

        async def send(*args, **kwargs):
            sent.append(kwargs)
            if isinstance(kwargs.get('view'), MoveView):
                register(kwargs['view'])
            elif isinstance(kwargs.get('view'), JournalView):
                continued.append(kwargs['view'])
                self.addCleanup(kwargs['view'].stop)
                return continued_message
            return message

        message.edit = AsyncMock(side_effect=edit)
        fake = interaction()
        fake.user.display_avatar = SimpleNamespace(url='https://example.com/avatar.png')
        fake.followup.send.return_value = loading
        fake.channel.send.side_effect = send

        async def journal_choice(journal, choice):
            click = component()
            click.user = fake.user
            click.channel = fake.channel
            click.message = journal.message
            click.data = {'values': [next(o.value for o in journal.path_select.options if o.value.endswith(':' + choice))]}
            click.edit_original_response.return_value = journal.message
            await journal.handle(click, 'path')
            click.response.defer.assert_awaited_once()

        try:
            with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('slime')), patch('utils.expeditions.random.random', return_value=1), patch('utils.expeditions.random.randint', side_effect=lambda a, b: a):
                # Keep testing the real MoveView rewind router for a saved
                # legacy expedition; new journal starts use the v3 cursor.
                saved = await expeditions.begin(42, 'woods', adventure_version=2)
                journal = await asyncio.wait_for(Hatch.explore.callback(Hatch(None), fake, 'woods'), 8)
                self.addCleanup(journal.stop)
                self.assertEqual(journal.trip['id'], saved['id'])
                self.assertEqual(journal.trip['payload']['adventure_version'], 2)
                self.assertEqual((await expeditions.energy(42)).charges, 0)
                await journal_choice(journal, 'tracks')
                await asyncio.wait_for(journal_choice(journal, 'challenge'), 8)
                self.assertEqual([b['beast_id'] for b in await storage.get_player_beasts(42)].count('slime'), 0)
                self.assertEqual(len(continued), 1)
                self.assertTrue(journal.closed)
                self.assertTrue(journal.is_finished())
                loading.delete.assert_awaited_once()
                self.assertIs(sent[-1]['view'], continued[0])
                self.assertIn('Victory', sent[-2]['embed'].title)
                journal = continued[0]
                self.assertIs(journal.message, continued_message)
                await journal_choice(journal, 'camp')
                await journal_choice(journal, 'return')
            self.assertEqual(len(prompts), 3)
            self.assertTrue(all(view is prompts[0] for view in prompts))
            self.assertEqual([b['beast_id'] for b in await storage.get_player_beasts(42)].count('slime'), 1)
            self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
            battles = await self.rows('SELECT winner_id,status FROM battles')
            self.assertEqual(len(battles), 1)
            result = battles[0]
            self.assertEqual((result['winner_id'], result['status']), (42, 'completed'))
        finally:
            results = await asyncio.gather(*pending, return_exceptions=True)
        self.assertFalse(any(isinstance(result, BaseException) for result in results), results)
        self.assertFalse(store._views)
        self.assertNotIn(42, active_fighters)

    async def test_deleted_message_cleanup_does_not_emit_a_failure_traceback(self):
        states = await self.states()
        states[1]['hp'] = 1
        store = ViewStore(None)
        pending = []
        message = SimpleNamespace(id=701, edit=AsyncMock())
        missing = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), {'code': 10008, 'message': 'Unknown Message'})
        message.edit.side_effect = missing

        async def choose(custom_id):
            await asyncio.sleep(0)
            click = self.dispatch(store, message, custom_id)
            await asyncio.wait_for(click.acknowledged.wait(), 1)

        async def send(**kwargs):
            view = kwargs['view']
            store.add_view(view, message.id)
            pending.append(asyncio.create_task(choose(view.children[0].custom_id)))
            return message

        fake = interaction()
        fake.channel.send.side_effect = send
        try:
            with self.assertNoLogs('chibibeasts.battle', level='WARNING'):
                result = await asyncio.wait_for(duel(fake, states, [42, None], 'Deleted panel'), 2)
            self.assertEqual(result[0], 0)
        finally:
            await asyncio.gather(*pending, return_exceptions=True)
        self.assertNotIn(42, combat.active_states)
