"""Exercise the real journal services and Discord component dispatch offline."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
from discord.ui.view import ViewStore

from cogs.journal import Journal
from cogs.utilities import Utilities
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import crafting, db as storage, expeditions, journal_state, story
from utils.journal import JournalView, open_journal
from utils.workshop import WorkshopView, open_workshop


class JournalFlow(DatabaseCase):
    async def ready(self):
        return await self.beast('twine', starter=True)

    async def view(self, page='home', **kwargs):
        view = JournalView(42, await journal_state.snapshot(42), page=page, **kwargs)
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        await view.load_page()
        view.render()
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(i.content for i in view.walk_children() if isinstance(i, discord.ui.TextDisplay))

    async def click(self, view, action, value=None, **kwargs):
        click = component(**kwargs)
        click.message = view.message
        if value is not None:
            click.data = {'values': [str(value)]}
        await view.handle(click, action)
        return click

    def assert_valid(self, view):
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        ids = [item.custom_id for item in view.walk_children() if isinstance(item, (discord.ui.Button, discord.ui.Select))]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for item in view.walk_children():
            if isinstance(item, discord.ui.Select):
                self.assertTrue(1 <= len(item.options) <= 25)
                self.assertTrue(all(len(option.value) <= 100 for option in item.options))

    async def test_journal_opens_the_hub_without_spending_a_charge(self):
        initial = interaction()
        await Journal.journal.callback(Journal(None), initial)
        initial.response.defer.assert_awaited_once()
        view = initial.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        self.assertIsInstance(view, JournalView)
        self.assertIn('Meet your starter', [b.label for b in view.walk_children() if isinstance(b, discord.ui.Button)])
        self.assert_valid(view)
        self.assertEqual((await expeditions.energy(42)).charges, 1)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])

    async def test_recipe_goal_uses_forge_costs_and_actual_equipped_ownership(self):
        await journal_state.pin_goal(42, 'recipe', 'gnome_iron_plating')
        await self.execute("INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,'gnome_iron_ore',2)")
        goal = (await journal_state.snapshot(42)).goal
        self.assertIn('2/3', goal['text'])
        self.assertEqual(goal['realm'], 'woods')
        await self.execute("INSERT INTO guilds(id,name) VALUES(3,'Forge')")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(3,42)')
        await self.execute('INSERT INTO guild_sanctuary(guild_id,gnome_forge) VALUES(3,1)')
        goal = (await journal_state.snapshot(42)).goal
        self.assertIn('All materials ready', goal['text'])
        row_id = await self.ready()
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (row_id,))
        self.assertTrue((await journal_state.snapshot(42)).goal['done'])

    async def test_hunt_only_offers_real_habitats_with_level_gates(self):
        with self.assertRaises(ValueError):
            await journal_state.pin_goal(42, 'creature', 'twine')
        await journal_state.pin_goal(42, 'creature', 'genesis')
        goal = (await journal_state.snapshot(42)).goal
        self.assertIsNone(goal['realm'])
        self.assertIn('Lv.10', goal['text'])
        await storage.update_player(42, level=10)
        goal = (await journal_state.snapshot(42)).goal
        self.assertEqual(goal['realm'], 'loom')
        await self.beast('genesis')
        self.assertTrue((await journal_state.snapshot(42)).goal['done'])

    async def test_recipe_gathering_goals_follow_material_regions_instead_of_creature_habitats(self):
        await storage.update_player(42, level=10)
        for recipe, expected in (('unicorn_healer_vestments', 'glacial'), ('basilisk_gaze_pauldrons', 'loom')):
            with self.subTest(recipe=recipe):
                await journal_state.pin_goal(42, 'recipe', recipe)
                goal = (await journal_state.snapshot(42)).goal
                self.assertEqual(goal['realm'], expected)
                self.assertIn(journal_state.BIOMES[expected]['name'], goal['text'])

    async def test_awakening_goal_follows_identity_and_handles_evolution_or_transfer(self):
        row_id = await self.ready()
        await journal_state.pin_goal(42, 'awakening', row_id)
        goal = (await journal_state.snapshot(42)).goal
        self.assertIn('Bond 0/30', goal['text'])
        await self.execute("UPDATE player_beasts SET beast_id='timestitch' WHERE id=?", (row_id,))
        self.assertTrue((await journal_state.snapshot(42)).goal['done'])
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (row_id,))
        goal = (await journal_state.snapshot(42)).goal
        self.assertFalse(goal['done'])
        self.assertIn('left your collection', goal['text'])

    async def test_goal_and_collection_pagination_reach_targets_beyond_twenty_five(self):
        await self.ready()
        for _ in range(25):
            await self.beast('slime')
        view = await self.view('goals')
        self.assertEqual(len(view.goal_target.options), 25)
        await self.click(view, 'goal_next')
        self.assertTrue(view.goal_target.options)
        target = view.goal_target.options[-1].value
        await self.click(view, 'goal_target', target)
        self.assertEqual(view.state.goal['target'], target.split(':', 1)[1])
        await self.click(view, 'page:companions')
        await self.click(view, 'comp_next')
        self.assertEqual(len(view.companion_select.options), 1)
        await self.click(view, 'make_active')
        self.assertEqual((await storage.get_active_beast(42))['player_number'], 26)
        self.assert_valid(view)

    async def test_buttons_acknowledge_before_waiting_for_database_or_lock(self):
        view = await self.view()
        started, release = asyncio.Event(), asyncio.Event()
        original = journal_state.snapshot
        click = component()

        async def delayed(uid):
            self.assertTrue(click.response.is_done())
            started.set()
            await release.wait()
            return await original(uid)

        with patch.object(journal_state, 'snapshot', side_effect=delayed):
            task = asyncio.create_task(view.handle(click, 'refresh'))
            try:
                await asyncio.wait_for(started.wait(), 1)
                click.response.defer.assert_awaited_once()
                second = component()
                await asyncio.wait_for(view.handle(second, 'begin'), 1)
                second.response.defer.assert_awaited_once()
                self.assertIn('updating', second.followup.send.call_args.args[0])
            finally:
                release.set()
                await asyncio.wait_for(task, 3)

    async def test_other_trainers_and_expired_pages_are_acknowledged_without_mutation(self):
        view = await self.view()
        wrong = await self.click(view, 'begin', uid=43)
        wrong.response.send_message.assert_awaited_once()
        await view.on_timeout()
        click = await self.click(view, 'begin')
        click.response.send_message.assert_awaited_once()
        self.assertTrue(all(i.disabled for i in view.walk_children() if isinstance(i, (discord.ui.Button, discord.ui.Select))))
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])

    async def test_real_viewstore_keeps_nav_and_revision_checked_path_select_usable(self):
        await self.ready()
        view = await self.view()
        message, store = view.message, ViewStore(None)
        self.addCleanup(lambda: store.remove_view(view))
        store.add_view(view, message.id)
        stable = view.path_select.custom_id
        for action in ('page:adventure', 'begin'):
            button = next(i for i in view.walk_children() if isinstance(i, discord.ui.Button) and f':{action}:' in i.custom_id)
            edited = asyncio.Event()

            async def edit(**kwargs):
                store.add_view(kwargs['view'], message.id)
                edited.set()
                return message

            click = component(edit=edit)
            click.message = message
            store.dispatch_view(2, button.custom_id, click)
            await asyncio.wait_for(edited.wait(), 3)
            click.response.defer.assert_awaited_once()
        stale = next(o.value for o in view.path_select.options if o.value.endswith(':gather'))
        for value in (stale, stale):
            edited.clear()
            click = component(edit=edit)
            click.data = {'values': [value]}
            click.message = message
            store.dispatch_view(3, stable, click)
            await asyncio.wait_for(edited.wait(), 3)
            click.response.defer.assert_awaited_once()
        self.assertEqual(view.trip['version'], 1)
        self.assertIn('older page', view.notice)
        self.assertEqual(view.path_select.custom_id, stable)
        self.assert_valid(view)

    async def test_expedition_survives_deleted_journal_and_new_view_can_finish(self):
        await self.ready()
        view = await self.view('adventure')
        await self.click(view, 'begin')
        trip_id = view.trip['id']
        await self.click(view, 'path', f'{trip_id}:0:shrine')
        opened = interaction()
        await open_journal(opened, page='adventure')
        resumed = opened.followup.send.call_args.kwargs['view']
        self.addCleanup(resumed.stop)
        self.assertEqual(resumed.trip['id'], trip_id)
        await self.click(resumed, 'path', f'{trip_id}:1:repair')
        await self.click(resumed, 'path', f'{trip_id}:2:return')
        self.assertEqual(resumed.trip['status'], 'complete')
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assert_valid(resumed)

    async def test_deleted_page_before_battle_upload_cannot_strand_the_trip(self):
        await self.ready()
        view = await self.view('adventure')
        await self.click(view, 'begin')
        with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
            await self.click(view, 'path', view.path_select.options[0].value)
        missing = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), {'code': 10008, 'message': 'Unknown Message'})
        click = component(edit=AsyncMock(side_effect=missing))
        click.message = view.message
        click.data = {'values': [next(o.value for o in view.path_select.options if o.value.endswith(':challenge'))]}
        await view.handle(click, 'path')
        saved = await expeditions.current(42)
        self.assertEqual(saved['status'], 'active')
        self.assertEqual(saved['payload']['encounter_result'], 'interrupted')
        self.assertFalse(view.mutating)

    async def test_completed_trip_is_visible_when_journal_is_reopened(self):
        await self.ready()
        trip = await expeditions.begin(42, 'woods')
        await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        opened = interaction()
        view = await open_journal(opened, page='adventure')
        self.addCleanup(view.stop)
        self.assertEqual(view.trip['id'], trip['id'])
        self.assertIn('Back from', self.text(view))

    async def test_workshop_pin_and_handoffs_preserve_selection_without_crafting(self):
        await self.ready()
        view = await self.view()
        await journal_state.pin_goal(42, 'recipe', 'gnome_iron_plating')
        click = await self.click(view, 'goal_workshop')
        workshop = click.edit_original_response.call_args.kwargs['view']
        self.addCleanup(workshop.stop)
        self.assertIsInstance(workshop, WorkshopView)
        self.assertEqual(workshop.selected, 'gnome_iron_plating')
        self.assertTrue(view.is_finished())
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), [])
        click = await self.click(workshop, 'pin_recipe')
        self.assertIn('pinned', workshop.notice)
        click = await self.click(workshop, 'journal')
        journal = click.edit_original_response.call_args.kwargs['view']
        self.addCleanup(journal.stop)
        self.assertIsInstance(journal, JournalView)
        self.assertEqual(journal.state.goal['target'], 'gnome_iron_plating')

    async def test_launch_uses_real_daily_command_and_refresh_marks_claimed(self):
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        self.addAsyncCleanup(bot.close)
        await bot.add_cog(Utilities(bot))
        view = await self.view('activities', bot=bot)
        before = (await storage.get_player(42))['gold']
        click = await self.click(view, 'launch:daily')
        click.response.defer.assert_awaited_once()
        self.assertGreater((await storage.get_player(42))['gold'], before)
        await self.click(view, 'refresh')
        daily = next(i for i in view.walk_children() if isinstance(i, discord.ui.Button) and i.label == 'Claim daily')
        self.assertTrue(daily.disabled)
        self.assertNotIn('{target}', self.text(view))

    async def test_story_all_chapters_final_choices_and_awakening_fit_component_limits(self):
        row_id = await self.ready()
        await storage.update_player(42, level=30)
        view = await self.view('story')
        for chapter in story.load_questline()['chapters'].values():
            view.chapter, view.story_state, view.introduce = chapter, {'step_progress': {}}, True
            view.render()
            self.assert_valid(view)
            view.story_state = {'step_progress': {chapter['id']: {s['id']: s.get('target', 1) for s in chapter['steps']}}}
            view.introduce = False
            view.render()
            self.assert_valid(view)
        view.page, view.reviewing = 'companions', True
        view.render()
        self.assert_valid(view)
        await self.execute('UPDATE player_beasts SET level=15,bond=30,happiness=80 WHERE id=?', (row_id,))
        view.state = await journal_state.snapshot(42)
        await self.click(view, 'awaken', f'{row_id}:twine')
        self.assertEqual((await storage.get_active_beast(42))['beast_id'], 'timestitch')
        self.assert_valid(view)
        await self.click(view, 'awaken', f'{row_id}:twine')
        self.assertIn('changed', view.notice)

    async def test_companions_explains_bond_growth_and_limits_combat_bonus_to_starters(self):
        await self.ready()
        view = await self.view('companions')
        self.assertIn('battle win: +2', self.text(view))
        self.assertIn('up to 10% at 100 bond', self.text(view))
        self.assertIn('No item is required', self.text(view))
        ordinary_id = await self.beast('wisp')
        view.state = await journal_state.snapshot(42)
        view.companion_id = ordinary_id
        view.render()
        self.assertNotIn('0.1%', self.text(view))
        self.assertIn('Any companion can build a bond', self.text(view))
        self.assert_valid(view)

    async def test_artwork_failure_falls_back_and_uploaded_files_close(self):
        await self.ready()
        view = await self.view()
        rejected = discord.HTTPException(SimpleNamespace(status=400, reason='Invalid'), 'attachments rejected')
        click = component()
        files = []

        async def edit(**kwargs):
            files.extend(f for f in kwargs['attachments'] if isinstance(f, discord.File))
            if len(files) and view.art_enabled:
                raise rejected
            return SimpleNamespace(id=123, attachments=[], edit=AsyncMock())

        click.edit_original_response.side_effect = edit
        await view.handle(click, 'refresh')
        self.assertFalse(view.art_enabled)
        self.assertTrue(files and all(f.fp.closed for f in files))
        self.assert_valid(view)
        self.assertFalse(any(isinstance(i, discord.ui.Thumbnail) for i in view.walk_children()))
