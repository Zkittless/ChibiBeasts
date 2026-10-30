"""Production journal callbacks against disposable player and event ledgers."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, patch

import discord

from cogs.hatch import Hatch, check_collection_completion, get_beast_by_rarity
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import battle_art, db as storage, expeditions, hollowlight, journal_log, journal_state
from utils.journal import JournalView


class HollowlightJournal(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.lead = await self.beast('twine', starter=True)
        self.inputs = 200000
        presentation = patch.object(battle_art, 'BattlePresentation', side_effect=lambda realm: SimpleNamespace(
            realm=realm, art=AsyncMock(return_value=None), sleep=AsyncMock()))
        presentation.start()
        self.patches.append(presentation)

    async def funds(self, amount=200):
        await self.execute('INSERT INTO hollowlight_wallets VALUES(?,?,?,?) ON CONFLICT(user_id,event_id) '
                           'DO UPDATE SET embers=excluded.embers,total_earned=excluded.total_earned',
                           (42, hollowlight.EVENT_ID, amount, amount))

    async def close_event(self):
        now = time.time()
        await self.execute('UPDATE hollowlight_events SET starts_at=?,ends_at=?,redeem_until=?',
                           (now - 30 * 86400, now - 16 * 86400, now - 9 * 86400))

    async def view(self, page='carnival', **kwargs):
        view = JournalView(42, await journal_state.snapshot(42), page=page, **kwargs)
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.art_enabled = False
        view.render()
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    def button(self, view, label):
        return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label.startswith(label))

    def select(self, view, action):
        return next(item for item in view.walk_children() if isinstance(item, discord.ui.Select)
                    and item.custom_id == view.prefix + ':' + action)

    def input(self, view, *, value=None, uid=42, input_id=None):
        click = component(uid=uid)
        self.inputs += 1
        click.id = self.inputs if input_id is None else input_id
        click.message = view.message
        click.edit_original_response.return_value = view.message
        if value is not None:
            click.data = {'values': [value]}
        return click

    async def click(self, view, control, *, value=None, uid=42, input_id=None):
        click = self.input(view, value=value, uid=uid, input_id=input_id)
        await control.callback(click)
        return click

    async def offer(self, view, offer_id, *, review=False):
        await self.click(view, self.select(view, 'carnival_offer'), value=offer_id)
        if review:
            await self.click(view, self.button(view, 'Review purchase'))

    def assert_valid(self, view):
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        controls = [item for item in view.walk_children() if isinstance(item, (discord.ui.Button, discord.ui.Select))]
        self.assertEqual(len(controls), len({item.custom_id for item in controls}))
        self.assertTrue(all(len(item.custom_id) <= 100 for item in controls))
        for item in controls:
            if isinstance(item, discord.ui.Select):
                self.assertTrue(1 <= len(item.options) <= 25)
        self.assertTrue(view.to_components())

    async def test_dropdown_is_free_preserves_saved_team_and_normal_destination(self):
        await storage.update_player(42, level=8)
        reserve = await self.beast('goblin')
        from utils import adventure_party
        await adventure_party.set_slot(42, reserve, 1)
        await journal_state.save_explore_default(42, 'ember')
        view = await self.view('adventure', realm='ember')
        options = {option.value for option in view.realm_select.options}
        self.assertIn('hollowlight', options)
        before = await adventure_party.assignments(42)
        await self.click(view, view.realm_select, value='hollowlight')
        self.assertTrue(view.carnival_selected)
        self.assertEqual(view.realm, 'ember')
        self.assertEqual(view.art_realm, 'hollowlight')
        self.assertTrue(self.button(view, 'Save destination').disabled)
        self.assertEqual(view.state.explore_default, 'ember')
        self.assertEqual(await adventure_party.assignments(42), before)
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_requests'), [])
        await view.handle(self.input(view), 'save_destination:ember')
        self.assertIn('current selection page', self.text(view))
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'ember')
        await self.click(view, view.realm_select, value='woods')
        self.assertFalse(view.carnival_selected)
        self.assertFalse(self.button(view, 'Save destination').disabled)
        self.assert_valid(view)

    async def test_slash_command_previews_carnival_without_spending_and_autocomplete_closes(self):
        command = interaction()
        command.channel.id = 999
        command.followup.send.return_value = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        original = JournalView.publish

        async def native(view, current, **kwargs):
            view.art_enabled = False
            view.render()
            return await original(view, current, **kwargs)

        with patch.object(JournalView, 'publish', new=native):
            view = await Hatch.explore.callback(Hatch(None), command, 'hollowlight')
        self.addCleanup(view.stop)
        self.assertTrue(view.carnival_selected)
        self.assertEqual(view.realm, 'woods')
        self.assertIsNone(view.trip)
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        choices = await Hatch.explore_destinations(Hatch(None), command, 'carnival')
        self.assertEqual([choice.value for choice in choices], ['hollowlight'])
        await self.close_event()
        self.assertEqual(await Hatch.explore_destinations(Hatch(None), command, 'carnival'), [])

    async def test_confirmed_departure_uses_one_charge_and_carnival_art_with_normal_underlay(self):
        view = await self.view('adventure')
        await self.click(view, view.realm_select, value='hollowlight')
        await self.click(view, self.button(view, 'Start adventure'))
        trip = await expeditions.current(42)
        self.assertIn(trip['realm'], journal_state.BIOMES)
        self.assertEqual(trip['payload']['event_id'], hollowlight.EVENT_ID)
        self.assertEqual(trip['payload']['visual_realm'], 'hollowlight')
        self.assertEqual(view.journey_presentation.realm, 'hollowlight')
        self.assertEqual(view.art_realm, 'hollowlight')
        self.assertEqual(view.header.name, 'journal-hollowlight.png')
        self.assertIn('Hollowlight Carnival', self.text(view))
        self.assertEqual((await expeditions.energy(42)).charges, 0)
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assert_valid(view)

    async def test_gift_claim_is_owner_guarded_acknowledged_and_duplicate_safe(self):
        view = await self.view()
        button = self.button(view, 'Claim welcome gift')
        foreign = await self.click(view, button, uid=43)
        foreign.response.send_message.assert_awaited_once()
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])
        started, release = asyncio.Event(), asyncio.Event()
        first = self.input(view)
        original = hollowlight.claim_gift

        async def blocked(*args, **kwargs):
            first.response.defer.assert_awaited_once()
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        with patch.object(hollowlight, 'claim_gift', side_effect=blocked):
            task = asyncio.create_task(button.callback(first))
            try:
                await asyncio.wait_for(started.wait(), 2)
                duplicate = await self.click(view, button)
                self.assertIn('updating', duplicate.followup.send.call_args.args[0])
            finally:
                release.set()
                await task
        await self.click(view, button)
        self.assertEqual(len(await self.rows('SELECT * FROM hollowlight_gifts')), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM hollowlight_eggs')), 1)
        self.assertEqual(view.state.carnival['embers'], 10)
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertIn('already claimed', self.text(view))
        self.assertEqual(sum(row['quantity'] for row in await storage.get_inventory(42)), 4)
        self.assert_valid(view)

    async def test_offer_selection_review_and_cancel_never_spend_or_auto_hatch(self):
        await self.funds()
        view = await self.view()
        self.assertIn(f"Shop until <t:{int(view.state.carnival['redeem_until'])}:f>", self.text(view))
        await self.offer(view, 'ghost_costume_2026', review=True)
        confirm = self.button(view, 'Confirm')
        self.assertIn(':carnival_buy:ghost_costume_2026:30:', confirm.custom_id)
        self.assertEqual(view.state.carnival['embers'], 200)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_purchases'), [])
        await self.click(view, self.button(view, 'Cancel'))
        self.assertIsNone(view.carnival_confirmation)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_requests'), [])
        await self.offer(view, 'veilmoth_egg')
        self.assertTrue(self.button(view, 'Review purchase').disabled)
        self.assertIn('Trainer level 30 required', self.text(view))
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_eggs'), [])
        self.assert_valid(view)

    async def test_purchase_rejects_old_selection_and_changed_quote_then_commits_once(self):
        await self.funds()
        view = await self.view()
        await self.offer(view, 'patchkin_egg', review=True)
        old = self.button(view, 'Confirm')
        await self.offer(view, 'wickwing_egg')
        await self.click(view, old)
        self.assertIn('choice or price changed', self.text(view))
        self.assertEqual(view.state.carnival['embers'], 200)
        await self.offer(view, 'patchkin_egg', review=True)
        old = self.button(view, 'Confirm')
        changed = deepcopy(hollowlight.config())
        next(item for item in changed['offers'] if item['id'] == 'patchkin_egg')['cost'] = 31
        with patch.object(hollowlight, '_config', return_value=changed):
            await self.click(view, old)
        self.assertIn('choice or price changed', self.text(view))
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_purchases'), [])
        await self.offer(view, 'patchkin_egg', review=True)
        confirm = self.button(view, 'Confirm')
        await self.click(view, confirm)
        await self.click(view, confirm)
        self.assertEqual(view.state.carnival['embers'], 170)
        self.assertEqual(len(await self.rows('SELECT * FROM hollowlight_purchases')), 1)
        eggs = await self.rows('SELECT * FROM hollowlight_eggs')
        self.assertEqual(len(eggs), 1)
        self.assertFalse(eggs[0]['hatched'])
        receipt = (await self.rows("SELECT result FROM hollowlight_requests WHERE operation='redeem'"))[0]
        self.assertEqual(json.loads(receipt['result'])['egg']['id'], eggs[0]['id'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assert_valid(view)

    async def test_transaction_rechecks_quote_after_ui_snapshot_before_deduction(self):
        await self.funds()
        view = await self.view()
        await self.offer(view, 'patchkin_egg', review=True)
        original = hollowlight.redeem
        changed = deepcopy(hollowlight.config())
        next(item for item in changed['offers'] if item['id'] == 'patchkin_egg')['cost'] = 31

        async def change_at_writer(*args, **kwargs):
            with patch.object(hollowlight, '_config', return_value=changed):
                return await original(*args, **kwargs)

        with patch.object(hollowlight, 'redeem', side_effect=change_at_writer):
            await self.click(view, self.button(view, 'Confirm'))
        self.assertIn('price changed', self.text(view))
        self.assertEqual(view.state.carnival['embers'], 200)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_purchases'), [])
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_eggs'), [])

    async def test_egg_selection_is_free_old_button_rejected_and_closed_hatch_has_receipt(self):
        gift = await hollowlight.claim_gift(42, request_key='setup-gift')
        await self.funds()
        bought = await hollowlight.redeem(42, 'patchkin_egg', request_key='setup-egg')
        view = await self.view()
        await self.click(view, self.select(view, 'carnival_egg'), value=gift['egg']['id'])
        old = self.button(view, 'Hatch ')
        await self.click(view, self.select(view, 'carnival_egg'), value=bought['egg']['id'])
        await self.click(view, old)
        self.assertIn('egg choice changed', self.text(view))
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        await self.close_event()
        await self.click(view, self.button(view, 'Hatch '))
        self.assertIn('Patchkin hatched!', self.text(view))
        self.assertEqual(view.state.carnival['phase'], 'ended')
        receipt = json.loads((await self.rows("SELECT result FROM hollowlight_requests WHERE operation='hatch'"))[0]['result'])
        self.assertEqual(receipt['egg_id'], bought['egg']['id'])
        companion = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (receipt['beast_row_id'],)))[0]
        self.assertEqual(companion['beast_id'], 'patchkin')
        self.assertEqual(companion['level'], bought['egg']['level'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 2)
        self.assert_valid(view)

    async def test_closed_panel_keeps_eggs_costumes_title_and_prevents_new_spending(self):
        await self.funds()
        await hollowlight.redeem(42, 'ghost_costume_2026', request_key='ghost')
        await hollowlight.redeem(42, 'hollowlight_title_2026', request_key='title')
        await hollowlight.redeem(42, 'wickwing_egg', request_key='egg')
        await self.close_event()
        view = await self.view('adventure')
        self.assertNotIn('hollowlight', {option.value for option in view.realm_select.options})
        await self.click(view, self.button(view, 'Carnival'))
        self.assertIn('shop are closed', self.text(view))
        self.assertIn('wickwing_egg', {offer['id'] for offer in view.state.carnival['offers']})
        self.assertTrue(self.button(view, 'Dress companions').custom_id)
        self.assertTrue(self.button(view, 'Wear 2026 title').custom_id)
        self.assertEqual(len(self.select(view, 'carnival_egg').options), 1)
        self.assertFalse(any(isinstance(item, discord.ui.Select) and ':carnival_offer' in item.custom_id for item in view.walk_children()))
        before = view.state.carnival['embers']
        await view.handle(self.input(view), 'carnival_gift')
        self.assertIn('closed', self.text(view))
        self.assertEqual(view.state.carnival['embers'], before)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])
        await self.click(view, self.button(view, 'Back to Adventure'))
        view.carnival_selected = True
        await view.handle(self.input(view), 'begin')
        self.assertIn('closed', self.text(view))
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assert_valid(view)

    async def test_owned_title_can_be_equipped_and_removed_after_closure(self):
        await self.funds()
        await hollowlight.redeem(42, 'hollowlight_title_2026', request_key='title')
        await storage.update_player(42, title='Keeper of the Hollow')
        await self.close_event()
        view = await self.view()
        await self.click(view, self.button(view, 'Wear 2026 title'))
        self.assertEqual((await storage.get_player(42))['title'], hollowlight.TITLE)
        await self.click(view, self.button(view, 'Remove title'))
        self.assertEqual((await storage.get_player(42))['title'], 'Keeper of the Hollow')
        self.assert_valid(view)

    async def test_stale_or_wrong_page_controls_cannot_claim_or_hatch(self):
        view = await self.view()
        gift = self.button(view, 'Claim welcome gift')
        view.last_input = 500000
        stale = await self.click(view, gift, input_id=499999)
        self.assertIn('older page', stale.followup.send.call_args.args[0])
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])
        view.last_input = 0
        await self.click(view, self.button(view, 'Back to Adventure'))
        await self.click(view, gift)
        self.assertIn('current Carnival page', self.text(view))
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])
        view.closed = True
        closed = await self.click(view, gift)
        closed.response.send_message.assert_awaited_once()

    async def test_previous_start_button_cannot_depart_from_a_reward_panel(self):
        view = await self.view('adventure')
        start = self.button(view, 'Start adventure')
        await self.click(view, self.button(view, 'Carnival'))
        await self.click(view, start)
        self.assertIn('selection page before setting out', self.text(view))
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])

    async def test_full_owned_panel_stays_within_native_discord_limits(self):
        await storage.update_player(42, level=30)
        await self.funds(1000)
        await hollowlight.claim_gift(42, request_key='gift')
        for offer in hollowlight.config()['offers']:
            await hollowlight.redeem(42, offer['id'], request_key='all-' + offer['id'])
        view = await self.view()
        await self.offer(view, 'ghost_costume_2026')
        await self.click(view, self.select(view, 'carnival_egg'), value=view.state.carnival['eggs'][0]['id'])
        self.assertEqual(len(self.select(view, 'carnival_egg').options), 4)
        self.assert_valid(view)
        self.assertNotIn('carnival', {option.value for option in view.page_menu.options})
        self.assertEqual(view.header.name, 'journal-hollowlight.png')

    async def test_history_labels_event_underlay_and_reports_embers(self):
        payload = dict(event_id=hollowlight.EVENT_ID, visual_realm='hollowlight', notes=['Returned from the lantern midway.'],
                       gold=30, exp_granted=5, hollowlight_embers=10, material_granted=0)
        await self.execute("INSERT INTO expeditions(id,user_id,realm,status,payload,created_at,updated_at) VALUES('carnival-log',42,'woods','complete',?,1,2)",
                           (json.dumps(payload),))
        view = await self.view('battle_log')
        await journal_log.load(view)
        view.render()
        self.assertIn('Hollowlight Carnival', self.text(view))
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select) and item.custom_id.endswith(':log_entry'))
        await self.click(view, picker, value='carnival-log')
        self.assertIn('10 Lantern Embers', self.text(view))
        self.assertIn('Battle log · 🎃 The Hollowlight Carnival', self.text(view))
        self.assert_valid(view)

    async def test_legacy_rarity_selector_excludes_event_species_even_in_fallback(self):
        for species in ('patchkin', 'wickwing', 'veilmoth'):
            seasonal = storage.get_beast_data(species)
            normal = dict(id='permanent', rarity=seasonal['rarity'], catch_rate=1)
            roster = {normal['id']: normal, seasonal['id']: seasonal}
            self.assertEqual(get_beast_by_rarity(seasonal['rarity'], roster)['id'], 'permanent')
            self.assertEqual(get_beast_by_rarity(seasonal['rarity'], roster, exclude_ids=['permanent'])['id'], 'permanent')
            self.assertIsNone(get_beast_by_rarity(seasonal['rarity'], {species: seasonal}))

    async def test_permanent_collection_completion_never_requires_seasonal_defs(self):
        collection = 'Cosmic Creators'
        normal = {**storage.get_beast_data('twine'), 'collection': collection}
        seasonal = {**storage.get_beast_data('veilmoth'), 'collection': collection}
        command = interaction()
        roster = {normal['id']: normal, seasonal['id']: seasonal}
        await check_collection_completion(command, seasonal, roster)
        self.assertEqual(await self.rows("SELECT * FROM achievements WHERE achievement_id='collection_cosmic_creators'"), [])
        await check_collection_completion(command, normal, roster)
        self.assertEqual(len(await self.rows("SELECT * FROM achievements WHERE achievement_id='collection_cosmic_creators'")), 1)
        command.channel.send.assert_awaited_once()
