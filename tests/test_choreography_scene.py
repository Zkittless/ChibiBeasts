"""Presentation containment and resolved-outcome choreography contracts."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PIL import ImageChops

from scripts.preview_move_animations import case
from utils import battle_art as art, battle_scene, move_animations as fx


class ChoreographyScene(unittest.TestCase):
    def test_changing_choreography_cannot_cover_hud_or_journal(self):
        before, after, action = case('genesis', 'Genesis Explosion')
        normal = art.frame(before, after, 'loom', action, .52)
        with patch.object(fx.Effects, 'effect'), patch.object(fx, 'paint_echoes'):
            plain = art.frame(before, after, 'loom', action, .52)
        self.assertIsNotNone(ImageChops.difference(normal, plain).getbbox())
        for box in ((0, 0, art.WIDTH, art.HUD),
                    (0, art.HUD+art.HEIGHT, art.WIDTH, art.TOTAL_HEIGHT)):
            self.assertIsNone(ImageChops.difference(normal.crop(box), plain.crop(box)).getbbox())

    def test_all_authored_paths_preserve_body_proportions_and_rest_endpoints(self):
        home, target = fx.Pose(162, 218, height=150), fx.Pose(478, 218, flip=True)
        for species, moves in fx.manifest().items():
            for name in moves:
                animation = fx.profile(species, name)
                for origin, destination in ((home, target), (target, home)):
                    self.assertEqual(fx.motion(animation, origin, destination, 0), origin)
                    self.assertEqual(fx.motion(animation, origin, destination, 1), origin)
                    for t in (.2, .4, .5, .7, .9):
                        pose = fx.motion(animation, origin, destination, t)
                        self.assertEqual((pose.size, pose.height, pose.flip),
                                         (origin.size, origin.height, origin.flip))
                        self.assertLess(abs(pose.angle), 17)

    def test_no_target_reaction_without_resolved_hp_loss(self):
        before, after, action = case('twine', 'Tick Bite')
        with patch.object(fx, 'reaction', wraps=fx.reaction) as reaction:
            art.frame(before, before, 'woods', action, .55)
            reaction.assert_not_called()
            art.frame(before, after, 'woods', action, .55)
            reaction.assert_called_once()

    def test_animation_does_not_change_snapshots_or_playback_window(self):
        before, after, action = case('chronos', 'End of Time')
        saved = (before, after, action)
        total = battle_scene.duration(before, after, action)
        for t in (0, .24, .48, .6, 1):
            art.frame(before, after, 'loom', action, t)
        self.assertEqual((before, after, action), saved)
        self.assertEqual(battle_scene.duration(before, after, action), total)
        self.assertEqual(action.milliseconds, 2650)

    def test_assists_use_new_beneficiary_centered_support_sequences(self):
        before, _, _ = case('gloop', 'Glitter Bounce')
        for kind, family in (('heal', 'heal'), ('guard', 'guard'), ('shield', 'guard')):
            animation = battle_scene.assist_profile(before[0], kind)
            self.assertEqual(animation.family, family)
            self.assertNotEqual(animation.sequence, 'legacy')

    def test_actual_raid_boss_attack_has_preparation_and_contact_layers(self):
        from cogs.guilds import RAID_BOSSES
        from scripts.preview_raid_arena import make_raid, capture
        from utils import raid_art
        session = make_raid(RAID_BOSSES['ancient'][0], 3)
        before = capture(session)
        session.resolve_boss_turn()
        after = capture(session)
        self.assertTrue(any(new.fighter.hp < old.fighter.hp
                            for old, new in zip(before.members, after.members)))
        with patch.object(fx.Effects, 'effect', autospec=True) as effect:
            raid_art.frame(before, after, tuple(session._art_events), .4)
        boss_profile = fx.profile(after.boss.species, session.enemy['moves'][0])
        calls = [call for call in effect.call_args_list if call.args[1] == boss_profile]
        self.assertTrue(any(call.kwargs.get('back') for call in calls))
        self.assertTrue(any(not call.kwargs.get('back') for call in calls))


if __name__ == '__main__':
    unittest.main()
