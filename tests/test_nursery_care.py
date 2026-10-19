"""Optional care still completes Patient Keeper, including interrupted updates."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage, nursery, progress


class NurseryCareTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.clock = datetime(2030, 1, 1, tzinfo=timezone.utc)
        patient_keeper = next(q for q in progress.QUEST_POOL if q['id'] == 'tend_egg')
        # Keep another real unfinished quest so the all-dailies bonus does not
        # obscure Patient Keeper's own 120 gold and 30 trainer EXP reward.
        other = next(q for q in progress.QUEST_POOL if q['id'] == 'craft_item')
        daily = patch('utils.progress.eligible_daily_set', new=AsyncMock(return_value=[patient_keeper, other]))
        daily.start()
        self.addCleanup(daily.stop)
        await storage.add_item(42, 'sprout_pod')
        self.egg = await nursery.incubate(42, 'sprout_pod', request_key='care-test-start', now=self.clock)
        self.start_player = await storage.get_player(42)

    async def visit(self, minutes):
        return await nursery.care(42, self.egg['id'], now=self.clock + timedelta(minutes=minutes))

    async def tend_count(self):
        rows = await self.rows("SELECT value FROM player_progress WHERE user_id=42 AND stat_key='tend'")
        return rows[0]['value'] if rows else 0

    async def assert_reward_once(self):
        player = await storage.get_player(42)
        self.assertEqual(player['gold'] - self.start_player['gold'], 120)
        self.assertEqual(player['exp'] - self.start_player['exp'], 30)
        daily = await self.rows("SELECT progress,completed FROM daily_quests WHERE user_id=42 AND quest_id='tend_egg'")
        self.assertEqual(daily, [{'progress': 2, 'completed': 1}])
        self.assertFalse(await self.rows("SELECT * FROM daily_quests WHERE quest_id='all_quests_bonus'"))

    async def test_two_care_visits_complete_patient_keeper_once_and_invalid_care_never_counts(self):
        with self.assertRaises(ValueError):
            await self.visit(0)
        self.assertEqual(await self.tend_count(), 0)
        self.assertEqual(await self.rows("SELECT * FROM nursery_requests WHERE operation LIKE 'care_%'"), [])
        first = await self.visit(15)
        self.assertEqual(first['care_count'], 1)
        self.assertEqual(await self.tend_count(), 1)
        with self.assertRaises(ValueError):
            await self.visit(15)
        self.assertEqual(await self.tend_count(), 1)
        second = await self.visit(30)
        self.assertEqual(second['care_count'], 2)
        await self.assert_reward_once()
        await nursery.snapshot(42)
        await nursery.snapshot(42)
        await self.assert_reward_once()
        third = await self.visit(45)
        self.assertEqual(third['care_count'], 3)
        self.assertEqual(await self.tend_count(), 3)
        with self.assertRaises(ValueError):
            await self.visit(50)
        self.assertEqual(await self.tend_count(), 3)
        await self.assert_reward_once()
        requests = await self.rows("SELECT request_key,operation FROM nursery_requests WHERE operation LIKE 'care_%' ORDER BY request_key")
        self.assertEqual(requests, [{'request_key': f"care:{self.egg['id']}:{count}", 'operation': 'care_done'} for count in (1, 2, 3)])

    async def test_notification_failure_preserves_visit_and_snapshot_retries_without_double_counter(self):
        with patch('utils.progress._track_daily_event', side_effect=RuntimeError('offline')):
            with self.assertLogs('chibibeasts.nursery', level='ERROR'):
                first = await self.visit(15)
        self.assertEqual(first['care_count'], 1)
        self.assertEqual((await self.rows('SELECT care_count FROM nursery_incubations'))[0]['care_count'], 1)
        self.assertEqual(await self.tend_count(), 1)
        self.assertEqual(await self.rows("SELECT * FROM daily_quests WHERE quest_id='tend_egg'"), [])
        self.assertEqual(len(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'")), 1)
        await nursery.snapshot(42)
        await nursery.snapshot(42)
        self.assertEqual(await self.tend_count(), 1)
        self.assertEqual((await self.rows("SELECT progress FROM daily_quests WHERE quest_id='tend_egg'"))[0]['progress'], 1)
        self.assertEqual(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'"), [])
        await self.visit(30)
        self.assertEqual(await self.tend_count(), 2)
        await self.assert_reward_once()

    async def test_retry_after_daily_reward_commits_cannot_pay_or_count_again(self):
        await self.visit(15)
        original = progress._track_daily_event

        async def interrupted_after_daily_commit(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('disconnected after reward commit')

        with patch('utils.progress._track_daily_event', side_effect=interrupted_after_daily_commit):
            with self.assertLogs('chibibeasts.nursery', level='ERROR'):
                second = await self.visit(30)
        self.assertEqual(second['care_count'], 2)
        await self.assert_reward_once()
        self.assertEqual(len(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'")), 1)
        await nursery.snapshot(42)
        await nursery.snapshot(42)
        self.assertEqual(await self.tend_count(), 2)
        await self.assert_reward_once()
        self.assertEqual(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'"), [])

    async def test_pending_notification_read_failure_keeps_care_and_retries_on_snapshot(self):
        with patch.object(nursery, '_all', side_effect=RuntimeError('read unavailable')):
            with self.assertLogs('chibibeasts.nursery', level='ERROR'):
                first = await self.visit(15)
        self.assertEqual(first['care_count'], 1)
        self.assertEqual(await self.tend_count(), 0)
        self.assertEqual(len(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'")), 1)
        await nursery.snapshot(42)
        await nursery.snapshot(42)
        self.assertEqual(await self.tend_count(), 1)
        self.assertEqual((await self.rows("SELECT progress FROM daily_quests WHERE quest_id='tend_egg'"))[0]['progress'], 1)
        self.assertEqual(await self.rows("SELECT * FROM nursery_requests WHERE operation='care_notify'"), [])
