"""A retired journal cannot publish late animation over a newer panel."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from tests.test_gameplay import interaction
from tests.test_trial_combat import fighter
from utils import journal_adventure, journey_combat


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
            payload=dict(adventure_version=3, completed=1, total=3, party_ids=[1], bonus=0,
                battle_target=2, battles_completed=1, boons=dict(attack=5, defense=0),
                history=[dict(outcome=OUTCOME)], page=dict(title='The lantern crossing', body=STORY,
                    choices=[dict(value='scene:a', label='Brace the crossing', description='A small temporary boon.'),
                             dict(value='scene:b', label='Inspect the keeper\'s drawing', description='Study the crossing.')],
                    root_id='woods.crossing', node_id='woods.crossing.entry', kind='ruin', repeatable=False)))

    def text(self, value):
        self.container.add_item(discord.ui.TextDisplay(value))

    def row(self, *items):
        self.container.add_item(discord.ui.ActionRow(*items))

    def button(self, label, action, style=discord.ButtonStyle.secondary):
        return discord.ui.Button(label=label, custom_id='qa:' + action, style=style)

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

    async def test_compact_lead_has_at_most_two_sentences_and_200_characters(self):
        lead = journal_adventure.scene_lead(STORY)
        self.assertLessEqual(len(lead), 200)
        self.assertEqual(lead, 'A damaged lantern hangs above the narrow crossing. Your companions find a keeper waiting beside the loose handrail.')
        long = journal_adventure.scene_lead('A ' + 'long passage ' * 40 + 'ends here.')
        self.assertLessEqual(len(long), 200)
        self.assertTrue(long.endswith('…'))
        self.assertEqual(journal_adventure.scene_lead(''), '')

    async def test_default_scene_keeps_lore_and_previous_outcome_out_of_the_way(self):
        self.stage.render()
        text = self.stage.text_content()
        self.assertIn('Battles won 1/2', text)
        self.assertIn(journal_adventure.scene_lead(STORY), text)
        self.assertNotIn('maintenance drawing', text)
        self.assertNotIn(OUTCOME, text)
        controls = [item for item in self.stage.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual([item.label for item in controls], ['Read more'])
        self.assertIn('journey_details:saved-trip:3', controls[0].custom_id)
        self.assertLessEqual(self.stage.content_length(), 4000)
        self.assertLessEqual(self.stage.total_children_count, 40)

    async def test_details_toggle_edits_same_panel_without_mutating_the_trip(self):
        self.stage.render()
        before = deepcopy(self.stage.trip)
        click = interaction()
        with patch('utils.expeditions.read', new=AsyncMock(return_value=self.stage.trip)):
            await journal_adventure.toggle_details(self.stage, click, 'saved-trip', 3)
            self.assertIn(STORY, self.stage.text_content())
            self.assertIn(OUTCOME, self.stage.text_content())
            self.assertIn('Hide details', [i.label for i in self.stage.walk_children() if isinstance(i, discord.ui.Button)])
            await journal_adventure.toggle_details(self.stage, interaction(), 'saved-trip', 3)
        self.assertNotIn('maintenance drawing', self.stage.text_content())
        self.assertEqual(self.stage.trip, before)
        self.assertEqual(self.stage.publish.await_count, 2)
        click.followup.send.assert_not_awaited()
        click.channel.send.assert_not_awaited()

    async def test_new_revision_collapses_expanded_lore_automatically(self):
        self.stage.journey_details = ('saved-trip', 3)
        self.stage.trip['version'] = 4
        self.stage.render()
        self.assertNotIn('maintenance drawing', self.stage.text_content())
        self.assertNotIn(OUTCOME, self.stage.text_content())

    async def test_details_require_owner_current_revision_and_noncombat_scene(self):
        wrong_owner = interaction(43)
        await journal_adventure.toggle_details(self.stage, wrong_owner, 'saved-trip', 3)
        wrong_owner.response.send_message.assert_awaited_once()
        self.stage.publish.assert_not_awaited()
        for kind in ('old_button', 'new_saved_revision', 'combat'):
            with self.subTest(kind=kind):
                self.stage.trip['status'] = 'battle' if kind == 'combat' else 'active'
                saved = deepcopy(self.stage.trip)
                if kind == 'new_saved_revision':
                    saved['version'] += 1
                with patch('utils.expeditions.read', new=AsyncMock(return_value=saved)):
                    await journal_adventure.toggle_details(self.stage, interaction(), 'saved-trip', 2 if kind == 'old_button' else 3)
                self.stage.publish.assert_not_awaited()

    async def test_battle_uses_combat_controls_and_has_no_lore_or_read_more(self):
        payload = dict(prepared_party=[fighter()])
        journey_combat.start(payload, journey_combat.plan('woods', 4, 'single', 2))
        self.stage.trip['payload']['combat'] = payload['combat']
        self.stage.trip['status'] = 'battle'
        self.stage.journey_details = ('saved-trip', 3)
        self.stage.render()
        text = self.stage.text_content()
        self.assertIn('Battles won 1/2', text)
        self.assertIn('HP ', text)
        self.assertIn('Hostile encounter · Cannot be recruited', text)
        self.assertNotIn('The lantern crossing', text)
        self.assertNotIn(OUTCOME, text)
        self.assertFalse(any(i.label in ('Read more', 'Hide details') for i in self.stage.walk_children() if isinstance(i, discord.ui.Button)))
        self.assertTrue(any(i.placeholder == 'Choose a move, swap or assist' for i in self.stage.walk_children() if isinstance(i, discord.ui.Select)))

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
        message_repurposed=False,journey_playing=False,journey_media=None,
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
        async def retire(*args):
            stage.closed=True
            return SimpleNamespace(data=b'late',extension='gif',seconds=0)
        stage.journey_presentation.art=AsyncMock(side_effect=retire)
        with patch('utils.expeditions.read',new=AsyncMock(return_value=stage.trip)):
            await journal_adventure.present(stage,object(),animate=True)
        stage.publish.assert_not_awaited()
        self.assertFalse(stage.journey_playing)

    async def test_changed_saved_revision_stops_stale_playback(self):
        stage=view()
        newer=deepcopy(stage.trip)
        newer['version']+=1
        with patch('utils.expeditions.read',new=AsyncMock(return_value=newer)):
            await journal_adventure.present(stage,object(),animate=True)
        stage.publish.assert_not_awaited()
        stage.journey_presentation.art.assert_not_awaited()

    async def test_accepted_playback_edits_the_same_view_then_restores_controls(self):
        stage=view()
        events=len(stage.trip['payload']['combat']['events'])
        with patch('utils.expeditions.read',new=AsyncMock(return_value=stage.trip)):
            await journal_adventure.present(stage,object(),animate=True)
        self.assertEqual(stage.publish.await_count,events+1)
        self.assertFalse(stage.journey_playing)
        self.assertTrue(stage.journey_media.name.endswith('.gif'))
