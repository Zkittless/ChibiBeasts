"""Exercise role editing, stale choices and full-size Discord team pages."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import adventure_party, db as storage, expeditions, journal_state, journal_team
from utils.journal import JournalView
from utils.journal_team_art import render_team
from utils.transfers import create_listing


class ExplorationTeamPanel(DatabaseCase):
    async def ready(self):
        ids = [await self.beast('twine', starter=True), await self.beast('gloop'), await self.beast('fairy')]
        await adventure_party.set_slot(42, ids[1], 1)
        await adventure_party.set_slot(42, ids[2], 2)
        return ids

    async def view(self):
        # This also verifies that the helper uses the real journal's native
        # components, unique custom IDs, portraits and hard message limits.
        view = JournalView(42, await journal_state.snapshot(42), page='team')
        self.addCleanup(view.stop)
        return view

    def render(self, view):
        view.render()

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    def assert_valid(self, view):
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        controls = [item for item in view.walk_children() if isinstance(item, (discord.ui.Button, discord.ui.Select))]
        self.assertEqual(len(controls), len({item.custom_id for item in controls}))
        for item in controls:
            if isinstance(item, discord.ui.Select):
                self.assertTrue(1 <= len(item.options) <= 25)
                self.assertTrue(all(len(option.value) <= 100 and len(option.label) <= 100 for option in item.options))

    async def test_overview_has_distinct_roles_portraits_and_one_editor_per_slot(self):
        await self.ready()
        view = await self.view()
        text = self.text(view)
        for role in ('Lead', 'Reserve 1', 'Reserve 2'):
            self.assertIn(role + ' ·', text)
        self.assertEqual(len(view.portraits), 3)
        edits = [item for item in view.walk_children() if isinstance(item, discord.ui.Button) and ':team_role:' in item.custom_id]
        self.assertEqual(len(edits), 3)
        self.assertNotIn('Editing ', text)
        self.assert_valid(view)

    async def test_actual_team_stage_is_the_gallery_attachment_above_compact_role_details(self):
        await self.ready()
        view = await self.view()
        view.team_art = await render_team([row for _, row in journal_team.roster(view)], view.art_realm)
        self.assertIsNotNone(view.team_art)
        self.render(view)
        items = view.to_components()[0]['components']
        galleries = [item for item in items if item['type'] == 12]
        self.assertEqual(len(galleries), 1)
        self.assertEqual(galleries[0]['items'][0]['media']['url'], 'attachment://' + view.team_art.name)
        self.assertEqual(view.portraits, {})
        self.assertFalse(any(item['type'] == 9 for item in items))
        gallery_index = items.index(galleries[0])
        lead_index = next(index for index, item in enumerate(items) if item['type'] == 10 and item['content'].startswith('**Lead ·'))
        self.assertLess(gallery_index, lead_index)
        self.assert_valid(view)

    async def test_picker_confirms_only_the_selected_slot_and_can_move_a_reserve(self):
        ids = await self.ready()
        other = await self.beast('barkley')
        view = await self.view()
        journal_team.select_role(view, '2')
        journal_team.select_candidate(view, f'2:{other}')
        role, row_id = journal_team.assignment(view, f'2:{other}')
        await adventure_party.set_slot(42, row_id, int(role))
        self.assertEqual(await adventure_party.assignments(42), {1: ids[1], 2: other})
        self.assertEqual((await storage.get_active_beast(42))['id'], ids[0])
        view.state = await journal_state.snapshot(42)
        journal_team.select_candidate(view, f'2:{ids[1]}')
        self.render(view)
        self.assertIn('moves from reserve 1 into the selected slot', self.text(view))
        role, row_id = journal_team.assignment(view, f'2:{ids[1]}')
        await adventure_party.set_slot(42, row_id, int(role))
        self.assertEqual(await adventure_party.assignments(42), {2: ids[1]})

    async def test_lead_promotion_explains_and_preserves_the_displaced_reserve_slot(self):
        ids = await self.ready()
        view = await self.view()
        journal_team.select_role(view, 'lead')
        journal_team.select_candidate(view, f'lead:{ids[2]}')
        self.render(view)
        self.assertIn('moves your current lead into reserve 2', self.text(view))
        role, row_id = journal_team.assignment(view, f'lead:{ids[2]}')
        self.assertEqual(role, 'lead')
        row = next(row for row in view.state.workshop.companions if row['id'] == row_id)
        await storage.set_active_companion(42, row['player_number'], expected_row_id=row_id)
        self.assertEqual((await storage.get_active_beast(42))['id'], ids[2])
        self.assertEqual(await adventure_party.assignments(42), {1: ids[1], 2: ids[0]})

    async def test_stale_role_candidate_and_removed_ownership_cannot_be_confirmed(self):
        ids = await self.ready()
        view = await self.view()
        journal_team.select_role(view, '1')
        with self.assertRaisesRegex(ValueError, 'another team slot'):
            journal_team.select_candidate(view, f'2:{ids[2]}')
        journal_team.select_candidate(view, f'1:{ids[2]}')
        with self.assertRaisesRegex(ValueError, 'choice changed'):
            journal_team.assignment(view, f'1:{ids[1]}')
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (ids[2],))
        view.state = await journal_state.snapshot(42)
        with self.assertRaisesRegex(ValueError, 'left your collection'):
            journal_team.assignment(view, f'1:{ids[2]}')
        with self.assertRaisesRegex(ValueError, 'assignment changed'):
            journal_team.removal(view, f'1:{ids[2]}')

    async def test_saved_trip_uses_frozen_ids_and_exposes_only_resume(self):
        ids = await self.ready()
        await expeditions.begin(42, 'woods')
        view = await self.view()
        # Presentation cannot substitute a new loadout for the saved roster.
        view.state.team_slots = {1: ids[2]}
        self.render(view)
        self.assertEqual([row['id'] for _, row in journal_team.roster(view)], ids)
        self.assertIn('Saved for this expedition', self.text(view))
        self.assertFalse(any(isinstance(item, discord.ui.Select) and item is not view.page_menu for item in view.walk_children()))
        self.assertFalse(any(isinstance(item, discord.ui.Button) and ':team_role:' in item.custom_id for item in view.walk_children()))
        for callback in (lambda: journal_team.select_role(view, '1'),
                         lambda: journal_team.select_candidate(view, f'1:{ids[2]}'),
                         lambda: journal_team.assignment(view, f'1:{ids[2]}'),
                         lambda: journal_team.removal(view, f'1:{ids[1]}')):
            with self.assertRaisesRegex(ValueError, 'saved expedition'):
                callback()
        self.assert_valid(view)

    async def test_recovering_and_listed_candidates_are_visible_but_unassignable(self):
        ids = await self.ready()
        listed = await self.beast('barkley')
        await create_listing(42, 4, 500)
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await self.execute('UPDATE player_beasts SET hp=0,knocked_out_until=? WHERE id=?', (until, ids[1]))
        view = await self.view()
        journal_team.select_role(view, '1')
        self.render(view)
        self.assertIn('Recovering · unavailable', self.text(view))
        # Recovery never prevents clearing an existing reserve assignment.
        self.assertEqual(journal_team.removal(view, f'1:{ids[1]}'), 1)
        for row_id in (ids[1], listed, ids[0]):
            journal_team.select_candidate(view, f'1:{row_id}')
            with self.assertRaisesRegex(ValueError, 'healthy, unlisted'):
                journal_team.assignment(view, f'1:{row_id}')
        self.render(view)
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select) and item is not view.page_menu)
        self.assertTrue(any('Listed' in option.description for option in picker.options))

    async def test_large_roster_paginates_without_changing_other_slots_or_message_limits(self):
        ids = await self.ready()
        for index in range(26):
            row_id = await self.beast('slime')
            await self.execute('UPDATE player_beasts SET nickname=? WHERE id=?', ('x' * 80 + str(index), row_id))
        view = await self.view()
        journal_team.select_role(view, '2')
        self.render(view)
        self.assert_valid(view)
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select) and item is not view.page_menu)
        self.assertEqual(len(picker.options), 25)
        journal_team.page_candidates(view, 1)
        self.render(view)
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select) and item is not view.page_menu)
        self.assertEqual(len(picker.options), 4)
        self.assertIn('Collection page 2/2', self.text(view))
        self.assertEqual(view.state.team_slots, {1: ids[1], 2: ids[2]})
        self.assert_valid(view)
        view.art_enabled = False
        self.render(view)
        self.assertEqual(len(view.portraits), 0)
        self.assert_valid(view)

    async def test_empty_team_and_empty_reserve_have_clear_next_actions(self):
        view = await self.view()
        self.assertIn('Lead · Empty', self.text(view))
        self.assertIn('Meet your starter', [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)])
        self.assert_valid(view)
        await self.beast('twine', starter=True)
        view = await self.view()
        journal_team.select_role(view, '2')
        self.render(view)
        clear = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == 'Clear slot')
        self.assertTrue(clear.disabled)
        assign = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == 'Assign reserve 2')
        self.assertTrue(assign.disabled)
        with self.assertRaisesRegex(ValueError, 'instead of removing'):
            journal_team.select_role(view, 'lead')
            journal_team.removal(view, f'lead:{view.team_candidate_id}')
        self.assert_valid(view)

    async def test_native_tab_editor_selection_and_confirmation_dispatch_to_the_exact_slot(self):
        ids = await self.ready()
        other = await self.beast('barkley')
        view = await self.view()
        message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.message, view.art_enabled = message, False

        async def click(control, value=None):
            interaction = component()
            interaction.message = message
            interaction.edit_original_response.return_value = message
            if value is not None:
                interaction.data = {'values': [value]}
            await control.callback(interaction)
            interaction.response.defer.assert_awaited_once()

        await click(view.page_menu, 'team')
        self.assertEqual(view.page, 'team')
        edit = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == 'Edit reserve 2')
        await click(edit)
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select) and item is not view.page_menu)
        await click(picker, f'2:{other}')
        confirm = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == 'Assign reserve 2')
        self.assertFalse(confirm.disabled)
        await click(confirm)
        self.assertEqual(await adventure_party.assignments(42), {1: ids[1], 2: other})
        self.assertIn('Reserve 2 assigned', self.text(view))
        self.assert_valid(view)

    async def test_a_trip_started_after_editing_blocks_a_previously_enabled_confirm(self):
        ids = await self.ready()
        other = await self.beast('barkley')
        view = await self.view()
        journal_team.select_role(view, '2')
        journal_team.select_candidate(view, f'2:{other}')
        self.render(view)
        confirm = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == 'Assign reserve 2')
        await expeditions.begin(42, 'woods')
        interaction = component()
        message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.message, view.art_enabled = message, False
        interaction.message = message
        interaction.edit_original_response.return_value = message
        await confirm.callback(interaction)
        self.assertEqual(await adventure_party.assignments(42), {1: ids[1], 2: ids[2]})
        self.assertIn('Return from your saved expedition', self.text(view))
        self.assertFalse(any(isinstance(item, discord.ui.Select) and item is not view.page_menu for item in view.walk_children()))
        self.assert_valid(view)
