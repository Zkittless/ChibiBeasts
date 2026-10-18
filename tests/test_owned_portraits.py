"""Owned portrait screens resolve live armor and acknowledge before image work."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.profile import Profile
from cogs.utilities import Utilities
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import gear_art


async def portrait(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'png')


class OwnedPortraits(DatabaseCase):
    async def armor(self, row_id, armor_id):
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)', (row_id, armor_id))

    async def test_lookup_excludes_unknown_runes_unbound_and_other_owners(self):
        row_id = await self.beast('twine', starter=True)
        for armor_id in ('bark_sigil', 'missing', 'kelpie_scale_mail', 'gnome_iron_plating'):
            await self.armor(row_id, armor_id)
        await self.armor(None, 'dragon_lord_aegis')
        other = await self.beast('gloop', uid=43)
        await self.armor(other, 'cinder_guard_vest')  # A corrupt cross-owner binding is ignored.
        self.assertEqual(await gear_art.equipped_armor_ids(42), {row_id: 'kelpie_scale_mail'})

    async def test_beast_browser_switches_same_species_and_refreshes_changed_gear(self):
        first = await self.beast('twine', starter=True)
        second = await self.beast('twine')
        await self.armor(first, 'gnome_iron_plating')
        await self.armor(second, 'kelpie_scale_mail')
        command = interaction()
        message = SimpleNamespace(attachments=[], edit=AsyncMock())
        command.followup.send.return_value = message
        with patch.object(gear_art, 'render_portrait', side_effect=portrait) as render:
            await Profile.beastinfo.callback(Profile(None), command, 1)
            command.response.defer.assert_awaited_once()
            render.assert_awaited_with('twine', 'gnome_iron_plating')
            view = command.followup.send.call_args.kwargs['view']
            self.addCleanup(view.stop)
            previous = gear_art.portrait_filename('twine', 'gnome_iron_plating')
            message.attachments = [SimpleNamespace(filename=previous)]
            click = component()
            click.edit_original_response.return_value = message
            await view.next_btn.callback(click)
            click.response.defer.assert_awaited_once()
            render.assert_awaited_with('twine', 'kelpie_scale_mail')
            self.assertNotIn(previous, [f.filename for f in click.edit_original_response.call_args.kwargs['attachments']])
            await self.execute("UPDATE player_equipment SET equipment_id='mossbark_wrapping' WHERE beast_row_id=?", (second,))
            again = component()
            again.edit_original_response.return_value = message
            await view.show(again)
            render.assert_awaited_with('twine', 'mossbark_wrapping')
            # Each update leaves a valid local attachment reference.
            payload = again.edit_original_response.call_args.kwargs
            self.assertEqual(payload['embed'].image.url, 'attachment://' + payload['attachments'][0].filename)

    async def test_play_and_evolution_display_the_equipped_individual(self):
        row_id = await self.beast('twine', starter=True)
        await self.armor(row_id, 'kelpie_scale_mail')
        with patch.object(gear_art, 'send_owned_portrait', new=AsyncMock()) as send:
            play = interaction()
            await Utilities.play.callback(Utilities(None), play)
            play.response.defer.assert_awaited_once()
            self.assertEqual(send.call_args.args[2:], ('twine', 'kelpie_scale_mail'))
            self.assertTrue(send.call_args.kwargs['thumbnail'])
            await self.execute('UPDATE player_beasts SET level=15,bond=30,happiness=80 WHERE id=?', (row_id,))
            evolve = interaction()
            await Utilities.evolve.callback(Utilities(None), evolve, 1)
            evolve.response.defer.assert_awaited_once()
            self.assertEqual(send.call_args.args[2:], ('timestitch', 'kelpie_scale_mail'))

    async def test_owned_upload_rejection_retries_text_and_closes_file(self):
        click = interaction()
        denied = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        click.followup.send.side_effect = [denied, None]
        embed = discord.Embed(title='Owned companion')
        with patch.object(gear_art, 'render_portrait', side_effect=portrait), self.assertLogs('chibibeasts.gear.art', level='WARNING'):
            await gear_art.send_owned_portrait(click, embed, 'twine', 'gnome_iron_plating', thumbnail=True)
        first, second = click.followup.send.call_args_list
        self.assertTrue(first.kwargs['file'].fp.closed)
        self.assertNotIn('file', second.kwargs)
        self.assertIsNone(second.kwargs['embed'].thumbnail.url)
