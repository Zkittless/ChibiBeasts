"""Real button callbacks keep saved combat revision and owner checks."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import adventure_party, expeditions, journal_adventure, journal_state
from utils.journal import JournalView


class AdventureButtons(DatabaseCase):
    async def ready(self, team_size=1):
        await self.beast('twine', starter=True)
        for slot, species in enumerate(('gloop', 'barkley')[:team_size - 1], 1):
            await adventure_party.set_slot(42, await self.beast(species), slot)
        await self.execute('UPDATE player_beasts SET attack=100000,speed=10000 WHERE user_id=42')
        await expeditions.begin(42, 'woods', adventure_version=3)
        view = JournalView(42, await journal_state.snapshot(42), page='adventure')
        self.addCleanup(view.stop)
        view.art_enabled = False
        view.render()
        return view

    def action(self, view, *, disabled=False):
        return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                    and ':journey_act:' in item.custom_id and item.disabled == disabled)

    async def team_menu(self, view):
        button = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                      and item.label == 'Swap / Assist')
        click = component()
        click.response.send_modal = AsyncMock()
        await button.callback(click)
        modal = click.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        return modal

    async def test_double_and_stale_combat_clicks_only_commit_one_turn(self):
        view = await self.ready()
        button, before = self.action(view), view.trip['version']
        with patch('utils.journal_adventure.present', new=AsyncMock()):
            await asyncio.gather(button.callback(component()), button.callback(component()))
            self.assertEqual((await expeditions.current(42))['version'], before + 1)
            await button.callback(component())
        self.assertEqual((await expeditions.current(42))['version'], before + 1)
        self.assertIn('refreshed adventure controls', view.notice)

    async def test_forged_disabled_ultimate_does_not_advance_combat(self):
        view = await self.ready()
        ultimate = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                        and item.style == discord.ButtonStyle.danger)
        self.assertTrue(ultimate.disabled)
        before = view.trip['version']
        with patch('utils.journal_adventure.present', new=AsyncMock()):
            await ultimate.callback(component())
        self.assertEqual((await expeditions.current(42))['version'], before)
        self.assertIn('current combat action', view.notice)

    async def test_another_trainer_cannot_use_visible_buttons(self):
        view = await self.ready()
        click = component(uid=43)
        before = view.trip['version']
        with patch('utils.journal_state.snapshot', new=AsyncMock()) as snapshot:
            await self.action(view).callback(click)
        snapshot.assert_not_awaited()
        click.response.send_message.assert_awaited_once()
        self.assertEqual((await expeditions.current(42))['version'], before)

    async def test_three_member_controls_use_short_unique_component_ids(self):
        view = await self.ready(team_size=3)
        # A saved long revision and 32-character trip id must fit Discord's
        # 100-character custom_id limit, including the journal router prefix.
        view.trip['version'] = 10000
        view.render()
        controls = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(len({item.custom_id for item in controls}), len(controls))
        self.assertTrue(all(len(item.custom_id) <= 100 for item in controls))
        self.assertLessEqual(view.total_children_count, 40)

    async def test_team_popup_opens_without_spending_a_turn_and_submits_one_real_swap(self):
        view = await self.ready(team_size=3)
        before = deepcopy(await expeditions.current(42))
        modal = await self.team_menu(view)
        self.assertIsInstance(modal, journal_adventure.JourneyTeamMenu)
        self.assertEqual(await expeditions.current(42), before)
        option = next(option for option in modal.action.options if option.label.startswith('Swap in '))
        modal.action._values = [option.value]
        with patch('utils.journal_adventure.present', new=AsyncMock()) as present:
            await modal.on_submit(component())
        saved = await expeditions.current(42)
        self.assertEqual(saved['version'], before['version'] + 1)
        self.assertEqual(saved['payload']['combat']['slot'], 1)
        present.assert_awaited_once()
        self.assertTrue(present.call_args.kwargs['animate'])

    async def test_stale_popup_and_another_trainers_submit_cannot_change_battle(self):
        view = await self.ready(team_size=3)
        modal = await self.team_menu(view)
        modal.action._values = [modal.action.options[0].value]
        wrong = component(uid=43)
        await modal.on_submit(wrong)
        wrong.response.send_message.assert_awaited_once()
        with patch('utils.journal_adventure.present', new=AsyncMock()):
            await self.action(view).callback(component())
            saved = await expeditions.current(42)
            await modal.on_submit(component())
        self.assertEqual(await expeditions.current(42), saved)
        self.assertIn('refreshed adventure controls', view.notice)

    async def test_popup_cannot_forge_attack_or_unavailable_assist(self):
        view = await self.ready(team_size=3)
        modal = await self.team_menu(view)
        self.assertTrue(all(option.label.startswith('Swap in ') for option in modal.action.options))
        before = await expeditions.current(42)
        modal.action._values = ['0']
        click = component()
        await modal.on_submit(click)
        click.response.send_message.assert_awaited_once()
        self.assertEqual(await expeditions.current(42), before)

    async def test_popup_cannot_act_after_the_source_journal_navigates_away(self):
        view = await self.ready(team_size=3)
        modal = await self.team_menu(view)
        modal.action._values = [modal.action.options[0].value]
        before = await expeditions.current(42)
        view.page = 'battle_log'
        click = component()
        await modal.on_submit(click)
        click.response.send_message.assert_awaited_once()
        self.assertEqual(await expeditions.current(42), before)

    async def test_popup_keeps_full_long_names_and_uses_native_select_label_components(self):
        view = await self.ready(team_size=3)
        # Nicknames are normally frozen from the collection at departure.
        name = 'A companion nickname of forty characters'
        view.trip['payload']['combat']['party'][1]['name'] = name
        modal = journal_adventure.JourneyTeamMenu(view, view.trip)
        self.addCleanup(modal.stop)
        self.assertIn('Swap in ' + name, [option.label for option in modal.action.options])
        self.assertTrue(all(len(option.label) <= 100 and len(option.description) <= 100 for option in modal.action.options))
        payload = modal.to_dict()
        self.assertEqual(payload['components'][0]['type'], 18)
        self.assertEqual(payload['components'][0]['component']['type'], 3)
