"""Journal navigation keeps the displayed individual's equipped appearance."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import json

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import gear_art, journal_state
from utils.journal import JournalView


async def preview(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'png')


class JournalAppearance(DatabaseCase):
    async def equipped(self, species, armor_id, *, starter=False):
        row_id = await self.beast(species, starter=starter)
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)',
                           (row_id, armor_id))
        return row_id

    async def view(self, page):
        view = JournalView(42, await journal_state.snapshot(42), page=page)
        view.message = SimpleNamespace(attachments=[], edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    async def test_home_companions_and_awakening_use_the_individuals_armor(self):
        await self.equipped('twine', 'gnome_iron_plating', starter=True)
        for page in ('home', 'companions'):
            view = await self.view(page)
            click = interaction()
            with patch.object(gear_art, 'render_portrait', side_effect=preview) as render:
                await view.publish(click)
            render.assert_awaited_once_with('twine', 'gnome_iron_plating')
            filename = gear_art.portrait_filename('twine', 'gnome_iron_plating')
            self.assertIn('attachment://' + filename, json.dumps(view.to_components()))
            self.assertIn(filename, [f.filename for f in click.edit_original_response.call_args.kwargs['attachments']])
            if page == 'companions':
                view.reviewing = True
                view.render()
                with patch.object(gear_art, 'render_portrait', side_effect=preview) as render:
                    await view.publish(interaction())
                self.assertIn(('timestitch', 'gnome_iron_plating'), [c.args for c in render.await_args_list])

    async def test_same_species_switch_and_external_reequip_replace_old_attachment(self):
        first = await self.equipped('twine', 'gnome_iron_plating', starter=True)
        second = await self.equipped('twine', 'kelpie_scale_mail')
        view = await self.view('companions')
        old_filename = gear_art.portrait_filename('twine', 'gnome_iron_plating')
        view.message.attachments = [SimpleNamespace(filename=view.header.name), SimpleNamespace(filename=old_filename)]
        click = component()
        click.data = {'values': [str(second)]}
        with patch.object(gear_art, 'render_portrait', side_effect=preview) as render:
            await view.handle(click, 'companion')
        render.assert_awaited_once_with('twine', 'kelpie_scale_mail')
        files = click.edit_original_response.call_args.kwargs['attachments']
        self.assertNotIn(old_filename, [f.filename for f in files])
        current = gear_art.portrait_filename('twine', 'kelpie_scale_mail')
        view.message.attachments = [SimpleNamespace(filename=view.header.name), SimpleNamespace(filename=current)]
        await self.execute("UPDATE player_equipment SET equipment_id='mossbark_wrapping' WHERE beast_row_id=?", (second,))
        refresh = component()
        with patch.object(gear_art, 'render_portrait', side_effect=preview) as render:
            await view.handle(refresh, 'refresh')
        render.assert_awaited_once_with('twine', 'mossbark_wrapping')
        self.assertNotIn(current, [f.filename for f in refresh.edit_original_response.call_args.kwargs['attachments']])

    async def test_upload_reuses_matching_appearance_and_renderer_busy_keeps_controls(self):
        await self.equipped('barkley', 'dragon_lord_aegis', starter=True)
        view = await self.view('companions')
        filename = gear_art.portrait_filename('barkley', 'dragon_lord_aegis')
        previous = [SimpleNamespace(filename=view.header.name), SimpleNamespace(filename=filename)]
        view.message.attachments = previous
        click = interaction()
        with patch.object(gear_art, 'render_portrait', side_effect=preview) as render:
            await view.publish(click)
        render.assert_not_awaited()
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], previous)
        view.message.attachments = []
        with patch.object(gear_art, 'render_portrait', return_value=None):
            await view.publish(interaction())
        self.assertFalse(view.art_enabled)
        self.assertIn('Make active', json.dumps(view.to_components()))
        self.assertNotIn('attachment://', json.dumps(view.to_components()))
