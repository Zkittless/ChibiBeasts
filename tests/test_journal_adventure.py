"""A retired journal cannot publish late animation over a newer panel."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.test_trial_combat import fighter
from utils import journal_adventure, journey_combat


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
