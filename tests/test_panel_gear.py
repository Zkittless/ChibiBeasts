"""The Gear panel reuses atomic equipment rules and binds confirmations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage, equipment
from utils.gear_view import GearView


class GearActions(DatabaseCase):
    async def ready(self):
        await storage.update_player(42, level=30)
        row_id = await self.beast('twine', starter=True)
        await self.execute("INSERT INTO player_equipment(user_id,equipment_id) VALUES(42,'gnome_iron_plating')")
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'bark_sigil',2)")
        view = GearView(42)
        view.art_enabled = False
        await view.reload()
        view.selected_id = row_id
        view.message = SimpleNamespace(id=123, attachments=[])
        view.render()
        self.addCleanup(view.stop)
        return view, row_id

    async def press(self, view, label):
        button = next(item for item in view.children if isinstance(item, discord.ui.Button) and item.label == label)
        click = component()
        click.edit_original_response.return_value = view.message
        await button.callback(click)
        return click

    async def test_armor_and_rune_can_be_equipped_and_removed_independently(self):
        view, row_id = await self.ready()
        await self.press(view, 'Change armor')
        await self.press(view, 'Equip selected')
        self.assertEqual(view.armor_and_rune()[0], 'gnome_iron_plating')
        await self.press(view, 'Change rune')
        await self.press(view, 'Equip selected')
        self.assertEqual(view.armor_and_rune(), ('gnome_iron_plating', 'bark_sigil'))
        await self.press(view, 'Remove rune')
        self.assertEqual(view.armor_and_rune(), ('gnome_iron_plating', None))
        self.assertEqual((await self.rows("SELECT SUM(quantity) n FROM player_inventory WHERE item_id='bark_sigil'"))[0]['n'], 2)
        await self.press(view, 'Change armor')
        await self.press(view, 'Remove armor')
        self.assertEqual(view.armor_and_rune(), ('', None))
        self.assertEqual((await self.rows('SELECT COUNT(*) n FROM player_equipment WHERE beast_row_id IS NULL'))[0]['n'], 1)
        self.assertEqual((await storage.get_active_beast(42))['id'], row_id)

    async def test_old_confirm_cannot_replace_new_equipment(self):
        view, row_id = await self.ready()
        await self.press(view, 'Change armor')
        old = next(item.callback for item in view.children if getattr(item, 'label', '') == 'Equip selected')
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'kelpie_scale_mail')", (row_id,))
        click = component()
        click.edit_original_response.return_value = view.message
        await old(click)
        self.assertIn('equipment changed', view.notice)
        self.assertEqual(view.armor_and_rune()[0], 'kelpie_scale_mail')

    async def test_old_confirm_cannot_follow_a_transferred_or_replaced_beast(self):
        view, row_id = await self.ready()
        await self.press(view, 'Change armor')
        old = next(item.callback for item in view.children if getattr(item, 'label', '') == 'Equip selected')
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (row_id,))
        await self.beast('gloop')
        click = component()
        click.edit_original_response.return_value = view.message
        await old(click)
        self.assertIn('no longer in your collection', view.notice)
        self.assertFalse(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id IS NOT NULL'))

    async def test_confirmation_rechecks_level_and_inventory(self):
        view, _ = await self.ready()
        await self.execute("UPDATE player_equipment SET equipment_id='unicorn_healer_vestments'")
        await self.press(view, 'Change armor')
        await self.execute('UPDATE players SET level=1 WHERE user_id=42')
        await self.press(view, 'Equip selected')
        self.assertIn('Reach trainer level', view.notice)
        self.assertEqual(view.armor_and_rune()[0], '')
        await storage.update_player(42, level=30)
        await view.handle(component(), 'refresh')
        await self.execute('DELETE FROM player_equipment')
        await self.press(view, 'Equip selected')
        self.assertIn('unequipped copy', view.notice)

    async def test_atomic_remove_rejects_changed_identity_and_current_gear(self):
        view, row_id = await self.ready()
        await equipment.equip(42, 1, 'gnome_iron_plating')
        for kwargs in ({'expected_row_id': row_id + 1}, {'expected_equipment': 'kelpie_scale_mail'}):
            with self.assertRaises(ValueError):
                await equipment.unequip(42, 1, slot='armor', **kwargs)
        self.assertTrue(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id=?', (row_id,)))

    async def test_other_users_cannot_open_or_confirm_gear_changes(self):
        view, _ = await self.ready()
        await self.press(view, 'Change armor')
        button = next(item for item in view.children if getattr(item, 'label', '') == 'Equip selected')
        click = component(uid=43)
        await button.callback(click)
        click.response.send_message.assert_awaited_once()
        self.assertFalse(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id IS NOT NULL'))
