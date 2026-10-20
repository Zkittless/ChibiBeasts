from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage
from utils.companion_actions import GrowthView, RenameModal


class GrowthActions(DatabaseCase):
    async def ready(self, operation='train'):
        row_id = await self.beast('twine', starter=True)
        await storage.update_player(42, gold=10000, level=8)
        for key in ('first_train', 'first_evolution'):
            await self.execute('INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(42,?)', (key,))
        from utils.progress import check_achievements
        await check_achievements(42)
        await storage.update_player(42, gold=10000)
        row = next(row for row in await storage.get_player_beasts(42) if row['id'] == row_id)
        view = GrowthView(42, row, operation)
        self.addCleanup(view.stop)
        return view, row_id

    async def test_training_preview_matches_cost_and_duplicate_confirmation_cannot_spend_twice(self):
        view, row_id = await self.ready()
        await view.preview()
        callback = view.confirm.callback
        cost = view.cost
        click = component()
        await callback(click)
        self.assertEqual((await storage.get_player(42))['gold'], 10000 - cost)
        self.assertEqual((await self.rows('SELECT train_atk FROM player_beasts WHERE id=?', (row_id,)))[0]['train_atk'], 1)
        await callback(component())
        self.assertEqual((await storage.get_player(42))['gold'], 10000 - cost)

    async def test_old_stat_confirmation_cannot_apply_a_new_selection(self):
        view, _ = await self.ready()
        await view.preview()
        old = view.confirm.callback
        selection = component()
        selection.data = {'values': ['hp']}
        await view.choose(selection)
        await old(component())
        self.assertEqual((await storage.get_player(42))['gold'], 10000)
        self.assertFalse(view.done)

    async def test_transfer_after_preview_cannot_train_a_replacement(self):
        view, row_id = await self.ready()
        await view.preview()
        callback = view.confirm.callback
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (row_id,))
        await self.beast('gloop')
        await callback(component())
        self.assertEqual((await storage.get_player(42))['gold'], 10000)
        self.assertEqual((await self.rows('SELECT SUM(train_atk) n FROM player_beasts'))[0]['n'], 0)

    async def test_evolution_preview_and_confirmation_reuse_bond_requirements(self):
        view, row_id = await self.ready('evolve')
        await self.execute('UPDATE player_beasts SET level=15,bond=40,happiness=100 WHERE id=?', (row_id,))
        await view.preview()
        self.assertFalse(view.confirm.disabled)
        confirm = view.confirm.callback
        await confirm(component())
        self.assertEqual((await self.rows('SELECT beast_id FROM player_beasts WHERE id=?', (row_id,)))[0]['beast_id'], 'timestitch')
        # Evolution can award existing companion-level achievements.
        balance = (await storage.get_player(42))['gold']
        self.assertGreaterEqual(balance, 10000)
        await confirm(component())
        self.assertEqual((await storage.get_player(42))['gold'], balance)

    async def test_rename_uses_identity_even_if_collection_numbers_change(self):
        _, row_id = await self.ready()
        target = next(row for row in await storage.get_player_beasts(42) if row['id'] == row_id)
        modal = RenameModal(42, target)
        self.addCleanup(modal.stop)
        modal.name_input._value = 'Clockwork'
        await self.execute('UPDATE player_beasts SET player_number=17 WHERE id=?', (row_id,))
        await modal.on_submit(component())
        self.assertEqual((await self.rows('SELECT nickname FROM player_beasts WHERE id=?', (row_id,)))[0]['nickname'], 'Clockwork')

    async def test_rename_cannot_change_a_transferred_companion(self):
        _, row_id = await self.ready()
        target = next(row for row in await storage.get_player_beasts(42) if row['id'] == row_id)
        modal = RenameModal(42, target)
        self.addCleanup(modal.stop)
        modal.name_input._value = 'Wrong owner'
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (row_id,))
        await modal.on_submit(component())
        self.assertFalse((await self.rows('SELECT nickname FROM player_beasts WHERE id=?', (row_id,)))[0]['nickname'])
