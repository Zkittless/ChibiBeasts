"""Buying and consuming old eggs enforce the same fresh progression gates."""
from unittest.mock import AsyncMock, patch

from cogs.hatch import HATCH_EGGS, Hatch
from cogs.world import EGGS, World
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage
from utils.economy_balance import INCUBATION_LEVELS
from utils.shops import purchase_gold


class HatchProgression(DatabaseCase):
    async def hatch(self, click, egg='celestial_egg'):
        from importlib import import_module
        hatch_module = import_module('cogs.hatch')
        with patch.object(hatch_module.asyncio, 'sleep', new=AsyncMock()), \
                patch.object(hatch_module, 'roll_with_altered', return_value=('epic', False)), \
                patch.object(hatch_module, 'roll_rarity', return_value='epic'):
            await hatch_module.Hatch.hatch.callback(hatch_module.Hatch(None), click, egg)
        message = click.followup.send.return_value
        if message.edit.call_args:
            view = message.edit.call_args.kwargs.get('view')
            if view:
                self.addCleanup(view.stop)

    async def test_instant_purchase_uses_the_new_tier_gates(self):
        self.assertEqual([egg['min_level'] for egg in HATCH_EGGS.values()], [1, 12, 30, 40])
        for egg, required in (('rare_egg', 12), ('celestial_egg', 30), ('abyssal_egg', 40)):
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
        await self.hatch(click)
        self.assertEqual(await storage.get_inventory(42), before)
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertIn('trainer level 30', click.response.send_message.call_args.kwargs['embed'].description)

    async def test_owned_celestial_egg_hatches_at_thirty(self):
        await storage.update_player(42, level=30)
        await storage.add_item(42, 'celestial_egg')
        await self.hatch(interaction())
        self.assertEqual(await storage.get_inventory(42), [])
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(len(beasts), 1)
        self.assertEqual(beasts[0]['rarity'], 'epic')

    async def test_stale_quantity_modal_rechecks_current_level_before_consuming(self):
        await storage.update_player(42, level=30)
        await storage.add_item(42, 'celestial_egg', 2)
        opened = interaction()
        opened.response.send_modal = AsyncMock()
        await Hatch.hatch.callback(Hatch(None), opened, 'celestial_egg')
        modal = opened.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        await storage.update_player(42, level=29)
        submitted = interaction()
        await modal._callback(submitted, 1)
        self.assertIn('trainer level 30', submitted.followup.send.call_args.kwargs['embed'].description)
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
            await World.incubate.callback(World(None), denied, egg)
            self.assertIn(f'trainer level {required}', denied.followup.send.call_args.kwargs['embed'].description)
            self.assertFalse(await self.rows('SELECT * FROM incubating_eggs'))
            self.assertEqual((await self.rows('SELECT quantity FROM player_inventory WHERE item_id=?', (egg,)))[0]['quantity'], 1)
            await storage.update_player(42, level=required)
            await World.incubate.callback(World(None), interaction(), egg)
            self.assertEqual((await self.rows('SELECT egg_type FROM incubating_eggs'))[0]['egg_type'], egg)
            self.assertFalse(await self.rows('SELECT * FROM player_inventory WHERE item_id=?', (egg,)))
            await self.execute('DELETE FROM incubating_eggs')

    async def test_earned_world_tree_seed_can_start_below_the_new_divine_gate(self):
        await storage.add_item(42, 'world_tree_seed')
        await World.incubate.callback(World(None), interaction(), 'world_tree_seed')
        self.assertEqual((await self.rows('SELECT egg_type FROM incubating_eggs'))[0]['egg_type'], 'world_tree_seed')
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_previously_started_high_tier_schedule_can_finish(self):
        await self.execute("INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at,next_tend_at,tends_required) "
                           "VALUES(42,'genesis_matrix','Legacy matrix','divine','2000-01-01 00:00:00','2000-01-01 00:00:00',1)")
        with patch('cogs.world.roll_egg_rarity', return_value='divine'), \
                patch('cogs.world.pick_beast_for_rarity', return_value=storage.get_beast_data('genesis')):
            await World.tend.callback(World(None), interaction())
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
