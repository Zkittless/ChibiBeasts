"""Buying and consuming old eggs enforce the same fresh progression gates."""
from unittest.mock import AsyncMock, patch

import discord

from cogs.hatch import HATCH_EGGS, Hatch
from cogs.world import EGGS, World
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage
from utils.economy_balance import INCUBATION_LEVELS
from utils.shops import purchase_gold


class HatchProgression(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # These tests exercise command acknowledgement, live gates and saved
        # selections. The portrait fallback keeps media work out of that path.
        portrait = patch('utils.gear_art.render_portrait', new=AsyncMock(return_value=None))
        portrait.start()
        self.addCleanup(portrait.stop)

    def view_text(self, view):
        return '\n'.join(child.content for child in view.walk_children()
                         if isinstance(child, discord.ui.TextDisplay))

    async def hatch(self, click, egg='celestial_egg'):
        view = await Hatch.hatch.callback(Hatch(None), click, egg)
        self.addCleanup(view.stop)
        click.response.defer.assert_awaited_once()
        self.assertIs(click.followup.send.call_args.kwargs['view'], view)
        return view

    async def test_instant_purchase_uses_the_new_tier_gates(self):
        self.assertEqual([egg['min_level'] for egg in HATCH_EGGS.values()], [1, 6, 12, 20, 30, 40])
        for egg, required in (('wildheart_egg', 6), ('rare_egg', 12), ('resonant_egg', 20),
                              ('celestial_egg', 30), ('abyssal_egg', 40)):
            await storage.update_player(42, level=required - 1, gold=100000)
            with self.assertRaisesRegex(ValueError, f'trainer level {required}'):
                await purchase_gold(42, egg)
            self.assertFalse(await self.rows('SELECT * FROM player_inventory WHERE item_id=?', (egg,)))
            self.assertEqual((await storage.get_player(42))['gold'], 100000)
            await storage.update_player(42, level=required)
            result = await purchase_gold(42, egg)
            self.assertEqual(result['total'], HATCH_EGGS[egg]['price'])

    async def test_owned_legacy_celestial_egg_waits_at_twenty_nine_without_consumption(self):
        await storage.update_player(42, level=29)
        await storage.add_item(42, 'celestial_egg')
        before = await storage.get_inventory(42)
        click = interaction()
        view = await self.hatch(click)
        self.assertEqual(await storage.get_inventory(42), before)
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertIn('trainer level 30', self.view_text(view))
        self.assertTrue(view.selected()['locked'])

    async def test_owned_celestial_egg_hatches_at_thirty(self):
        await storage.update_player(42, level=30)
        await storage.add_item(42, 'celestial_egg')
        await self.hatch(interaction())
        self.assertEqual(await storage.get_inventory(42), [])
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(len(beasts), 1)
        self.assertIn(beasts[0]['rarity'], ('legendary', 'divine'))
        self.assertGreaterEqual(beasts[0]['level'], 34)
        self.assertLessEqual(beasts[0]['level'], 38)

    async def test_stale_quantity_modal_rechecks_current_level_before_consuming(self):
        await storage.update_player(42, level=30)
        await storage.add_item(42, 'celestial_egg', 2)
        opened = interaction()
        view = await self.hatch(opened)
        quantity_click = interaction()
        quantity_click.response.send_modal = AsyncMock()
        await view.handle(quantity_click, 'quantity', revision=view.revision)
        modal = quantity_click.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        await storage.update_player(42, level=29)
        submitted = interaction()
        await modal._callback(submitted, 1)
        submitted.response.defer.assert_awaited_once()
        self.assertIn('trainer level 30', self.view_text(view))
        self.assertIs(submitted.edit_original_response.call_args.kwargs['view'], view)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 2)
        self.assertEqual(await storage.get_player_beasts(42), [])

    async def test_owned_incubation_eggs_use_current_tier_gates_at_start(self):
        for rarity, required in INCUBATION_LEVELS.items():
            if required == 1:
                continue
            egg = next(key for key, item in EGGS.items() if item['rarity'] == rarity and key != 'world_tree_seed')
            await storage.add_item(42, egg)
            await storage.update_player(42, level=required - 1)
            denied = interaction()
            denied_view = await World.incubate.callback(World(None), denied, egg)
            self.addCleanup(denied_view.stop)
            denied.response.defer.assert_awaited_once()
            self.assertIn(f'trainer level {required}', self.view_text(denied_view))
            self.assertFalse(await self.rows('SELECT * FROM incubating_eggs'))
            self.assertEqual((await self.rows('SELECT quantity FROM player_inventory WHERE item_id=?', (egg,)))[0]['quantity'], 1)
            await storage.update_player(42, level=required)
            allowed_view = await World.incubate.callback(World(None), interaction(), egg)
            self.addCleanup(allowed_view.stop)
            self.assertEqual((await self.rows('SELECT egg_type FROM incubating_eggs'))[0]['egg_type'], egg)
            self.assertFalse(await self.rows('SELECT * FROM player_inventory WHERE item_id=?', (egg,)))
            await self.execute('DELETE FROM incubating_eggs')

    async def test_earned_world_tree_seed_can_start_below_the_new_divine_gate(self):
        await storage.add_item(42, 'world_tree_seed')
        view = await World.incubate.callback(World(None), interaction(), 'world_tree_seed')
        self.addCleanup(view.stop)
        self.assertEqual((await self.rows('SELECT egg_type FROM incubating_eggs'))[0]['egg_type'], 'world_tree_seed')
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_previously_started_high_tier_schedule_can_finish(self):
        await self.execute("INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at,next_tend_at,tends_required) "
                           "VALUES(42,'genesis_matrix','Legacy matrix','divine','2000-01-01 00:00:00','2000-01-01 00:00:00',1)")
        click = interaction()
        view = await World.tend.callback(World(None), click)
        self.addCleanup(view.stop)
        click.response.defer.assert_awaited_once()
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 0)
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual({c['beast_id'] for c in view.offer['candidates']}, {'genesis', 'terminus', 'paradox'})
        self.assertTrue(all(c['rarity'] == 'divine' for c in view.offer['candidates']))
        # Existing schedules stay claimable even below today's Divine gate.
        # Choosing is explicit, and must use the persisted offer rather than a
        # second rarity roll hidden inside /tend.
        selected_id = view.offer['candidates'][0]['beast_id']
        await view.handle(interaction(), 'claim:0', revision=view.revision)
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(len(beasts), 1)
        self.assertEqual(beasts[0]['beast_id'], selected_id)
