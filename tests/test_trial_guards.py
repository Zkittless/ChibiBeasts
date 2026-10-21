"""Paused towers keep their team frozen across other command entry points."""
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase, interaction
from utils import adventure_party, care, db as storage, trial_locks
from utils.duels import FighterBusyError, reserve_fighters
from utils.items import use_item


class TrialMutationGuards(DatabaseCase):
    async def frozen_team(self):
        lead = await self.beast('twine', starter=True)
        other = await self.beast('barkley')
        payload = json.dumps(dict(party_ids=[lead], party=[dict(id=lead)]))
        await self.execute('INSERT INTO trial_runs(id,user_id,destination,status,version,payload,created_at,updated_at,expires_at) '
                           "VALUES('guard-run',42,'woods','paused',1,?,1,1,9999999999)", (payload,))
        trial_locks.lock(42)
        return lead, other

    async def close_run(self):
        await self.execute("UPDATE trial_runs SET status='complete' WHERE id='guard-run'")
        trial_locks.unlock(42)

    async def test_paused_team_cannot_be_healed_through_care_but_other_beasts_can(self):
        lead, other = await self.frozen_team()
        await self.execute('UPDATE player_beasts SET hp=1 WHERE user_id=42')
        await storage.add_item(42, 'pixie_dust_potion', 2)
        state = await care.snapshot(42, lead)
        self.assertTrue(state.trial_locked)
        self.assertFalse(care.preview(state, 'pixie_dust_potion').usable)
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await use_item(42, 'pixie_dust_potion', expected_row_id=lead)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 2)
        await use_item(42, 'pixie_dust_potion', expected_row_id=other)
        rows = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual(rows[lead]['hp'], 1)
        self.assertGreater(rows[other]['hp'], 1)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_cache_is_restored_from_persistent_runs_and_is_database_scoped(self):
        await self.frozen_team()
        trial_locks.unlock(42)
        self.assertFalse(trial_locks.locked(42))
        async with storage.connect() as db:
            await trial_locks.reload(db)
        with self.assertRaises(FighterBusyError):
            with reserve_fighters([42]):
                self.fail('A paused trial must reserve its trainer after reload.')
        with reserve_fighters([42], allow_trial=True):
            pass
        with patch.object(storage, 'DB_PATH', self.db_path + '.other'):
            self.assertFalse(trial_locks.locked(42))

    async def test_database_checks_block_team_and_growth_edits_even_without_cache(self):
        lead, other = await self.frozen_team()
        trial_locks.unlock(42)
        other_row = next(row for row in await storage.get_player_beasts(42) if row['id'] == other)
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await storage.set_active_companion(42, other_row['player_number'])
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await adventure_party.set_slot(42, other, 1)
        from utils.equipment import equip
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await equip(42, 1, 'gnome_iron_plating')
        from utils.gameplay import evolve_beast
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await evolve_beast(42, 1)
        from cogs.economy import Economy
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await Economy(None)._train(interaction(), 1, 'attack')
        self.assertEqual((await storage.get_player(42))['gold'], 500)

    async def test_knockout_recovery_cannot_revive_frozen_members(self):
        lead, other = await self.frozen_team()
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await self.execute('UPDATE player_beasts SET hp=0,knocked_out_until=? WHERE user_id=42', (expired,))
        members = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual(members[lead]['hp'], 0)
        self.assertEqual(members[other]['hp'], members[other]['max_hp'])
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await storage.revive_beast(lead)
        await self.close_run()
        members = {row['id']: row for row in await storage.get_player_beasts(42)}
        self.assertEqual(members[lead]['hp'], members[lead]['max_hp'])

    async def test_paid_reset_cannot_bypass_live_run_and_resets_new_cooldown_afterward(self):
        await self.frozen_team()
        await storage.add_item(42, 'chrono_biscuit')
        await self.execute('INSERT INTO trial_cooldowns VALUES(42,9999999999)')
        with self.assertRaisesRegex(ValueError, 'Trial'):
            await use_item(42, 'chrono_biscuit')
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        await self.close_run()
        await use_item(42, 'chrono_biscuit')
        self.assertEqual((await self.rows('SELECT ready_at FROM trial_cooldowns WHERE user_id=42'))[0]['ready_at'], 0)
        self.assertEqual(await storage.get_inventory(42), [])

    async def test_nursery_care_remains_available_while_team_is_frozen(self):
        await self.frozen_team()
        from utils import nursery
        await storage.add_item(42, 'common_egg')
        grant = await nursery.hatch(42, 'common_egg', request_key='trial-nursery')
        self.assertTrue(grant)
        self.assertTrue(trial_locks.locked(42))
