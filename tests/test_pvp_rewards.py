"""Normal and ranked PvP share one atomic, non-transferable daily XP budget."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage
from utils.progression import beast_exp_for_level, trainer_exp_for_level
from utils.pvp_rewards import award_match


DAY = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


class PvpRewards(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.beasts = {}
        for uid in (42, 43, 44, 45, 46):
            await storage.get_or_create_player(uid, f'Player {uid}')
            await storage.update_player(uid, level=10, exp=0)
            self.beasts[uid] = await storage.add_beast_to_player(
                uid, {**storage.get_beast_data('slime'), 'level': 10})

    async def award(self, key, opponent=43, winner=0, *, ids=None, rows=None, clean=True, now=DAY):
        ids = ids or [42, opponent]
        rows = rows or [self.beasts[uid] for uid in ids]
        return await award_match(key, ids, rows, winner, clean_ko=clean, now=now)

    async def account(self, uid):
        return (await self.rows('SELECT * FROM players WHERE user_id=?', (uid,)))[0]

    async def companion(self, row_id):
        return (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]

    async def test_first_clean_match_awards_actual_xp_gold_and_replay_awards_nothing(self):
        before = await self.account(42)
        result = await self.award('normal:1')
        self.assertEqual([(r['beast_xp'], r['trainer_xp'], r['gold']) for r in result],
                         [(60, 80, 100), (20, 25, 25)])
        self.assertTrue(all(r['eligible'] for r in result))
        self.assertEqual((await self.companion(self.beasts[42]))['exp'], 60)
        self.assertEqual((await self.account(42))['exp'], 80)
        self.assertEqual((await self.account(42))['gold'], before['gold'] + 100)
        replay = await self.award('normal:1', now=DAY + timedelta(days=1))
        self.assertTrue(all(not r['eligible'] and not r['gold'] and not r['beast_xp'] and not r['trainer_xp'] for r in replay))
        self.assertEqual((await self.account(42))['exp'], 80)

    async def test_concurrent_retry_settles_both_players_once(self):
        outcomes = await asyncio.gather(*(self.award('ranked:race') for _ in range(4)))
        self.assertEqual(sum(r[0]['beast_xp'] for r in outcomes), 60)
        self.assertEqual(sum(r[1]['trainer_xp'] for r in outcomes), 25)
        self.assertEqual(sum(r[0]['gold'] for r in outcomes), 100)
        self.assertEqual(len(await self.rows("SELECT * FROM gameplay_events WHERE event_type LIKE 'pvp_%_reward'")), 4)

    async def test_same_opponent_cannot_pay_again_in_another_mode_or_order(self):
        await self.award('normal:2')
        result = await self.award('ranked:3', ids=[43, 42], winner=0)
        self.assertTrue(all(not r['eligible'] and r['gold'] == r['beast_xp'] == r['trainer_xp'] == 0 for r in result))
        # A previously rejected completed match cannot be replayed tomorrow.
        replay = await self.award('ranked:3', ids=[43, 42], now=DAY + timedelta(days=1))
        self.assertTrue(all(not r['eligible'] for r in replay))

    async def test_different_opponents_share_actual_daily_budgets(self):
        outcomes = [await self.award(f'normal:loss:{uid}', uid, winner=1) for uid in (43, 44, 45, 46)]
        rewards = [r[0] for r in outcomes]
        self.assertEqual([r['beast_xp'] for r in rewards], [20, 20, 20, 0])
        self.assertEqual([r['trainer_xp'] for r in rewards], [25, 25, 25, 5])
        self.assertEqual(sum(r['gold'] for r in rewards), 100)
        counters = await self.rows("SELECT stat_key,value FROM player_progress WHERE user_id=42 AND stat_key LIKE 'pvp_%'")
        self.assertEqual({r['stat_key']: r['value'] for r in counters},
                         {'pvp_beast_xp:2026-10-18': 60, 'pvp_trainer_xp:2026-10-18': 80})

    async def test_switching_companions_does_not_reset_trainer_budget(self):
        await self.award('normal:first')
        second = await storage.add_beast_to_player(42, {**storage.get_beast_data('wisp'), 'level': 10})
        result = await self.award('ranked:other', 44, rows=[second, self.beasts[44]])
        self.assertTrue(result[0]['eligible'])
        self.assertEqual((result[0]['beast_xp'], result[0]['trainer_xp'], result[0]['gold']), (0, 0, 100))
        self.assertEqual((await self.companion(second))['exp'], 0)

    async def test_utc_midnight_resets_pair_claim_and_budget(self):
        local_zone = timezone(timedelta(hours=-5))
        before = datetime(2026, 10, 18, 18, 59, tzinfo=local_zone)
        after = before + timedelta(minutes=1)
        first = await self.award('normal:before-midnight', now=before)
        second = await self.award('ranked:after-midnight', now=after)
        self.assertEqual((first[0]['beast_xp'], second[0]['beast_xp']), (60, 60))
        self.assertTrue(all(r['eligible'] for r in second))
        keys = {r['stat_key'] for r in await self.rows("SELECT stat_key FROM player_progress WHERE user_id=42 AND stat_key LIKE 'pvp_beast_xp:%'")}
        self.assertEqual(keys, {'pvp_beast_xp:2026-10-18', 'pvp_beast_xp:2026-10-19'})

    async def test_bonus_perks_and_library_do_not_bypass_actual_caps(self):
        for uid in (42, 43):
            await self.perk('fable_scholar', uid)
        await self.execute("INSERT INTO guilds(id,name) VALUES(8,'PvP Library')")
        for uid in (42, 43):
            await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(8,?)', (uid,))
        await self.execute('INSERT INTO guild_sanctuary(guild_id,arcane_library) VALUES(8,1)')
        with patch('utils.duels.beast_exp', side_effect=AssertionError('PvP cannot use generic bonus XP')):
            result = await self.award('ranked:bonuses')
        self.assertEqual((result[0]['beast_xp'], result[0]['trainer_xp']), (60, 80))
        self.assertEqual((await self.companion(self.beasts[42]))['exp'], 60)
        self.assertEqual((await self.account(42))['exp'], 80)

    async def test_timeout_and_draw_have_no_rewards_or_claims(self):
        for winner, clean in ((0, False), (None, False), (None, True)):
            result = await self.award('normal:unfinished', winner=winner, clean=clean)
            self.assertTrue(all(not r['eligible'] and r['gold'] == r['beast_xp'] == r['trainer_xp'] == 0 for r in result))
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type LIKE 'pvp_%_reward'"), [])
        self.assertEqual(await self.rows("SELECT * FROM player_progress WHERE stat_key LIKE 'pvp_%'"), [])
        result = await self.award('normal:unfinished')
        self.assertTrue(all(r['eligible'] for r in result))

    async def test_level_caps_report_only_actual_progress_and_still_claim_opponent(self):
        await self.execute('UPDATE player_beasts SET level=49,exp=? WHERE id=?',
                           (beast_exp_for_level(49) - 7, self.beasts[42]))
        await storage.update_player(42, level=49, exp=trainer_exp_for_level(49) - 9)
        await self.execute('UPDATE player_beasts SET level=50,exp=0 WHERE id=?', (self.beasts[43],))
        await storage.update_player(43, level=50, exp=0)
        result = await self.award('normal:near-cap')
        self.assertEqual((result[0]['beast_xp'], result[0]['trainer_xp']), (7, 9))
        self.assertEqual((result[0]['beast_level'], result[0]['trainer_level']), (50, 50))
        self.assertTrue(result[0]['beast_leveled'] and result[0]['trainer_leveled'])
        self.assertEqual((result[1]['beast_xp'], result[1]['trainer_xp']), (0, 0))
        self.assertTrue(result[1]['eligible'])
        self.assertFalse(result[1]['beast_leveled'] or result[1]['trainer_leveled'])
        self.assertEqual((await self.companion(self.beasts[42]))['exp'], 0)
        self.assertEqual((await self.account(42))['exp'], 0)

    async def test_invalid_owner_rolls_back_both_participants_without_claims(self):
        before = await self.account(42)
        with self.assertRaisesRegex(ValueError, 'belong'):
            await self.award('normal:wrong-owner', rows=[self.beasts[42], self.beasts[44]])
        self.assertEqual(await self.account(42), before)
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type LIKE 'pvp_%_reward'"), [])
        result = await self.award('normal:wrong-owner')
        self.assertTrue(all(r['eligible'] for r in result))

    async def test_invalid_participants_or_winner_are_rejected(self):
        for ids, winner in (([42, 42], 0), ([42], 0), ([42, 43], 2), ([42, 43], True)):
            with self.subTest(ids=ids, winner=winner), self.assertRaises(ValueError):
                await award_match('normal:invalid', ids, [self.beasts[42], self.beasts[43]], winner, clean_ko=True, now=DAY)

    async def test_second_participant_failure_rolls_back_first_reward_and_claim(self):
        from utils import pvp_rewards
        original = pvp_rewards.player_exp

        async def fail_second(db, uid, amount):
            if uid == 43:
                raise RuntimeError('simulated interrupted settlement')
            return await original(db, uid, amount)

        before = await self.account(42)
        with patch('utils.pvp_rewards.player_exp', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                await self.award('ranked:atomic')
        self.assertEqual(await self.account(42), before)
        self.assertEqual((await self.companion(self.beasts[42]))['exp'], 0)
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type LIKE 'pvp_%_reward'"), [])
        self.assertEqual(await self.rows("SELECT * FROM player_progress WHERE stat_key LIKE 'pvp_%'"), [])
        self.assertTrue(all(r['eligible'] for r in await self.award('ranked:atomic')))
