"""Gear browsing keeps the viewed companion distinct from the active beast."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.utilities import Utilities
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, gear_art
from utils.gear_view import GearSearch


async def portrait(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'preview png')


class GearPicker(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        artwork = patch.object(gear_art, 'render_portrait', side_effect=portrait)
        artwork.start()
        self.patches.append(artwork)

    async def open(self, number=None):
        command = interaction()
        message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        command.followup.send.return_value = message
        view = await Utilities.gear.callback(Utilities(None), command, number)
        self.addCleanup(view.stop)
        return view, command, message

    async def click(self, view, action, value=None, *, edit=None):
        click = component(edit=edit)
        click.data = {'values': [str(value)]} if action == 'select' else {}
        if edit is None:
            click.edit_original_response.return_value = view.message
        await view.handle(click, action, value)
        return click

    async def armor(self, row_id, armor_id):
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)',
                           (row_id, armor_id))

    async def test_optional_command_defaults_to_active_and_browsing_never_switches_it(self):
        lead = await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        view, command, _ = await self.open()
        self.assertEqual(view.selected_id, lead)
        command.response.defer.assert_awaited_once()
        selected = await self.click(view, 'select', second)
        self.assertEqual(view.selected_id, second)
        self.assertIn('Barkley', selected.edit_original_response.call_args.kwargs['embed'].title)
        self.assertEqual((await storage.get_active_beast(42))['id'], lead)
        self.assertTrue(any(option.default and option.value == str(second) for option in view.select.options))

    async def test_autocomplete_finds_nickname_species_and_collection_number(self):
        rid = await self.beast('twine', starter=True)
        await self.execute("UPDATE player_beasts SET nickname='Clockwork Buddy',level=17 WHERE id=?", (rid,))
        cog = Utilities(None)
        for query in ('Clockwork', 'Twine', '#1'):
            choices = await cog.beast_autocomplete(interaction(), query)
            self.assertEqual([choice.value for choice in choices], [1])
            self.assertIn('Clockwork Buddy', choices[0].name)
            self.assertIn('Lv.17', choices[0].name)
        parameter = next(parameter for parameter in Utilities.gear.parameters if parameter.name == 'beast_id')
        self.assertFalse(parameter.required)
        self.assertTrue(parameter.autocomplete)

    async def test_large_collection_pages_and_search_can_reach_every_beast(self):
        await self.beast('twine', starter=True)
        for _ in range(26):
            await self.beast('slime')
        view, _, _ = await self.open()
        self.assertEqual(len(view.select.options), 25)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)
        await self.click(view, 'next')
        self.assertEqual(len(view.select.options), 2)
        self.assertEqual(view.selected()['player_number'], 26)
        self.assertTrue(view.next.disabled)
        await self.click(view, 'search', '#27')
        self.assertEqual(view.selected()['player_number'], 27)
        self.assertEqual(len(view.select.options), 1)
        await self.click(view, 'clear')
        self.assertEqual(view.picker.page, 0)
        self.assertEqual(len(view.select.options), 25)

    async def test_explicit_selection_opens_its_page_and_identity_survives_renumbering(self):
        lead = await self.beast('twine', starter=True)
        target = await self.beast('slime')
        view, _, _ = await self.open(2)
        self.assertEqual(view.selected_id, target)
        await self.execute('UPDATE player_beasts SET player_number=19 WHERE id=?', (target,))
        refreshed = await self.click(view, 'refresh')
        self.assertEqual(view.selected_id, target)
        self.assertIn('#19', refreshed.edit_original_response.call_args.kwargs['embed'].title)
        self.assertEqual((await storage.get_active_beast(42))['id'], lead)

    async def test_transferred_target_is_not_replaced_by_another_beast_with_its_old_number(self):
        await self.beast('twine', starter=True)
        target = await self.beast('slime')
        replacement = await self.beast('fairy')
        view, _, _ = await self.open(2)
        await storage.get_or_create_player(77, 'Other trainer')
        await self.execute('UPDATE player_beasts SET user_id=77 WHERE id=?', (target,))
        await self.execute('UPDATE player_beasts SET player_number=2 WHERE id=?', (replacement,))
        click = await self.click(view, 'select', target)
        self.assertIsNone(view.selected_id)
        payload = click.edit_original_response.call_args.kwargs
        self.assertIn('selection changed', payload['embed'].description)
        self.assertEqual(payload['attachments'], [])
        self.assertTrue(view.care.disabled)
        self.assertNotIn(str(target), [option.value for option in view.select.options])

    async def test_refresh_reads_new_equipment_and_lists_held_rune_stacks(self):
        rid = await self.beast('twine', starter=True)
        await self.armor(rid, 'kelpie_scale_mail')
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'bark_sigil',3)")
        view, command, _ = await self.open()
        first = command.followup.send.call_args.kwargs['file'].filename
        await self.execute("UPDATE player_equipment SET equipment_id='gnome_iron_plating' WHERE beast_row_id=?", (rid,))
        click = await self.click(view, 'refresh')
        payload = click.edit_original_response.call_args.kwargs
        self.assertNotEqual(payload['attachments'][0].filename, first)
        fields = '\n'.join(field.name + field.value for field in payload['embed'].fields)
        self.assertIn('Gnome-Iron Plating', fields)
        self.assertIn('Bark Sigil ×3', fields)

    async def test_no_search_match_still_allows_clearing_or_searching_again(self):
        await self.beast('twine', starter=True)
        view, _, _ = await self.open()
        click = await self.click(view, 'search', 'unowned phoenix')
        self.assertIn('No beasts match', click.edit_original_response.call_args.kwargs['embed'].description)
        self.assertTrue(view.select.disabled)
        self.assertFalse(view.search.disabled)
        self.assertFalse(view.clear.disabled)
        self.assertIsNone(view.selected_id)
        await self.click(view, 'clear')
        self.assertEqual(view.selected()['beast_id'], 'twine')

    async def test_empty_collection_and_invalid_number_keep_a_working_selector(self):
        empty, command, _ = await self.open()
        self.assertIn('collection is empty', command.followup.send.call_args.kwargs['embed'].description)
        self.assertTrue(empty.select.disabled)
        await self.beast('twine', starter=True)
        view, command, _ = await self.open(999)
        self.assertIn('No beast found', command.followup.send.call_args.kwargs['embed'].description)
        self.assertFalse(view.select.disabled)
        self.assertIsNone(view.selected_id)

    async def test_other_owner_cannot_search_or_select_and_timeout_removes_controls(self):
        await self.beast('twine', starter=True)
        view, _, message = await self.open()
        click = component(uid=77)
        await view.handle(click, 'open_search')
        click.response.send_message.assert_awaited_once()
        self.assertIsNone(view.picker.query or None)
        await view.on_timeout()
        self.assertTrue(view.closed)
        self.assertEqual(view.children, [])
        message.edit.assert_awaited_once_with(view=None)
        expired = component()
        await view.handle(expired, 'refresh')
        expired.response.send_message.assert_awaited_once()

    async def test_modal_uses_same_owned_search_handler(self):
        await self.beast('twine', starter=True)
        view, _, _ = await self.open()
        click = component()
        click.response.send_modal = AsyncMock()
        await view.handle(click, 'open_search')
        modal = click.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, GearSearch)
        self.addCleanup(modal.stop)
        modal.query._value = 'Twine'
        submitted = component()
        with patch.object(gear_art, 'render_portrait', side_effect=portrait):
            await modal.on_submit(submitted)
        self.assertEqual(view.picker.query, 'Twine')
        self.assertEqual(view.selected()['beast_id'], 'twine')

    async def test_care_handoff_keeps_the_identity_shown_when_the_button_was_rendered(self):
        lead = await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        view, _, _ = await self.open()
        displayed_button = view.care.callback
        await self.click(view, 'select', second)
        launch = AsyncMock()
        # Isolate the handoff contract from Care's own inventory interactions.
        with patch.dict(sys.modules, {'utils.care_view': SimpleNamespace(open_care=launch)}):
            click = component()
            await displayed_button(click)
            launch.assert_awaited_once_with(click, beast_row_id=lead)
            launch.reset_mock()
            await view.care.callback(component(uid=77))
            launch.assert_not_awaited()

    async def test_care_cannot_fall_back_to_an_unintended_target_when_selection_is_empty(self):
        await self.beast('twine', starter=True)
        view, _, _ = await self.open(999)
        click = component()
        await view.care.callback(click)
        click.response.send_message.assert_awaited_once()
        self.assertIn('Choose a beast', click.response.send_message.call_args.args[0])

    async def test_overlapping_refresh_and_selection_cannot_publish_out_of_order(self):
        await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        view, _, _ = await self.open()
        entered, release = asyncio.Event(), asyncio.Event()
        titles = []

        async def blocked_edit(**kwargs):
            entered.set()
            await release.wait()
            titles.append(kwargs['embed'].title)

        async def next_edit(**kwargs):
            titles.append(kwargs['embed'].title)

        tasks = []
        try:
            tasks.append(asyncio.create_task(self.click(view, 'refresh', edit=blocked_edit)))
            await asyncio.wait_for(entered.wait(), 2)
            tasks.append(asyncio.create_task(self.click(view, 'select', second, edit=next_edit)))
            await asyncio.sleep(0)
            self.assertEqual(titles, [])
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIn('Twine', titles[0])
        self.assertIn('Barkley', titles[-1])
        self.assertEqual(view.selected_id, second)

    async def test_attachment_rejection_during_edit_keeps_selection_controls_and_clears_old_art(self):
        await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        view, _, _ = await self.open()
        click = component()
        click.data = {'values': [str(second)]}
        error = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        click.edit_original_response.side_effect = [error, view.message]
        with patch.object(gear_art, 'render_portrait', side_effect=portrait), self.assertLogs('chibibeasts.gear.art', level='WARNING'):
            await view.handle(click, 'select')
        self.assertEqual(click.edit_original_response.await_count, 2)
        first, second_call = click.edit_original_response.call_args_list
        self.assertTrue(first.kwargs['attachments'][0].fp.closed)
        self.assertEqual(second_call.kwargs['attachments'], [])
        self.assertIsNone(second_call.kwargs['embed'].image.url)
        self.assertFalse(view.select.disabled)
        self.assertEqual(view.selected_id, second)
