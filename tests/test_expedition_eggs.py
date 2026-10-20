"""Scarce, persisted nest discoveries share one trip's material reward slot."""
import asyncio
import copy
import random
import unittest
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage, expedition_events as events, expeditions as trips
from utils.economy_balance import RELEASE_VALUES, item_sell_price
from utils.nursery_catalog import definition, rarity_pool


class NestRng:
    """Keep the travel/material RNG real; select only the journal's saved seed."""
    def __init__(self, seed):
        self.seed = seed
        self.random = random.Random(7)

    def getrandbits(self, bits):
        return self.seed

    def __getattr__(self, name):
        return getattr(self.random, name)


def discoverable_seed(realm='woods', level=1):
    # The real departure gold is always >=3 * the region's middle level.
    for seed in range(10000):
        p = dict(seed=str(seed), level=level, base_gold=9)
        events.prepare_nest(p, realm)
        if p['egg_offer']:
            return seed
    raise AssertionError('No nest seed found')


class NestBudgets(unittest.TestCase):
    def test_every_region_has_low_grade_timed_family_with_bounded_value(self):
        for region, level, gold in (('woods', 1, 13), ('ember', 6, 58),
                                    ('glacial', 12, 103), ('trenches', 20, 148),
                                    ('loom', 30, 193)):
            for seed in range(100):
                p = dict(seed=str(seed), level=level, base_gold=gold)
                events.prepare_nest(p, region)
                self.assertLessEqual(p['nest_chance'], .10)
                egg_id = events._variant(p, events.REGIONAL_NEST_EGGS[region], 'nest-family')
                egg = definition(egg_id)
                rates = rarity_pool(egg_id,
                                    perks=({'perk_id': 'stardust_touch', 'equipped': 1},
                                           {'perk_id': 'glimmering_fortune', 'equipped': 1}),
                                    sanctuary={'celestial_observatory': 1})
                release = sum(RELEASE_VALUES[r] * w for r, w in rates.items())
                liquid = max(release, item_sell_price(egg, merchant=True))
                self.assertLessEqual(p['nest_chance'] * liquid, gold * .25 + 1e-8)
                self.assertLessEqual(p['nest_chance'] * egg['price'], gold * 2 + 1e-8)
                self.assertLessEqual(egg['min_level'], level)
                self.assertIn(egg['grade'], ('common', 'uncommon', 'rare'))
                self.assertGreater(egg['incubation_hours'], 0)
                self.assertNotEqual(egg_id, 'world_tree_seed')

    def test_level_gate_and_low_region_do_not_upgrade_for_veterans(self):
        p = dict(seed='1', level=1, base_gold=100)
        events.prepare_nest(p, 'loom')
        self.assertEqual(p['nest_chance'], 0)
        self.assertIsNone(p['egg_offer'])
        for seed in range(100):
            low = dict(seed=str(seed), level=1, base_gold=13)
            high = dict(seed=str(seed), level=50, base_gold=13)
            events.prepare_nest(low, 'woods')
            events.prepare_nest(high, 'woods')
            self.assertEqual(low['egg_offer'], high['egg_offer'])
            self.assertEqual(low['nest_chance'], high['nest_chance'])

    def test_saved_offer_does_not_mutate_when_journal_is_opened(self):
        p = dict(gold=15, exp=20, base_exp=20, level=1,
                 material='enchanted_bark', offered=['enchanted_bark'],
                 bond=0, happiness=0, notes=[])
        events.initialize(p, 'woods', discoverable_seed(),
                          [dict(id=1, name='Twine', type='nature')])
        events.prepare_nest(p, 'woods')
        events.resolve(p, 'woods', 'shrine')
        events.resolve(p, 'woods', 'study')
        self.assertIn('recover_egg', [c['value'] for c in p['page']['choices']])
        before = copy.deepcopy(p)
        for _ in range(20):
            view = events.presentation(dict(payload=p, status='active'))
            view['choices'].clear()
        self.assertEqual(p, before)
        self.assertIn('instead of', before['page']['body'])
        self.assertIn('max/trip', events.presentation(dict(payload=p, status='active'))['footer'])


