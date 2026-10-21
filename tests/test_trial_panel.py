"""Actual saved tower callbacks keep one panel and never publish stale revisions."""
import asyncio
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import adventure_party, battle_art, db as storage, journal_sessions, trial_panel, trials
from utils.journal import open_journal


class TrialPanelFlows(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ids = []
        for species in ('twine', 'gloop', 'barkley'):
            row = await self.beast(species, starter=not self.ids)
            self.ids.append(row)
        await adventure_party.set_slot(42, self.ids[1], 1)
        await adventure_party.set_slot(42, self.ids[2], 2)
        await storage.update_player(42, level=50)
        await self.execute('UPDATE player_beasts SET hp=2000,max_hp=2000,attack=2000,defense=1000,speed=500,mana=500,max_mana=500 WHERE user_id=42')
        self.frames = []
        self.message = SimpleNamespace(id=8181, attachments=[], delete=AsyncMock())
        async def edit(**kwargs):
            self.frames.append(dict(message=8181, components=kwargs['view'].to_components()))
            return self.message
        self.message.edit = AsyncMock(side_effect=edit)
        self.command = interaction()
        self.command.channel.id = 12345
        self.command.channel.send.return_value = self.message
        self.command.followup.send.return_value = self.message
        self.views = []
        for target, name, value in ((battle_art.BattlePresentation, 'art', AsyncMock(return_value=None)),
                                    (trial_panel.journal_team_art, 'render_team', AsyncMock(return_value=None))):
            change = patch.object(target, name, value)
            change.start()
            self.patches.append(change)

    async def asyncTearDown(self):
        for view in self.views:
            view.cancel_timer()
            view.stop()
            if trial_panel._panels.get(view.key) is view:
                trial_panel._panels.pop(view.key, None)
        await asyncio.sleep(0)
        await super().asyncTearDown()

    async def open(self, destination='woods'):
        self.view = await trial_panel.open_trials(self.command, destination=destination)
        self.views.append(self.view)
        return self.view

    def click(self, value=None, uid=42):
        click = component(section=value, uid=uid)
        click.message = self.message
        click.channel = self.command.channel
        click.edit_original_response.return_value = self.message
        return click

    def button(self, label):
        return next(item for item in self.view.walk_children() if isinstance(item, discord.ui.Button) and item.label == label)

    async def press(self, label):
        click = self.click()
        await self.button(label).callback(click)
        return click

    async def begin(self):
        await self.open()
        await self.press('Begin tower')
        await self.press('Next wave')
        return self.view.run

    async def basic(self):
        return await self.press(self.view.payload['party'][self.view.payload['slot']]['moves'][0])

    async def test_browsing_and_destination_override_are_free_and_show_three_members(self):
        view = await self.open('woods_deep')
        self.assertEqual(view.destination, 'woods_deep')
        picker = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select))
        await picker.callback(self.click('ember_deep'))
        self.assertEqual(view.destination, 'ember_deep')
        self.assertIsNone(await trials.current(42))
        self.assertEqual(await self.rows('SELECT * FROM trial_cooldowns'), [])
        text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertTrue(all(name in text for name in ('Twine', 'Gloop', 'Barkley')))
        self.assertEqual(self.command.followup.send.await_count, 1)
        self.command.channel.send.assert_not_awaited()

    async def test_wave_battle_pause_resume_and_result_all_edit_one_message(self):
        await self.begin()
        await self.basic()
        self.assertEqual(self.view.run['status'], 'between')
        await self.press('Pause')
        self.assertEqual(self.view.run['status'], 'paused')
        await self.press('Resume climb')
        await self.press('Retreat & bank')
        self.assertEqual(self.view.run['status'], 'complete')
        self.assertTrue(self.view.payload['settlement'])
        self.assertEqual(self.command.followup.send.await_count, 1)
        self.command.channel.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()
        self.assertTrue(all(frame['message'] == 8181 for frame in self.frames))
        self.assertGreater(len(self.frames), 4)
        self.assertIsNotNone(self.button('Back to trials'))

    async def test_stale_double_click_and_foreign_controls_do_not_advance(self):
        await self.open()
        await self.press('Begin tower')
        old = self.button('Next wave')
        foreign = self.click(uid=77)
        await old.callback(foreign)
        foreign.response.send_message.assert_awaited_once()
        await old.callback(self.click())
        version = self.view.run['version']
        stale = self.click()
        await old.callback(stale)
        stale.followup.send.assert_awaited_once()
        self.assertEqual((await trials.current(42))['version'], version)

    async def test_every_action_commits_before_renderer_receives_frames(self):
        await self.begin()
        async def rendered(*args):
            saved = await trials.read(42, self.view.run['id'])
            self.assertEqual(saved['version'], self.view.run['version'])
            self.assertEqual(saved['payload']['party'], self.view.payload['party'])
            return None
        with patch.object(self.view.presentation, 'art', side_effect=rendered) as render:
            await self.basic()
        self.assertGreater(render.await_count, 0)

    async def test_deadline_settles_only_the_current_saved_turn_and_keeps_back(self):
        await self.begin()
        self.view.cancel_timer()
        run = self.view.run
        await self.execute('UPDATE trial_runs SET payload=json_set(payload,\'$.turn_deadline\',?) WHERE id=?', (time.time() - 1, run['id']))
        await self.view.deadline(self.view.version, time.time() - 1)
        self.assertEqual(self.view.run['status'], 'complete')
        self.assertEqual(self.view.payload['settlement']['outcome'], 'timeout')
        self.assertIsNotNone(self.button('Back to journal'))
        self.assertEqual(self.command.followup.send.await_count, 1)

    async def test_journal_round_trip_reuses_the_components_v2_message(self):
        journal = await open_journal(self.command, page='adventure', realm='woods_deep')
        click = self.click()
        self.view = await journal.launch(click, 'challenge')
        self.views.append(self.view)
        self.assertEqual(self.view.message.id, journal.message.id)
        self.assertTrue(journal.message_repurposed)
        back = await self.press('Back to journal')
        self.assertTrue(self.view.closed)
        self.assertEqual(back.edit_original_response.call_args.kwargs['view'].page, 'adventure')
        self.assertEqual(self.command.followup.send.await_count, 1)
        self.command.channel.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()
        back.edit_original_response.call_args.kwargs['view'].stop()

    async def test_deleted_panel_is_recreated_once_without_repeating_saved_action(self):
        await self.open()
        missing = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Deleted')
        replacement = SimpleNamespace(id=9191, edit=AsyncMock(), delete=AsyncMock())
        replacement.edit.return_value = replacement
        self.message.edit.side_effect = missing
        self.command.channel.send.return_value = replacement
        await self.press('Begin tower')
        self.assertEqual(self.view.message.id, 9191)
        self.assertEqual(self.command.channel.send.await_count, 1)
        self.assertEqual(len(await self.rows('SELECT * FROM trial_runs')), 1)
        replacement.edit.side_effect = missing
        await self.press('Refresh') if any(getattr(item, 'label', '') == 'Refresh' for item in self.view.walk_children()) else await self.press('Back to trials')
        self.assertEqual(self.command.channel.send.await_count, 1)

    async def test_attachment_rejection_keeps_text_controls_and_saved_resources(self):
        await self.open()
        await self.press('Begin tower')
        self.view.media = ('trial-review.png', b'artwork')
        rejected = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'No Attach Files')
        self.message.edit.side_effect = [rejected, self.message]
        self.assertTrue(await self.view.deliver())
        self.assertIsNone(self.view.media)
        self.assertFalse(self.view.presentation.enabled)
        self.assertEqual(self.message.edit.call_args.kwargs['attachments'], [])
        self.assertIsNotNone(self.button('Next wave'))
        self.assertEqual(self.view.payload['cleared'], 0)

    async def test_item_target_controls_stay_within_discord_limits(self):
        await storage.add_item(42, 'pixie_dust_potion', 3)
        await self.open()
        await self.press('Begin tower')
        item = next(item for item in self.view.walk_children() if isinstance(item, discord.ui.Select) and item.placeholder.startswith('Care item'))
        await item.callback(self.click('pixie_dust_potion'))
        target = next(item for item in self.view.walk_children() if isinstance(item, discord.ui.Select) and item.placeholder == 'Apply to a companion')
        await target.callback(self.click(str(self.ids[1])))
        self.assertLessEqual(self.view.total_children_count, 40)
        self.assertLessEqual(self.view.content_length(), 3900)
        self.assertIsNotNone(self.button('Use care item'))

    async def test_restart_keeps_exact_enemy_and_resumes_with_a_fresh_deadline(self):
        await self.begin()
        self.view.cancel_timer()
        saved = self.view.payload
        enemy, party = saved['enemy'], saved['party']
        await storage.init_db()
        command = interaction()
        command.channel = self.command.channel
        command.followup.send.return_value = self.message
        view = await trial_panel.open_trials(command, destination='loom_deep')
        self.views.append(view)
        self.view = view
        self.assertEqual(view.run['status'], 'paused')
        self.assertEqual(view.destination, 'woods')
        self.assertEqual(view.payload['enemy'], enemy)
        self.assertEqual(view.payload['party'], party)
        await self.press('Resume climb')
        self.assertEqual(view.run['status'], 'fighting')
        self.assertGreater(view.run['turn_deadline'], time.time())
        self.assertIsNotNone(view.timer)

    async def test_slow_renderer_cannot_overwrite_a_newer_saved_revision(self):
        await self.open()
        await self.press('Begin tower')
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            started.set()
            await release.wait()
            return None
        self.view.mode = 'run'
        before = self.message.edit.await_count
        with patch.object(trial_panel.journal_team_art, 'render_team', side_effect=slow):
            render = asyncio.create_task(self.view.prepare_media())
            await asyncio.wait_for(started.wait(), 2)
            await trials.pause(42, *self.view.version)
            release.set()
            await render
        self.assertFalse(await self.view.deliver())
        self.assertEqual(self.message.edit.await_count, before)

    async def test_back_to_trials_refreshes_team_after_the_run_has_settled(self):
        await self.begin()
        await self.basic()
        await self.press('Retreat & bank')
        newcomer = await self.beast('fairy')
        await adventure_party.set_slot(42, newcomer, 1)
        await self.press('Back to trials')
        self.assertIsNone(self.view.run)
        self.assertEqual(self.view.mode, 'selector')
        self.assertIn(newcomer, [row['id'] for row in self.view.rows])

    async def test_selector_displays_the_effective_hourglass_cooldown(self):
        await self.perk('chronos_hourglass')
        await self.open()
        text = '\n'.join(item.content for item in self.view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn('24 minute cooldown', text)

    async def test_between_wave_quote_matches_the_actual_retreat_receipt(self):
        await self.begin()
        await self.basic()
        quote = self.view.payload['bank_quote']
        text = '\n'.join(item.content for item in self.view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn(f"+{quote['gold']:,} gold", text)
        await self.press('Retreat & bank')
        settled = self.view.payload['settlement']
        self.assertEqual((settled['gold'], settled['trainer_xp']), (quote['gold'], quote['trainer_xp']))

    async def test_queued_journal_timeout_cannot_overwrite_the_trials_handoff(self):
        journal = await open_journal(self.command, page='adventure')
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(panel):
            started.set()
            await release.wait()
        with patch.object(trial_panel.TrialPanel, 'prepare_media', new=slow):
            handoff = asyncio.create_task(journal.launch(self.click(), 'challenge'))
            await asyncio.wait_for(started.wait(), 2)
            timeout = asyncio.create_task(journal.on_timeout())
            await asyncio.sleep(0)
            release.set()
            self.view = await handoff
            self.views.append(self.view)
            await timeout
        self.assertIs(self.message.edit.call_args.kwargs['view'], self.view)
        self.assertTrue(journal.message_repurposed)

    async def test_failed_journal_return_preserves_trial_back_controls(self):
        await self.open()
        await self.press('Begin tower')
        rejected = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Cannot open')
        with patch('utils.journal.open_journal', new=AsyncMock(side_effect=rejected)):
            await self.press('Back to journal')
        self.assertFalse(self.view.closed)
        self.assertEqual(self.view.run['status'], 'paused')
        self.assertIsNotNone(self.button('Back to journal'))
        self.assertIsNotNone(self.button('Resume climb'))

    async def test_selector_upfront_team_uses_all_three_actual_equipped_armors(self):
        armors = ('gnome_iron_plating', 'kelpie_scale_mail', 'sprite_silk_attire')
        for row_id, armor in zip(self.ids, armors):
            await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)', (row_id, armor))
        await self.open('woods_deep')
        rows, destination = trial_panel.journal_team_art.render_team.await_args.args
        self.assertEqual(destination, 'woods_deep')
        self.assertEqual([row['armor_id'] for row in rows], list(armors))

    async def test_clean_clear_shows_stamp_then_selected_biome_best_action_count(self):
        await self.begin()
        for _ in range(20):
            if self.view.run['status'] == 'complete':
                break
            if self.view.run['status'] == 'between':
                await self.press('Next wave')
            else:
                await self.basic()
        self.assertEqual(self.view.payload['settlement']['outcome'], 'won')
        self.assertTrue(self.view.payload['settlement']['stamp'])
        text = '\n'.join(item.content for item in self.view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn('First-clear stamp earned', text)
        actions = self.view.payload['actions']
        await self.press('Back to trials')
        text = '\n'.join(item.content for item in self.view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn(f'Best {actions} actions', text)

    async def test_failed_old_panel_cleanup_cannot_retire_the_new_published_panel(self):
        await self.open()
        previous = self.view
        previous.message.delete.side_effect = TimeoutError()
        new_message = SimpleNamespace(id=9292, attachments=[], edit=AsyncMock(), delete=AsyncMock())
        command = interaction()
        command.channel = self.command.channel
        command.followup.send.return_value = new_message
        self.view = await trial_panel.open_trials(command, destination='woods_deep')
        self.views.append(self.view)
        self.assertTrue(self.view.current())
        self.assertEqual(self.view.message.id, 9292)
        self.assertTrue(previous.closed)
