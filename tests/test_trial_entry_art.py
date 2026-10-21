"""Real equipped-party arrival clips keep the next wave separate from attacks."""
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageSequence

from cogs.battle import build_pve_beast_state
from utils import battle_art as art, battle_scene, db as storage
from utils.team_combat import PartyBattle


def team_scene():
    party = [build_pve_beast_state(storage.get_beast_data(key), 20)
             for key in ('twine', 'gloop', 'barkley')]
    for index, (member, armor) in enumerate(zip(party, ('gnome_iron_plating', 'kelpie_scale_mail', 'sprite_silk_attire'))):
        member.update(id=index + 101, armor_id=armor)
    enemy = build_pve_beast_state(storage.get_beast_data('basilisk'), 20)
    return PartyBattle(party, enemy, 42).snapshot()


class TrialEntryArtwork(unittest.TestCase):
    def test_arrival_moves_only_new_opponent_and_settles_into_normal_team_scene(self):
        scene = team_scene()
        action = art.Action(1, 'Glasskeeper enters', 'enter')
        positions = []
        for t in (0, .5, 1):
            with patch.object(battle_scene, '_paint_fighter', wraps=battle_scene._paint_fighter) as paint:
                art.frame(scene, scene, 'ember_deep', action, t)
            poses = {call.args[1].species: call.args[2] for call in paint.call_args_list}
            positions.append(poses['basilisk'].x)
            self.assertEqual(poses['twine'].x, battle_scene.TEAM_HOMES[0][0])
        self.assertGreater(positions[0], art.WIDTH)
        self.assertGreater(positions[0], positions[1])
        self.assertGreater(positions[1], positions[2])
        self.assertEqual(positions[-1], battle_scene.TEAM_HOMES[1][0])
        crop = (0, art.HUD, art.WIDTH, art.HUD + art.HEIGHT)
        settled = art.frame(scene, scene, 'ember_deep', action, 1).crop(crop)
        still = art.frame(scene, scene, 'ember_deep').crop(crop)
        self.assertIsNone(ImageChops.difference(settled, still).getbbox())

    def test_arrival_is_a_short_one_shot_without_damage_or_mutating_snapshots(self):
        scene = team_scene()
        action = art.Action(1, 'Next wave', 'enter')
        clip = art.render(scene, scene, 'loom_deep', action)
        self.assertEqual(clip.seconds, 1.2)
        self.assertLess(len(clip.data), 1_000_000)
        with Image.open(io.BytesIO(clip.data)) as image:
            self.assertGreater(image.n_frames, 10)
            self.assertNotIn('loop', image.info)
            self.assertEqual(sum(frame.info.get('duration', 0) for frame in ImageSequence.Iterator(image)), 1200)
        self.assertTrue(all(fighter.hp == fighter.max_hp for fighter in scene))
