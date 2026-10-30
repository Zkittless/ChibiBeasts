"""Equipped appearance travels with combat snapshots, without changing rules."""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from PIL import Image

from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from scripts.preview_raid_arena import make_raid, capture
from tests.test_gameplay import DatabaseCase
from utils import armor_art, battle_art as art, combat, db as storage, move_animations as fx, raid_art
from utils.catalog import load_catalog
from utils.duels import prepare_state


class AppearanceSnapshots(unittest.TestCase):
    def test_malformed_optional_armor_manifest_keeps_the_base_battle_portrait(self):
        self.addCleanup(art.sprite.cache_clear)
        art.sprite.cache_clear()
        expected = art.sprite('twine').tobytes()
        with patch.object(armor_art, 'manifest', return_value={}), self.assertLogs('chibibeasts.battle.art', level='WARNING'):
            image = art.sprite('twine', 'gnome_iron_plating')
        self.assertEqual(image.tobytes(), expected)

    def test_all_armor_ids_are_captured_without_changing_equipment_effects(self):
        catalog = load_catalog('equipment.json')
        data = storage.get_beast_data('twine')
        row = build_pve_beast_state(data, 6)
        rune = catalog['runes']['bark_sigil']
        for armor in catalog['equipment'].values():
            with self.subTest(armor=armor['id']):
                equipped = combat.build_state(data, row, gear=[armor, rune])
                effects_only = combat.build_state(data, row, gear=[
                    {'effect': armor['effect']}, {'effect': rune['effect']}])
                self.assertEqual(equipped.pop('armor_id'), armor['id'])
                self.assertEqual(effects_only.pop('armor_id'), '')
                self.assertEqual(equipped, effects_only)
        self.assertEqual(combat.build_state(data, row, gear=[rune])['armor_id'], '')

    def test_snapshot_freezes_armor_separately_from_name_and_species(self):
        states = [build_pve_beast_state(storage.get_beast_data(k), 6)
                  for k in ('twine', 'barkley')]
        states[0].update(armor_id='gnome_iron_plating', name='Pocket knight', id=22)
        before = art.snapshot(states)
        states[0]['armor_id'] = 'sprite_silk_attire'
        after = art.snapshot(states)
        self.assertEqual(before[0].armor_id, 'gnome_iron_plating')
        self.assertEqual(after[0].armor_id, 'sprite_silk_attire')
        self.assertEqual(before[0].species, 'twine')
        self.assertEqual(before[0].name, 'Pocket knight')
        self.assertEqual(before[1].armor_id, '')
        with self.assertRaises(FrozenInstanceError):
            before[0].armor_id = ''

    def test_duel_portraits_body_and_time_ghosts_use_equipped_appearance(self):
        states = [build_pve_beast_state(storage.get_beast_data(k), 6)
                  for k in ('twine', 'barkley')]
        states[0]['armor_id'] = 'gnome_iron_plating'
        states[1]['armor_id'] = 'sprite_silk_attire'
        saved = art.snapshot(states)
        action = art.Action(0, 'Borrowed Moment', ultimate=True)
        appearances = {
            ('twine', 'gnome_iron_plating'): Image.new('RGBA', (80, 100), '#d985ba'),
            ('barkley', 'sprite_silk_attire'): Image.new('RGBA', (80, 100), '#8bd1a3'),
        }
        with patch.object(art, 'sprite', side_effect=lambda *key: appearances[key]) as sprite, \
             patch.object(fx, 'paint_sprite', wraps=fx.paint_sprite) as paint:
            # Sample the teleport itself, after its stationary windup. The
            # trail reuses a fitted source rather than reloading each ghost.
            art.frame(saved, saved, 'woods', action, .43)
        self.assertIn(unittest.mock.call('twine', 'gnome_iron_plating'), sprite.call_args_list)
        self.assertIn(unittest.mock.call('barkley', 'sprite_silk_attire'), sprite.call_args_list)
        self.assertTrue(all(len(call.args) == 2 for call in sprite.call_args_list))
        ghosts = [call for call in paint.call_args_list if 0 < call.args[2].alpha < 1]
        self.assertGreaterEqual(len(ghosts), 2)
        self.assertTrue(all(call.args[1] is appearances[('twine', 'gnome_iron_plating')]
                            for call in ghosts), 'Every trail must use the equipped actor source')
        for appearance in appearances.values():
            self.assertTrue(any(call.args[1] is appearance and call.args[2].alpha == 1
                                for call in paint.call_args_list), 'Both equipped bodies must be drawn')

    def test_raid_swap_captures_each_companions_own_armor(self):
        raid = make_raid(RAID_BOSSES['corrupted'][2], 1)
        raid.party[0][0]['armor_id'] = 'gnome_iron_plating'
        raid.party[0][1]['armor_id'] = 'kelpie_scale_mail'
        before = capture(raid)
        raid.slots[0] = 1
        after = capture(raid)
        self.assertEqual(before.members[0].fighter.armor_id, 'gnome_iron_plating')
        self.assertEqual(after.members[0].fighter.armor_id, 'kelpie_scale_mail')
        event = raid_art.Event(1, 0, 'swap')
        with patch.object(art, 'sprite', side_effect=lambda *_: Image.new('RGBA', (80, 100))) as sprite:
            raid_art.frame(before, after, (event,), .1)
            self.assertIn(unittest.mock.call(before.members[0].fighter.species,
                                           'gnome_iron_plating'), sprite.call_args_list)
            sprite.reset_mock()
            raid_art.frame(before, after, (event,), .8)
            self.assertIn(unittest.mock.call(after.members[0].fighter.species,
                                           'kelpie_scale_mail'), sprite.call_args_list)
            sprite.reset_mock()
            raid_art.marker_boxes(after)
            self.assertIn(unittest.mock.call(after.members[0].fighter.species,
                                           'kelpie_scale_mail'), sprite.call_args_list)


class EquippedAppearancePreparation(DatabaseCase):
    async def test_battle_preparation_loads_bound_armor_and_preserves_rune_effects(self):
        rid = await self.beast('twine', starter=True)
        other = await self.beast('barkley')
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(?,?,?)',
                           (42, rid, 'gnome_iron_plating'))
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(?,?,?)',
                           (42, other, 'kelpie_scale_mail'))
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(?,NULL,?)',
                           (42, 'sprite_silk_attire'))
        await self.execute('UPDATE player_beasts SET rune_id=? WHERE id=?', ('bark_sigil', rid))
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (rid,)))[0]
        state = await prepare_state(42, row, storage.get_beast_data('twine'), [], consume=False)
        self.assertEqual(state['armor_id'], 'gnome_iron_plating')
        self.assertGreater(state['max_hp'], row['max_hp'], 'The rune still contributes its HP effect')
        self.assertEqual(art.snapshot([state])[0].armor_id, 'gnome_iron_plating')
        await self.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE beast_row_id=?', (rid,))
        next_state = await prepare_state(42, row, storage.get_beast_data('twine'), [], consume=False)
        self.assertEqual(next_state['armor_id'], '')
        self.assertEqual(state['armor_id'], 'gnome_iron_plating', 'Preparing again cannot alter an active state')
