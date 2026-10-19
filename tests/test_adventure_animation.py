"""Team identity, spatial transitions and real attachment budget regressions."""
from dataclasses import FrozenInstanceError
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageSequence

from scripts.preview_adventure_team import capture, cases, make_team
from utils import battle_art as art, battle_scene as scene, move_animations as fx


def overlaps(left, right):
    return max(left[0], right[0]) < min(left[2], right[2]) and max(left[1], right[1]) < min(left[3], right[3])


class TeamSnapshots(unittest.TestCase):
    def test_recursive_live_metadata_freezes_nonrecursive_party_and_own_armor(self):
        party, enemy, active = make_team()
        # The renderer must safely accept a lead present in its own live party.
        party[active]['_party_states'] = party
        before = art.snapshot([party[active], enemy])
        party[1].update(hp=1, armor_id='')
        self.assertEqual([f.member_id for f in before[0].party], [101, 102, 103])
        self.assertTrue(all(not f.party for f in before[0].party))
        self.assertEqual(before[0].party[1].armor_id, 'kelpie_scale_mail')
        self.assertNotEqual(before[0].party[1].hp, 1)
        self.assertFalse(before[1].party)
        with self.assertRaises(FrozenInstanceError):
            before[0].party[1].hp = 1

    def test_two_same_species_use_collection_identity_for_reserves(self):
        party, enemy, active = make_team()
        party[1]['beast_id'] = party[0]['beast_id']
        lead = capture(party, enemy, active)[0]
        self.assertEqual([f.member_id for f in scene.team_reserves(lead)], [102, 103])

    def test_swapped_resource_tracks_never_interpolate_unrelated_health(self):
        _, before, after, _ = next(cases())
        for tick in (0, .2, .49, .5, .7, 1):
            shown, hp, mana, visible = scene.panel_state(before[0], after[0], tick)
            expected = before[0] if tick < .5 else after[0]
            self.assertEqual((shown.member_id, hp, mana, visible.member_id),
                             (expected.member_id, expected.hp, expected.mana, expected.member_id))


class TeamGeometry(unittest.TestCase):
    def test_settled_fighters_reserves_and_visiting_support_own_separate_space(self):
        # Maximum sprite rectangles, independent of transparent artwork bounds.
        fighter_boxes = [(x-size/2, feet-size, x+size/2, feet) for x, feet, size in scene.TEAM_HOMES]
        support = scene.ASSIST_HOME
        support_box = (support.x-support.size/2, support.feet-support.size, support.x+support.size/2, support.feet)
        boxes = [*fighter_boxes, *scene.RESERVE_BOXES, support_box]
        for index, left in enumerate(boxes):
            self.assertGreaterEqual(left[0], 0)
            self.assertGreaterEqual(left[1], 0)
            self.assertLessEqual(left[2], art.WIDTH)
            self.assertLessEqual(left[3], art.HEIGHT)
            for right in boxes[index+1:]:
                self.assertFalse(overlaps(left, right), (left, right))

    def test_swap_exits_before_entrance_and_holds_each_identity(self):
        _, before, after, _ = next(cases())
        home = fx.Pose(*scene.TEAM_HOMES[0][:2], size=scene.TEAM_HOMES[0][2])
        cast = lambda t: scene.swap_cast(before[0], after[0], home, t)
        self.assertEqual(cast(0), ((before[0], home),))
        self.assertEqual(cast(.08), ((before[0], home),))
        self.assertEqual(cast(.2)[0][0].member_id, 101)
        self.assertLess(cast(.39)[0][1].x+home.size/2, 0)
        self.assertFalse(cast(.42))
        self.assertFalse(cast(.48))
        self.assertEqual(cast(.51)[0][0].member_id, 102)
        self.assertLess(cast(.51)[0][1].x+home.size/2, 0)
        self.assertEqual(cast(.9), ((after[0], home),))
        self.assertEqual(cast(1), ((after[0], home),))

    def test_assist_enters_and_returns_to_its_own_bench_slot(self):
        home = fx.Pose(43, 213, size=50)
        self.assertIsNone(scene.assist_pose(home, 0))
        self.assertIsNone(scene.assist_pose(home, .12))
        near = scene.assist_pose(home, .12001)
        self.assertAlmostEqual(near.x, home.x, places=3)
        self.assertAlmostEqual(near.size, home.size, places=3)
        self.assertEqual(scene.assist_pose(home, .5), scene.ASSIST_HOME)
        returned = scene.assist_pose(home, .89999)
        self.assertAlmostEqual(returned.x, home.x, places=3)
        self.assertAlmostEqual(returned.size, home.size, places=3)
        self.assertIsNone(scene.assist_pose(home, 1))


