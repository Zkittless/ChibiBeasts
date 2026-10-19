"""Payment, saved choices and family progress survive retries and legacy saves."""
import asyncio
from datetime import datetime, timedelta, timezone
import random
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage, nursery, nursery_catalog as catalog
from utils.shops import purchase_gold


class Nursery(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await storage.update_player(42, level=50, gold=200000)
        self.clock = datetime(2030, 1, 1, tzinfo=timezone.utc)

    async def egg(self, key='common_egg', count=1):
        await storage.add_item(42, key, count)

    async def ready(self, key='prism_sphere'):
        await self.egg(key)
        started = await nursery.incubate(42, key, request_key='start:'+key, now=self.clock)
        return await nursery.prepare_offer(42, started['id'], now=self.clock+timedelta(days=8), rng=random.Random(1))

    async def test_rare_floor_and_grade_level_use_real_growth(self):
        await self.egg('rare_egg', 10)
        grants = await nursery.hatch(42, 'rare_egg', 10, request_key='bulk', rng=random.Random(1))
        self.assertEqual(len(grants), 10)
        self.assertTrue(all(g['rarity'] in {'rare', 'epic'} and 14 <= g['level'] <= 18 for g in grants))
        rows = await storage.get_player_beasts(42)
        self.assertTrue(all(r['max_hp'] > storage.get_beast_data(r['beast_id'])['base_stats']['hp'] for r in rows))
        self.assertEqual(sum(r['is_active'] for r in rows), 1)

    async def test_split_inventory_consumed_exactly_and_retry_never_rerolls(self):
        await self.egg(count=2)
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'common_egg',2)")
        first = await nursery.hatch(42, 'common_egg', 3, request_key='same', rng=random.Random(3))
        replay = await nursery.hatch(42, 'common_egg', 3, request_key='same', rng=random.Random(99))
        self.assertEqual(first, replay)
        self.assertEqual(len(await storage.get_player_beasts(42)), 3)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE stat_key='hatch'"))[0]['value'], 3)
        with self.assertRaisesRegex(ValueError, 'different choices'):
            await nursery.hatch(42, 'common_egg', 1, request_key='same')

    async def test_bulk_transaction_rolls_back_every_grant_and_egg_on_failure(self):
        await self.egg(count=2)
        original = storage.grant_beast
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('injected insert failure')
            return await original(*args, **kwargs)

        with patch.object(storage, 'grant_beast', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                await nursery.hatch(42, 'common_egg', 2, request_key='rollback')
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 2)
        self.assertEqual(await self.rows('SELECT * FROM nursery_grants'), [])
        self.assertEqual(await self.rows('SELECT * FROM nursery_seen'), [])

    async def test_gate_validation_and_insufficient_quantity_preserve_inventory(self):
        await self.egg('rare_egg')
        await storage.update_player(42, level=11)
        with self.assertRaisesRegex(ValueError, 'trainer level 12'):
            await nursery.hatch(42, 'rare_egg', request_key='gate')
        await storage.update_player(42, level=12)
        for quantity in (0, True, 26, 1.5, 2):
            with self.assertRaises(ValueError):
                await nursery.hatch(42, 'rare_egg', quantity, request_key='bad:'+str(quantity))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_purchase_receipt_prevents_double_charge_and_inventory(self):
        first = await purchase_gold(42, 'rare_egg', request_key='buy')
        self.assertEqual(await purchase_gold(42, 'rare_egg', request_key='buy'), first)
        self.assertEqual((await storage.get_player(42))['gold'], 198500)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        with self.assertRaisesRegex(ValueError, 'different choices'):
            await purchase_gold(42, 'rare_egg', 2, request_key='buy')

    async def test_incubation_passive_deadline_capacity_and_chronos(self):
        await self.egg('sprout_pod', 4)
        await self.perk('chronos_hourglass')
        for i in range(3):
            row = await nursery.incubate(42, 'sprout_pod', request_key=f'start{i}', now=self.clock)
        self.assertEqual(nursery._time(row['ready_at']) - self.clock, timedelta(minutes=48))
        with self.assertRaisesRegex(ValueError, 'three incubator'):
            await nursery.incubate(42, 'sprout_pod', request_key='full', now=self.clock)
        state = await nursery.snapshot(42, now=self.clock+timedelta(hours=1))
        self.assertTrue(all(r['ready'] and not r['care_available'] for r in state['incubations']))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_offer_rolled_once_and_all_candidates_distinct_same_rarity(self):
        offer = await self.ready()
        again = await nursery.prepare_offer(42, offer['incubation_id'], now=self.clock+timedelta(days=10), rng=random.Random(999))
        self.assertEqual(offer, again)
        self.assertEqual(len({c['rarity'] for c in offer['candidates']}), 1)
        self.assertEqual(len({c['beast_id'] for c in offer['candidates']}), len(offer['candidates']))
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual((await nursery.snapshot(42))['offers'], [offer])

    async def test_claim_one_saved_choice_retry_and_other_trainer_isolation(self):
        offer = await self.ready()
        await storage.get_or_create_player(99, 'Other')
        with self.assertRaises(ValueError):
            await nursery.claim_offer(99, offer['id'], 0, request_key='theft')
        first = await nursery.claim_offer(42, offer['id'], 0, request_key='claim')
        self.assertEqual(first['beast_id'], offer['candidates'][0]['beast_id'])
        self.assertEqual(await nursery.claim_offer(42, offer['id'], 0, request_key='claim'), first)
        with self.assertRaises(ValueError):
            await nursery.claim_offer(42, offer['id'], 0, request_key='other-claim')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)

    async def test_optional_care_cannot_be_spammed_or_raise_stats(self):
        await self.egg('sprout_pod')
        row = await nursery.incubate(42, 'sprout_pod', request_key='carestart', now=self.clock)
        with self.assertRaises(ValueError):
            await nursery.care(42, row['id'], now=self.clock)
        for minutes in (15, 30, 45):
            result = await nursery.care(42, row['id'], now=self.clock+timedelta(minutes=minutes))
            with self.assertRaises(ValueError):
                await nursery.care(42, row['id'], now=self.clock+timedelta(minutes=minutes))
        self.assertEqual(result['bond_bonus'], 3)
        offer = await nursery.prepare_offer(42, row['id'], now=self.clock+timedelta(hours=1))
        grant = await nursery.claim_offer(42, offer['id'], 0, request_key='caredclaim')
        beast = await storage.get_player_beasts(42)
        self.assertEqual(beast[0]['bond'], 4)  # Three care plus normal hatch bond.
        self.assertEqual(grant['bond'], 3)

    async def test_legacy_schedule_keeps_deadline_and_can_claim_below_new_gate(self):
        await storage.update_player(42, level=1)
        await self.execute("INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at) VALUES(42,'genesis_matrix','Old egg','divine','2000-01-01 00:00:00')")
        offer = await nursery.prepare_offer(42, 1, rng=random.Random(7))
        self.assertTrue(all(c['level'] == 1 for c in offer['candidates']))
        await nursery.claim_offer(42, offer['id'], 0, request_key='legacy')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual((await self.rows('SELECT ready_at FROM incubating_eggs'))[0]['ready_at'], '2000-01-01 00:00:00')

    async def test_duplicate_points_persist_after_release_and_research_is_paid_floor(self):
        pool = catalog.research_pool('common_egg')
        beast = pool[0]
        await self.beast(beast['id'])
        await self.egg(count=10)
        with patch.object(catalog, 'choose_rarity', return_value='common'), patch.object(catalog, 'species_pool', return_value=[beast]):
            await nursery.hatch(42, 'common_egg', 8, request_key='duplicates')
        self.assertEqual((await nursery.snapshot(42))['eggs'][0]['research'], 8)
        await self.execute('DELETE FROM player_beasts WHERE user_id=42')
        targets = await nursery.research_targets(42, 'common_egg')
        self.assertEqual({b['id'] for b in targets}, {b['id'] for b in pool})
        grant = (await nursery.hatch(42, 'common_egg', request_key='research', research_target=beast['id']))[0]
        self.assertTrue(grant['duplicate'])
        self.assertEqual(grant['research'], 0)
        self.assertTrue(grant['researched'])
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        with self.assertRaises(ValueError):
            await nursery.hatch(42, 'common_egg', request_key='illegal', research_target='genesis')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_research_incubator_requires_paid_schedule_and_same_family_floor(self):
        offer = await self.ready('prism_sphere')
        await self.execute('INSERT INTO nursery_research VALUES(?,?,8)', (42, catalog.research_key('prism_sphere')))
        target = catalog.research_pool('prism_sphere')[0]
        grant = await nursery.claim_offer(42, offer['id'], 0, request_key='choose-research', research_target=target['id'])
        self.assertEqual(grant['beast_id'], target['id'])
        self.assertEqual(grant['rarity'], 'rare')
        self.assertEqual(grant['research'], 0)

    async def test_notification_failure_cannot_lose_reward_and_snapshot_retries_once(self):
        await self.egg()
        with patch('utils.progress._track_daily_event', side_effect=RuntimeError('offline')):
            with self.assertLogs('chibibeasts.nursery', level='ERROR'):
                grant = (await nursery.hatch(42, 'common_egg', request_key='disconnect'))[0]
        self.assertFalse(grant['notified'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        await nursery.snapshot(42)
        await nursery.snapshot(42)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE stat_key='hatch'"))[0]['value'], 1)
        self.assertEqual((await storage.get_player_beasts(42))[0]['bond'], 1)
        self.assertEqual((await self.rows('SELECT notified FROM nursery_grants'))[0]['notified'], 1)

    async def test_attunement_consumes_only_matching_family_and_rolled_rarity(self):
        await self.egg()
        pool = catalog.species_pool('common_egg', 'common')
        matching_type = pool[0]['type']
        await self.execute('UPDATE players SET astral_type=? WHERE user_id=42', (matching_type,))
        with patch.object(catalog, 'choose_rarity', return_value='common'):
            grant = (await nursery.hatch(42, 'common_egg', request_key='attuned'))[0]
        self.assertEqual(storage.get_beast_data(grant['beast_id'])['type'], matching_type)
        self.assertIsNone((await storage.get_player(42))['astral_type'])

    async def test_daily_hatch_receipt_and_reward_commit_together(self):
        from utils.progress import _track_daily_event
        quest = {'id': 'nursery-test', 'event': 'hatch', 'target': 1, 'reward_gold': 10,
                 'reward_exp': 0, 'reward_shards': 0}
        other = {**quest, 'id': 'other-test', 'event': 'craft'}
        with patch('utils.progress.eligible_daily_set', new=AsyncMock(return_value=[quest, other])):
            first = await _track_daily_event(42, 'hatch', event_key='durable-daily')
            second = await _track_daily_event(42, 'hatch', event_key='durable-daily')
        self.assertEqual(first, [quest])
        self.assertEqual(second, [])
        self.assertEqual((await storage.get_player(42))['gold'], 200010)
        self.assertEqual((await self.rows("SELECT progress FROM daily_quests WHERE quest_id='nursery-test'"))[0]['progress'], 1)

    async def test_simultaneous_requests_cannot_spend_same_egg_twice(self):
        await self.egg()
        results = await asyncio.gather(nursery.hatch(42, 'common_egg', request_key='a'),
                                       nursery.hatch(42, 'common_egg', request_key='b'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, list) for r in results), 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
