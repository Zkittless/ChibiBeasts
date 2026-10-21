"""Exercise actual callbacks across one message, including the return journey."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import adventure_party, db as storage, gear_art, journal_team_art
from utils.beast_workspace import BeastResultView
from utils.roster_view import open_roster


class BeastWorkspaceFlows(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async def portrait(species, armor=''):
            return gear_art.Portrait(gear_art.portrait_filename(species, armor), b'png')
        for module, name, replacement in (
                (gear_art, 'render_portrait', AsyncMock(side_effect=portrait)),
                (journal_team_art, 'render_team', AsyncMock(return_value=None))):
            change = patch.object(module, name, replacement)
            change.start()
            self.patches.append(change)
        self.lead = await self.beast('twine', starter=True)
        self.reserve = await self.beast('gloop')
        await storage.update_player(42, gold=10000, level=30)
        for key in ('first_train', 'first_evolution', 'first_equip'):
            await self.execute('INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(42,?)', (key,))
        from utils.progress import check_achievements
        await check_achievements(42)
        await storage.update_player(42, gold=10000)
        self.clicks, self.views = [], []
        self.message = SimpleNamespace(id=8181, attachments=[], edit=AsyncMock(), delete=AsyncMock())

    async def asyncTearDown(self):
        for view in self.views:
            view.stop()
        if hasattr(self, 'nav'):
            self.nav.active.stop()
        await super().asyncTearDown()

    async def open(self, number=1):
        command = interaction()
        command.followup.send.return_value = self.message
        root = await open_roster(command, beast_number=number, details=True)
        self.nav = root.navigator
        self.views.append(root)
        command.followup.send.assert_awaited_once()
        return root

    def click(self, value=None, uid=42, edit=None):
        click = component(section=value, uid=uid, edit=edit)
        click.message = self.message
        click.edit_original_response.return_value = self.message
        self.clicks.append(click)
        return click

    async def route(self, key):
        view = self.nav.active
        click = self.click(key)
        picker = next(item for item in view.children if isinstance(item, discord.ui.Select)
                      and item.placeholder in {'Companion actions', 'Beast workspace'})
        await picker.callback(click)
        self.views.append(self.nav.active)
        return self.nav.active, click

    async def press(self, label, *, click=None):
        button = next(item for item in self.nav.active.children if getattr(item, 'label', '') == label)
        self.assertFalse(button.disabled, label)
        click = click or self.click()
        await button.callback(click)
        self.views.append(self.nav.active)
        return click

    def assert_one_message(self):
        self.message.delete.assert_not_awaited()
        for click in self.clicks:
            click.followup.send.assert_not_awaited()
            for call in click.edit_original_response.call_args_list:
                view = call.kwargs['view']
                self.assertIs(view.message, self.message)
                self.assertLessEqual(len(view.children), 25)
                self.assertLessEqual(len(view.to_components()), 5)

    async def test_every_action_round_trip_edits_one_message_and_keeps_selection(self):
        root = await self.open(2)
        before = await storage.get_player_beasts(42)
        for route in ('care', 'gear', 'train', 'evolve', 'team'):
            old = self.nav.active
            child, click = await self.route(route)
            self.assertIsNot(child, old)
            self.assertTrue(old.is_finished())
            self.assertEqual(child.workspace_route, route)
            self.assertIs(child.lock, root.lock)
            click.edit_original_response.assert_awaited_once()
            await self.press('Back to beast')
            self.assertEqual(self.nav.active.selected_id, self.reserve)
            self.assertEqual(self.nav.active.mode, 'detail')
        self.assertEqual(await storage.get_player_beasts(42), before)
        self.assertEqual((await storage.get_player(42))['gold'], 10000)
        self.assert_one_message()

    async def test_return_preserves_filters_sort_page_and_expanded_card(self):
        ids = [self.lead] + [await self.beast('twine') for _ in range(11)]
        root = await self.open()
        await root.show(self.click(), query='twine', rarity='common', sort='level')
        await root.next_page.callback(self.click())
        await root.beast_select.callback(self.click(str(ids[-1])))
        await root.details_button.callback(self.click())
        context = (root.picker.query, root.picker.rarity, root.picker.sort, root.picker.page)
        await self.route('gear')
        await self.press('Back to beast')
        fresh = self.nav.active
        self.assertEqual((fresh.picker.query, fresh.picker.rarity, fresh.picker.sort, fresh.picker.page), context)
        self.assertTrue(fresh.expanded)
        self.assertEqual(fresh.selected_id, ids[-1])
        await fresh.back_button.callback(self.click())
        self.assertEqual(fresh.picker.page, 1)
        self.assertEqual(fresh.mode, 'overview')
        self.assert_one_message()

    async def test_care_use_updates_hp_and_back_cannot_repeat_old_item_callback(self):
        await self.execute('UPDATE player_beasts SET hp=10 WHERE id=?', (self.reserve,))
        await storage.add_item(42, 'pixie_dust_potion', 2)
        await self.open(2)
        care, _ = await self.route('care')
        self.assertNotIn(care.companion_select, care.children)
        await care.item_select.callback(self.click('pixie_dust_potion'))
        use = care.use_button.callback
        await self.press('Use 1')
        hp = care.state.target['hp']
        self.assertGreater(hp, 10)
        await self.press('Back to beast')
        self.assertEqual(self.nav.active.selected()['hp'], hp)
        stale = self.click()
        await use(stale)
        stale.edit_original_response.assert_not_awaited()
        self.assertEqual(sum(row['quantity'] for row in await storage.get_inventory(42)
                             if row['item_id'] == 'pixie_dust_potion'), 1)
        self.assert_one_message()

    async def test_gear_edit_returns_with_new_portrait_and_can_switch_to_care(self):
        await self.execute("INSERT INTO player_equipment(user_id,equipment_id) VALUES(42,'gnome_iron_plating')")
        await self.open(2)
        gear, _ = await self.route('gear')
        self.assertNotIn(gear.select, gear.children)
        await self.press('Change armor')
        await self.press('Equip selected')
        self.assertEqual(gear.armor_and_rune()[0], 'gnome_iron_plating')
        await self.press('Care')
        self.assertEqual(self.nav.active.state.selected_id, self.reserve)
        back = await self.press('Back to beast')
        self.assertEqual(self.nav.active.selected_id, self.reserve)
        self.assertEqual(back.edit_original_response.call_args.kwargs['embed'].image.url,
                         'attachment://' + gear_art.portrait_filename('gloop', 'gnome_iron_plating'))
        self.assertEqual((await storage.get_active_beast(42))['id'], self.lead)
        self.assert_one_message()

    async def test_training_result_has_back_and_repeat_preview_without_extra_spend(self):
        await self.open()
        growth, _ = await self.route('train')
        confirm, cost = growth.confirm.callback, growth.cost
        await self.press(growth.confirm.label)
        self.assertIsInstance(self.nav.active, BeastResultView)
        balance = (await storage.get_player(42))['gold']
        self.assertEqual(balance, 10000 - cost)
        stale = self.click()
        await confirm(stale)
        stale.edit_original_response.assert_not_awaited()
        await self.press('Train again')
        self.assertEqual(self.nav.active.workspace_route, 'train')
        self.assertEqual((await storage.get_player(42))['gold'], balance)
        await self.press('Back to beast')
        self.assertEqual(self.nav.active.selected()['train_atk'], 1)
        self.assert_one_message()

    async def test_evolution_returns_to_same_individual_even_outside_saved_filter(self):
        await self.execute('UPDATE player_beasts SET level=15,bond=40,happiness=100 WHERE id=?', (self.lead,))
        root = await self.open()
        await root.show(self.click(), query='twine', rarity='common', sort='rarity')
        await root.beast_select.callback(self.click(str(self.lead)))
        growth, _ = await self.route('evolve')
        await self.press(growth.confirm.label)
        self.assertIsInstance(self.nav.active, BeastResultView)
        await self.press('Back to beast')
        root = self.nav.active
        self.assertEqual((root.selected_id, root.selected()['beast_id'], root.mode), (self.lead, 'timestitch', 'detail'))
        self.assertEqual((root.picker.query, root.picker.rarity, root.picker.sort), ('twine', 'common', 'rarity'))
        self.assertTrue(root.prev_btn.disabled and root.next_btn.disabled)
        await root.refresh_button.callback(self.click())
        self.assertEqual(root.selected_id, self.lead)
        await root.back_button.callback(self.click())
        self.assertEqual(root.mode, 'overview')
        self.assertFalse(root.matches())
        self.assert_one_message()

    async def test_rename_refreshes_original_card_and_keeps_filtered_individual(self):
        await self.execute("UPDATE player_beasts SET nickname='Old name' WHERE id=?", (self.reserve,))
        root = await self.open(2)
        await root.show(self.click(), query='Old name')
        await root.beast_select.callback(self.click(str(self.reserve)))
        click = self.click('rename')
        click.response.send_modal = AsyncMock()
        await root.act(click, dict(root.selected()))
        modal = click.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        modal.name_input._value = 'New name'
        result = self.click()
        await modal.on_submit(result)
        self.assertEqual(self.nav.active.selected()['nickname'], 'New name')
        self.assertEqual(self.nav.active.selected_id, self.reserve)
        self.assertEqual(self.nav.active.picker.query, 'Old name')
        self.assertIn('Nickname saved', result.edit_original_response.call_args.kwargs['embed'].description)
        self.assert_one_message()

    async def test_team_assignments_stay_separate_and_return_without_retiring_message(self):
        await self.open(2)
        team, _ = await self.route('team')
        await self.press('Assign reserve 1')
        self.assertEqual(await adventure_party.assignments(42), {1: self.reserve})
        await self.press('Raids')
        await self.press('Assign front')
        self.assertEqual((await storage.get_player_beasts(42))[1]['raid_slot'], 1)
        await self.press('Remove from slot')
        self.assertIsNone((await storage.get_player_beasts(42))[1]['raid_slot'])
        self.assertEqual(await adventure_party.assignments(42), {1: self.reserve})
        await self.press('Exploration')
        await self.press('Back to beast')
        self.assertEqual(self.nav.active.selected_id, self.reserve)
        self.assert_one_message()

    async def test_transferred_target_never_falls_back_to_another_beast(self):
        await self.open(2)
        await self.route('gear')
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (self.reserve,))
        view, click = await self.route('care')
        self.assertIsInstance(view, BeastResultView)
        self.assertIn('left your collection', click.edit_original_response.call_args.kwargs['embed'].description)
        await self.press('Back to beast')
        self.assertEqual(self.nav.active.mode, 'overview')
        self.assertIsNone(self.nav.active.selected_id)
        self.assert_one_message()

    async def test_stranger_and_retired_back_and_timeout_cannot_replace_current_view(self):
        root = await self.open()
        care, _ = await self.route('care')
        back = next(item.callback for item in care.children if getattr(item, 'label', '') == 'Back to beast')
        stranger = self.click(uid=43)
        await back(stranger)
        stranger.edit_original_response.assert_not_awaited()
        self.assertIs(self.nav.active, care)
        await root.on_timeout()
        self.message.edit.assert_not_awaited()
        await self.press('Back to beast')
        latest = self.nav.active
        stale = self.click()
        await back(stale)
        stale.edit_original_response.assert_not_awaited()
        await care.on_timeout()
        self.message.edit.assert_not_awaited()
        self.assertIs(self.nav.active, latest)
        self.assert_one_message()

    async def test_back_waits_for_item_transaction_then_refreshes_without_stale_overwrite(self):
        await self.execute('UPDATE player_beasts SET hp=10 WHERE id=?', (self.lead,))
        await storage.add_item(42, 'pixie_dust_potion', 2)
        await self.open()
        care, _ = await self.route('care')
        await care.item_select.callback(self.click('pixie_dust_potion'))
        started, release = asyncio.Event(), asyncio.Event()
        from utils.items import use_item
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return await use_item(*args, **kwargs)
        back = next(item.callback for item in care.children if getattr(item, 'label', '') == 'Back to beast')
        with patch('utils.care_view.use_item', side_effect=delayed):
            use_task = asyncio.create_task(care.use_button.callback(self.click()))
            await asyncio.wait_for(started.wait(), 3)
            back_click = self.click()
            back_task = asyncio.create_task(back(back_click))
            try:
                await asyncio.sleep(0)
                back_click.response.defer.assert_awaited_once()
                self.assertIs(self.nav.active, care)
                release.set()
                await asyncio.wait_for(asyncio.gather(use_task, back_task), 5)
            finally:
                release.set()
                await asyncio.gather(use_task, back_task, return_exceptions=True)
        self.assertEqual(self.nav.active.workspace_route, 'beast')
        self.assertGreater(self.nav.active.selected()['hp'], 10)
        self.assertEqual(sum(row['quantity'] for row in await storage.get_inventory(42)
                             if row['item_id'] == 'pixie_dust_potion'), 1)
        self.assert_one_message()

    async def test_failed_navigation_keeps_original_card_live(self):
        root = await self.open()
        click = self.click('gear', edit=RuntimeError('connection failed'))
        with self.assertRaisesRegex(RuntimeError, 'connection failed'):
            await root.act(click, dict(root.selected()))
        self.assertIs(self.nav.active, root)
        self.assertFalse(root.is_finished())
        await self.route('care')
        await self.press('Back to beast')
        self.assert_one_message()