class TeamClips(unittest.TestCase):
    def test_real_swaps_assists_and_knockouts_fit_and_hold_the_true_final_field(self):
        for slug, before, after, action in cases():
            with self.subTest(clip=slug):
                clip = art.render(before, after, 'woods', action)
                self.assertLess(len(clip.data), art.MAX_BYTES)
                self.assertEqual(clip.seconds, scene.duration(before, after, action)/1000)
                with Image.open(io.BytesIO(clip.data)) as gif:
                    self.assertEqual(gif.size, (640, art.TOTAL_HEIGHT))
                    # A KO replacement deliberately holds the empty first half;
                    # GIF compression combines those identical source frames.
                    self.assertGreaterEqual(gif.n_frames, 10)
                    self.assertNotIn('loop', gif.info)
                    self.assertEqual(sum(f.info['duration'] for f in ImageSequence.Iterator(gif)), scene.duration(before, after, action))
                end = art.frame(before, after, 'woods', action, 1)
                still = art.frame(after, after, 'woods')
                field = (0, art.HUD, art.WIDTH, art.HUD+art.HEIGHT)
                self.assertIsNone(ImageChops.difference(end.crop(field), still.crop(field)).getbbox())

    def test_assist_motion_cannot_change_hud_or_journal_when_resources_are_held(self):
        _, before, _, action = next(c for c in cases() if c[0] == 'attack-assist')
        early = art.frame(before, before, 'woods', action, .15)
        cast = art.frame(before, before, 'woods', action, .6)
        self.assertIsNone(ImageChops.difference(early.crop((0, 0, 640, art.HUD)), cast.crop((0, 0, 640, art.HUD))).getbbox())
        self.assertIsNone(ImageChops.difference(early.crop((0, art.HUD+art.HEIGHT, 640, art.TOTAL_HEIGHT)), cast.crop((0, art.HUD+art.HEIGHT, 640, art.TOTAL_HEIGHT))).getbbox())
        self.assertIsNotNone(ImageChops.difference(early, cast).getbbox())

    def test_swapping_sprite_cannot_cover_reserve_cards_or_their_hp(self):
        _, before, after, action = next(cases())
        initial = art.frame(before, after, 'woods', action, 0)
        for tick in (.2, .3, .38):
            moving = art.frame(before, after, 'woods', action, tick)
            for left, top, right, bottom in scene.RESERVE_BOXES:
                box = (left, art.HUD+top, right+1, art.HUD+bottom+1)
                self.assertIsNone(ImageChops.difference(initial.crop(box), moving.crop(box)).getbbox())

    def test_companion_knockout_does_not_end_battle_with_live_reserves(self):
        _, before, after, action = next(c for c in cases() if c[0] == 'companion-knockout')
        with patch.object(scene.ui, 'heading') as heading:
            art.frame(after, after, 'woods')
        self.assertEqual(heading.call_args.args[1], 'CHOOSE A COMPANION')
        # Automatic replacement still leaves the first companion marked KO.
        _, before, after, action = next(c for c in cases() if c[0] == 'reserve-replacement')
        self.assertEqual(scene.team_reserves(after[0])[0].hp, 0)
        with patch.object(scene, '_paint_fighter') as paint:
            art.frame(before, after, 'woods', action, .2)
        self.assertFalse(any(call.args[1].member_id == 101 for call in paint.call_args_list))

    def test_equipped_armor_follows_active_swap_and_assist_visitors(self):
        for slug in ('tag-switch', 'heal-assist'):
            _, before, after, action = next(c for c in cases() if c[0] == slug)
            with patch.object(scene, '_paint_fighter') as paint:
                art.frame(before, after, 'woods', action, .6)
            visitors = [call.args[1] for call in paint.call_args_list if call.args[1].member_id == 102]
            self.assertTrue(visitors)
            self.assertTrue(all(f.armor_id == 'kelpie_scale_mail' for f in visitors))


if __name__ == '__main__':
    unittest.main()
