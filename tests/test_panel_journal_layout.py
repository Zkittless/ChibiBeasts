from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage, expeditions, journal_state
from utils.journal import JournalView
from utils.roster_view import OwnedRosterView


class JournalLayout(DatabaseCase):
    async def ready(self, page):
        await self.beast('twine', starter=True)
        await storage.update_player(42, level=3)
        view = JournalView(42, await journal_state.snapshot(42), page=page)
        view.art_enabled = False
        view.message = SimpleNamespace(id=321, attachments=[], delete=AsyncMock(), edit=AsyncMock())
        await view.load_page()
        view.render()
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    async def click(self, view, action, value=None):
        click = component()
        click.message = view.message
        click.data = {'values': [value]} if value is not None else {}
        click.edit_original_response.return_value = view.message
        await view.handle(click, action)
        return click

    async def test_story_dialogue_can_be_reopened_after_introduction_is_recorded(self):
        view = await self.ready('story')
        intro = view.chapter['intro'][0]
        before = await storage.get_player(42)
        self.assertNotIn(intro, self.text(view))
        await self.click(view, 'story_dialogue')
        self.assertFalse(view.introduce)
        self.assertIn(intro, self.text(view))
        await self.click(view, 'story_dialogue')
        self.assertNotIn(intro, self.text(view))
        after = await storage.get_player(42)
        self.assertEqual((before['gold'], before['exp']), (after['gold'], after['exp']))

    async def test_daily_objective_details_toggle_without_claiming_rewards(self):
        view = await self.ready('activities')
        quest = view.dailies[0]
        description = quest['desc'].format(target=quest['target'])
        self.assertNotIn(description, self.text(view))
        before = await storage.get_player(42)
        await self.click(view, 'daily_details')
        self.assertIn(description, self.text(view))
        after = await storage.get_player(42)
        self.assertEqual(before, after)

    async def test_companions_menu_uses_shared_roster_and_retires_previous_journal(self):
        view = await self.ready('home')
        old = view.message
        click = await self.click(view, 'page_menu', 'companions')
        roster = click.followup.send.call_args.kwargs['view']
        self.addCleanup(roster.stop)
        self.assertIsInstance(roster, OwnedRosterView)
        self.assertTrue(view.closed)
        old.delete.assert_awaited_once()
        self.assertIsNone(await expeditions.current(42))
        self.assertEqual((await expeditions.energy(42)).charges, 1)
