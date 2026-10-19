"""Saved destinations select a future trip without spending or changing a team."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.dev import Dev
from cogs.hatch import Hatch
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import adventure_party, db as storage, expeditions, journal_state


class ExplorePreferences(DatabaseCase):
    async def ready(self):
        await storage.update_player(42, level=6)
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('slime')
        await adventure_party.set_slot(42, reserve, 1)
        return lead, reserve

    async def open(self, destination=None):
        command = interaction()
        command.channel.id = 999
        command.followup.send.return_value = SimpleNamespace(id=123, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        view = await Hatch.explore.callback(Hatch(None), command, destination)
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    async def click(self, view, action, *, value=None, uid=42):
        click = component(uid=uid)
        click.message = view.message
        click.edit_original_response.return_value = view.message
        if value is not None:
            click.data = {'values': [value]}
        await view.handle(click, action)
        return click

    async def test_saved_destination_survives_restart_without_starting_or_changing_team(self):
        lead, reserve = await self.ready()
        player_before = await storage.get_player(42)
        before = await self.rows('SELECT * FROM adventure_team')
        view = await self.open('ember')
        save = next(item for item in view.walk_children()
                    if isinstance(item, discord.ui.Button) and item.label == 'Save destination')
        save_click = component()
        save_click.edit_original_response.return_value = view.message
        await save.callback(save_click)
        save_click.response.defer.assert_awaited_once()
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'ember')
        self.assertIn('Default destination saved', self.text(view))
        await storage.init_db()
        reopened = await self.open()
        self.assertEqual(reopened.realm, 'ember')
        self.assertIn('Saved destination', self.text(reopened))
        self.assertIsNone(reopened.trip)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertEqual(await self.rows('SELECT * FROM adventure_team'), before)
        self.assertEqual([row['id'] for row in await adventure_party.rows(42)], [lead, reserve])
        player_after = await storage.get_player(42)
        self.assertEqual((player_after['gold'], player_after['exp']), (player_before['gold'], player_before['exp']))

    async def test_explicit_override_changes_only_this_opening_selection(self):
        await self.ready()
        await journal_state.save_explore_default(42, 'ember')
        view = await self.open('woods_deep')
        self.assertEqual(view.realm, 'woods_deep')
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'ember')
        self.assertEqual((await self.open()).realm, 'ember')
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_clear_default_preserves_current_selection_and_future_open_uses_woods(self):
        await self.ready()
        await journal_state.save_explore_default(42, 'ember')
        view = await self.open()
        await self.click(view, 'clear_destination')
        self.assertEqual(view.realm, 'ember')
        self.assertIsNone((await journal_state.snapshot(42)).explore_default)
        self.assertEqual((await self.open()).realm, 'woods')
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_save_rechecks_destination_and_level_without_overwriting_existing_default(self):
        await self.ready()
        await journal_state.save_explore_default(42, 'woods')
        for realm in ('deleted_realm', 'loom'):
            with self.assertRaises(ValueError):
                await journal_state.save_explore_default(42, realm)
            self.assertEqual((await journal_state.snapshot(42)).explore_default, 'woods')
        with self.assertRaises(ValueError):
            await journal_state.save_explore_default(99, 'woods')
        locked = await self.open('loom')
        save = next(item for item in locked.walk_children()
                    if isinstance(item, discord.ui.Button) and item.label == 'Save destination')
        self.assertTrue(save.disabled)
        await self.click(locked, 'save_destination:loom')
        self.assertIn('Reach trainer level 30', self.text(locked))
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'woods')
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_invalid_or_newly_locked_stored_destination_has_safe_selection(self):
        await self.ready()
        for saved in ('deleted_realm', 'loom'):
            await self.execute('INSERT OR REPLACE INTO exploration_preferences VALUES(42,?)', (saved,))
            view = await self.open()
            self.assertEqual(view.realm, 'woods')
            self.assertIn('saved destination is unavailable', self.text(view))
            self.assertIsNone(view.trip)
            self.assertLessEqual(view.total_children_count, 40)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_saved_route_and_exact_party_override_a_changed_future_default(self):
        lead, reserve = await self.ready()
        trip = await expeditions.begin(42, 'woods')
        party_ids = list(trip['payload']['party_ids'])
        await journal_state.save_explore_default(42, 'ember')
        view = await self.open('woods_deep')
        self.assertEqual(view.trip['id'], trip['id'])
        self.assertEqual(view.trip['realm'], 'woods')
        self.assertEqual(view.trip['payload']['party_ids'], party_ids)
        self.assertEqual(party_ids, [lead, reserve])
        self.assertNotIn(view.realm_select, list(view.walk_children()))
        await self.click(view, 'save_destination:woods_deep')
        self.assertIn('current selection page', self.text(view))
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'ember')
        self.assertEqual((await expeditions.energy(42)).charges, 0)

    async def test_foreign_or_closed_page_cannot_save_or_clear_a_destination(self):
        await self.ready()
        view = await self.open('ember')
        foreign = await self.click(view, 'save_destination:ember', uid=99)
        foreign.response.send_message.assert_awaited_once()
        self.assertIsNone((await journal_state.snapshot(42)).explore_default)
        await journal_state.save_explore_default(42, 'ember')
        view.closed = True
        closed = await self.click(view, 'clear_destination')
        closed.response.send_message.assert_awaited_once()
        self.assertEqual((await journal_state.snapshot(42)).explore_default, 'ember')

    async def test_save_button_from_an_older_destination_cannot_save_a_different_page(self):
        await self.ready()
        view = await self.open('ember')
        await self.click(view, 'realm', value='woods_deep')
        await self.click(view, 'save_destination:ember')
        self.assertIn('current selection page', self.text(view))
        self.assertIsNone((await journal_state.snapshot(42)).explore_default)
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_owner_reset_clears_only_the_target_destination_preference(self):
        await self.ready()
        await storage.get_or_create_player(99, 'Other trainer')
        await journal_state.save_explore_default(42, 'ember')
        await journal_state.save_explore_default(99, 'woods')
        opened = interaction(1)
        await Dev.reset.callback(Dev(None), opened, SimpleNamespace(id=42, display_name='Target'))
        view = opened.response.send_message.call_args.kwargs['view']
        self.addCleanup(view.stop)
        await view.children[0].callback(interaction(1))
        self.assertEqual(await self.rows('SELECT * FROM exploration_preferences WHERE user_id=42'), [])
        self.assertEqual(await self.rows('SELECT realm FROM exploration_preferences WHERE user_id=99'), [{'realm': 'woods'}])
