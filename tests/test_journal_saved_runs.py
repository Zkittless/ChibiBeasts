"""Saved trials must be visible, and expired reservations must not trap Start."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage, expeditions, journal_state, journey_service, trial_combat, trial_locks, trials
from utils.duels import FighterBusyError, active_fighters
from utils.journal import JournalView


class SavedRunJournal(DatabaseCase):
    async def ready(self):
        row_id = await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET attack=9999,defense=9999 WHERE id=?', (row_id,))
        return row_id

    def view(self, state, page='adventure'):
        view = JournalView(42, state, page=page)
        self.addCleanup(view.stop)
        view.art_enabled = False
        view.message = SimpleNamespace(id=123, attachments=[])
        view.render()
        return view

    def labels(self, view):
        return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    async def two_waves(self, run):
        for _ in range(30):
            if run['payload']['cleared'] == 2:
                return run
            if run['status'] == 'between':
                run = await trials.next_wave(42, run['id'], run['version'])
            else:
                choice = next(action['choice'] for action in trial_combat.legal_actions(run['payload'])
                              if action['enabled'] and action['kind'] == 'move')
                run = await trials.act(42, run['id'], run['version'], choice)
        self.fail('Fixture did not finish two waves.')

    async def test_active_trial_replaces_start_with_resume_without_changing_the_run(self):
        await self.ready()
        run = await trials.begin(42, 'woods', seed=73)
        before = (await expeditions.energy(42)).charges
        state = await journal_state.snapshot(42)
        self.assertEqual(state.trial['id'], run['id'])
        for page in ('home', 'adventure'):
            view = self.view(state, page)
            self.assertIn('Resume trial', self.labels(view))
            self.assertNotIn('Set out', self.labels(view))
            self.assertFalse(any(label.startswith('Start adventure') for label in self.labels(view)))
            text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
            self.assertIn('Retreat & bank', text)
            self.assertIn('0/5', text)
            self.assertLessEqual(view.content_length(), 4000)
            self.assertLessEqual(view.total_children_count, 40)
        resume = next(item for item in view.walk_children() if getattr(item, 'label', '') == 'Resume trial')
        click = component()
        click.message = view.message
        with patch('utils.trial_panel.open_trials', new=AsyncMock()) as open_trials:
            await resume.callback(click)
        open_trials.assert_awaited_once_with(click, bot=view.bot, destination=view.realm,
                                            source=view, already_deferred=True)
        self.assertEqual((await trials.current(42))['payload'], run['payload'])
        self.assertEqual((await expeditions.energy(42)).charges, before)
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])

    async def test_start_from_an_older_page_explains_and_exposes_the_saved_trial(self):
        await self.ready()
        view = self.view(await journal_state.snapshot(42))
        run = await trials.begin(42, 'woods', seed=73)
        before = (await expeditions.energy(42)).charges
        click = component()
        click.message = view.message
        with patch.object(view, 'publish', new=AsyncMock()), \
             patch.object(expeditions, 'begin', wraps=expeditions.begin) as begin:
            await view.handle(click, 'begin')
        begin.assert_not_awaited()
        click.followup.send.assert_awaited_once()
        self.assertIn('Resume trial', click.followup.send.call_args.args[0])
        self.assertTrue(click.followup.send.call_args.kwargs['ephemeral'])
        self.assertIn('Resume trial', self.labels(view))
        self.assertFalse(any(label.startswith('Start adventure') for label in self.labels(view)))
        self.assertEqual((await trials.current(42))['id'], run['id'])
        self.assertEqual((await expeditions.energy(42)).charges, before)
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])

    async def test_opening_journal_settles_expired_progress_once_before_reading_stats(self):
        await self.ready()
        run = await self.two_waves(await trials.begin(42, 'woods', seed=73))
        run = await trials.pause(42, run['id'], run['version'])
        before = await storage.get_player(42)
        charges = (await expeditions.energy(42)).charges
        await self.execute('UPDATE trial_runs SET expires_at=0 WHERE id=?', (run['id'],))
        # Assert the trial payout independently of unrelated achievement grants.
        with patch('utils.progress.check_achievements', new=AsyncMock()):
            states = await asyncio.gather(journal_state.snapshot(42), journal_state.snapshot(42))
        settled = await trials.read(42, run['id'])
        self.assertEqual(settled['payload']['outcome'], 'expired')
        paid = settled['payload']['settlement']['gold']
        self.assertGreater(paid, 0)
        for state in states:
            self.assertIsNone(state.trial)
            self.assertEqual(state.player['gold'], before['gold'] + paid)
        self.assertFalse(trial_locks.locked(42))
        view = self.view(states[0])
        start = next(item for item in view.walk_children() if getattr(item, 'label', '').startswith('Start adventure'))
        self.assertFalse(start.disabled)
        again = await journal_state.snapshot(42)
        self.assertEqual(again.player['gold'], states[0].player['gold'])
        self.assertEqual((await expeditions.energy(42)).charges, charges)
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])
        self.assertEqual(len(await self.rows('SELECT id FROM trial_runs')), 1)

    async def test_direct_departure_recovers_expired_trial_without_opening_challenge(self):
        await self.ready()
        run = await trials.begin(42, 'woods', seed=73)
        await self.execute('UPDATE trial_runs SET expires_at=0 WHERE id=?', (run['id'],))
        before = (await expeditions.energy(42)).charges
        trip = await journey_service.begin(42, 'woods')
        self.assertEqual((await trials.read(42, run['id']))['status'], 'complete')
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [{'id': trip['id']}])
        self.assertEqual((await expeditions.energy(42)).charges, before - 1)
        self.assertTrue(trial_locks.locked(42), 'The new saved adventure must remain protected.')

    async def test_departure_repairs_an_orphaned_cache_entry_for_only_this_trainer(self):
        await self.ready()
        trial_locks.lock(42)
        trial_locks.lock(99)
        before = (await expeditions.energy(42)).charges
        trip = await journey_service.begin(42, 'woods')
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [{'id': trip['id']}])
        self.assertEqual((await expeditions.energy(42)).charges, before - 1)
        self.assertTrue(trial_locks.locked(42))
        self.assertTrue(trial_locks.locked(99), 'Recovery must not clear another trainer\'s reservation.')

    async def test_journal_rebuilds_a_saved_adventure_lock_without_releasing_it(self):
        await self.ready()
        trip = await journey_service.begin(42, 'woods')
        trial_locks.unlock(42)  # Cache was lost; the persisted adventure survives.
        state = await journal_state.snapshot(42)
        self.assertTrue(trial_locks.locked(42))
        self.assertIsNone(state.trial)
        self.assertEqual(state.trip['id'], trip['id'])
        self.assertEqual(state.trip['payload'], trip['payload'])
        with self.assertRaises(FighterBusyError):
            await trials.begin(42, 'woods', seed=73)
        self.assertEqual(await self.rows('SELECT id FROM trial_runs'), [])
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [{'id': trip['id']}])

    async def test_cache_recovery_does_not_release_an_actual_battle_reservation(self):
        await self.ready()
        trial_locks.lock(42)
        active_fighters.add(42)
        before = (await expeditions.energy(42)).charges
        await journal_state.snapshot(42)
        self.assertFalse(trial_locks.locked(42))
        self.assertIn(42, active_fighters)
        with self.assertRaisesRegex(FighterBusyError, 'already in a battle'):
            await journey_service.begin(42, 'woods')
        self.assertIn(42, active_fighters)
        self.assertEqual(await self.rows('SELECT id FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, before)
