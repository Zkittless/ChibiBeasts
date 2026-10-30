"""Past recaps stay readable without changing a saved trip or its payouts."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage, expeditions, journal_log, journal_state
from utils.journal import JournalView


class BattleLog(DatabaseCase):
    async def ready(self):
        await self.beast('twine', starter=True)

    async def view(self, page='adventure'):
        view = JournalView(42, await journal_state.snapshot(42), page=page)
        view.trip = view.state.trip or await expeditions.latest(42)
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.art_enabled = False
        await view.load_page()
        view.render()
        self.addCleanup(view.stop)
        return view

    async def click(self, view, action, value=None, uid=42):
        click = component(uid=uid)
        click.message = view.message
        click.edit_original_response.return_value = view.message
        if value is not None:
            click.data = {'values': [value]}
        await view.handle(click, action)
        return click

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    def assert_valid(self, view):
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        ids = [item.custom_id for item in view.walk_children() if isinstance(item, (discord.ui.Button, discord.ui.Select))]
        self.assertEqual(len(ids), len(set(ids)))

    async def record(self, key, *, uid=42, updated=10, notes=None, **payload):
        p = dict(material='gnome_iron_ore', material_granted=1, gold=30, exp=8,
                 notes=notes or ['Found a quiet clearing.'], caught=False, encounter='', **payload)
        await self.execute("INSERT INTO expeditions(id,user_id,realm,status,payload,created_at,updated_at) VALUES(?,?,'woods','complete',?,1,?)",
                           (key, uid, json.dumps(p), updated))

    async def test_completed_results_move_off_adventure_and_remain_in_log(self):
        await self.ready()
        await self.record('first', notes=['Victory at the clearing.'], exp_granted=6,
                          party_xp=[dict(name='Twine', amount=4, leveled=True, level=2)])
        view = await self.view()
        self.assertIn('Back from', self.text(view))
        self.assertNotIn('Victory at the clearing', self.text(view))
        self.assertNotIn('trainer EXP', self.text(view))
        self.assertLess(view.content_length(), 900)
        await self.click(view, 'page:battle_log')
        await self.click(view, 'log_entry', 'first')
        text = self.text(view)
        for expected in ('Victory at the clearing.', '30 gold', '6 trainer EXP', 'Twine: +4 EXP', 'Lv.2'):
            self.assertIn(expected, text)
        self.assert_valid(view)

    async def test_history_is_owned_newest_first_and_paginates_with_ties(self):
        await self.ready()
        for key in ('a', 'b', 'c', 'd', 'e', 'f', 'g'):
            await self.record(key, updated=20)
        await self.record('foreign', uid=43, updated=999)
        view = await self.view('battle_log')
        self.assertEqual([r['id'] for r in view.log_entries], ['g', 'f', 'e'])
        self.assertEqual(view.log_total, 7)
        await self.click(view, 'log_next')
        self.assertEqual([r['id'] for r in view.log_entries], ['d', 'c', 'b'])
        await self.click(view, 'log_entry', 'c')
        await self.click(view, 'log_back')
        self.assertEqual(view.log_page, 1)
        await self.click(view, 'log_next')
        self.assertEqual([r['id'] for r in view.log_entries], ['a'])
        self.assertTrue(next(b for b in view.walk_children() if getattr(b, 'label', '') == 'Older trips').disabled)
        await self.click(view, 'log_prev')
        self.assertEqual(view.log_page, 1)
        self.assert_valid(view)

    async def test_missing_and_foreign_entries_are_rejected_and_empty_pages_clamp(self):
        await self.ready()
        await self.record('own')
        await self.record('foreign', uid=43)
        view = await self.view('battle_log')
        await self.click(view, 'log_entry', 'foreign')
        self.assertIsNone(view.log_selected)
        self.assertIn('current log page', self.text(view))
        await self.execute("DELETE FROM expeditions WHERE id='own'")
        await self.click(view, 'log_entry', 'own')
        self.assertIn('no longer available', self.text(view))
        self.assertIsNone(view.log_selected)
        await self.click(view, 'log_next')
        self.assertEqual(view.log_page, 0)
        self.assertIn('No expeditions recorded', self.text(view))

    async def test_pending_battle_result_is_never_labelled_paid_or_caught(self):
        await self.ready()
        trip = await expeditions.begin(42, 'woods')
        p = trip['payload']
        p.update(encounter='wisp', encounter_level=4, encounter_result='won', caught=True,
                 notes=['Won the encounter. Return to bank the trip.'])
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(p), trip['id']))
        view = await self.view('battle_log')
        await self.click(view, 'log_entry', trip['id'])
        self.assertIn('Victory', self.text(view))
        self.assertIn('Rewards pending return', self.text(view))
        self.assertIn('Catch pending return', self.text(view))
        self.assertNotIn('Rewards received', self.text(view))
        self.assertNotIn('Joined your collection', self.text(view))
        self.assert_valid(view)

    async def test_browsing_does_not_spend_or_mutate_and_current_choices_resume(self):
        await self.ready()
        await journal_state.save_explore_default(42, 'woods')
        trip = await expeditions.begin(42, 'woods')
        view = await self.view()
        choices = [o.value for o in view.path_select.options]
        before_trip = await expeditions.read(42, trip['id'])
        before_player = await storage.get_player(42)
        before_energy = (await expeditions.energy(42)).charges
        for _ in range(2):
            await self.click(view, 'page:battle_log')
            await self.click(view, 'log_entry', trip['id'])
            await self.click(view, 'page:adventure')
        self.assertEqual(view.trip, before_trip)
        self.assertEqual([o.value for o in view.path_select.options], choices)
        self.assertEqual(await expeditions.read(42, trip['id']), before_trip)
        self.assertEqual(await storage.get_player(42), before_player)
        self.assertEqual((await expeditions.energy(42)).charges, before_energy)
        self.assertEqual(view.state.explore_default, 'woods')
        self.assertNotIn('**Rewards received**', self.text(view))
        self.assertNotIn('**Expedition notes**', self.text(view))
        self.assertIn('Search a mossy grove; choose your one material bundle.', self.text(view))
        buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                   and ':path:' in item.custom_id]
        self.assertEqual(len(buttons), len(choices))
        self.assertNotIn(view.path_select, list(view.walk_children()))

    async def test_other_users_and_closed_views_cannot_load_the_log(self):
        view = await self.view()
        with patch.object(journal_log, 'load', new=AsyncMock()) as load:
            foreign = await self.click(view, 'page:battle_log', uid=43)
            foreign.response.send_message.assert_awaited_once()
            view.closed = True
            closed = await self.click(view, 'page:battle_log')
            closed.response.send_message.assert_awaited_once()
            load.assert_not_awaited()

    async def test_log_navigation_acknowledges_before_database_wait(self):
        view = await self.view()
        started, release = asyncio.Event(), asyncio.Event()
        original = journal_log.load
        click = component()
        click.edit_original_response.return_value = view.message

        async def blocked(current):
            click.response.defer.assert_awaited_once()
            started.set()
            await release.wait()
            await original(current)

        with patch.object(journal_log, 'load', side_effect=blocked):
            task = asyncio.create_task(view.handle(click, 'page:battle_log'))
            try:
                await asyncio.wait_for(started.wait(), 2)
            finally:
                release.set()
                await task

    async def test_oversized_legacy_notes_fit_discord_and_are_escaped(self):
        await self.ready()
        await self.record('legacy', notes=['@everyone **old note** ' * 300])
        view = await self.view('battle_log')
        await self.click(view, 'log_entry', 'legacy')
        self.assert_valid(view)
        self.assertIn('Earlier notes omitted', self.text(view))
        self.assertNotIn('@everyone', self.text(view))
        self.assertIn('Rewards received', self.text(view))
