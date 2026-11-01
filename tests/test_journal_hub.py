"""Exercise the shipped Discord controls and durable one-message transport."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncio

import discord

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, journal_team_art
from utils.journal import open_journal
from utils.journal_hub import HubFrame, text_pages


class JournalHubFlows(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.lead = await self.beast('twine', starter=True)
        self.patient = await self.beast('slime')
        await self.execute('UPDATE player_beasts SET hp=1 WHERE id=?', (self.patient,))
        self.message = SimpleNamespace(id=515, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        self.message.edit.return_value = self.message
        self.art = patch.object(journal_team_art, 'render_team', AsyncMock(return_value=None))
        self.art.start()
        self.addCleanup(self.art.stop)
        self.clicks = []

    async def open(self, **kwargs):
        click = interaction()
        click.followup.send.return_value = self.message
        self.root = await open_journal(click, **kwargs)
        self.hub = self.root.hub
        self.addCleanup(self.root.stop)
        self.assertIsInstance(click.followup.send.call_args.kwargs['view'], HubFrame)
        click.followup.send.assert_awaited_once()
        return self.hub.frame

    def click(self, value=None, uid=42):
        click = component(uid=uid)
        click.message = self.message
        click.data = {'values': [value]} if value is not None else {}
        click.response.send_modal = AsyncMock()
        click.edit_original_response.return_value = self.message
        self.clicks.append(click)
        return click

    async def press(self, label, *, uid=42):
        item = next(i for i in self.hub.frame.walk_children() if isinstance(i, discord.ui.Button) and i.label == label)
        click = self.click(uid=uid)
        await item.callback(click)
        return click

    async def pick(self, placeholder, value):
        item = next(i for i in self.hub.frame.walk_children() if isinstance(i, discord.ui.Select) and i.placeholder == placeholder)
        click = self.click(value)
        await item.callback(click)
        return click

    def assert_one_message(self):
        self.message.delete.assert_not_awaited()
        for click in self.clicks:
            click.followup.send.assert_not_awaited()
            for call in click.edit_original_response.call_args_list:
                self.assertEqual(call.kwargs.get('embeds'), [])
                frame = call.kwargs['view']
                self.assertIsInstance(frame, HubFrame)
                self.assertLessEqual(frame.total_children_count, 40)
                self.assertLessEqual(frame.content_length(), 4000)

    async def test_sections_nursery_workshop_and_back_reuse_message(self):
        await self.open()
        await self.press('Camp')
        await self.pick('Choose a place', 'nursery')
        await self.press('Back to Camp')
        await self.pick('Choose a place', 'workshop')
        await self.press('Back to Camp')
        await self.press('Adventure')
        self.assertEqual(self.root.page, 'adventure')
        self.assert_one_message()

    async def test_roster_gear_back_keeps_selected_beast(self):
        await self.open()
        await self.press('Beasts')
        child = self.hub.frame.source
        selected = self.patient
        await child.show(self.hub_click(), target_id=selected, mode='detail')
        await self.pick('Companion actions', 'gear')
        await self.press('Back to beast')
        self.assertEqual(self.hub.frame.source.selected_id, selected)
        await self.press('Back to beasts')
        self.assertEqual(self.hub.frame.source.mode, 'overview')
        self.assert_one_message()

    def hub_click(self):
        from utils.journal_hub import HubInteraction
        return HubInteraction(self.click(), self.hub)

    async def test_stale_frame_and_other_trainer_cannot_navigate(self):
        await self.open()
        old = self.hub.frame
        button = next(i for i in old.walk_children() if getattr(i, 'label', '') == 'Camp')
        await self.press('Camp')
        wrong = self.click(uid=99)
        await button.callback(wrong)
        wrong.response.send_message.assert_awaited_once()
        stale = self.click()
        await button.callback(stale)
        stale.response.send_message.assert_awaited_once()
        self.assertEqual(self.root.page, 'camp')

    async def test_native_text_is_paginated_without_loss(self):
        original = ['a' * 5000, 'Final sentence.']
        pages = text_pages(original)
        self.assertTrue(all(len(page) <= 2800 for page in pages))
        self.assertEqual(''.join(pages).replace('\n', ''), ''.join(original))

    async def test_sdk_dispatch_survives_repeated_edits_and_old_frame_cleanup(self):
        from discord.ui.view import ViewStore
        await self.open()
        store = ViewStore(SimpleNamespace())
        store.add_view(self.hub.frame, self.message.id)
        self.message._state = SimpleNamespace(store_view=store.add_view)
        old_ids = set()
        for label in ('Camp', 'Beasts', 'Adventure', 'Camp', 'Adventure'):
            frame = self.hub.frame
            ids = {i.custom_id for i in frame.walk_children() if getattr(i, 'custom_id', None)}
            self.assertFalse(old_ids & ids)
            old_ids |= ids
            item = next(i for i in frame.walk_children() if getattr(i, 'label', None) == label)
            click = self.click()
            click.type = discord.InteractionType.component
            click.data = {'custom_id': item.custom_id, 'component_type': item.type.value}
            async def edit(**kwargs):
                store.add_view(kwargs['view'], self.message.id)
                return self.message
            click.edit_original_response.side_effect = edit
            tasks = []
            with patch.object(store, 'add_task', side_effect=tasks.append):
                store.dispatch_view(item.type.value, item.custom_id, click)
                self.assertTrue(tasks, 'The new controls were removed from Discord’s dispatcher')
                await asyncio.wait_for(asyncio.gather(*tasks), 15)
            self.assertIsNot(self.hub.frame, frame)
            self.assertTrue(store.is_message_tracked(self.message.id))
        self.assert_one_message()

    async def test_care_filters_patients_and_old_use_cannot_consume_twice(self):
        await storage.add_item(42, 'pixie_dust_potion', 2)
        await self.open(page='care')
        source = self.hub.frame.source
        patients = source.companion_select.options
        self.assertEqual([p.value for p in patients], [str(self.patient)])
        await self.pick('Choose an owned care item', 'pixie_dust_potion')
        old = next(i for i in self.hub.frame.walk_children() if getattr(i, 'label', None) == 'Use 1')
        await self.press('Use 1')
        stock = next(row for row in await storage.get_inventory(42) if row['item_id'] == 'pixie_dust_potion')
        self.assertEqual(stock['quantity'], 1)
        await old.callback(self.click())
        stock = next(row for row in await storage.get_inventory(42) if row['item_id'] == 'pixie_dust_potion')
        self.assertEqual(stock['quantity'], 1)
        await self.press('Back to Beasts')
        self.assertEqual(self.hub.frame.source.mode, 'overview')

    async def test_rename_modal_keeps_beast_and_returns_in_same_message(self):
        await self.open(page='beasts')
        await self.pick('Open a companion’s details', str(self.patient))
        opened = await self.pick('Companion actions', 'rename')
        modal = opened.response.send_modal.call_args.args[0]
        modal.name_input._value = 'Spooky friend'
        await modal.on_submit(self.click())
        self.assertEqual(self.hub.frame.source.selected_id, self.patient)
        self.assertEqual(self.hub.frame.source.selected()['nickname'], 'Spooky friend')
        await self.press('Back to beasts')
        self.assert_one_message()

    async def test_modal_from_previous_screen_cannot_rename_a_beast(self):
        await self.open(page='beasts')
        await self.pick('Open a companion’s details', str(self.patient))
        opened = await self.pick('Companion actions', 'rename')
        modal = opened.response.send_modal.call_args.args[0]
        modal.name_input._value = 'Stale name'
        await self.press('Camp')
        await modal.on_submit(self.click())
        row = next(row for row in await storage.get_player_beasts(42) if row['id'] == self.patient)
        self.assertNotEqual(row['nickname'], 'Stale name')
        self.assertEqual(self.root.page, 'camp')

    async def test_edit_preserves_omitted_text_and_attachments(self):
        await self.open()
        source = discord.ui.View()
        source.add_item(discord.ui.Button(label='A control'))
        embed = discord.Embed(title='Full page', description='Preserve this text')
        attachment = SimpleNamespace(filename='portrait.png')
        self.message.attachments = [attachment]
        await self.hub.present(self.click(), view=source, embed=embed, attachments=[attachment])
        await self.hub_click().response.edit_message(view=source)
        self.assertEqual(self.hub.frame.embeds, [embed])
        self.assertEqual(self.clicks[-1].edit_original_response.call_args.kwargs['attachments'], [attachment])
        self.assert_one_message()

    async def test_failed_edit_keeps_previous_frame_as_recovery_point(self):
        await self.open()
        old = self.hub.frame
        click = self.click()
        click.edit_original_response.side_effect = discord.HTTPException(
            SimpleNamespace(status=503, reason='Unavailable'), 'Unavailable')
        with self.assertRaises(discord.HTTPException):
            await self.hub.present(click, content='New page')
        self.assertIs(self.hub.frame, old)
        self.assertFalse(old.is_finished())
        self.assertFalse(self.hub.busy)
        await self.press('Camp')
        self.assertEqual(self.root.page, 'camp')

    async def test_trial_handoff_and_return_keep_the_message_and_resume_button(self):
        from utils import battle_art, trial_panel, trials
        from discord.ui.view import ViewStore
        await self.open()
        store = ViewStore(SimpleNamespace())
        store.add_view(self.hub.frame, self.message.id)
        self.message._state = SimpleNamespace(store_view=store.add_view)
        async def edit(**kwargs):
            store.add_view(kwargs['view'], self.message.id)
            return self.message
        self.message.edit.side_effect = edit
        with patch.object(battle_art.BattlePresentation, 'art', AsyncMock(return_value=None)):
            await self.pick('More travel options', 'trials')
            trial = trial_panel._panels[trial_panel._identity(42)]
            self.addCleanup(trial.stop)
            self.addCleanup(trial.cancel_timer)
            self.addCleanup(trial_panel._panels.pop, trial.key, None)
            self.assertTrue(self.hub.closed)
            self.assertTrue(store.is_message_tracked(self.message.id))
            self.assertEqual(trial.message.id, self.message.id)
            begin = next(i for i in trial.walk_children() if getattr(i, 'label', None) == 'Begin tower')
            await begin.callback(self.click())
            run_id = trial.run['id']
            back = next(i for i in trial.walk_children() if getattr(i, 'label', None) == 'Back to journal')
            click = self.click()
            click.edit_original_response.side_effect = edit
            await back.callback(click)
            frame = click.edit_original_response.call_args.kwargs['view']
            self.assertIsInstance(frame, HubFrame)
            self.addCleanup(frame.hub.root.stop)
            self.assertEqual(frame.hub.message.id, self.message.id)
            self.assertTrue(store.is_message_tracked(self.message.id))
            self.assertTrue(any(getattr(i, 'label', None) == 'Resume trial' for i in frame.walk_children()))
            self.assertEqual((await trials.current(42))['id'], run_id)

    async def test_nursery_and_workshop_largest_pages_fit_with_section_navigation(self):
        from utils import crafting, nursery
        from utils.nursery_view import NurseryView
        from utils.workshop import WorkshopView
        await storage.update_player(42, level=50, gold=100000)
        for _ in range(26):
            await self.beast('slime')
        await self.open()
        self.hub.section, self.hub.route = 'camp', 'nursery'
        state = await nursery.snapshot(42)
        for egg in state['eggs']:
            for page in ('eggs', 'pool', 'incubations', 'research', 'recent'):
                source = NurseryView(42, state, egg_id=egg['id'], page=page)
                frame = HubFrame(self.hub, source)
                frame.stop()
                source.stop()
        state = await crafting.workshop_state(42)
        self.hub.route = 'workshop'
        for recipe_id in crafting.recipes():
            source = WorkshopView(42, state, selected=recipe_id)
            source.using = True
            source.drawer = 'materials'
            source.render()
            frame = HubFrame(self.hub, source)
            frame.stop()
            source.stop()

    async def test_large_rarity_pagination_keeps_every_companion_reachable(self):
        commons = [self.lead, self.patient] + [await self.beast('slime') for _ in range(12)]
        await self.open(page='beasts')
        seen = set()
        while True:
            source = self.hub.frame.source
            seen.update(int(option.value) for option in source.beast_select.options)
            next_button = next((i for i in self.hub.frame.walk_children()
                                if getattr(i, 'label', '') == source.next_page.label), None)
            if next_button is None or next_button.disabled:
                break
            await next_button.callback(self.click())
        self.assertEqual(seen, set(commons))
        await self.pick('Open a companion’s details', str(commons[-1]))
        await self.pick('Companion actions', 'gear')
        await self.press('Back to beast')
        await self.press('Back to beasts')
        self.assertEqual(self.hub.frame.source.picker.page, 3)
        self.assertEqual(self.hub.frame.source.picker.rarity, 'common')
        self.assert_one_message()

    async def test_registered_daily_handler_claims_once_and_returns_to_camp(self):
        from discord.ext import commands
        from utils.journal_commands import JournalCommandTree
        async with commands.Bot(command_prefix='!', intents=discord.Intents.none(), tree_cls=JournalCommandTree) as bot:
            await bot.load_extension('cogs.utilities')
            self.assertIsNone(bot.tree.get_command('daily'))
            await self.open(bot=bot)
            await self.press('Camp')
            before = (await storage.get_player(42))['gold']
            old = next(i for i in self.hub.frame.walk_children() if getattr(i, 'label', '') == 'Claim daily')
            await self.press('Claim daily')
            after = (await storage.get_player(42))['gold']
            self.assertGreater(after, before)
            await self.press('Back to Camp')
            self.assertFalse(any(getattr(i, 'label', '') == 'Claim daily' for i in self.hub.frame.walk_children()))
            await old.callback(self.click())
            self.assertEqual((await storage.get_player(42))['gold'], after)

    async def test_roster_uses_each_individuals_equipped_portrait_and_text_fallback(self):
        from utils import gear_art
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (self.lead,))
        async def portrait(species, armor=''):
            return gear_art.Portrait(gear_art.portrait_filename(species, armor), b'png')
        with patch.object(gear_art, 'render_portrait', side_effect=portrait) as render:
            await self.open(page='beasts')
        self.assertIn(('twine', 'gnome_iron_plating'), [call.args for call in render.await_args_list])
        self.assertEqual(sum(isinstance(i, discord.ui.Thumbnail) for i in self.hub.frame.walk_children()), 2)
        with patch.object(gear_art, 'render_portrait', AsyncMock(return_value=None)):
            await self.press('Beasts')
        self.assertFalse(any(isinstance(i, discord.ui.Thumbnail) for i in self.hub.frame.walk_children()))
        await self.pick('Open a companion’s details', str(self.patient))
        self.assertEqual(self.hub.frame.source.selected_id, self.patient)

    async def test_shared_raid_launch_gets_the_original_interaction(self):
        callback = AsyncMock()
        command = SimpleNamespace(binding=None, callback=callback)
        bot = SimpleNamespace(tree=SimpleNamespace(get_command=lambda name: command))
        await self.open(bot=bot)
        click = self.hub_click()
        await self.root.launch(click, 'raid')
        callback.assert_awaited_once_with(click.original)

    async def test_pinned_goals_open_current_workshop_and_beast_routes(self):
        await self.open()
        await self.hub.navigate(self.click(), 'goals')
        await self.pick('Choose a goal to pin', 'recipe:gnome_iron_plating')
        self.assertEqual(self.root.page, 'goals')
        await self.press('Open recipe')
        self.assertEqual(self.hub.frame.source.selected, 'gnome_iron_plating')
        await self.hub.navigate(self.click(), 'goals')
        await self.pick('Choose a goal category', 'awakening')
        await self.pick('Choose a goal to pin', 'awakening:' + str(self.lead))
        await self.press('View companion')
        self.assertEqual(self.hub.frame.source.selected_id, self.lead)
        self.assertEqual(self.hub.frame.source.mode, 'detail')
        self.assert_one_message()
