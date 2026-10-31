"""A retired journal cannot publish late animation over a newer panel."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from tests.test_gameplay import interaction
from tests.test_trial_combat import fighter
from utils import adventure_content, journal_adventure, journey_combat
from utils.habitats import BIOMES


STORY = ('A damaged lantern hangs above the narrow crossing. '
         'Your companions find a keeper waiting beside the loose handrail. '
         'She has folded the old maintenance drawing into a paper boat and is using a quiet pool to demonstrate where the current changed overnight.')
OUTCOME = 'The team braced the old ladder and left the keeper a careful set of notes about the sheltered path.'


class SceneView(discord.ui.LayoutView):
    """Native components with no DB, account or media dependency."""
    def __init__(self):
        super().__init__(timeout=300)
        self.user_id = 42
        self.closed = self.message_repurposed = self.journey_playing = False
        self.journey_media = None
        self.art_enabled = False
        self.lock = asyncio.Lock()
        self.publish = AsyncMock()
        self.container = discord.ui.Container()
        self.add_item(self.container)
        self.path_select = discord.ui.Select(placeholder='Choose your next step')
        self.trip = dict(id='saved-trip', user_id=42, realm='woods', version=3, status='active',
            payload=dict(adventure_version=3, pacing_version=1, completed=1, total=3, party_ids=[1], bonus=0,
                battle_target=2, battles_completed=1, boons=dict(attack=5, defense=0),
                history=[dict(outcome=OUTCOME)], page=dict(title='The lantern crossing', body=STORY,
                    choices=[dict(value='scene:a', label='Brace the crossing', description='A small temporary boon.'),
                             dict(value='scene:b', label='Inspect the keeper\'s drawing', description='Study the crossing.')],
                    root_id='woods.crossing', node_id='woods.crossing.entry', kind='ruin', repeatable=False)))

    def text(self, value):
        self.container.add_item(discord.ui.TextDisplay(value))

    def row(self, *items):
        self.container.add_item(discord.ui.ActionRow(*items))

    def button(self, label, action, style=discord.ButtonStyle.secondary, *, disabled=False):
        return discord.ui.Button(label=label, custom_id='qa:' + action, style=style, disabled=disabled)

    def select(self, action, placeholder):
        return discord.ui.Select(custom_id='qa:' + action, placeholder=placeholder)

    def render_compact_team(self, trip):
        self.text('**Team** · Twine (lead) · Gloop · Barkley')

    def render(self):
        self.container.clear_items()
        journal_adventure.render(self, self.trip)

    def text_content(self):
        return '\n'.join(item.content for item in self.walk_children() if isinstance(item, discord.ui.TextDisplay))


class AdventureScenes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stage = SceneView()
        self.addCleanup(self.stage.stop)

    async def test_complete_story_text_is_visible_without_a_disclosure_step(self):
        self.stage.render()
        self.assertIn(STORY, self.stage.text_content())
        self.assertIn('maintenance drawing', self.stage.text_content())

    async def test_scene_uses_dropdown_with_complete_wrapping_choices_and_effects(self):
        self.stage.render()
        text = self.stage.text_content()
        self.assertIn('Battles won 1/2', text)
        self.assertIn(STORY, text)
        self.assertNotIn(OUTCOME, text)
        controls = [item for item in self.stage.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(controls, [])
        self.assertEqual([option.label for option in self.stage.path_select.options],
                         ['Brace the crossing', "Inspect the keeper's drawing", 'Retreat and return'])
        self.assertIn('A small temporary boon.', text)
        self.assertIn('Study the crossing.', text)
        self.assertIn(self.stage.path_select, list(self.stage.walk_children()))
        self.assertLessEqual(self.stage.content_length(), 4000)
        self.assertLessEqual(self.stage.total_children_count, 40)

    async def test_prompt_rendering_does_not_advance_or_mutate_saved_story(self):
        before = deepcopy(self.stage.trip)
        for _ in range(2):
            self.stage.render()
        self.assertEqual(self.stage.trip, before)
        self.stage.publish.assert_not_awaited()

    async def test_choice_result_flows_into_next_encounter_without_a_details_button(self):
        self.stage.trip['payload']['history'][-1]['root_id'] = 'woods.crossing'
        self.stage.trip['payload']['page']['kind'] = 'battle'
        self.stage.render()
        self.assertIn(OUTCOME, self.stage.text_content())
        self.assertFalse(any(item.label in ('Read more', 'Hide details') for item in self.stage.walk_children()
                             if isinstance(item, discord.ui.Button)))

    async def test_return_stays_separate_from_story_actions(self):
        self.stage.trip['payload']['page']['kind'] = 'homeward'
        self.stage.render()
        footer = self.stage.container.children[-1]
        self.assertIsInstance(footer, discord.ui.ActionRow)
        self.assertEqual([button.label for button in footer.children], ['Retreat and return'])

    async def test_battle_uses_move_buttons_with_costs_and_disabled_ultimate(self):
        payload = dict(prepared_party=[fighter()])
        journey_combat.start(payload, journey_combat.plan('woods', 4, 'single', 2))
        self.stage.trip['payload']['combat'] = payload['combat']
        self.stage.trip['status'] = 'battle'
        self.stage.render()
        text = self.stage.text_content()
        self.assertIn('Battles won 1/2', text)
        self.assertIn('HP ', text)
        self.assertIn('Hostile encounter · Cannot be recruited', text)
        self.assertNotIn('The lantern crossing', text)
        self.assertNotIn(OUTCOME, text)
        self.assertFalse(any(i.label in ('Read more', 'Hide details') for i in self.stage.walk_children() if isinstance(i, discord.ui.Button)))
        self.assertFalse(any(isinstance(i, discord.ui.Select) for i in self.stage.walk_children()))
        controls = [i for i in self.stage.walk_children() if isinstance(i, discord.ui.Button)]
        self.assertTrue(any(' MP' in i.label and i.style == discord.ButtonStyle.primary for i in controls))
        ultimate = next(i for i in controls if i.style == discord.ButtonStyle.danger)
        self.assertIn('0/3', ultimate.label)
        self.assertTrue(ultimate.disabled)

    async def test_one_team_button_replaces_reserve_rows_and_modal_lists_legal_actions(self):
        payload = dict(prepared_party=[fighter(identity=1), fighter('gloop', identity=2), fighter('barkley', identity=3)])
        journey_combat.start(payload, journey_combat.plan('woods', 4, 'single', 2))
        self.stage.trip['payload']['combat'] = payload['combat']
        self.stage.trip['status'] = 'battle'
        self.stage.render()
        controls = [i for i in self.stage.walk_children() if isinstance(i, discord.ui.Button)]
        self.assertEqual([i.label for i in controls if i.label == 'Swap / Assist'], ['Swap / Assist'])
        self.assertFalse(any(i.label.startswith(('Swap:', 'Heal:', 'Guard:', 'Attack:')) for i in controls))
        modal = journal_adventure.JourneyTeamMenu(self.stage, self.stage.trip)
        self.addCleanup(modal.stop)
        self.assertEqual(len(modal.action.options), 2)
        self.assertTrue(all(option.label.startswith('Swap in ') for option in modal.action.options))
        self.assertTrue(all('HP ' in option.description and 'MP ' in option.description for option in modal.action.options))
        modal.to_dict()
        self.assertLessEqual(self.stage.total_children_count, 40)
        self.assertLessEqual(self.stage.content_length(), 4000)
        self.stage.to_components()

    async def test_full_catalog_fits_native_limits_without_cutting_scene_or_choice_text(self):
        for biome in BIOMES:
            for root in adventure_content.load(biome)['encounters']:
                for node_id, node in root['nodes'].items():
                    with self.subTest(node=node_id):
                        self.stage.trip['realm'] = biome
                        choices = [dict(value=f'scene:{index}', label=choice['label'], description=choice['description'])
                                   for index, choice in enumerate(node['choices'])]
                        self.stage.trip['payload']['page'] = dict(title=root['title'], body=node['body'], choices=choices,
                            root_id=root['id'], node_id=node_id, kind=root['kind'], repeatable=root['repeatable'])
                        self.stage.render()
                        text = self.stage.text_content()
                        self.assertIn(node['body'], text)
                        for choice in choices:
                            self.assertIn(choice['label'], text)
                            self.assertIn(choice['description'], text)
                        self.assertLessEqual(self.stage.content_length(), 4000)
                        self.assertLessEqual(self.stage.total_children_count, 40)
                        self.stage.to_components()

    async def test_long_effect_stays_complete_above_the_platform_limited_menu_description(self):
        description = 'A complete choice effect with several conditions. ' * 8
        self.stage.trip['payload']['page']['choices'][0]['description'] = description
        self.stage.render()
        self.assertIn(description, self.stage.text_content())
        self.assertLessEqual(len(self.stage.path_select.options[0].description), 100)

    async def test_only_designated_first_opponent_is_labelled_recruitable(self):
        payload = dict(prepared_party=[fighter()])
        journey_combat.start(payload, journey_combat.plan('woods', 4, 'pack2', 2))
        self.stage.trip['payload']['combat'] = payload['combat']
        self.stage.trip['status'] = 'battle'
        for capture, wave in ((True, 0), (True, 1), (False, 0)):
            with self.subTest(capture=capture, wave=wave):
                self.stage.trip['payload']['capture_fight'] = capture
                self.stage.trip['payload']['combat']['wave_index'] = wave
                self.stage.render()
                text = self.stage.text_content()
                self.assertEqual('Can join your team after victory' in text, capture and wave == 0)
                self.assertEqual('Cannot be recruited' in text, not (capture and wave == 0))


def view():
    payload={'prepared_party':[fighter()]}
    journey_combat.start(payload,journey_combat.plan('woods',4,'single',2))
    trip=dict(id='saved-trip',user_id=42,realm='woods',version=3,status='battle',payload=payload)
    art=SimpleNamespace(data=b'clip',extension='gif',seconds=0)
    presentation=SimpleNamespace(realm='woods',art=AsyncMock(return_value=art),sleep=AsyncMock())
    return SimpleNamespace(trip=trip,user_id=42,closed=False,is_finished=lambda:False,
        message_repurposed=False,journey_playing=False,journey_media=None,art_enabled=True,
        journey_presentation=presentation,render=Mock(),publish=AsyncMock())


class AdventurePlayback(unittest.IsolatedAsyncioTestCase):
    async def test_closed_and_repurposed_views_do_not_start_playback(self):
        for attribute in ('closed','message_repurposed'):
            stage=view()
            setattr(stage,attribute,True)
            with patch('utils.expeditions.read',new=AsyncMock(return_value=stage.trip)):
                await journal_adventure.present(stage,object(),animate=True)
            stage.publish.assert_not_awaited()
            stage.journey_presentation.art.assert_not_awaited()

    async def test_retirement_while_rendering_cannot_upload_a_late_clip(self):
        stage=view()
        published=[]
        stage.publish=AsyncMock(side_effect=lambda *args: published.append(stage.journey_media))
        async def retire(*args):
            stage.closed=True
            return SimpleNamespace(data=b'late',extension='gif',seconds=0)
        stage.journey_presentation.art=AsyncMock(side_effect=retire)
        with patch('utils.expeditions.read',new=AsyncMock(return_value=stage.trip)):
            await journal_adventure.present(stage,object(),animate=True)
        self.assertEqual(published,[None], 'Only native preparing feedback may precede retirement')
        self.assertFalse(stage.journey_playing)

    async def test_changed_saved_revision_refreshes_controls_without_stale_playback(self):
        stage=view()
        newer=deepcopy(stage.trip)
        newer['version']+=1
        with patch('utils.expeditions.read',new=AsyncMock(return_value=newer)), \
             patch('utils.journal_state.snapshot',new=AsyncMock(return_value=SimpleNamespace(trip=newer))):
            await journal_adventure.present(stage,object(),animate=True)
        stage.publish.assert_awaited_once()
        self.assertEqual(stage.trip['version'], newer['version'])
        stage.journey_presentation.art.assert_not_awaited()

    async def test_accepted_playback_edits_the_same_view_then_restores_controls(self):
        stage=view()
        events=len(stage.trip['payload']['combat']['events'])
        published=[]
        stage.publish=AsyncMock(side_effect=lambda *args: published.append(
            (stage.journey_playing,stage.journey_media is None,stage.art_enabled)))
        with patch('utils.expeditions.read',new=AsyncMock(return_value=stage.trip)):
            await journal_adventure.present(stage,object(),animate=True)
        self.assertEqual(stage.publish.await_count,events+2)
        self.assertEqual(published[0],(True,True,False))
        self.assertFalse(published[-1][0])
        self.assertTrue(published[-1][2])
        self.assertFalse(stage.journey_playing)
        self.assertTrue(stage.journey_media.name.endswith('.gif'))

    async def test_existing_battle_media_also_recovers_after_revision_changes(self):
        stage = view()
        stage.journey_media = SimpleNamespace(name='previous.gif', data=b'old')
        newer = deepcopy(stage.trip)
        newer['version'] += 1
        read = AsyncMock(side_effect=[stage.trip, stage.trip, newer])
        with patch('utils.expeditions.read', new=read), \
             patch('utils.journal_state.snapshot', new=AsyncMock(return_value=SimpleNamespace(trip=newer))):
            await journal_adventure.present(stage, object(), animate=True)
        stage.publish.assert_awaited_once()
        self.assertEqual(stage.trip['version'], newer['version'])
        self.assertFalse(stage.journey_playing)
        self.assertIsNone(stage.journey_media)

    async def test_retirement_during_resync_read_cannot_overwrite_replacement(self):
        stage = view()

        async def replaced(*args):
            stage.message_repurposed = True
            return SimpleNamespace(trip=stage.trip)

        with patch('utils.journal_state.snapshot', new=AsyncMock(side_effect=replaced)):
            await journal_adventure.restore_saved_controls(stage, object())
        stage.publish.assert_not_awaited()
        stage.render.assert_not_called()
