"""Independent failure, ownership and restart checks for durable Nursery grants."""
import importlib
import random
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage, nursery
from utils.nursery_catalog import RESEARCH_COST, research_key


class NurserySafetyTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.notifications = patch('utils.nursery.notify_grants', new=AsyncMock())
        self.notifications.start()
        self.addCleanup(self.notifications.stop)

    async def _points(self, egg_id='rare_egg', points=RESEARCH_COST):
        await self.execute('INSERT INTO nursery_research VALUES(?,?,?)', (42, research_key(egg_id), points))

    async def test_failure_during_second_bulk_grant_rolls_back_everything(self):
        await storage.add_item(42, 'common_egg', 2)
        original = storage.grant_beast
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('simulated grant failure')
            return await original(*args, **kwargs)

        with patch.object(storage, 'grant_beast', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'simulated grant failure'):
                await nursery.hatch(42, 'common_egg', 2, request_key='bulk-failure', rng=random.Random(2))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 2)
        for table in ('player_beasts', 'nursery_seen', 'nursery_grants', 'nursery_requests', 'nursery_research'):
            self.assertEqual(await self.rows('SELECT * FROM ' + table), [], table)
        result = await nursery.hatch(42, 'common_egg', 2, request_key='bulk-failure', rng=random.Random(2))
        self.assertEqual(len(result), 2)

    async def test_invalid_research_target_preserves_paid_egg_and_points(self):
        await storage.update_player(42, level=12)
        await storage.add_item(42, 'rare_egg')
        await self._points()
        with self.assertRaisesRegex(ValueError, 'grade floor'):
            await nursery.hatch(42, 'rare_egg', request_key='bad-target', research_target='genesis')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.assertEqual((await self.rows('SELECT points FROM nursery_research'))[0]['points'], RESEARCH_COST)
        self.assertEqual(await self.rows('SELECT * FROM nursery_requests'), [])
        self.assertEqual(await storage.get_player_beasts(42), [])

    async def test_research_selection_spends_egg_and_cannot_refund_itself_a_point(self):
        await storage.update_player(42, level=12)
        await storage.add_item(42, 'rare_egg')
        await self._points()
        await self.beast('unicorn')
        grant = (await nursery.hatch(42, 'rare_egg', request_key='select-unicorn', research_target='unicorn'))[0]
        self.assertTrue(grant['duplicate'])
        self.assertTrue(grant['researched'])
        self.assertEqual(grant['research'], 0)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_request_key_argument_conflict_cannot_spend_another_egg(self):
        await storage.add_item(42, 'common_egg', 3)
        first = await nursery.hatch(42, 'common_egg', request_key='one-receipt', rng=random.Random(9))
        with self.assertRaisesRegex(ValueError, 'different choices'):
            await nursery.hatch(42, 'common_egg', 2, request_key='one-receipt')
        replay = await nursery.hatch(42, 'common_egg', request_key='one-receipt', rng=random.Random(99))
        self.assertEqual(first, replay)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 2)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_cross_owner_offer_and_care_never_transfer_a_hatchling(self):
        await storage.get_or_create_player(43, 'Another trainer')
        await storage.add_item(42, 'sprout_pod')
        incubation = await nursery.incubate(42, 'sprout_pod', request_key='owned', now=100000)
        offer = await nursery.prepare_offer(42, incubation['id'], now=104000, rng=random.Random(2))
        with self.assertRaisesRegex(ValueError, 'another trainer'):
            await nursery.prepare_offer(43, incubation['id'], now=104000)
        with self.assertRaisesRegex(ValueError, 'another trainer'):
            await nursery.care(43, incubation['id'], now=101000)
        with self.assertRaisesRegex(ValueError, 'another trainer'):
            await nursery.claim_offer(43, offer['id'], 0, request_key='steal')
        grant = await nursery.claim_offer(42, offer['id'], 0, request_key='real-owner')
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(await storage.get_player_beasts(43), [])
        self.assertEqual(grant['beast_id'], offer['candidates'][0]['beast_id'])

    async def test_failure_after_commit_is_recoverable_by_receipt_without_second_payment(self):
        await storage.add_item(42, 'common_egg', 2)
        with patch('utils.nursery.notify_grants', new=AsyncMock(side_effect=RuntimeError('network stopped'))):
            with self.assertRaisesRegex(RuntimeError, 'network stopped'):
                await nursery.hatch(42, 'common_egg', request_key='disconnected', rng=random.Random(7))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        replay = await nursery.hatch(42, 'common_egg', request_key='disconnected', rng=random.Random(99))
        self.assertEqual(len(replay), 1)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_offer_survives_module_reload_and_does_not_reroll(self):
        await storage.add_item(42, 'sprout_pod')
        incubation = await nursery.incubate(42, 'sprout_pod', request_key='restart', now=100000)
        offer = await nursery.prepare_offer(42, incubation['id'], now=104000, rng=random.Random(2))
        # A process restart loses module state. The serialized offer remains the
        # source of truth, including level, rarity and all candidate species.
        importlib.reload(nursery)
        with patch('utils.nursery.notify_grants', new=AsyncMock()):
            resumed = await nursery.prepare_offer(42, incubation['id'], now=204000, rng=random.Random(999))
            self.assertEqual(offer, resumed)
            first = await nursery.claim_offer(42, offer['id'], 0, request_key='restart-claim')
            second = await nursery.claim_offer(42, offer['id'], 0, request_key='restart-claim')
        self.assertEqual(first, second)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_old_and_new_audits_use_identical_exact_odds(self):
        import json
        from scripts.audit_economy import egg_values as old_values
        from scripts.audit_nursery import egg_values as new_values
        old, new = old_values(), new_values()
        self.assertEqual(len(old), len(new))
        for left, right in zip(old, new):
            self.assertEqual(left['egg'], right['egg'])
            self.assertEqual(json.loads(left['effective_pool']), right['effective_pool'])
            self.assertEqual(left['expected_release_gold'], right['expected_release_gold'])
