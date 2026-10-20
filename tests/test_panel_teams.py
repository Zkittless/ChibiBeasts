from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import adventure_party, db as storage, journal_state, journal_team, expeditions
from utils.journal import JournalView


class SharedTeams(DatabaseCase):
    async def ready(self):
        self.lead = await self.beast('twine', starter=True)
        self.reserve = await self.beast('gloop')
        self.third = await self.beast('barkley')
        await adventure_party.set_slot(42, self.reserve, 1)
        view = JournalView(42, await journal_state.snapshot(42), page='team')
        view.art_enabled = False
        view.message = SimpleNamespace(id=123, attachments=[])
        self.addCleanup(view.stop)
        return view

    async def click(self, view, action, value=None):
        click = component()
        click.edit_original_response.return_value = view.message
        click.data = {'values': [value]} if value is not None else {}
        await view.handle(click, action)
        return click

    async def assign(self, view, role, row_id):
        await self.click(view, 'team_role:' + role)
        await self.click(view, 'team_candidate', f'{role}:{row_id}')
        await self.click(view, f'team_assign:{role}:{row_id}')

    async def test_raid_mode_shares_editor_and_preserves_exploration_assignments(self):
        view = await self.ready()
        before = await self.rows('SELECT * FROM adventure_team')
        await self.click(view, 'team_mode:raid')
        for role, row_id in zip(journal_team.RAID_ROLES, (self.lead, self.reserve, self.third)):
            await self.assign(view, role, row_id)
        self.assertEqual([row['id'] for row in await storage.get_raid_party(42)], [self.lead, self.reserve, self.third])
        self.assertEqual(await self.rows('SELECT * FROM adventure_team'), before)
        await self.click(view, 'team_mode:exploration')
        self.assertEqual([row['id'] for _, row in journal_team.roster(view) if row], [self.lead, self.reserve])
        self.assertLessEqual(view.total_children_count, 40)

    async def test_stale_clear_does_not_remove_reassigned_raid_slot(self):
        view = await self.ready()
        await self.click(view, 'team_mode:raid')
        await self.assign(view, 'raid1', self.lead)
        await storage.set_raid_slot(42, self.third, 1)
        await self.click(view, f'team_clear:raid1:{self.lead}')
        self.assertEqual((await storage.get_raid_party(42))[0]['id'], self.third)

    async def test_active_combat_still_blocks_raid_changes(self):
        from utils.duels import active_fighters
        view = await self.ready()
        await self.click(view, 'team_mode:raid')
        await self.click(view, 'team_role:raid1')
        await self.click(view, 'team_candidate', f'raid1:{self.lead}')
        active_fighters.add(42)
        try:
            await self.click(view, f'team_assign:raid1:{self.lead}')
        finally:
            active_fighters.discard(42)
        self.assertEqual(await storage.get_raid_party(42), [None, None, None])

    async def test_menu_navigation_does_not_spend_a_charge(self):
        view = await self.ready()
        before = (await expeditions.energy(42)).charges
        await self.click(view, 'page_menu', 'adventure')
        self.assertEqual(view.page, 'adventure')
        self.assertIsNone(view.trip)
        self.assertEqual((await expeditions.energy(42)).charges, before)
