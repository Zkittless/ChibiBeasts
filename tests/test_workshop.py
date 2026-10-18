"""Real workshop transactions and Discord dispatch, without a live player account."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui.view import ViewStore

from cogs.world import World
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import crafting, db as storage
from utils.duels import reserve_fighters
from utils.equipment import equip
from utils.gameplay import evolve_beast
from utils.workshop import WorkshopView


class Workshop(DatabaseCase):
    async def material(self, key, amount):
        await self.execute('INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,?,?)', (key, amount))

    async def view(self, selected=None):
        view = WorkshopView(42, await crafting.workshop_state(42), selected=selected)
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    async def click(self, view, action, values=None, uid=42, edit=None):
        click = component(uid=uid, edit=edit)
        if values is not None:
            click.data = {'values': [str(values)]}
        await view.handle(click, action)
        return click

    async def test_craft_opens_without_spending_and_named_quick_craft_stays_compatible(self):
        await self.material('gnome_iron_ore', 3)
        initial = interaction()
        await World.craft.callback(World(None), initial)
        view = initial.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        self.assertIsInstance(view, WorkshopView)
        self.assertEqual(view.selected, 'sprite_silk_attire')
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])
        self.assertFalse(World.craft.parameters[0].required)
        self.assertNotIn('recipes', {command.name for command in World.__cog_app_commands__})
        named = interaction()
        await World.craft.callback(World(None), named, 'Gnome-Iron Plating')
        view = named.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        self.assertEqual(view.selected, 'gnome_iron_plating')
        self.assertEqual(view.owned, 1)
        self.assertIn('Ready to equip', view.notice)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_material_button_opens_the_workshop_bag(self):
        view = await self.view()
        click = await self.click(view, 'materials')
        click.response.defer.assert_awaited_once()
        self.assertEqual(view.drawer, 'materials')
        self.assertIn('material bag is empty', self.text(view))

    async def test_failed_craft_rolls_back_ingredients_already_checked(self):
        await self.material('pixie_silk', 2)
        before = await self.rows('SELECT * FROM player_materials')
        with self.assertRaisesRegex(ValueError, 'Enchanted Bark'):
            await crafting.craft_item(42, 'sprite_silk_attire')
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), before)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])

    async def test_two_open_panels_cannot_spend_the_same_material_stack(self):
        await self.material('gnome_iron_ore', 3)
        results = await asyncio.gather(crafting.craft_item(42, 'gnome_iron_plating'),
            crafting.craft_item(42, 'gnome_iron_plating'), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment')), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_legacy_split_material_stacks_are_counted_and_charged_exactly(self):
        await self.material('pixie_silk', 1)
        await self.material('pixie_silk', 2)
        await self.material('enchanted_bark', 1)
        view = await self.view()
        self.assertEqual(view.state.materials['pixie_silk'], 3)
        await self.click(view, 'craft')
        self.assertEqual(sum(row['quantity'] for row in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment')), 1)

    async def test_forge_membership_is_rechecked_when_a_ready_panel_crafts(self):
        await self.material('gnome_iron_ore', 2)
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Forge')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,gnome_forge) VALUES(3,1)')
        view = await self.view('gnome_iron_plating')
        self.assertFalse(view.craft_button.disabled)
        await self.execute('DELETE FROM guild_members WHERE user_id=42')
        click = await self.click(view, 'craft')
        click.response.defer.assert_awaited_once()
        self.assertIn('Not enough', view.notice)
        self.assertEqual((await self.rows('SELECT quantity FROM player_materials'))[0]['quantity'], 2)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])
        self.assertIn('3 needed', self.text(view))

    async def test_buttons_acknowledge_before_waiting_for_database_reads(self):
        view = await self.view()
        started, release = asyncio.Event(), asyncio.Event()
        original = crafting.workshop_state
        click = component()

        async def delayed(uid):
            self.assertTrue(click.response.is_done())
            started.set()
            await release.wait()
            return await original(uid)

        with patch.object(crafting, 'workshop_state', side_effect=delayed):
            task = asyncio.create_task(view.handle(click, 'refresh'))
            try:
                await asyncio.wait_for(started.wait(), 1)
                click.response.defer.assert_awaited_once()
            finally:
                release.set()
                await asyncio.wait_for(task, 2)

    async def test_rapid_double_craft_is_acknowledged_and_grants_one_copy(self):
        await self.material('gnome_iron_ore', 6)
        view = await self.view('gnome_iron_plating')
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed_edit(**kwargs):
            started.set()
            await release.wait()

        first, second = component(edit=delayed_edit), component()
        task = asyncio.create_task(view.handle(first, 'craft'))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(view.handle(second, 'craft'), 1)
            second.response.defer.assert_awaited_once()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment')), 1)
        self.assertEqual((await self.rows('SELECT quantity FROM player_materials'))[0]['quantity'], 3)

    async def test_dispatch_keeps_every_category_and_recipe_usable_after_edits(self):
        view = await self.view()
        message = view.message
        store = ViewStore(None)
        store.add_view(view, message.id)
        stable = view.recipe_select.custom_id
        for _ in range(2):
            for button, section in zip(view.tabs.children, crafting.SECTIONS):
                edited = asyncio.Event()

                async def edit(**kwargs):
                    store.add_view(kwargs['view'], message.id)
                    edited.set()
                    return message

                click = component(edit=edit)
                click.message = message
                store.dispatch_view(2, button.custom_id, click)
                await asyncio.wait_for(edited.wait(), 2)
                self.assertEqual(view.section, section)
                self.assertEqual(view.recipe_select.custom_id, stable)
                for key in crafting.recipe_ids(section):
                    edited.clear()
                    click = component(section=key, edit=edit)
                    click.message = message
                    store.dispatch_view(3, stable, click)
                    await asyncio.wait_for(edited.wait(), 2)
                    self.assertEqual(view.selected, key)
                    click.response.defer.assert_awaited_once()
                    self.assertLessEqual(view.content_length(), 4000)
                    self.assertLessEqual(len(view.recipe_select.options), 25)

    async def test_other_trainers_cannot_use_any_panel_controls(self):
        view = await self.view()
        for action in ('craft', 'use', 'confirm', 'next', 'previous', 'section:runes', 'target'):
            click = await self.click(view, action, uid=77)
            click.response.send_message.assert_awaited_once()
            self.assertTrue(click.response.send_message.call_args.kwargs['ephemeral'])
            click.edit_original_response.assert_not_awaited()
        self.assertEqual(view.section, 'armor')

    async def test_late_input_cannot_craft_the_recipe_from_a_newer_selection(self):
        await self.material('pixie_silk', 4)
        view = await self.view()
        new = component()
        new.id = 200
        await view.handle(new, 'section:runes')
        old = component()
        old.id = 199
        await view.handle(old, 'craft')
        self.assertIn('older panel', old.followup.send.call_args.args[0])
        self.assertEqual((await self.rows('SELECT quantity FROM player_materials'))[0]['quantity'], 4)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_armor_crafts_then_replaces_on_the_same_panel(self):
        row_id = await self.beast('twine')
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (row_id,))
        await self.material('pixie_silk', 2)
        await self.material('enchanted_bark', 1)
        view = await self.view()
        await self.click(view, 'craft')
        await self.click(view, 'use')
        self.assertIn('Gnome-Iron Plating', self.text(view))
        self.assertIn('returns to your bag', self.text(view))
        await self.click(view, 'confirm')
        rows = await self.rows('SELECT equipment_id,beast_row_id FROM player_equipment ORDER BY id')
        self.assertEqual(rows, [{'equipment_id':'gnome_iron_plating','beast_row_id':None},
                                {'equipment_id':'sprite_silk_attire','beast_row_id':row_id}])
        self.assertFalse(view.using)

    async def test_rune_replacement_is_atomic_and_returns_the_old_rune(self):
        row_id = await self.beast()
        await self.execute("UPDATE player_beasts SET rune_id='pixie_ribbon' WHERE id=?", (row_id,))
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        self.assertEqual(view.confirm_button.label, 'Replace & return old')
        await self.click(view, 'confirm')
        self.assertEqual((await storage.get_player_beasts(42))[0]['rune_id'], 'iron_dust_chip')
        self.assertEqual({row['item_id']:row['quantity'] for row in await storage.get_inventory(42)}, {'pixie_ribbon':1})

    async def test_legacy_unequipped_runes_are_visible_and_can_be_equipped(self):
        await self.beast()
        await self.execute("INSERT INTO player_equipment(user_id,equipment_id) VALUES(42,'iron_dust_chip')")
        view = await self.view('iron_dust_chip')
        self.assertEqual(view.owned, 1)
        await self.click(view, 'use')
        await self.click(view, 'confirm')
        self.assertEqual((await storage.get_player_beasts(42))[0]['rune_id'], 'iron_dust_chip')
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])

    async def test_armor_replacement_preserves_a_legacy_equipped_rune(self):
        row_id = await self.beast()
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'pixie_ribbon')", (row_id,))
        await self.execute("INSERT INTO player_equipment(user_id,equipment_id) VALUES(42,'gnome_iron_plating')")
        view = await self.view('gnome_iron_plating')
        self.assertEqual(view.state.companions[0]['rune_id'], 'pixie_ribbon')
        await self.click(view, 'use')
        await self.click(view, 'confirm')
        rows = await self.rows("SELECT beast_row_id FROM player_equipment WHERE equipment_id='pixie_ribbon'")
        self.assertEqual(rows, [{'beast_row_id':row_id}])

    async def test_replacing_a_legacy_rune_returns_one_copy_even_if_its_column_is_mirrored(self):
        row_id = await self.beast()
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'pixie_ribbon')", (row_id,))
        await self.execute("UPDATE player_beasts SET rune_id='pixie_ribbon' WHERE id=?", (row_id,))
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        await self.click(view, 'confirm')
        self.assertEqual(view.state.owned.get('pixie_ribbon'), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])

    async def test_unequipping_a_mirrored_legacy_rune_returns_one_copy(self):
        from utils.equipment import unequip
        row_id = await self.beast()
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'pixie_ribbon')", (row_id,))
        await self.execute("UPDATE player_beasts SET rune_id='pixie_ribbon' WHERE id=?", (row_id,))
        await unequip(42, 1)
        self.assertEqual((await crafting.workshop_state(42)).owned.get('pixie_ribbon'), 1)

    async def test_a_battle_refusal_preserves_gear_and_the_panel_can_be_retried(self):
        await self.beast()
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        with reserve_fighters([42]):
            await self.click(view, 'confirm')
        self.assertIn('already in a battle', view.notice)
        self.assertEqual((await storage.get_player_beasts(42))[0]['rune_id'], None)
        self.assertEqual(view.owned, 1)
        await self.click(view, 'confirm')
        self.assertIn('equipped', view.notice)

    async def test_changed_equipment_requires_a_new_review_without_consuming_a_copy(self):
        row_id = await self.beast()
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        await self.execute("UPDATE player_beasts SET rune_id='pixie_ribbon' WHERE id=?", (row_id,))
        await self.click(view, 'confirm')
        self.assertIn('equipment changed', view.notice)
        self.assertEqual(view.owned, 1)
        self.assertEqual((await storage.get_player_beasts(42))[0]['rune_id'], 'pixie_ribbon')

    async def test_renumbering_does_not_apply_a_confirmation_to_another_companion(self):
        first = await self.beast()
        second = await self.beast()
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        await self.execute('UPDATE player_beasts SET player_number=99 WHERE id=?', (first,))
        await self.execute('UPDATE player_beasts SET player_number=1 WHERE id=?', (second,))
        await self.click(view, 'confirm')
        self.assertIn('companion changed', view.notice)
        self.assertTrue(all(not row['rune_id'] for row in await storage.get_player_beasts(42)))
        self.assertEqual(view.owned, 1)

    async def test_pagination_reaches_companions_beyond_discord_select_limit(self):
        for _ in range(26):
            await self.beast('slime')
        await storage.add_item(42, 'iron_dust_chip')
        view = await self.view('iron_dust_chip')
        await self.click(view, 'use')
        self.assertEqual(len(view.target_select.options), 25)
        await self.click(view, 'next')
        self.assertEqual(len(view.target_select.options), 1)
        self.assertIn('#26', view.target_select.options[0].label)
        await self.click(view, 'confirm')
        rows = await storage.get_player_beasts(42)
        self.assertEqual([row['player_number'] for row in rows if row['rune_id']], [26])
        self.assertLessEqual(sum(1 for _ in view.walk_children()), 40)

    async def test_catalyst_evolution_uses_the_selected_item_and_shows_the_new_form(self):
        row_id = await self.beast('goblin')
        await self.execute('UPDATE player_beasts SET level=25 WHERE id=?', (row_id,))
        await storage.add_item(42, 'forge_ember')
        view = await self.view('forge_ember')
        await self.click(view, 'use')
        self.assertIn('Radiant Goblin', self.text(view))
        await self.click(view, 'confirm')
        self.assertEqual((await storage.get_player_beasts(42))[0]['beast_id'], 'radiant_goblin')
        self.assertEqual(view.result_species, 'radiant_goblin')
        self.assertEqual(view.owned, 0)
        await self.click(view, 'confirm')
        self.assertIn('review it before confirming', view.notice)

    async def test_stale_catalyst_confirmation_never_substitutes_a_sunforge_core(self):
        row_id = await self.beast('goblin')
        await self.execute('UPDATE player_beasts SET level=25 WHERE id=?', (row_id,))
        await storage.add_item(42, 'forge_ember')
        await storage.add_item(42, 'sunforge_core')
        view = await self.view('forge_ember')
        await self.click(view, 'use')
        await self.execute("DELETE FROM player_inventory WHERE item_id='forge_ember'")
        await self.click(view, 'confirm')
        self.assertEqual((await storage.get_player_beasts(42))[0]['beast_id'], 'goblin')
        self.assertEqual((await storage.get_inventory(42))[0]['item_id'], 'sunforge_core')

    async def test_legacy_split_catalysts_consume_one_copy_for_one_evolution(self):
        row_id = await self.beast('goblin')
        await self.execute('UPDATE player_beasts SET level=25 WHERE id=?', (row_id,))
        for _ in range(2):
            await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'forge_ember',1)")
        view = await self.view('forge_ember')
        self.assertEqual(view.owned, 2)
        await self.click(view, 'use')
        await self.click(view, 'confirm')
        self.assertEqual(view.owned, 1)
        self.assertEqual((await storage.get_player_beasts(42))[0]['beast_id'], 'radiant_goblin')

    async def test_underlevel_evolution_and_a_mismatched_catalyst_preserve_inventory(self):
        row_id = await self.beast('goblin')
        await storage.add_item(42, 'forge_ember')
        view = await self.view('forge_ember')
        await self.click(view, 'use')
        self.assertTrue(view.confirm_button.disabled)
        await self.click(view, 'confirm')
        self.assertEqual(view.owned, 1)
        await self.execute('UPDATE player_beasts SET level=25 WHERE id=?', (row_id,))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            await evolve_beast(42, 1, required_item='void_shard')

    async def test_rejected_art_keeps_the_complete_workshop_usable(self):
        view = await self.view()
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message':'Missing Attach Files'})
        command = interaction()
        command.followup.send.side_effect = [forbidden, view.message]
        with self.assertLogs('chibibeasts.workshop', level='WARNING'):
            await view.publish(command, opening=True)
        self.assertFalse(view.art_enabled)
        self.assertIn('Materials for one', self.text(view))
        self.assertFalse(any(isinstance(child, discord.ui.MediaGallery) for child in view.walk_children()))
        await self.click(view, 'section:runes')
        self.assertEqual(view.section, 'runes')

    async def test_redraw_retains_the_uploaded_banner(self):
        view = await self.view()
        banner = SimpleNamespace(filename='header.png')
        view.message.attachments = [banner]
        click = component()
        await view.publish(click)
        self.assertEqual(click.edit_original_response.call_args.kwargs['attachments'], [banner])

    async def test_deleted_panel_does_not_retry_a_completed_craft(self):
        await self.material('gnome_iron_ore', 6)
        view = await self.view('gnome_iron_plating')
        missing = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), {'message':'Unknown Message','code':10008})
        click = await self.click(view, 'craft', edit=AsyncMock(side_effect=missing))
        self.assertTrue(view.closed)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment')), 1)
        self.assertIn('message was removed', click.followup.send.call_args.args[0])

    async def test_timeout_disables_all_controls_and_returns_a_clear_expiry_message(self):
        view = await self.view()
        await view.on_timeout()
        controls = [item for item in view.walk_children() if isinstance(item, (discord.ui.Select,discord.ui.Button))]
        self.assertTrue(all(item.disabled for item in controls))
        click = await self.click(view, 'refresh')
        click.response.defer.assert_awaited_once()
        self.assertIn('closed', click.followup.send.call_args.args[0])