class ExpeditionEggs(DatabaseCase):
    async def ready(self):
        await self.beast('twine', starter=True)

    async def pick(self, trip, choice):
        return await trips.choose(42, trip['id'], trip['version'], choice)

    async def found(self):
        return await trips.begin(42, 'woods', rng=NestRng(discoverable_seed()))

    async def discovered(self):
        trip = await self.found()
        return await self.pick(await self.pick(trip, 'shrine'), 'study')

    async def sheltered(self):
        return await self.pick(await self.discovered(), 'recover_egg')

    async def test_nest_choice_replaces_material_and_retains_three_stop_budget(self):
        await self.ready()
        trip = await self.discovered()
        egg = trip['payload']['egg_offer']
        self.assertIn('recover_egg', trips.options(trip))
        self.assertIn('map', trips.options(trip))
        self.assertEqual(trip['payload']['completed'], 2)
        trip = await self.pick(trip, 'recover_egg')
        self.assertEqual(trip['payload']['completed'], 3)
        self.assertEqual(trips.options(trip), ('return', 'push'))
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), [])
        trip = await self.pick(trip, 'return')
        self.assertEqual(await self.rows('SELECT item_id,quantity FROM player_inventory'),
                         [dict(item_id=egg['id'], quantity=1)])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertFalse(trip['payload']['material_granted'])
        self.assertTrue(trip['payload']['egg_granted'])
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type IN ('material_collect','material_story')"), [])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_normal_choice_keeps_material_including_story_supplies(self):
        await self.ready()
        with patch('utils.story.quest_material_options', return_value=['spirit_crystal']):
            trip = await self.found()
        trip = await self.pick(trip, 'gather')
        self.assertIn('material:spirit_crystal', trips.options(trip))
        trip = await self.pick(trip, 'material:spirit_crystal')
        trip = await self.pick(trip, 'return_early')
        self.assertEqual(await self.rows('SELECT material_id,quantity FROM player_materials'),
                         [dict(material_id='spirit_crystal', quantity=1)])
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), [])
        self.assertTrue(trip['payload']['material_granted'])

    async def test_leaving_discovered_nest_keeps_ordinary_reward(self):
        await self.ready()
        trip = await self.discovered()
        trip = await self.pick(trip, 'map')
        trip = await self.pick(trip, 'return')
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), [])
        self.assertFalse(trip['payload']['egg_granted'])

    async def test_rescuing_a_creature_can_reveal_egg_and_bank_it_immediately(self):
        await self.ready()
        trip = await self.found()
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'observe')
        self.assertEqual(trip['payload']['event'], 'rescue')
        self.assertIn('abandoned nest', trips.presentation(trip)['body'])
        trip = await self.pick(trip, 'recover_egg')
        self.assertTrue(trip['payload']['flags']['sanctuary'])
        self.assertEqual(trip['payload']['completed'], 3)
        trip = await self.pick(trip, 'return_early')
        self.assertTrue(trip['payload']['egg_granted'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_inventory')), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_camp_discovery_and_saved_battle_still_grant_at_most_one_catch(self):
        await self.ready()
        trip = await self.found()
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            trip = await self.pick(trip, 'tracks')
        trip = await self.pick(trip, 'challenge')
        await trips.battle_result(42, trip['id'], trip['payload']['battle_token'], True)
        trip = await trips.read(42, trip['id'])
        self.assertEqual(trip['payload']['event'], 'camp')
        trip = await self.pick(trip, 'recover_egg')
        trip = await self.pick(trip, 'return')
        await trips.report({**trip, 'reported': 0})
        self.assertEqual([r['beast_id'] for r in await storage.get_player_beasts(42)], ['twine', 'wisp'])
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_inventory')), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_egg_settlement_is_atomic_and_once_across_two_clicks_and_reports(self):
        await self.ready()
        trip = await self.sheltered()
        before = await storage.get_player(42)
        with patch('utils.rewards.player_exp', side_effect=RuntimeError('rollback')):
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                await self.pick(trip, 'return')
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), [])
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        self.assertEqual((await trips.current(42))['version'], trip['version'])
        results = await asyncio.gather(self.pick(trip, 'return'), self.pick(trip, 'return'),
                                       return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        done = await trips.read(42, trip['id'])
        await trips.report({**done, 'reported': 0})
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_inventory')), 1)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_opening_restarting_and_pushing_do_not_reroll_or_grant_another_egg(self):
        await self.ready()
        trip = await self.discovered()
        saved = copy.deepcopy(trip['payload'])
        with patch('utils.expedition_events.prepare_nest', side_effect=AssertionError('rerolled')):
            await storage.init_db()
            trip = await trips.current(42)
            self.assertEqual(trip['payload'], saved)
            trip = await self.pick(trip, 'recover_egg')
            trip = await self.pick(trip, 'push')
            self.assertNotIn('recover_egg', trips.options(trip))
            trip = await self.pick(trip, 'sketch')
            trip = await self.pick(trip, 'return_early')
        self.assertEqual(trip['payload']['completed'], 4)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_inventory')), 1)

    async def test_early_return_before_recovery_never_grants_unearned_egg(self):
        await self.ready()
        trip = await self.discovered()
        trip = await self.pick(trip, 'return_early')
        self.assertFalse(trip['payload']['egg_granted'])
        self.assertEqual(await self.rows('SELECT * FROM player_inventory'), [])
        self.assertTrue(trip['payload']['material_granted'])

    async def test_saved_routes_and_foreign_or_hidden_recovery_choices_stay_protected(self):
        await self.ready()
        trip = await self.found()
        with self.assertRaises(ValueError):
            await self.pick(trip, 'recover_egg')
        trip = await self.pick(await self.pick(trip, 'shrine'), 'study')
        with self.assertRaises(ValueError):
            await trips.choose(43, trip['id'], trip['version'], 'recover_egg')
        # A pre-Nursery v2 save has no offer and retains exactly its old actions.
        payload = trip['payload']
        for key in ('nest_version', 'nest_chance', 'egg_offer', 'egg_reward', 'egg_granted'):
            payload.pop(key)
        events.page(payload, 'woods', 'discovery')
        self.assertEqual(trips.options(trip), ('map', 'watch', 'return_early'))

    async def test_completed_log_summary_names_egg_and_no_ungranted_material(self):
        import discord
        from utils import journal_state
        from utils.catalog import load_catalog
        from utils.journal import JournalView

        await self.ready()
        trip = await self.pick(await self.sheltered(), 'return')
        view = JournalView(42, await journal_state.snapshot(42), page='battle_log')
        self.addCleanup(view.stop)
        view.art_enabled = False
        view.trip = trip
        view.log_selected_id = trip['id']
        await view.load_page()
        view.render()
        summary = next(item.content for item in view.walk_children()
                       if isinstance(item, discord.ui.TextDisplay) and item.content.startswith('**Rewards received**'))
        loot_line = summary.splitlines()[1]
        material = load_catalog('materials.json')['materials'][trip['payload']['material']]['name']
        self.assertIn(definition(trip['payload']['egg_reward'])['name'], loot_line)
        self.assertNotIn(material, loot_line)
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
