"""Owned roster browsing uses fresh ownership and preserves search context."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.profile import Profile
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, gear_art, owned_roster
from utils.roster_view import SearchModal
from utils.transfers import complete_trade


class RosterBrowser(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        art = patch.object(gear_art, 'render_portrait', new=AsyncMock(return_value=None))
        self.render_portrait = art.start()
        self.patches.append(art)

    async def open_roster(self, *, number=None, details=False):
        command = interaction()
        message = SimpleNamespace(attachments=[], edit=AsyncMock())
        command.followup.send.return_value = message
        method = Profile.beastinfo if details else Profile.collection
        if details:
            await method.callback(Profile(None), command, number)
        else:
            await method.callback(Profile(None), command)
        command.response.defer.assert_awaited_once()
        view = command.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        return view, message, command

    def click(self, message, *, value=None, uid=42, edit=None):
        click = component(section=value, uid=uid, edit=edit)
        click.edit_original_response.return_value = message
        return click

    def assert_compact_menu(self, view):
        self.assertIn(view.beast_select, view.children)
        self.assertNotIn('Filter by rarity', [getattr(item, 'placeholder', '') for item in view.children])
        if view.mode == 'detail':
            self.assertEqual([item.row for item in view.children if isinstance(item, discord.ui.Select)], [2, 1])
        self.assertIn('rarities', [option.value for option in view.beast_select.options])

    async def choose_rarity(self, view, message, rarity):
        await view.beast_select.callback(self.click(message, value='rarities'))
        click = self.click(message, value='rarity:' + rarity)
        await view.beast_select.callback(click)
        return click

    async def test_beastinfo_starts_common_with_rarity_on_the_card_and_no_all_view(self):
        common = await self.beast('twine')
        rare_species = next(key for key, row in storage.load_beasts().items() if row['rarity'] == 'rare')
        rare = await self.beast(rare_species)
        await self.execute('UPDATE player_beasts SET is_active=(id=?) WHERE user_id=42', (rare,))
        view, message, _ = await self.open_roster(details=True)
        self.assertEqual((view.picker.rarity, view.mode, view.selected_id), ('common', 'detail', common))
        self.assert_compact_menu(view)
        await view.beast_select.callback(self.click(message, value='rarities'))
        self.assertEqual(view.selected_id, common)
        self.assertEqual([option.value for option in view.beast_select.options],
                         ['beasts'] + ['rarity:' + rarity for rarity in owned_roster.RARITIES])
        click = self.click(message, value='rarity:rare')
        await view.beast_select.callback(click)
        self.assertEqual((view.picker.rarity, view.mode, view.selected_id), ('rare', 'detail', rare))
        self.assert_compact_menu(view)
        click.edit_original_response.assert_awaited_once()
        click.followup.send.assert_not_awaited()
        self.assertEqual((await storage.get_active_beast(42))['id'], rare)

    async def test_detail_selector_reaches_every_beast_across_large_rarity_pages(self):
        ids = [await self.beast('twine') for _ in range(52)]
        before = await storage.get_player_beasts(42)
        view, message, _ = await self.open_roster(details=True)
        seen = []
        while True:
            self.assertLessEqual(len(view.beast_select.options), 25)
            self.assert_compact_menu(view)
            seen.extend(int(option.value) for option in view.beast_select.options if option.value.isdecimal())
            forward = next((option for option in view.beast_select.options if option.label.startswith('Next beasts')), None)
            if not forward:
                break
            click = self.click(message, value=forward.value)
            await view.beast_select.callback(click)
            self.assertEqual(view.mode, 'detail')
            click.edit_original_response.assert_awaited_once()
            click.followup.send.assert_not_awaited()
        self.assertEqual(seen, ids)
        previous = next(option for option in view.beast_select.options if 'Previous beasts' in option.label)
        await view.beast_select.callback(self.click(message, value=previous.value))
        self.assertEqual(view.selected_id, ids[22])
        await view.beast_select.callback(self.click(message, value=str(ids[30])))
        self.assertEqual(view.selected_id, ids[30])
        self.assertEqual(await storage.get_player_beasts(42), before)

    async def test_empty_common_category_can_switch_directly_to_an_owned_rarity(self):
        species = next(key for key, row in storage.load_beasts().items() if row['rarity'] == 'rare')
        rare = await self.beast(species)
        view, message, command = await self.open_roster(details=True)
        self.assertEqual(view.picker.rarity, 'common')
        self.assertIsNone(view.selected_id)
        self.assertNotIn('no longer', command.followup.send.call_args.kwargs['embed'].description)
        await self.choose_rarity(view, message, 'rare')
        self.assertEqual((view.mode, view.selected_id), ('detail', rare))
        await self.choose_rarity(view, message, 'uncommon')
        self.assertIsNone(view.selected_id)
        await self.choose_rarity(view, message, 'rare')
        self.assertEqual((view.mode, view.selected_id), ('detail', rare))

    async def test_explicit_beast_number_opens_its_rarity_and_clear_returns_common(self):
        species = next(key for key, row in storage.load_beasts().items() if row['rarity'] == 'ancient')
        rare = await self.beast(species)
        view, message, _ = await self.open_roster(number=1, details=True)
        self.assertEqual((view.picker.rarity, view.selected_id), ('ancient', rare))
        await view.clear_button.callback(self.click(message))
        self.assertEqual(view.picker.rarity, 'common')
        await view.beast_select.callback(self.click(message, value='rarities'))
        self.assertNotIn('rarity:all', [option.value for option in view.beast_select.options])

    async def test_obsolete_all_filter_cannot_reopen_the_combined_view(self):
        await self.beast('twine')
        view, message, _ = await self.open_roster(details=True)
        click = self.click(message, value='rarity:all')
        await view.beast_select.callback(click)
        self.assertEqual(view.picker.rarity, 'common')
        click.edit_original_response.assert_not_awaited()
        click.followup.send.assert_awaited_once()

    async def test_rarity_menu_cancel_and_foreign_click_keep_the_current_beast(self):
        await self.beast('twine')
        second = await self.beast('gloop')
        view, message, _ = await self.open_roster(number=2, details=True)
        before = await storage.get_player_beasts(42)
        for value in ('rarities', 'beasts'):
            click = self.click(message, value=value)
            await view.beast_select.callback(click)
            self.assertEqual(view.selected_id, second)
            self.assertEqual(view.mode, 'detail')
            click.edit_original_response.assert_awaited_once()
            click.followup.send.assert_not_awaited()
        self.assert_compact_menu(view)
        stranger = self.click(message, value='rarities', uid=43)
        await view.beast_select.callback(stranger)
        stranger.edit_original_response.assert_not_awaited()
        stranger.response.send_message.assert_awaited_once()
        self.assertEqual(view.picker_mode, 'beasts')
        self.assertEqual(await storage.get_player_beasts(42), before)

    async def test_roster_larger_than_discord_menu_is_fully_reachable_without_mutation(self):
        ids = [await self.beast('twine', starter=index == 0) for index in range(27)]
        before = await storage.get_player_beasts(42)
        view, message, command = await self.open_roster()
        first = command.followup.send.call_args.kwargs
        self.assertEqual(len(first['embed'].fields), 10)
        seen = [int(option.value) for option in view.beast_select.options if option.value.isdecimal()]
        for length in (10, 7):
            click = self.click(message)
            await view.next_page.callback(click)
            click.response.defer.assert_awaited_once()
            payload = click.edit_original_response.call_args.kwargs
            self.assertEqual(len(payload['embed'].fields), length)
            self.assertLess(len(payload['embed']), 6000)
            self.assertLessEqual(len(view.children), 25)
            seen.extend(int(option.value) for option in view.beast_select.options if option.value.isdecimal())
        self.assertEqual(seen, ids)
        self.assertTrue(view.next_page.disabled)
        await view.beast_select.callback(self.click(message, value=str(ids[-1])))
        self.assertEqual((view.mode, view.selected_id), ('detail', ids[-1]))
        await view.back_button.callback(self.click(message))
        self.assertEqual((view.mode, view.picker.page), ('overview', 2))
        self.assertEqual(await storage.get_player_beasts(42), before)

    async def test_filtered_detail_arrows_and_back_preserve_search_sort_and_page(self):
        await self.beast(starter=True)
        species = next(key for key, row in storage.load_beasts().items() if row['rarity'] == 'uncommon')
        ids = [await self.beast(species) for _ in range(12)]
        for index, row_id in enumerate(ids):
            await self.execute('UPDATE player_beasts SET level=? WHERE id=?', (index + 2, row_id))
        view, message, _ = await self.open_roster()
        await view.show(self.click(message), query=species.replace('_', ' '), rarity='uncommon', sort='level')
        await view.next_page.callback(self.click(message))
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [ids[1], ids[0]])
        await view.beast_select.callback(self.click(message, value=str(ids[1])))
        await view.next_btn.callback(self.click(message))
        self.assertEqual(view.selected_id, ids[0])
        self.assertTrue(view.next_btn.disabled)
        await view.back_button.callback(self.click(message))
        self.assertEqual((view.picker.query, view.picker.rarity, view.picker.sort, view.picker.page),
                         (species.replace('_', ' '), 'uncommon', 'level', 1))
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [ids[1], ids[0]])

    async def test_search_modal_finds_nickname_species_or_exact_number_and_clears(self):
        first = await self.beast('twine', starter=True)
        second = await self.beast('twine')
        await self.execute("UPDATE player_beasts SET nickname='Ember' WHERE id=?", (second,))
        view, message, _ = await self.open_roster()
        search = self.click(message)
        search.response.send_modal = AsyncMock()
        await view.search_button.callback(search)
        modal = search.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, SearchModal)
        modal.query._value = ' eMbEr '
        await modal.on_submit(self.click(message))
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [second])
        await view.show(self.click(message), query='Twine')
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [first, second])
        await view.show(self.click(message), query='#2')
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [second])
        await view.show(self.click(message), query='No such companion')
        self.assertEqual([option.value for option in view.beast_select.options], ['rarities'])
        await view.clear_button.callback(self.click(message))
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [first, second])
        self.assertEqual((view.picker.query, view.picker.page), ('', 0))

    async def test_number_resolution_rejects_numbered_internal_id_but_keeps_legacy(self):
        row_id = await self.beast('twine')
        await self.execute('UPDATE player_beasts SET player_number=10 WHERE id=?', (row_id,))
        view, _, command = await self.open_roster(number=row_id, details=True)
        payload = command.followup.send.call_args.kwargs
        self.assertEqual(view.mode, 'overview')
        self.assertIsNone(view.selected_id)
        self.assertIn('not found', payload['embed'].description)
        proper, _, _ = await self.open_roster(number=10, details=True)
        self.assertEqual(proper.selected_id, row_id)
        await self.execute('UPDATE player_beasts SET player_number=NULL WHERE id=?', (row_id,))
        legacy, _, _ = await self.open_roster(number=row_id, details=True)
        self.assertEqual(legacy.selected_id, row_id)
        self.assertIn(f'Beast #{row_id}', legacy.render()[0].footer.text)

    async def test_traded_companion_cannot_be_reopened_from_stale_card_or_dropdown(self):
        await self.beast(starter=True)
        sold = await self.beast('slime')
        await storage.get_or_create_player(43, 'Recipient')
        view, message, _ = await self.open_roster(number=2, details=True)
        self.assertEqual(view.selected_id, sold)
        message.attachments = [SimpleNamespace(filename='old-portrait.png')]
        await complete_trade(42, 43, sold, offer_key='roster-transfer')
        click = self.click(message)
        await view.refresh_button.callback(click)
        self.assertEqual((view.mode, view.selected_id), ('overview', None))
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], [])
        self.assertIn('no longer', click.edit_original_response.call_args.kwargs['embed'].description)
        await view.beast_select.callback(self.click(message, value=str(sold)))
        self.assertEqual((view.mode, view.selected_id), ('overview', None))
        self.assertNotIn(str(sold), [option.value for option in view.beast_select.options])

    async def test_empty_collection_refresh_can_find_new_companion(self):
        view, message, command = await self.open_roster()
        self.assertIn('No companions yet', command.followup.send.call_args.kwargs['embed'].description)
        row_id = await self.beast('twine', starter=True)
        await view.refresh_button.callback(self.click(message))
        self.assertEqual([int(option.value) for option in view.beast_select.options if option.value.isdecimal()], [row_id])

    async def test_search_from_detail_opens_results_without_a_false_ownership_warning(self):
        lead = await self.beast('twine', starter=True)
        await self.beast('slime')
        view, message, _ = await self.open_roster(details=True)
        self.assertEqual(view.selected_id, lead)
        click = self.click(message)
        await view.show(click, query='slime')
        self.assertEqual(view.mode, 'overview')
        description = click.edit_original_response.call_args.kwargs['embed'].description
        self.assertNotIn('no longer', description)
        self.assertEqual(view.notice, '')

    async def test_owner_checks_and_timeout_disable_all_controls(self):
        await self.beast(starter=True)
        view, message, _ = await self.open_roster(details=True)
        stranger = self.click(message, uid=43)
        await view.next_btn.callback(stranger)
        stranger.response.send_message.assert_awaited_once()
        stranger.response.defer.assert_not_awaited()
        stranger.edit_original_response.assert_not_awaited()
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        message.edit.assert_awaited_once_with(view=view)
        view.stop()
        closed = self.click(message)
        await view.refresh_button.callback(closed)
        closed.edit_original_response.assert_not_awaited()
        self.assertIn('closed', closed.followup.send.call_args.args[0])

    async def test_art_upload_rejection_keeps_card_and_navigation_and_closes_file(self):
        await self.beast('twine', starter=True)
        command = interaction()
        message = SimpleNamespace(attachments=[], edit=AsyncMock())
        denied = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        command.followup.send.side_effect = [denied, message]
        self.render_portrait.return_value = gear_art.Portrait(gear_art.portrait_filename('twine'), b'png')
        with self.assertLogs('chibibeasts.roster', level='WARNING'):
            await Profile.beastinfo.callback(Profile(None), command, 1)
        first, fallback = command.followup.send.call_args_list
        view = fallback.kwargs['view']
        self.addCleanup(view.stop)
        self.assertTrue(first.kwargs['files'][0].fp.closed)
        self.assertEqual(fallback.kwargs['files'], [])
        self.assertIsNone(fallback.kwargs['embed'].image.url)
        self.assertIn(view.back_button, view.children)
        self.assertFalse(view.art_enabled)

    async def test_late_portrait_unavailability_does_not_keep_an_old_attachment(self):
        await self.beast('twine', starter=True)
        await self.beast('slime')
        view, message, _ = await self.open_roster(details=True)
        message.attachments = [SimpleNamespace(filename='stale.png')]
        click = self.click(message)
        await view.next_btn.callback(click)
        payload = click.edit_original_response.call_args.kwargs
        self.assertEqual(payload['attachments'], [])
        self.assertIsNone(payload['embed'].image.url)
        self.assertEqual(view.mode, 'detail')

    async def test_concurrent_inputs_acknowledge_and_publish_in_order(self):
        await self.beast(starter=True)
        await self.beast('slime')
        view, message, _ = await self.open_roster()
        started, release = asyncio.Event(), asyncio.Event()
        published = []

        async def first_edit(**kwargs):
            title = kwargs['embed'].description
            controls = kwargs['view'].to_components()
            started.set()
            await release.wait()
            published.append((title, controls))
            return message

        async def second_edit(**kwargs):
            published.append((kwargs['embed'].description, kwargs['view'].to_components()))
            return message

        first = self.click(message, edit=first_edit)
        second = self.click(message, edit=second_edit)
        tasks = [asyncio.create_task(view.show(first, query='Twine'))]
        try:
            await asyncio.wait_for(started.wait(), 2)
            tasks.append(asyncio.create_task(view.show(second, query='Slime')))
            await asyncio.sleep(0)
            second.response.defer.assert_awaited_once()
            self.assertEqual(view.picker.query, 'Twine')
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIn('Twine', published[0][0])
        self.assertIn('Slime', published[1][0])
        self.assertEqual(view.picker.query, 'Slime')
        self.assertEqual(published[-1][1], view.to_components())

    async def test_beastinfo_autocomplete_uses_owned_display_numbers_and_names(self):
        mine = await self.beast('twine', starter=True)
        await self.execute("UPDATE player_beasts SET player_number=27,nickname='Ember' WHERE id=?", (mine,))
        await self.beast('twine', uid=43)
        choices = await owned_roster.autocomplete(interaction(), 'ember')
        self.assertEqual([(choice.name, choice.value) for choice in choices], [('#27 · Ember · Lv.1', 27)])
        parameter = next(parameter for parameter in Profile.beastinfo.parameters if parameter.name == 'beast_number')
        self.assertTrue(parameter.autocomplete)

    async def test_care_opens_the_viewed_companion_and_rejects_other_trainers(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('slime')
        view, message, _ = await self.open_roster(number=2, details=True)
        with patch.object(view.navigator, 'navigate', new=AsyncMock()) as care:
            stranger = self.click(message, uid=43)
            await view.care_button.callback(stranger)
            care.assert_not_awaited()
            click = self.click(message)
            await view.care_button.callback(click)
            care.assert_awaited_once_with(click, view, 'care', view.selected())
            self.assertEqual(view.selected()['id'], reserve)
        self.assertNotEqual(lead, reserve)
        self.assertEqual((await storage.get_active_beast(42))['id'], lead)
