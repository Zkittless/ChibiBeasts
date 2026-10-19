"""Private Care interactions exercise the real item transaction and live party."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.care import CompanionCare
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import adventure_party, care_view, combat, db as storage, gear_art, journal_state
from utils.care_view import CareName, CareSearch, open_care
from utils.duels import prepare_state
from utils.journal import JournalView


async def portrait(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'preview png')


class CareInteractions(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        artwork = patch.object(gear_art, 'render_portrait', side_effect=portrait)
        artwork.start()
        self.patches.append(artwork)

    async def open(self, row_id=None, number=None):
        command = interaction()
        message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        command.followup.send.return_value = message
        if row_id is None:
            view = await CompanionCare.care.callback(CompanionCare(None), command, number)
        else:
            view = await open_care(command, beast_row_id=row_id)
        self.addCleanup(view.stop)
        return view, command, message

    async def click(self, view, action, value=None):
        click = component()
        click.data = {'values': [str(value)]} if action in {'companion', 'item'} else {}
        click.edit_original_response.return_value = view.message
        await view.handle(click, action, value=value)
        return click

    async def stock(self, item_id):
        return sum(row['quantity'] for row in await storage.get_inventory(42) if row['item_id'] == item_id)

    async def hurt(self, row_id, hp=20):
        await self.execute('UPDATE player_beasts SET hp=? WHERE id=?', (hp, row_id))

    async def test_command_is_private_and_use_updates_same_panel_with_exact_hp_and_stock(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion', 3)
        view, command, _ = await self.open()
        command.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(command.followup.send.call_args.kwargs['ephemeral'])
        self.assertIsNone(view.item_id)
        self.assertTrue(view.use_button.disabled)
        await self.click(view, 'item', 'pixie_dust_potion')
        before = view.state.target['hp']
        expected = min(view.state.target['max_hp'], before + int(view.state.target['max_hp'] * .2))
        self.assertIn(f'{expected}/', view.current_preview.text)
        used = await self.click(view, 'use')
        self.assertEqual(view.state.target['hp'], expected)
        self.assertEqual(await self.stock('pixie_dust_potion'), 2)
        self.assertEqual(view.current_preview.quantity, 2)
        used.edit_original_response.assert_awaited_once()
        used.followup.send.assert_not_awaited()
        self.assertEqual(command.followup.send.await_count, 1)

    async def test_exhausted_potion_stays_selected_and_never_switches_to_owned_permanent_item(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion')
        await storage.add_item(42, 'ambrosia_tart')
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        await self.click(view, 'use')
        self.assertEqual(view.item_id, 'pixie_dust_potion')
        self.assertEqual(view.current_preview.quantity, 0)
        self.assertTrue(view.use_button.disabled)
        self.assertTrue(any(option.value == 'pixie_dust_potion' and option.default for option in view.item_select.options))
        self.assertEqual(await self.stock('ambrosia_tart'), 1)
        self.assertEqual(view.state.beast['damage_bonus'], 0)

    async def test_old_or_overlapping_use_buttons_can_spend_only_once_per_revision(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid, 1)
        await storage.add_item(42, 'pixie_dust_potion', 3)
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        displayed = view.use_button.callback
        entered, release = asyncio.Event(), asyncio.Event()
        real_use = care_view.use_item

        async def slow_use(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real_use(*args, **kwargs)

        first, second = component(), component()
        first.edit_original_response.return_value = view.message
        tasks = []
        try:
            with patch.object(care_view, 'use_item', side_effect=slow_use) as use:
                tasks.append(asyncio.create_task(displayed(first)))
                await asyncio.wait_for(entered.wait(), 2)
                tasks.append(asyncio.create_task(displayed(second)))
                await asyncio.sleep(0)
                release.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 3)
                use.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(await self.stock('pixie_dust_potion'), 2)
        self.assertIn('preview has changed', second.response.send_message.call_args.args[0])
        replay = component()
        await displayed(replay)
        self.assertEqual(await self.stock('pixie_dust_potion'), 2)

    async def test_changed_hp_requires_fresh_review_and_does_not_spend_the_published_preview(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion', 2)
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        await self.hurt(rid, 30)
        await self.click(view, 'use')
        self.assertEqual(await self.stock('pixie_dust_potion'), 2)
        self.assertIn('changed', view.notice)
        self.assertEqual(view.state.target['hp'], 30)
        await self.click(view, 'use')
        self.assertEqual(await self.stock('pixie_dust_potion'), 1)

    async def test_transferred_companion_never_redirects_items_to_the_old_collection_number(self):
        await self.beast('twine', starter=True)
        target = await self.beast('slime')
        replacement = await self.beast('fairy')
        await self.hurt(target)
        await self.hurt(replacement)
        await storage.add_item(42, 'pixie_dust_potion')
        view, _, _ = await self.open(number=2)
        await self.click(view, 'item', 'pixie_dust_potion')
        await storage.get_or_create_player(77, 'Other trainer')
        await self.execute('UPDATE player_beasts SET user_id=77 WHERE id=?', (target,))
        await self.execute('UPDATE player_beasts SET player_number=2 WHERE id=?', (replacement,))
        await self.click(view, 'use')
        self.assertEqual(await self.stock('pixie_dust_potion'), 1)
        self.assertIsNone(view.state.beast)
        self.assertTrue(view.use_button.disabled)
        self.assertEqual((await self.rows('SELECT hp FROM player_beasts WHERE id=?', (replacement,)))[0]['hp'], 20)

    async def test_knockout_blocks_ordinary_heal_and_explicit_revive_clears_recovery(self):
        rid = await self.beast('twine', starter=True)
        await storage.knockout_beast(rid, 'common')
        await storage.add_item(42, 'pixie_dust_potion')
        await storage.add_item(42, 'phoenix_elixir')
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        self.assertTrue(view.use_button.disabled)
        self.assertIn('Phoenix', view.current_preview.reason)
        await self.click(view, 'item', 'phoenix_elixir')
        self.assertFalse(view.use_button.disabled)
        await self.click(view, 'use')
        self.assertIsNone(view.state.beast['knocked_out_until'])
        self.assertEqual(view.state.beast['hp'], max(1, int(view.state.beast['max_hp']*.5)))
        self.assertEqual(await self.stock('pixie_dust_potion'), 1)
        self.assertEqual(await self.stock('phoenix_elixir'), 0)

    async def test_live_blight_cure_and_reserve_healing_update_the_actual_selected_member(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        await adventure_party.set_slot(42, reserve, 1)
        party = [await prepare_state(42, row, storage.get_beast_data(row['beast_id']), [], consume=False)
                 for row in await adventure_party.rows(42)]
        party[1].update(hp=10, status='blight', status_turns=2)
        combat.active_states[42], combat.active_parties[42] = party[0], party
        await storage.add_item(42, 'pixie_dust_potion')
        await storage.add_item(42, 'aether_tonic')
        view, _, _ = await self.open(row_id=reserve)
        await self.click(view, 'item', 'pixie_dust_potion')
        self.assertTrue(view.use_button.disabled)
        self.assertIn('Blight', view.current_preview.reason)
        await self.click(view, 'item', 'aether_tonic')
        await self.click(view, 'use')
        self.assertIsNone(party[1]['status'])
        self.assertTrue(party[1]['status_ward'])
        await self.click(view, 'item', 'pixie_dust_potion')
        await self.click(view, 'use')
        self.assertGreater(party[1]['hp'], 10)
        self.assertEqual(combat.active_states[42]['id'], lead)
        self.assertEqual(view.state.target['hp'], party[1]['hp'])

    async def test_permanent_item_needs_separate_review_and_confirmation(self):
        await self.beast('twine', starter=True)
        await storage.add_item(42, 'ambrosia_tart', 2)
        view, _, _ = await self.open()
        await self.click(view, 'item', 'ambrosia_tart')
        self.assertEqual(view.use_button.label, 'Review')
        await self.click(view, 'confirm')
        self.assertEqual(await self.stock('ambrosia_tart'), 2)
        await self.click(view, 'use')
        self.assertTrue(view.confirming)
        self.assertEqual(await self.stock('ambrosia_tart'), 2)
        displayed = view.confirm_button.callback
        click = component()
        click.edit_original_response.return_value = view.message
        await displayed(click)
        self.assertEqual(await self.stock('ambrosia_tart'), 1)
        self.assertEqual(view.state.beast['damage_bonus'], 5)
        self.assertFalse(view.confirming)
        await displayed(component())
        self.assertEqual(await self.stock('ambrosia_tart'), 1)

    async def test_party_review_names_exact_team_and_changed_members_require_review_again(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        stranger = await self.beast('slime')
        await adventure_party.set_slot(42, reserve, 1)
        for rid in (lead, reserve, stranger):
            await self.hurt(rid)
        await storage.add_item(42, 'tear_of_leviathan')
        view, _, _ = await self.open()
        await self.click(view, 'item', 'tear_of_leviathan')
        self.assertIn('Twine', view.current_preview.text)
        self.assertIn('Fairy', view.current_preview.text)
        self.assertNotIn('Slime', view.current_preview.text)
        await self.click(view, 'use')
        await adventure_party.clear_slot(42, 1)
        await self.click(view, 'confirm')
        self.assertEqual(await self.stock('tear_of_leviathan'), 1)
        self.assertFalse(view.confirming)
        await self.click(view, 'use')
        await self.click(view, 'confirm')
        self.assertEqual(await self.stock('tear_of_leviathan'), 0)
        saved = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual(saved[lead]['hp'], saved[lead]['max_hp'])
        self.assertEqual(saved[reserve]['hp'], 20)
        self.assertEqual(saved[stranger]['hp'], 20)

    async def test_full_health_no_benefit_and_missing_stock_disable_use_without_spending(self):
        rid = await self.beast('twine', starter=True)
        await storage.add_item(42, 'pixie_dust_potion')
        await storage.add_item(42, 'tear_of_leviathan')
        view, _, _ = await self.open()
        for item in ('pixie_dust_potion', 'tear_of_leviathan'):
            await self.click(view, 'item', item)
            self.assertTrue(view.use_button.disabled)
            await self.click(view, 'use')
            self.assertEqual(await self.stock(item), 1)
        await self.hurt(rid)
        await self.click(view, 'item', 'pixie_dust_potion')
        await self.execute("DELETE FROM player_inventory WHERE item_id='pixie_dust_potion'")
        await self.click(view, 'use')
        self.assertTrue(view.use_button.disabled)
        self.assertEqual(view.item_id, 'pixie_dust_potion')
        self.assertEqual(view.state.beast['hp'], 20)

    async def test_name_modal_does_not_consume_token_until_named_preview_is_used(self):
        await self.beast('twine', starter=True)
        await storage.add_item(42, 'beast_rename_token')
        view, _, _ = await self.open()
        await self.click(view, 'item', 'beast_rename_token')
        self.assertTrue(view.use_button.disabled)
        click = component()
        click.response.send_modal = AsyncMock()
        await view.name_button.callback(click)
        modal = click.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, CareName)
        self.addCleanup(modal.stop)
        modal.name._value = 'Little Clock'
        submitted = component()
        submitted.edit_original_response.return_value = view.message
        await modal.on_submit(submitted)
        self.assertEqual(await self.stock('beast_rename_token'), 1)
        self.assertIn('Little Clock', view.current_preview.text)
        await self.click(view, 'use')
        self.assertEqual(view.state.beast['nickname'], 'Little Clock')
        self.assertEqual(await self.stock('beast_rename_token'), 0)

    async def test_search_modal_pages_and_empty_search_cannot_apply_to_previous_target(self):
        await self.beast('twine', starter=True)
        for _ in range(25):
            await self.beast('slime')
        await storage.add_item(42, 'pixie_dust_potion')
        view, _, _ = await self.open()
        self.assertEqual(len(view.companion_select.options), 25)
        await self.click(view, 'next')
        self.assertEqual(view.state.beast['player_number'], 26)
        self.assertEqual(len(view.companion_select.options), 1)
        click = component()
        click.response.send_modal = AsyncMock()
        await view.search_button.callback(click)
        modal = click.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, CareSearch)
        self.addCleanup(modal.stop)
        modal.query._value = 'missing phoenix'
        submitted = component()
        submitted.edit_original_response.return_value = view.message
        await modal.on_submit(submitted)
        self.assertIsNone(view.state.beast)
        self.assertTrue(view.use_button.disabled)
        self.assertTrue(view.companion_select.disabled)
        await self.click(view, 'clear')
        self.assertEqual(view.state.beast['beast_id'], 'twine')

    async def test_foreign_owner_and_closed_panel_cannot_use_items_or_open_modals(self):
        await self.beast('twine', starter=True)
        view, _, message = await self.open()
        foreign = component(uid=77)
        await view.handle(foreign, 'open_search')
        foreign.response.send_message.assert_awaited_once()
        foreign.response.defer.assert_not_awaited()
        await view.on_timeout()
        message.edit.assert_awaited_once_with(view=None)
        self.assertEqual(view.children, [])
        closed = component()
        await view.handle(closed, 'use')
        closed.response.send_message.assert_awaited_once()

    async def test_unchanged_portrait_attachment_is_reused_and_rejected_new_art_falls_back(self):
        rid = await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (rid,))
        view, command, message = await self.open()
        sent = command.followup.send.call_args.kwargs['file']
        self.assertTrue(sent.fp.closed)
        uploaded = SimpleNamespace(filename=sent.filename)
        message.attachments = [uploaded]
        with patch.object(gear_art, 'render_portrait', side_effect=portrait) as render:
            refreshed = await self.click(view, 'refresh')
            render.assert_not_awaited()
        self.assertEqual(refreshed.edit_original_response.call_args.kwargs['attachments'], [uploaded])
        click = component()
        click.data = {'values': [str(second)]}
        denied = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        click.edit_original_response.side_effect = [denied, message]
        with self.assertLogs('chibibeasts.care', level='WARNING'):
            await view.handle(click, 'companion')
        first, fallback = click.edit_original_response.call_args_list
        self.assertTrue(first.kwargs['attachments'][0].fp.closed)
        self.assertEqual(fallback.kwargs['attachments'], [])
        self.assertIsNone(fallback.kwargs['embed'].image.url)
        self.assertFalse(view.companion_select.disabled)

    async def test_unconfirmed_service_error_does_not_retry_or_reuse_the_same_button_revision(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion', 2)
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        displayed = view.use_button.callback
        click = component()
        click.edit_original_response.return_value = view.message
        with patch.object(care_view, 'use_item', side_effect=OSError('outcome uncertain')) as use, self.assertLogs('chibibeasts.care', level='ERROR'):
            await displayed(click)
            await displayed(component())
            use.assert_awaited_once()
        self.assertIn('could not be confirmed', view.notice)
        self.assertEqual(await self.stock('pixie_dust_potion'), 2)
        self.assertEqual(view.state.beast['hp'], 20)

    async def test_empty_roster_and_invalid_explicit_number_never_fall_back_to_another_beast(self):
        empty, _, _ = await self.open()
        self.assertTrue(empty.companion_select.disabled)
        self.assertTrue(empty.use_button.disabled)
        await self.beast('twine', starter=True)
        invalid, _, _ = await self.open(number=999)
        self.assertIsNone(invalid.state.beast)
        self.assertFalse(invalid.companion_select.disabled)
        self.assertTrue(invalid.use_button.disabled)

    async def test_team_role_care_button_opens_the_exact_reserve_in_a_private_panel(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        await adventure_party.set_slot(42, reserve, 1)
        journal = JournalView(42, await journal_state.snapshot(42), page='team')
        self.addCleanup(journal.stop)
        button = next(item for item in journal.walk_children() if isinstance(item, discord.ui.Button)
                      and f':care:{reserve}:' in item.custom_id)
        click = component()
        click.followup.send.return_value = SimpleNamespace(id=456, attachments=[], edit=AsyncMock())
        view = await button.callback(click)
        self.addCleanup(view.stop)
        self.assertEqual(view.state.selected_id, reserve)
        self.assertTrue(click.followup.send.call_args.kwargs['ephemeral'])
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertFalse(journal.closed)
        self.assertEqual((await storage.get_active_beast(42))['id'], lead)

    async def test_failed_publish_after_successful_use_offers_reopen_without_replaying_the_item(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion', 2)
        view, _, _ = await self.open()
        await self.click(view, 'item', 'pixie_dust_potion')
        displayed = view.use_button.callback
        button = view.use_button
        click = component()
        error = discord.HTTPException(SimpleNamespace(status=503, reason='Unavailable'), {'message': 'Unavailable'})
        click.edit_original_response.side_effect = error
        with self.assertLogs('chibibeasts.care', level='WARNING'):
            with self.assertRaises(discord.HTTPException):
                await displayed(click)
            # Discord's View dispatcher invokes on_error after a callback fails.
            await view.on_error(click, error, button)
        self.assertEqual(await self.stock('pixie_dust_potion'), 1)
        self.assertGreater(view.state.beast['hp'], 20)
        self.assertIn('Open /care', click.followup.send.call_args.args[0])
        self.assertTrue(click.followup.send.call_args.kwargs['ephemeral'])
        await displayed(component())
        self.assertEqual(await self.stock('pixie_dust_potion'), 1)

    async def test_art_unavailable_still_sends_private_preview_and_valid_controls_without_mentions(self):
        rid = await self.beast('twine', starter=True)
        await self.hurt(rid)
        await storage.add_item(42, 'pixie_dust_potion')
        with patch.object(gear_art, 'render_portrait', new=AsyncMock(return_value=None)):
            view, command, _ = await self.open()
            selected = await self.click(view, 'item', 'pixie_dust_potion')
        self.assertNotIn('file', command.followup.send.call_args.kwargs)
        self.assertIsNone(selected.edit_original_response.call_args.kwargs['embed'].image.url)
        self.assertFalse(view.use_button.disabled)
        mentions = selected.edit_original_response.call_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
