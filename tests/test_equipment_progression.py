"""Legacy gear remains owned while trainer gates control its combat effects."""
from unittest.mock import AsyncMock, patch

import discord

from cogs.utilities import Utilities
from tests.test_gameplay import DatabaseCase, interaction
from utils import crafting, db as storage, journal_state
from utils.duels import prepare_state
from utils.equipment import definitions, equip
from utils.journal import JournalView
from utils.workshop import WorkshopView


class EquipmentProgression(DatabaseCase):
    async def ready(self):
        return await self.beast('twine', starter=True)

    async def armor(self, row_id, item='dragon_lord_aegis'):
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)', (row_id, item))

    async def state(self, *, consume=False):
        row = await storage.get_active_beast(42)
        return await prepare_state(42, row, storage.get_beast_data(row['beast_id']), [], consume=consume)

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    async def test_new_locked_armor_and_rune_do_not_move_owned_copies(self):
        row_id = await self.ready()
        await self.armor(None)
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'ouroboros_ring',1)")
        before_armor = await self.rows('SELECT * FROM player_equipment')
        before_items = await self.rows('SELECT * FROM player_inventory')
        for item, level in (('dragon_lord_aegis', 30), ('ouroboros_ring', 40)):
            with self.assertRaisesRegex(ValueError, f'trainer level {level}'):
                await equip(42, 1, item)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), before_armor)
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), before_items)
        self.assertIsNone((await storage.get_active_beast(42))['rune_id'])

    async def test_bound_legacy_gear_is_dormant_without_losing_binding_or_appearance(self):
        row_id = await self.ready()
        baseline = await self.state()
        await self.armor(row_id)
        await self.execute("UPDATE player_beasts SET rune_id='ouroboros_ring' WHERE id=?", (row_id,))
        before = await self.rows('SELECT * FROM player_equipment')
        for consume in (False, True):
            dormant = await self.state(consume=consume)
            for stat in ('hp', 'max_hp', 'attack', 'defense', 'speed', 'mana', 'max_mana'):
                self.assertEqual(dormant[stat], baseline[stat], stat)
            self.assertNotIn('lifesteal_percent', dormant)
            self.assertNotIn('damage_reduction_flat_percent', dormant)
            self.assertEqual(dormant['armor_id'], 'dragon_lord_aegis')
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), before)
        self.assertEqual((await storage.get_active_beast(42))['rune_id'], 'ouroboros_ring')

    async def test_existing_gear_activates_at_its_real_trainer_threshold(self):
        row_id = await self.ready()
        baseline = await self.state()
        await self.armor(row_id)
        await self.execute("UPDATE player_beasts SET rune_id='ouroboros_ring' WHERE id=?", (row_id,))
        await storage.update_player(42, level=30)
        armor_only = await self.state()
        self.assertEqual(armor_only['damage_reduction_flat_percent'], 20)
        self.assertNotIn('lifesteal_percent', armor_only)
        await storage.update_player(42, level=40)
        full = await self.state()
        self.assertEqual(full['lifesteal_percent'], definitions()['ouroboros_ring']['effect']['lifesteal_percent'])
        self.assertGreater(full['max_hp'], armor_only['max_hp'])

    async def test_legacy_rune_rows_are_gated_and_common_gear_still_works(self):
        row_id = await self.ready()
        baseline = await self.state()
        await self.armor(row_id, 'core_of_the_phoenix')
        self.assertNotIn('lifesteal_percent', await self.state())
        await self.armor(None, 'gnome_iron_plating')
        await equip(42, 1, 'gnome_iron_plating')
        legal = await self.state()
        self.assertGreater(legal['defense'], baseline['defense'])
        self.assertNotIn('lifesteal_percent', legal)
        self.assertTrue(await self.rows("SELECT * FROM player_equipment WHERE equipment_id='core_of_the_phoenix' AND beast_row_id=?", (row_id,)))

    async def test_workshop_journal_and_gear_panel_explain_dormant_copies(self):
        row_id = await self.ready()
        await self.armor(row_id)
        await self.armor(None)
        workshop = WorkshopView(42, await crafting.workshop_state(42), selected='dragon_lord_aegis')
        journal = JournalView(42, await journal_state.snapshot(42), page='companions')
        self.addCleanup(workshop.stop)
        self.addCleanup(journal.stop)
        self.assertIn('Dormant until Trainer Lv.30', self.text(workshop))
        self.assertTrue(workshop.use_button.disabled)
        self.assertIn('Dormant until Trainer Lv.30', self.text(journal))
        click = interaction()
        with patch('utils.gear_art.render_portrait', new=AsyncMock(return_value=None)):
            await Utilities.gear.callback(Utilities(None), click, 1)
        embed = click.followup.send.call_args.kwargs['embed']
        self.assertIn('Dormant until Trainer Lv.30', '\n'.join(field.value for field in embed.fields))
