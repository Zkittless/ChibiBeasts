"""Exploration opens a charge-free selection, then saves the confirmed trip."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import time

import discord

from cogs.hatch import Hatch
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage, expeditions, trial_combat
from utils.journal import JournalView


class ExploreJournalFlow(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        def presentation(realm):
            return SimpleNamespace(realm=realm,art=AsyncMock(return_value=None),sleep=AsyncMock())
        p=patch('utils.battle_art.BattlePresentation',side_effect=presentation)
        p.start()
        self.patches.append(p)

    async def open(self, destination=None, expedition=None, *, channel_id=999):
        command = interaction()
        command.channel.id = channel_id
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

    async def begin(self, view):
        button = next(item for item in view.walk_children()
                      if isinstance(item, discord.ui.Button) and item.label.startswith('Start adventure'))
        self.assertFalse(button.disabled)
        click = component()
        click.edit_original_response.return_value = view.message
        await button.callback(click)
        click.response.defer.assert_awaited_once()

    async def pick(self, view, choice):
        value = next(option.value for option in view.path_select.options if option.value.endswith(':' + choice))
        click = component()
        click.data = {'values': [value]}
        click.edit_original_response.return_value = view.message
        await view.handle(click, 'path')
        click.response.defer.assert_awaited_once()

    async def test_default_and_both_legacy_modes_open_selection_without_spending(self):
        await self.beast('twine', starter=True)
        self.assertFalse(hasattr(Hatch, '_quick_explore'))
        for legacy in (None, False, True):
            _, view = await self.open(expedition=legacy)
            self.assertIsNone(view.trip)
            self.assertEqual(view.realm, 'woods')
            self.assertIn('Choose an expedition', self.text(view))
            self.assertIn('1/3 exploration charges', self.text(view))
            self.assertIn(view.realm_select, list(view.walk_children()))
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 1)

    async def test_begin_spends_once_and_reopening_resumes_the_saved_trip(self):
        await self.beast('twine', starter=True)
        _, view = await self.open()
        await self.begin(view)
        trip_id = view.trip['id']
        self.assertEqual(view.trip['payload']['mode'], 'expedition')
        self.assertEqual(view.trip['payload']['adventure_version'], 3)
        self.assertEqual(view.trip['payload']['stage'], 0)
        self.assertEqual(view.trip['status'],'battle')
        self.assertIn('Battles won 0/2', self.text(view))
        self.assertIn('Progress saved', self.text(view))
        self.assertEqual(expeditions.options(view.trip),())
        frozen=expeditions.presentation(view.trip)
        for legacy in (None, False, True):
            _, resumed = await self.open('ember', expedition=legacy)
            self.assertEqual(resumed.trip['id'], trip_id)
            self.assertEqual(resumed.trip['realm'], 'woods')
            self.assertEqual(expeditions.presentation(resumed.trip),frozen)
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 0)

    async def test_decisions_save_and_one_material_is_banked_only_on_return(self):
        await self.beast('twine', starter=True)
        # Routing fixture: real moves and writes, deterministic quick victories.
        await self.execute('UPDATE player_beasts SET attack=100000,speed=10000 WHERE user_id=42')
        _, view = await self.open()
        await self.begin(view)
        async def safe_step(stage):
            choice=next(item['value'] for item in stage.trip['payload']['page']['choices']
                        if not item.get('effects',{}).get('battle') and not item.get('effects',{}).get('hp_cost'))
            await self.pick(stage,choice)
        async def combat_step(stage):
            state=stage.trip['payload']['combat']
            click=component()
            click.edit_original_response.return_value=stage.message
            if state['status']=='between':
                action=f"journey_next:{stage.trip['id']}:{stage.trip['version']}"
            else:
                actions=trial_combat.legal_actions(state)
                index=next(i for i,a in enumerate(actions) if a['enabled'])
                click.data={'values':[f"{stage.trip['id']}:{stage.trip['version']}:{index}"]}
                action='journey_action'
            await stage.handle(click,action)
        for _ in range(20):
            if view.trip['status']!='battle':
                break
            await combat_step(view)
        saved = await expeditions.current(42)
        self.assertEqual(saved['payload']['completed'], 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        while view.trip['payload']['completed']<2:
            await safe_step(view)
        self.assertEqual(view.trip['payload']['stage'], 2)
        _, resumed = await self.open('ember')
        self.assertEqual(resumed.trip['id'], saved['id'])
        self.assertEqual(resumed.trip['realm'], 'woods')
        self.assertEqual(resumed.trip['payload']['stage'], 2)
        self.assertEqual(resumed.trip['payload']['history'], view.trip['payload']['history'])
        # Authored pages and an optional material picker remain saved; neither
        # grants its bundle until the real return control is accepted.
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        for _ in range(15):
            if resumed.trip['payload']['journey_status']=='homeward':
                break
            if resumed.trip['status']=='battle':
                await combat_step(resumed)
            elif resumed.trip['payload']['page']['kind']=='battle':
                await self.pick(resumed,expeditions.options(resumed.trip)[0])
            else:
                await safe_step(resumed)
        self.assertEqual(resumed.trip['payload']['journey_status'],'homeward')
        self.assertEqual(resumed.trip['payload']['completed'],resumed.trip['payload']['total'])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        await self.pick(resumed, 'return')
        self.assertEqual(resumed.trip['status'], 'complete')
        self.assertTrue(resumed.trip['payload']['material_granted'])
        self.assertLessEqual(resumed.trip['payload']['gold'], int(resumed.trip['payload']['base_gold'] * 1.25))
        self.assertLessEqual(resumed.trip['payload']['exp'], int(resumed.trip['payload']['base_exp'] * 1.25))
        self.assertEqual(sum(row['quantity'] for row in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 0)

    async def test_locked_or_empty_bank_keeps_journal_controls_and_spends_nothing(self):
        await self.beast('twine', starter=True)
        _, locked = await self.open('ember')
        self.assertEqual(locked.realm, 'ember')
        self.assertIn('Trainer Lv.6 required', self.text(locked))
        begin = next(item for item in locked.walk_children() if isinstance(item, discord.ui.Button) and item.label.startswith('Start adventure'))
        self.assertTrue(begin.disabled)
        self.assertIsNone(await expeditions.current(42))
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,0,?)', (time.time(),))
        _, empty = await self.open()
        self.assertIn('0/3 exploration charges', self.text(empty))
        self.assertIn('Next charge in', self.text(empty))
        begin = next(item for item in empty.walk_children() if isinstance(item, discord.ui.Button) and item.label.startswith('Start adventure'))
        self.assertTrue(begin.disabled)
        self.assertIsNone(await expeditions.current(42))

    async def test_two_simultaneous_openers_spend_nothing_and_begin_spends_once(self):
        await self.beast('twine', starter=True)
        await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,?)', (time.time(),))
        # Separate channels retain both real views. Same-channel reopening
        # intentionally retires its previous journal before another click.
        first, second = await asyncio.gather(self.open(channel_id=999), self.open(channel_id=1000))
        self.assertIsNone(first[1].trip)
        self.assertIsNone(second[1].trip)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, 3)
        await asyncio.gather(self.begin(first[1]), self.begin(second[1]))
        self.assertEqual(first[1].trip['id'], second[1].trip['id'])
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        self.assertEqual((await expeditions.energy(42)).charges, 2)

    async def test_defer_precedes_account_reads_and_journal_rendering(self):
        command = interaction()

        async def opened(*args, **kwargs):
            command.response.defer.assert_awaited_once()
            self.assertFalse(kwargs.get('begin_expedition', False))
            self.assertTrue(kwargs['already_deferred'])
            return 'journal'

        with patch('utils.journal.open_journal', side_effect=opened), patch('cogs.hatch.get_or_create_player', new=AsyncMock(side_effect=AssertionError('Duplicate account read'))):
            self.assertEqual(await Hatch.explore.callback(Hatch(None), command), 'journal')
