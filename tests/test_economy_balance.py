"""Economy regressions exercise actual purchase, crafting, item and reward paths."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from utils import db as storage, combat
from utils.catalog import load_catalog
from utils.economy_balance import STAT_TREAT_LIMIT, material_sources
from utils.equipment import effect_summary
from utils.items import use_item
from utils.shops import purchase_gold, purchase_guild, purchase_shard, gold_catalog
from tests.test_gameplay import DatabaseCase, interaction


class CatalogBalance(unittest.TestCase):
    def test_every_gold_offer_costs_money_and_the_story_seed_is_not_for_sale(self):
        offers = gold_catalog()
        self.assertNotIn('world_tree_seed',offers)
        self.assertTrue(all(item['price']>0 for item in offers.values()))
        self.assertEqual(offers['genesis_matrix']['price'],80000)

    def test_cheap_eggs_cannot_farm_release_gold_with_all_bonuses(self):
        from scripts.audit_economy import egg_values
        self.assertTrue(all(row['expected_net_gold']<0 for row in egg_values()))

    def test_common_eggs_keep_their_advertised_tiers_with_stardust_and_observatory(self):
        from cogs.hatch import get_egg_pool
        pool = get_egg_pool('common_egg',[{'perk_id':'stardust_touch','equipped':1}],{'celestial_observatory':1})
        self.assertEqual(set(pool),{'common','uncommon'})
        self.assertAlmostEqual(sum(pool.values()),1)

    def test_exact_compass_odds_exclude_empty_legendary_loom_pool(self):
        from utils.habitats import set_rarity_chance, BIOMES
        for chance in (.2,.3):
            pool = set_rarity_chance(BIOMES['loom']['weights'],'loom','divine',chance)
            self.assertNotIn('legendary',pool)
            self.assertAlmostEqual(pool['divine'],chance)
            self.assertAlmostEqual(sum(pool.values()),1)

    def test_every_crafting_material_has_a_real_route_and_positive_recipe(self):
        from scripts.audit_economy import RECIPES
        from utils.habitats import BIOMES, material_pool
        # Expeditions roll a regional bundle before discovering any creature.
        gatherable = {mid for biome in BIOMES for mid in material_pool({'id': '', 'type': ''}, biome)}
        raid_only={'chronos_gear','tear_of_the_creator','blighted_world_core'}
        for item in RECIPES.values():
            for mid,qty in item['recipe'].items():
                self.assertGreater(qty,0)
                self.assertTrue(mid in raid_only or mid in gatherable,mid)
                self.assertTrue(material_sources(mid))

    def test_every_gear_effect_is_explained_to_players(self):
        data=load_catalog('equipment.json')
        for group in data.values():
            for key,item in group.items():
                text=effect_summary(item)
                self.assertTrue(text,key)
                self.assertNotIn('True',text,key)

    def test_gathering_hints_describe_expedition_regions_without_old_species_bonuses(self):
        self.assertIn('Whispering Woods', material_sources('pixie_silk'))
        self.assertNotIn('Fairy', material_sources('pixie_silk'))
        self.assertIn('Sunken Abyssal Trenches', material_sources('void_essence'))
        self.assertNotIn('shadow encounters', material_sources('void_essence'))
        for material in load_catalog('materials.json')['materials']:
            self.assertNotIn('extra weight', material_sources(material), material)

    def test_historical_currency_reset_model_has_no_positive_expected_profit(self):
        from scripts.audit_economy import reset_loops
        self.assertTrue(all(row['expected_net_gold']<0 for row in reset_loops()))


class EconomyTransactions(DatabaseCase):
    async def test_divine_purchase_checks_level_then_charges_the_real_price(self):
        await storage.update_player(42,gold=200000)
        with self.assertRaisesRegex(ValueError,'level 40'):
            await purchase_gold(42,'genesis_matrix')
        self.assertEqual(await storage.get_inventory(42),[])
        await storage.update_player(42,level=40)
        result=await purchase_gold(42,'genesis_matrix',2)
        self.assertEqual((result['total'],result['remaining']),(160000,40000))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],2)

    async def test_overlapping_gold_purchases_cannot_overspend(self):
        await storage.update_player(42,gold=200)
        results=await asyncio.gather(*(purchase_gold(42,'common_egg') for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
        self.assertEqual((await storage.get_player(42))['gold'],0)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],1)

    async def test_shop_uses_fresh_price_and_merchant_discount_at_checkout(self):
        from cogs.profile import Shop
        from cogs.hatch import HATCH_EGGS
        await self.perk('whimsy_merchant')
        it=interaction()
        await Shop.shop.callback(Shop(None),it)
        view=it.followup.send.call_args.kwargs['view']
        # The default page is instant eggs; choose its first one-item buy button.
        button=view.children[1]
        changed={**HATCH_EGGS['common_egg'],'price':300}
        with patch.dict(HATCH_EGGS,{'common_egg':changed}), patch('utils.progress.track_quest_event',new=AsyncMock(return_value=[])):
            await button.callback(interaction())
        self.assertEqual((await storage.get_player(42))['gold'],215)
        self.assertEqual((await storage.get_inventory(42))[0]['item_id'],'common_egg')

    async def test_invalid_or_zero_quantities_cannot_create_items_or_gold(self):
        for quantity in (-1,0,True,101):
            with self.assertRaises(ValueError):
                await purchase_gold(42,'common_egg',quantity)
        self.assertEqual((await storage.get_player(42))['gold'],500)
        self.assertEqual(await storage.get_inventory(42),[])

    async def test_locked_shard_item_preserves_currency_and_weekly_limit(self):
        await storage.update_player(42,celestial_shards=100)
        with self.assertRaisesRegex(ValueError,'level 30'):
            await purchase_shard(42,'divine_compass')
        player=await storage.get_player(42)
        self.assertEqual(player['celestial_shards'],100)
        self.assertFalse(player['shard_shop_week'])

    async def test_legacy_personal_tokens_migrate_once_without_touching_guild_bank(self):
        await self.execute("DELETE FROM game_meta WHERE key='economy-tickets-2026-10'")
        await storage.update_player(42,guild_tokens=70,guild_tickets=20)
        await self.execute("INSERT INTO guilds(id,name,guild_tokens) VALUES(3,'Bank',250)")
        await storage.init_db()
        first=await storage.get_player(42)
        self.assertEqual((first['guild_tickets'],first['guild_tokens']),(90,0))
        await storage.init_db()
        self.assertEqual(await storage.get_player(42),first)
        self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds'))[0]['guild_tokens'],250)

    async def test_raid_tickets_can_be_spent_and_reward_replay_cannot_duplicate_them(self):
        from utils.raids import RaidSession
        from cogs.guilds import RAID_BOSSES
        await self.execute("INSERT INTO guilds(id,name,level) VALUES(3,'Raid guild',5)")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        raid=RaidSession(None,RAID_BOSSES['corrupted'][0],{42},3)
        raid.id=55
        with patch('utils.raids.random.random',return_value=1):
            await raid.reward(42)
        self.assertEqual((await storage.get_player(42))['guild_tickets'],10)
        await purchase_guild(42,'guild_elixir')
        self.assertEqual((await storage.get_player(42))['guild_tickets'],5)
        await raid.reward(42)
        self.assertEqual((await storage.get_player(42))['guild_tickets'],5)

    async def test_concurrent_contributions_pay_and_advance_the_guild_once(self):
        from cogs.guilds import Guilds
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Contribution guild')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await asyncio.gather(*(Guilds.contribute.callback(Guilds(None),interaction()) for _ in range(2)))
        guild=(await self.rows('SELECT * FROM guilds'))[0]
        self.assertEqual(guild['total_contributions'],1)
        self.assertTrue(80<=guild['exp']<=150)
        self.assertTrue(5<=(await storage.get_player(42))['guild_tickets']<=10)

    async def test_temporary_boost_does_not_compound_or_consume_repeated_items(self):
        row_id=await self.beast()
        await storage.add_item(42,'ironbark_salve',2)
        data=storage.get_beast_data('prismite')
        row=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row_id,)))[0]
        actor=combat.build_state(data,row)
        combat.active_states[42]=actor
        base=actor['defense']
        await use_item(42,'ironbark_salve')
        self.assertEqual(actor['defense'],base+int(base*.2))
        with self.assertRaisesRegex(ValueError,'already active'):
            await use_item(42,'ironbark_salve')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],1)

    async def test_queued_boost_does_not_consume_a_duplicate(self):
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await storage.add_item(42,'quicksilver_tonic',2)
        await use_item(42,'quicksilver_tonic')
        with self.assertRaisesRegex(ValueError,'already queued'):
            await use_item(42,'quicksilver_tonic')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],1)

    async def test_growth_food_never_scales_with_trainer_level(self):
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await storage.update_player(42,level=100)
        await storage.add_item(42,'gnome_grown_carrots')
        await use_item(42,'gnome_grown_carrots')
        row=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row_id,)))[0]
        total=row['exp']+sum(storage.calc_exp_for_level(level) for level in range(1,row['level']))
        self.assertEqual(total,20)

    async def test_stat_treat_limit_preserves_the_next_item(self):
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await self.execute('UPDATE player_beasts SET stat_treats_used=? WHERE id=?',(STAT_TREAT_LIMIT-1,row_id))
        await storage.add_item(42,'star_candy_shards',2)
        await use_item(42,'star_candy_shards')
        with self.assertRaisesRegex(ValueError,'treat limit'):
            await use_item(42,'star_candy_shards')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],1)
        self.assertEqual((await self.rows('SELECT stat_treats_used FROM player_beasts'))[0]['stat_treats_used'],30)

    async def test_craft_unlock_preserves_materials_then_all_recipes_can_be_created(self):
        from cogs.world import World
        from scripts.audit_economy import RECIPES
        for mid in load_catalog('materials.json')['materials']:
            await self.execute('INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,?,1000)',(mid,))
        before=await self.rows('SELECT * FROM player_materials')
        await World.craft.callback(World(None),interaction(),'dragon_lord_aegis')
        self.assertEqual(await self.rows('SELECT * FROM player_materials'),before)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'),[])
        await storage.update_player(42,level=50)
        for key in RECIPES:
            await World.craft.callback(World(None),interaction(),key)
        crafted=len(await self.rows('SELECT * FROM player_equipment'))+sum(r['quantity'] for r in await storage.get_inventory(42))
        self.assertEqual(crafted,len(RECIPES))

    async def test_exploration_grants_exactly_one_material_on_quiet_win_and_loss(self):
        from cogs.hatch import Hatch
        from utils import expeditions
        import discord
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        async def result(*args, **kwargs):
            await args[7 if outcome == 'win' else 8](discord.Embed(), {}, {})
        for outcome in ('quiet','win','loss'):
            await storage.update_player(42,explore_last_at=0)
            await self.execute('DELETE FROM exploration_energy WHERE user_id=42')
            before=sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
            view = await Hatch.explore.callback(Hatch(None), interaction())
            self.addCleanup(view.stop)
            trip = view.trip
            if outcome != 'quiet':
                with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('fairy')):
                    trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks')
                trip = await expeditions.choose(42, trip['id'], trip['version'], 'challenge')
                with patch('cogs.battle.run_pve_battle', side_effect=result):
                    trip = await expeditions.run_battle(interaction(), trip)
            self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), before)
            await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
            after=sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
            self.assertEqual(after-before,1,outcome)

    async def test_workshop_explains_effects_and_respects_discord_limits(self):
        import discord
        from cogs.world import World
        it=interaction()
        await World.craft.callback(World(None),it)
        view=it.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        self.assertTrue(any('SPD' in child.content for child in view.walk_children() if isinstance(child,discord.ui.TextDisplay)))
        for section in ('runes','evolution'):
            bi=interaction()
            await view.handle(bi,'section:'+section)
            self.assertLessEqual(view.content_length(),4000,section)
            self.assertLessEqual(sum(1 for _ in view.walk_children()),40,section)
        await view.handle(interaction(),'sources')
        self.assertTrue(any('Where to gather' in child.content for child in view.walk_children() if isinstance(child,discord.ui.TextDisplay)))

    async def test_crafting_autocomplete_uses_actual_unlocks_and_forge_costs(self):
        from cogs.world import World
        await self.execute('INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,\'gnome_iron_ore\',2)')
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Forge')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,gnome_forge) VALUES(3,1)')
        choices=await World.craft_autocomplete(World(None),interaction(),'Gnome-Iron')
        self.assertEqual(choices[0].value,'gnome_iron_plating')
        self.assertTrue(choices[0].name.startswith('✅'))
        locked=await World.craft_autocomplete(World(None),interaction(),'Dragon-Lord')
        self.assertTrue(locked[0].name.startswith('🔒'))

    async def test_prepared_battle_boost_cannot_be_stacked_again_with_an_item(self):
        from utils.duels import prepare_state
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await storage.add_item(42,'ironbark_salve',2)
        await use_item(42,'ironbark_salve')
        row=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row_id,)))[0]
        actor=await prepare_state(42,row,storage.get_beast_data('prismite'),[])
        combat.active_states[42]=actor
        with self.assertRaisesRegex(ValueError,'already active'):
            await use_item(42,'ironbark_salve')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'],1)

    async def test_treat_allowance_and_gains_survive_starter_evolution(self):
        from utils.gameplay import evolve_beast
        await storage.update_player(42, level=8)
        row_id=await self.beast(starter=True)
        row=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row_id,)))[0]
        async with storage.connect() as db:
            await storage.apply_beast_levelup(db,row,15,0)
            await db.commit()
        await self.execute('UPDATE player_beasts SET bond=40,happiness=80,stat_treats_used=12 WHERE id=?',(row_id,))
        await evolve_beast(42,1)
        row=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row_id,)))[0]
        self.assertEqual((row['beast_id'],row['stat_treats_used']),('prismara',12))

    async def test_rare_only_habitat_keeps_the_ancient_compass_charge(self):
        from cogs.hatch import Hatch
        from utils import expeditions
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await storage.update_player(42,level=30,ancient_compass_charges=1)
        view = await Hatch.explore.callback(Hatch(None), interaction(), 'loom')
        self.addCleanup(view.stop)
        trip = view.trip
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('genesis')):
            await expeditions.choose(42, trip['id'], trip['version'], 'tracks')
        self.assertEqual((await storage.get_player(42))['ancient_compass_charges'],1)

    async def test_exploration_library_bonus_is_applied_once(self):
        from cogs.hatch import Hatch
        from utils import expeditions, rewards
        row_id=await self.beast()
        await storage.set_active_companion(42,row_id)
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Library')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,arcane_library) VALUES(3,1)')
        view = await Hatch.explore.callback(Hatch(None), interaction())
        self.addCleanup(view.stop)
        trip = view.trip
        expected = round(trip['payload']['exp'] * 1.15)
        with patch('utils.rewards.player_exp', wraps=rewards.player_exp) as award:
            done = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
            await expeditions.report({**done, 'reported': 0})
        self.assertEqual(award.await_count, 1)
        self.assertEqual(award.call_args.args[2], expected)
        self.assertEqual(done['payload']['exp_granted'], expected)
