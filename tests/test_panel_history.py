from tests.test_gameplay import DatabaseCase
from utils import journal_history
from utils.journal_log import safe


class SharedHistory(DatabaseCase):
    async def test_all_categories_have_working_empty_queries(self):
        for kind in journal_history.SOURCES:
            with self.subTest(kind=kind):
                total, page, entries = await journal_history.load(42, kind, 99)
                self.assertEqual((total, page, entries), (0, 0, []))

    async def test_battle_history_is_owned_and_paginated_in_stable_order(self):
        for _ in range(5):
            await self.execute("INSERT INTO battles(challenger_id,opponent_id,winner_id,status,battle_type) VALUES(42,43,42,'completed','pvp')")
        await self.execute('INSERT INTO battles(challenger_id,opponent_id) VALUES(44,45)')
        total, page, first = await journal_history.load(42, 'battles', 0)
        self.assertEqual((total, page, len(first)), (5, 0, 3))
        _, page, second = await journal_history.load(42, 'battles', 1)
        self.assertEqual((page, len(second)), (1, 2))
        self.assertGreater(min(row['id'] for row in first), max(row['id'] for row in second))
        self.assertTrue(all('Win' in journal_history.text(42, 'battles', row, safe) for row in first))

    async def test_pending_battle_is_not_labelled_a_draw(self):
        await self.execute("INSERT INTO battles(challenger_id,opponent_id,status,battle_type) VALUES(42,43,'pending','pvp')")
        await self.execute('UPDATE battles SET battle_type=NULL')
        _, _, rows = await journal_history.load(42, 'battles', 0)
        text = journal_history.text(42, 'battles', rows[0], safe)
        self.assertIn('Pending', text)
        self.assertIn('vs ', text)
        self.assertNotIn('Draw', text)

    async def test_trade_history_preserves_missing_beast_records_and_owner_scope(self):
        await self.execute("INSERT INTO trades(sender_id,receiver_id,status) VALUES(42,43,'completed')")
        await self.execute("INSERT INTO trades(sender_id,receiver_id,status) VALUES(44,45,'completed')")
        total, _, rows = await journal_history.load(42, 'trades', 0)
        self.assertEqual(total, 1)
        self.assertIn('Open offer', journal_history.text(42, 'trades', rows[0], safe))
