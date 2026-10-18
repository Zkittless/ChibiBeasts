"""Exercise bounded care rewards through real commands and disposable saves."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage
from utils.items import use_item
from utils.transfers import complete_trade


class ActivityProgression(DatabaseCase):
    async def total_xp(self, row_id):
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        return sum(storage.calc_exp_for_level(level) for level in range(1, row['level'])) + row['exp']

    async def train(self, number=1, uid=42, stat='attack'):
        from cogs.economy import Economy
        command = interaction(uid)
        await Economy.train.callback(Economy(None), command, number, stat)
        return command

    async def test_carrots_and_soda_share_daily_budget_and_preserve_unused_items(self):
        row_id = await self.beast()
        await storage.add_item(42, 'gnome_grown_carrots', 4)
        await storage.add_item(42, 'singularity_soda', 2)
        await use_item(42, 'gnome_grown_carrots', beast_number=1)
        await use_item(42, 'singularity_soda', beast_number=1)
        self.assertEqual(await self.total_xp(row_id), 60)
        inventory = await storage.get_inventory(42)
        with self.assertRaisesRegex(ValueError, '60 growth-treat EXP'):
            await use_item(42, 'gnome_grown_carrots', beast_number=1)
        with self.assertRaisesRegex(ValueError, '60 growth-treat EXP'):
            await use_item(42, 'singularity_soda', beast_number=1)
        self.assertEqual(await storage.get_inventory(42), inventory)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        with patch('utils.rewards.datetime') as clock:
            clock.now.return_value = tomorrow
            await use_item(42, 'gnome_grown_carrots', beast_number=1)
        self.assertEqual(await self.total_xp(row_id), 80)

    async def test_training_has_four_daily_sessions_across_stats(self):
        row_id = await self.beast()
        await storage.update_player(42, gold=10000)
        before = (await storage.get_player_beasts(42))[0]
        for stat in ('attack', 'defense', 'speed', 'hp'):
            await self.train(stat=stat)
        trained = (await storage.get_player_beasts(42))[0]
        self.assertEqual(await self.total_xp(row_id), 40)
        self.assertEqual(sum(trained[key] for key in ('train_atk', 'train_def', 'train_spd', 'train_hp')), 4)
        self.assertEqual(trained['attack'], before['attack'] + 1 + trained['level'] - 1)
        gold = (await storage.get_player(42))['gold']
        command = await self.train()
        self.assertIn('4 training sessions today', command.followup.send.call_args.kwargs['embed'].description)
        self.assertEqual((await storage.get_player(42))['gold'], gold)
        self.assertEqual((await storage.get_player_beasts(42))[0], trained)

    async def test_trading_cannot_reset_feed_or_training_allowance(self):
        row_id = await self.beast('slime')
        await storage.get_or_create_player(43, 'Recipient')
        await storage.update_player(42, gold=10000)
        await storage.update_player(43, gold=10000)
        await storage.add_item(42, 'singularity_soda')
        await use_item(42, 'singularity_soda', beast_number=1)
        for _ in range(4):
            await self.train()
        await self.execute('UPDATE player_beasts SET is_active=0 WHERE id=?', (row_id,))
        await complete_trade(42, 43, row_id, offer_key='daily-budget-transfer')
        await storage.add_item(43, 'gnome_grown_carrots')
        with self.assertRaisesRegex(ValueError, '60 growth-treat EXP'):
            await use_item(43, 'gnome_grown_carrots', beast_number=1)
        before = (await storage.get_player_beasts(43))[0]
        gold = (await storage.get_player(43))['gold']
        await self.train(uid=43)
        self.assertEqual((await storage.get_player_beasts(43))[0], before)
        self.assertEqual((await storage.get_player(43))['gold'], gold)
        self.assertEqual(await self.total_xp(row_id), 100)

    async def test_training_daily_budget_cannot_be_amplified_by_scholar_and_library(self):
        row_id = await self.beast()
        await storage.update_player(42, gold=10000)
        await self.perk('fable_scholar')
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Library')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,arcane_library) VALUES(3,1)')
        for _ in range(4):
            await self.train()
        self.assertEqual(await self.total_xp(row_id), 40)

    async def test_training_reward_failure_rolls_back_gold_stats_and_daily_claim(self):
        await self.beast()
        before = (await storage.get_player_beasts(42))[0]
        gold = (await storage.get_player(42))['gold']
        with patch('utils.duels.beast_exp_in_transaction', side_effect=RuntimeError('Interrupted award')):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted award'):
                await self.train()
        self.assertEqual((await storage.get_player_beasts(42))[0], before)
        self.assertEqual((await storage.get_player(42))['gold'], gold)
        self.assertFalse(await self.rows("SELECT * FROM player_progress WHERE stat_key LIKE 'activity_xp:%' OR stat_key LIKE 'training_sessions:%'"))

    async def test_max_level_treat_keeps_item_and_does_not_accumulate_exp(self):
        row_id = await self.beast()
        before = (await storage.get_player_beasts(42))[0]
        async with storage.connect() as db:
            await storage.apply_beast_levelup(db, before, 50, 0)
            await db.commit()
        await storage.add_item(42, 'singularity_soda')
        with self.assertRaisesRegex(ValueError, 'already at level 50'):
            await use_item(42, 'singularity_soda', beast_number=1)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        row = (await self.rows('SELECT level,exp FROM player_beasts WHERE id=?', (row_id,)))[0]
        self.assertEqual(row, {'level': 50, 'exp': 0})
