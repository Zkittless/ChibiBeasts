"""Party recovery targets the same members in memory and in the saved game."""
from tests.test_gameplay import DatabaseCase
from utils import adventure_party, combat, db as storage
from utils.duels import persist_pve_states, prepare_state
from utils.items import use_item


class AdventureRecovery(DatabaseCase):
    async def test_party_revive_clears_adventure_reserve_knockout_after_persistence(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        await adventure_party.set_slot(42, reserve, 1)
        party = [await prepare_state(42, row, storage.get_beast_data(row['beast_id']), [], consume=False)
                 for row in await adventure_party.rows(42)]
        self.assertEqual([state['id'] for state in party], [lead, reserve])
        await storage.knockout_beast(reserve, 'common')
        party[1].update(hp=0, ko_recorded=True)
        combat.active_states[42], combat.active_parties[42] = party[0], party
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'tear_of_leviathan',1)")
        result = await use_item(42, 'tear_of_leviathan')
        self.assertIn('battle team', result)
        self.assertEqual(party[1]['hp'], party[1]['max_hp'])
        await persist_pve_states(42, party, party[0])
        saved = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertIsNone(saved[reserve]['knocked_out_until'])
        self.assertEqual(saved[reserve]['hp'], saved[reserve]['max_hp'])
        self.assertFalse(party[1]['ko_recorded'])

    async def test_home_recovery_includes_assigned_adventure_reserve_not_unassigned_collection(self):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        stranger = await self.beast('imp')
        await adventure_party.set_slot(42, reserve, 1)
        for row_id in (lead, reserve, stranger):
            await storage.knockout_beast(row_id, 'common')
        await self.execute("INSERT INTO player_inventory(user_id,item_id,quantity) VALUES(42,'tear_of_leviathan',1)")
        await use_item(42, 'tear_of_leviathan')
        saved = {row['id']: row for row in await storage.get_player_beasts(42)}
        for row_id in (lead, reserve):
            self.assertIsNone(saved[row_id]['knocked_out_until'])
            self.assertEqual(saved[row_id]['hp'], saved[row_id]['max_hp'])
        self.assertTrue(storage.is_knocked_out(saved[stranger]))
