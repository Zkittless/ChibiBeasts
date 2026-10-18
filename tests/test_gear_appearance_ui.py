"""Equipment portraits follow confirmed UI choices and fail back to usable text."""
import asyncio
import json
from types import SimpleNamespace
import threading
from unittest.mock import AsyncMock, patch
import unittest

import discord

from cogs.utilities import Utilities
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import crafting, gear_art
from utils.workshop import WorkshopView


async def fake_portrait(species, armor_id=''):
    return gear_art.Portrait(gear_art.portrait_filename(species, armor_id), b'preview png')


class EquippedPanels(DatabaseCase):
    async def armor(self, row_id, armor_id):
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)',
                           (row_id, armor_id))

    async def view(self, selected):
        view = WorkshopView(42, await crafting.workshop_state(42), selected=selected)
        view.message = SimpleNamespace(attachments=[], edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    async def click(self, view, action, value=None):
        click = component()
        if value is not None:
            click.data = {'values': [str(value)]}
        click.edit_original_response.return_value = view.message
        await view.handle(click, action)
        return click

    async def test_workshop_previews_new_armor_then_shows_the_saved_equipped_result(self):
        rid = await self.beast('twine', starter=True)
        await self.armor(rid, 'kelpie_scale_mail')
        await self.armor(None, 'gnome_iron_plating')
        view = await self.view('gnome_iron_plating')
        with patch.object(gear_art, 'render_portrait', side_effect=fake_portrait) as portrait:
            click = await self.click(view, 'use')
            portrait.assert_awaited_with('twine', 'gnome_iron_plating')
            filename = gear_art.portrait_filename('twine', 'gnome_iron_plating')
            self.assertIn('attachment://' + filename, json.dumps(view.to_components()))
            self.assertIn(filename, [file.filename for file in click.edit_original_response.call_args.kwargs['attachments']])
            # Discord has accepted these files; the next redraw should retain
            # the exact same attachment, without regenerating the portrait.
            banner, uploaded = SimpleNamespace(filename='header.png'), SimpleNamespace(filename=filename)
            view.message.attachments = [banner, uploaded]
            portrait.reset_mock()
            confirmed = await self.click(view, 'confirm')
            self.assertEqual(view.appearance(), ('twine', 'gnome_iron_plating'))
            self.assertEqual(view.result_row_id, rid)
            self.assertEqual(view.result_species, 'twine')
            self.assertFalse(view.using)
            portrait.assert_not_awaited()
            self.assertEqual(confirmed.edit_original_response.call_args.kwargs['attachments'], [banner, uploaded])
            self.assertIn('equipped to', view.notice)

    async def test_rune_confirmation_and_result_keep_the_companions_existing_armor(self):
        rid = await self.beast('gloop', starter=True)
        await self.armor(rid, 'kelpie_scale_mail')
        await self.execute('INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,?,1)', ('bark_sigil',))
        view = await self.view('bark_sigil')
        with patch.object(gear_art, 'render_portrait', side_effect=fake_portrait) as portrait:
            await self.click(view, 'use')
            portrait.assert_awaited_with('gloop', 'kelpie_scale_mail')
            await self.click(view, 'confirm')
            self.assertEqual(view.appearance(), ('gloop', 'kelpie_scale_mail'))
            self.assertEqual(view.target()['rune_id'], 'bark_sigil')
            portrait.assert_awaited_with('gloop', 'kelpie_scale_mail')

    async def test_changing_companion_removes_old_appearance_attachment(self):
        await self.beast('twine', starter=True)
        second = await self.beast('barkley')
        await self.armor(None, 'gnome_iron_plating')
        view = await self.view('gnome_iron_plating')
        with patch.object(gear_art, 'render_portrait', side_effect=fake_portrait):
            await self.click(view, 'use')
            old = gear_art.portrait_filename('twine', 'gnome_iron_plating')
            banner = SimpleNamespace(filename='header.png')
            view.message.attachments = [banner, SimpleNamespace(filename=old)]
            changed = await self.click(view, 'target', second)
            filenames = [file.filename for file in changed.edit_original_response.call_args.kwargs['attachments']]
            self.assertNotIn(old, filenames)
            self.assertIn(gear_art.portrait_filename('barkley', 'gnome_iron_plating'), filenames)
            self.assertIn(banner, changed.edit_original_response.call_args.kwargs['attachments'])

    async def test_busy_portrait_worker_keeps_complete_workshop_text_and_controls(self):
        await self.beast('twine', starter=True)
        await self.armor(None, 'gnome_iron_plating')
        view = await self.view('gnome_iron_plating')
        with patch.object(gear_art, 'render_portrait', new=AsyncMock(return_value=None)):
            click = await self.click(view, 'use')
        self.assertFalse(view.art_enabled)
        self.assertFalse(view.confirm_button.disabled)
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], [])
        self.assertNotIn('attachment://', json.dumps(view.to_components()))
        self.assertIn('Gnome-Iron Plating', json.dumps(view.to_components()))

    async def test_gear_uses_first_valid_armor_and_keeps_legacy_rune_separate(self):
        rid = await self.beast('twine', starter=True)
        for key in ('bark_sigil', 'old_unknown_piece', 'gnome_iron_plating', 'kelpie_scale_mail'):
            await self.armor(rid, key)
        command = interaction()
        with patch.object(gear_art, 'render_portrait', side_effect=fake_portrait) as portrait:
            await Utilities.gear.callback(Utilities(None), command, 1)
        command.response.defer.assert_awaited_once()
        portrait.assert_awaited_once_with('twine', 'gnome_iron_plating')
        payload = command.followup.send.call_args.kwargs
        self.assertEqual(payload['embed'].image.url, 'attachment://' + payload['file'].filename)
        fields = '\n'.join(field.name for field in payload['embed'].fields)
        self.assertEqual(fields.count('Bark Sigil'), 1)
        self.assertIn('Gnome-Iron Plating', fields)
        self.assertNotIn('Kelpie-Scale Mail', fields)

    async def test_gear_rejected_attachment_retries_text_and_closes_the_file(self):
        rid = await self.beast('twine', starter=True)
        await self.armor(rid, 'gnome_iron_plating')
        command = interaction()
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        command.followup.send.side_effect = [forbidden, None]
        with patch.object(gear_art, 'render_portrait', side_effect=fake_portrait), self.assertLogs('chibibeasts.gear.art', level='WARNING'):
            await Utilities.gear.callback(Utilities(None), command, 1)
        self.assertEqual(command.followup.send.await_count, 2)
        first, second = command.followup.send.call_args_list
        self.assertTrue(first.kwargs['file'].fp.closed)
        self.assertNotIn('file', second.kwargs)
        self.assertIsNone(second.kwargs['embed'].image.url)


class PortraitWorkers(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_portrait_keeps_its_worker_slot_until_the_thread_finishes(self):
        entered, release = threading.Event(), threading.Event()

        def encode(*_):
            entered.set()
            release.wait(2)
            return b'png'

        # One real worker can be canceled without prematurely returning its
        # slot. The remaining slot is occupied to prove no third job queues.
        self.assertTrue(gear_art._slots.acquire(blocking=False))
        task = None
        try:
            with patch.object(gear_art, '_encode', side_effect=encode):
                task = asyncio.create_task(gear_art.render_portrait('twine', 'gnome_iron_plating'))
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertIsNone(await gear_art.render_portrait('gloop', 'gnome_iron_plating'))
                release.set()
                for _ in range(100):
                    if gear_art._slots.acquire(blocking=False):
                        gear_art._slots.release()
                        break
                    await asyncio.sleep(.005)
                else:
                    self.fail('Portrait worker did not release its slot')
        finally:
            release.set()
            gear_art._slots.release()
            if task:
                await asyncio.gather(task, return_exceptions=True)
