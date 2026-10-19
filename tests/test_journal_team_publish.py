"""The native Team page and its uploaded artwork must describe the same roster."""
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from utils import adventure_party, db as storage, expeditions, gear_art, journal_state
from utils.journal import JournalView
from utils.journal_team_art import TeamArtwork


async def portrait(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'portrait')


class TeamPagePublication(DatabaseCase):
    async def ready(self):
        self.ids = []
        self.appearances = [('twine', 'gnome_iron_plating'), ('gloop', 'mossbark_wrapping'), ('fairy', 'kelpie_scale_mail')]
        for species, armor in self.appearances:
            row_id = await self.beast(species, starter=not self.ids)
            self.ids.append(row_id)
            await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)', (row_id, armor))
        await adventure_party.set_slot(42, self.ids[1], 1)
        await adventure_party.set_slot(42, self.ids[2], 2)

    async def view(self):
        view = JournalView(42, await journal_state.snapshot(42), page='team', realm='woods')
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    def attachment_names(self, values):
        return {value.filename for value in values}

    def referenced_names(self, view):
        return set(re.findall(r'attachment://([^"\s]+)', json.dumps(view.to_components())))

    async def test_opening_uploads_only_the_actual_equipped_team_stage(self):
        await self.ready()
        view, click = await self.view(), interaction()
        stage = TeamArtwork('exploration-team-review.png', b'stage')
        with patch('utils.journal_team_art.render_team', return_value=stage) as render, patch.object(gear_art, 'render_portrait', side_effect=portrait) as thumbnails:
            await view.publish(click, opening=True)
        members, realm = render.await_args.args
        self.assertEqual([(row['beast_id'], row['armor_id']) for row in members], self.appearances)
        self.assertEqual(realm, 'woods')
        thumbnails.assert_not_awaited()
        sent = click.followup.send.call_args.kwargs['files']
        self.assertEqual(self.attachment_names(sent), {stage.name})
        self.assertEqual(self.referenced_names(view), {stage.name})
        self.assertTrue(all(file.fp.closed for file in sent))

    async def test_refresh_reuses_matching_stage_without_a_banner_or_portrait_upload(self):
        await self.ready()
        view, click = await self.view(), interaction()
        stage = TeamArtwork('exploration-team-review.png', b'stage')
        previous = SimpleNamespace(filename=stage.name)
        view.message.attachments = [previous, SimpleNamespace(filename='obsolete.png')]
        with patch('utils.journal_team_art.render_team', return_value=stage), patch.object(gear_art, 'render_portrait', side_effect=portrait) as thumbnails:
            await view.publish(click)
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], [previous])
        self.assertEqual(self.referenced_names(view), {stage.name})
        thumbnails.assert_not_awaited()

    async def test_busy_stage_renderer_falls_back_to_matching_equipped_portraits(self):
        await self.ready()
        view, click = await self.view(), interaction()
        view.team_art = TeamArtwork('exploration-team-old.png', b'old stage')
        view.message.attachments = [SimpleNamespace(filename=view.team_art.name)]
        view.render()
        with patch('utils.journal_team_art.render_team', return_value=None), patch.object(gear_art, 'render_portrait', side_effect=portrait) as thumbnails:
            await view.publish(click)
        self.assertIsNone(view.team_art)
        self.assertTrue(view.art_enabled)
        self.assertEqual({call.args for call in thumbnails.await_args_list}, set(self.appearances))
        sent = click.edit_original_response.call_args.kwargs['attachments']
        expected = {view.header.name} | {gear_art.portrait_filename(*row) for row in self.appearances}
        self.assertEqual(self.attachment_names(sent), expected)
        self.assertEqual(self.referenced_names(view), expected)

    async def test_rejected_team_image_keeps_native_roles_and_actions_without_attachment_references(self):
        await self.ready()
        view, click = await self.view(), interaction()
        stage = TeamArtwork('exploration-team-review.png', b'stage')
        rejected = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Missing Attach Files')
        click.edit_original_response.side_effect = [rejected, SimpleNamespace(id=123, attachments=[], edit=AsyncMock())]
        with patch('utils.journal_team_art.render_team', return_value=stage), self.assertLogs('chibibeasts.journal', level='WARNING'):
            await view.publish(click)
        self.assertEqual(click.edit_original_response.await_count, 2)
        self.assertFalse(view.art_enabled)
        self.assertEqual(self.referenced_names(view), set())
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], [])
        text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertTrue(all(role in text for role in ('Lead', 'Reserve 1', 'Reserve 2')))
        self.assertEqual(len([item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label.startswith('Edit ')]), 3)

    async def test_active_trip_realm_drives_stage_and_leaving_team_replaces_stage_attachment(self):
        await self.ready()
        await storage.update_player(42, level=6)
        await expeditions.begin(42, 'ember')
        view, click = await self.view(), interaction()
        stage = TeamArtwork('exploration-team-ember-review.png', b'stage')
        with patch('utils.journal_team_art.render_team', return_value=stage) as render:
            await view.publish(click)
        self.assertEqual(render.await_args.args[1], 'ember')
        self.assertEqual(self.referenced_names(view), {stage.name})
        view.message = SimpleNamespace(id=123, attachments=[SimpleNamespace(filename=stage.name)], edit=AsyncMock())
        view.page = 'adventure'
        view.render()
        with patch.object(gear_art, 'render_portrait', side_effect=portrait):
            await view.publish(click)
        names = self.attachment_names(click.edit_original_response.call_args.kwargs['attachments'])
        self.assertIn('journal-ember.png', names)
        self.assertNotIn(stage.name, names)
        self.assertEqual(self.referenced_names(view), names)
