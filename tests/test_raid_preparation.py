"""Raid admission and consumable conservation at the lobby/payment boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from cogs.guilds import RAID_BOSSES
from utils import combat, db as storage, raids
from utils.duels import active_fighters


class RaidPreparation(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await storage.update_player(42, level=15, status_ward_charges=2)
        for slot, species in enumerate(('griffin', 'basilisk', 'wyvern'), 1):
            row_id = await storage.add_beast_to_player(42, {**storage.get_beast_data(species), 'level': 15})
            await storage.set_raid_slot(42, row_id, slot)
        await self.execute("INSERT INTO guilds(id,name,leader_id,level,guild_tokens) VALUES(5,'Raid guild',42,5,100)")
        await self.execute("INSERT INTO guild_members(guild_id,user_id,rank) VALUES(5,42,'leader')")
        self.raid = raids.RaidSession(SimpleNamespace(id=7, send=AsyncMock()), RAID_BOSSES['corrupted'][0], {42}, 5)

    async def asyncTearDown(self):
        raids.active_raids.pop(self.raid.id, None)
        active_fighters.discard(42)
        combat.active_states.pop(42, None)
        combat.active_parties.pop(42, None)
        await super().asyncTearDown()

    async def test_queued_wards_are_not_copied_to_all_three_raid_companions(self):
        await self.raid.start(42, None)
        self.assertEqual([state['status_ward'] for state in self.raid.party[42]], [True, False, False])
        self.assertEqual((await storage.get_player(42))['status_ward_charges'], 1)

    async def test_member_leaving_during_preparation_prevents_payment(self):
        original = raids.party_rows
        async def changed(uid):
            result = await original(uid)
            await self.execute('DELETE FROM guild_members WHERE user_id=?', (uid,))
            return result
        with patch.object(raids, 'party_rows', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'guild'):
                await self.raid.start(42, None)
        self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds'))[0]['guild_tokens'], 100)
        self.assertEqual(await self.rows('SELECT * FROM raid_payments'), [])
        self.assertNotIn(42, active_fighters)

    async def test_demoted_host_cannot_spend_guild_tokens_from_old_lobby(self):
        await self.execute("UPDATE guild_members SET rank='member' WHERE user_id=42")
        with self.assertRaisesRegex(ValueError, 'leader|officer'):
            await self.raid.start(42, None)
        self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds'))[0]['guild_tokens'], 100)
