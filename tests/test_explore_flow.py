"""The public exploration entry point always opens the saved choice flow."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import time

import discord

from cogs.hatch import Hatch
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, expeditions
from utils.journal import JournalView


class ExploreJournalFlow(DatabaseCase):
    async def open(self, destination='woods', expedition=None):
        command = interaction()
        command.followup.send.return_value = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        arguments = {} if expedition is None else {'expedition': expedition}
        view = await Hatch.explore.callback(Hatch(None), command, destination, **arguments)
        self.addCleanup(view.stop)
        command.response.defer.assert_awaited_once()
        self.assertIsInstance(view, JournalView)
        self.assertEqual(view.page, 'adventure')
        return command, view

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    async def pick(self, view, choice):
        value = next(option.value for option in view.path_select.options if option.value.endswith(':' + choice))
        click = component()
        click.data = {'values': [value]}
        click.edit_original_response.return_value = view.message
        await view.handle(click, 'path')
        click.response.defer.assert_awaited_once()

    async def test_default_and_both_legacy_modes_open_the_same_saved_trip(self):
        await self.beast('twine', starter=True)
        trip_id = None
        self.assertFalse(hasattr(Hatch, '_quick_explore'))
        for legacy in (None, False, True):
            _, view = await self.open(expedition=legacy)
            trip_id = trip_id or view.trip['id']
            self.assertEqual(view.trip['id'], trip_id)
            self.assertEqual(view.trip['payload']['mode'], 'expedition')
            self.assertEqual(view.trip['payload']['stage'], 0)
            self.assertIn('Exploration 0/3', self.text(view))
            self.assertIn('Next charge in', self.text(view))
            self.assertEqual({o.value.rsplit(':', 1)[1] for o in view.path_select.options},
                             {'tracks', 'gather', 'shrine', 'return_early'})
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 0)

    async def test_decisions_save_and_one_material_is_banked_only_on_return(self):
        await self.beast('twine', starter=True)
        _, view = await self.open()
        await self.pick(view, 'gather')
        saved = await expeditions.current(42)
        self.assertEqual(saved['payload']['route'], 'gather')
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        choice = view.path_select.options[0].value.split(':', 2)[2]
        await self.pick(view, choice)
        self.assertEqual(view.trip['payload']['stage'], 2)
        _, resumed = await self.open('ember')
        self.assertEqual(resumed.trip['id'], saved['id'])
        self.assertEqual(resumed.trip['realm'], 'woods')
        self.assertEqual(resumed.trip['payload']['stage'], 2)
        await self.pick(resumed, 'return')
        self.assertEqual(resumed.trip['status'], 'complete')
        self.assertEqual(sum(row['quantity'] for row in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 0)

    async def test_locked_or_empty_bank_keeps_journal_controls_and_spends_nothing(self):
        await self.beast('twine', starter=True)
        _, locked = await self.open('ember')
        self.assertIn('Reach trainer level 3', self.text(locked))
        self.assertIsNone(await expeditions.current(42))
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,0,?)', (time.time(),))
        _, empty = await self.open()
        self.assertIn('0/3 exploration charges', self.text(empty))
        self.assertIn('Next charge in', self.text(empty))
        begin = next(item for item in empty.walk_children() if isinstance(item, discord.ui.Button) and item.label.startswith('Begin expedition'))
        self.assertTrue(begin.disabled)
        self.assertIsNone(await expeditions.current(42))

    async def test_two_simultaneous_invocations_resume_one_trip_and_spend_once(self):
        await self.beast('twine', starter=True)
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,?)', (time.time(),))
        first, second = await asyncio.gather(self.open(), self.open())
        self.assertEqual(first[1].trip['id'], second[1].trip['id'])
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 2)

    async def test_defer_precedes_account_reads_and_journal_rendering(self):
        command = interaction()

        async def opened(*args, **kwargs):
            command.response.defer.assert_awaited_once()
            self.assertTrue(kwargs['begin_expedition'])
            self.assertTrue(kwargs['already_deferred'])
            return 'journal'

        with patch('utils.journal.open_journal', side_effect=opened), patch('cogs.hatch.get_or_create_player', new=AsyncMock(side_effect=AssertionError('Duplicate account read'))):
            self.assertEqual(await Hatch.explore.callback(Hatch(None), command), 'journal')
