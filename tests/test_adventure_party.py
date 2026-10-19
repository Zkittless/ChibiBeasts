"""Real loadout, ownership, growth and Discord team controls on disposable saves."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import adventure_party as teams, db as storage, expeditions, journal_state
from utils.journal import JournalView
from utils.progression import beast_exp_for_level, encounter_beast_xp
from utils.transfers import create_listing


class AdventureParty(DatabaseCase):
    async def ready(self):
        ids = [await self.beast('barkley', starter=True), await self.beast('gloop'), await self.beast('fairy')]
        await teams.set_slot(42, ids[1], 1)
        await teams.set_slot(42, ids[2], 2)
        return ids

    async def test_early_team_is_explicit_independent_of_raid_slots_and_unique(self):
        ids = await self.ready()
        self.assertEqual([row['id'] for row in await teams.rows(42)], ids)
        self.assertEqual(await storage.get_raid_party(42), [None, None, None])
        await teams.set_slot(42, ids[2], 1)
        self.assertEqual(await teams.assignments(42), {1: ids[2]})
        with self.assertRaises(ValueError):
            await teams.set_slot(42, ids[0], 2)
        with self.assertRaises(ValueError):
            await teams.set_slot(42, ids[1], 3)

    async def test_saved_ids_do_not_fill_with_new_loadout_or_foreign_owned_members(self):
        ids = await self.ready()
        await teams.clear_slot(42, 2)
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (ids[1],))
        self.assertEqual([row['id'] for row in await teams.rows(42, ids=ids)], [ids[0], ids[2]])
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (ids[2],))
        self.assertEqual([row['id'] for row in await teams.rows(42, ids=ids)], [ids[0]])

    async def test_assignment_blocks_foreign_listed_and_unhealthy_members(self):
        lead = await self.beast('twine', starter=True)
        other = await self.beast('fairy')
        await create_listing(42, 2, 500)
        with self.assertRaises(ValueError):
            await teams.set_slot(42, other, 1)
        with self.assertRaises(ValueError):
            await teams.set_slot(43, lead, 1)

    async def test_assigned_reserve_cannot_be_sold_until_removed(self):
        ids = await self.ready()
        with self.assertRaisesRegex(ValueError, 'adventure team'):
            await create_listing(42, 2, 500)
        await teams.clear_slot(42, 1)
        listing = await create_listing(42, 2, 500)
        self.assertEqual(listing['id'], ids[1])

    async def test_trip_locks_team_until_return_and_new_lead_does_not_duplicate_reserve(self):
        ids = await self.ready()
        trip = await expeditions.begin(42, 'woods')
        for call in (lambda: teams.clear_slot(42, 1), lambda: teams.set_slot(42, ids[2], 2),
                     lambda: storage.set_active_companion(42, 2)):
            with self.assertRaisesRegex(ValueError, 'saved expedition'):
                await call()
        await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        await storage.set_active_companion(42, 2)
        self.assertNotIn(ids[1], (await teams.assignments(42)).values())
        self.assertEqual([row['id'] for row in await teams.rows(42)], [ids[1], ids[0], ids[2]])
        self.assertEqual(await teams.assignments(42), {1: ids[0], 2: ids[2]})

    async def test_reward_atomic_and_reserve_gains_are_bounded_to_own_level(self):
        ids = await self.ready()
        base = encounter_beast_xp(50, 50)
        await self.execute('UPDATE player_beasts SET level=50 WHERE id=?', (ids[0],))
        result = await teams.award_xp(42, ids + ids, ids[0], base, 50, participated=[ids[1]])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(row['amount'] < beast_exp_for_level(1) * 1.2 for row in result))
        self.assertGreater(result[0]['amount'], result[1]['amount'])
        self.assertEqual(len({row['id'] for row in result}), 2)
        self.assertEqual(teams.xp_budget(50, 50, 50, base), 0)
        self.assertLess(teams.xp_budget(40, 40, 1, base), teams.xp_budget(40, 40, 40, base))

    async def test_journal_team_controls_dispatch_and_saved_trip_stays_within_discord_limits(self):
        await self.beast('barkley', starter=True)
        other = await self.beast('fairy')
        view = JournalView(42, await journal_state.snapshot(42), page='team')
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        self.addCleanup(view.stop)
        click = component()
        click.message = view.message
        click.data = {'values': [str(other)]}
        await view.handle(click, 'companion')
        click = component()
        click.message = view.message
        click.data = {'values': [f'{other}:1']}
        await view.handle(click, 'team_slot')
        self.assertEqual(await teams.assignments(42), {1: other})
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        await expeditions.begin(42, 'woods')
        view.state = await journal_state.snapshot(42)
        view.render()
        self.assertTrue(view.team_select.disabled)
        self.assertLessEqual(view.total_children_count, 40)
