"""Native Nursery ownership, stale controls, purchases and saved-choice flows."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.hatch import Hatch
from cogs.world import World
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage, nursery
from utils.gear_art import Portrait
from utils.nursery_catalog import RESEARCH_COST, research_key
from utils.nursery_view import NurseryView, open_nursery


class NurseryInteractions(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.media = patch('utils.nursery_view.gear_art.render_portrait', new=AsyncMock(return_value=None))
        self.media.start()
        self.addCleanup(self.media.stop)
        self.input_id = 100

    def click(self, *, uid=42, value=None):
        click = interaction(uid)
        self.input_id += 1
        click.id = self.input_id
        click.data = {'values': [str(value)]} if value is not None else {}
        click.response.send_modal = AsyncMock()
        return click

    async def view(self, **kwargs):
        view = await open_nursery(self.click(), **kwargs)
        self.addCleanup(view.stop)
        return view

    def text(self, view):
        return '\n'.join(c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))

    def button(self, view, label):
        return next(c for c in view.walk_children() if isinstance(c, discord.ui.Button) and c.label == label)

    def valid(self, view):
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)
        custom_ids = []
        for child in view.walk_children():
            if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                custom_ids.append(child.custom_id)
                self.assertLessEqual(len(child.custom_id), 100)
            if isinstance(child, discord.ui.Select):
                self.assertTrue(1 <= len(child.options) <= 25)
        self.assertEqual(len(custom_ids), len(set(custom_ids)))
        self.assertTrue(view.to_components())

    async def ready(self, egg='sprout_pod'):
        await storage.add_item(42, egg)
        row = await nursery.incubate(42, egg, request_key='start:' + egg,
            now=datetime.now(timezone.utc) - timedelta(days=8))
        return row

    async def test_opening_and_commands_acknowledge_before_database_and_art(self):
        click = self.click()
        original = nursery.snapshot
        async def checked_snapshot(*args, **kwargs):
            click.response.defer.assert_awaited_once()
            return await original(*args, **kwargs)
        with patch('utils.nursery.snapshot', side_effect=checked_snapshot):
            view = await open_nursery(click)
        self.addCleanup(view.stop)
        self.assertIsInstance(view, discord.ui.LayoutView)
        self.valid(view)
        self.assertIn('The Nursery', self.text(view))
        for command, cog in ((Hatch.nursery, Hatch(None)), (World.eggs, World(None))):
            click = self.click()
            await command.callback(cog, click)
            click.response.defer.assert_awaited_once()
            self.addCleanup(click.followup.send.call_args.kwargs['view'].stop)

    async def test_owner_check_prevents_mutation_without_deferring(self):
        await storage.add_item(42, 'common_egg')
        view = await self.view()
        click = self.click(uid=99)
        await self.button(view, 'Hatch one').callback(click)
        click.response.send_message.assert_awaited_once()
        click.response.defer.assert_not_awaited()
        self.assertEqual(len(await storage.get_player_beasts(42)), 0)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)

    async def test_stale_hatch_button_cannot_consume_another_egg(self):
        await storage.add_item(42, 'common_egg', 2)
        view = await self.view()
        button = self.button(view, 'Hatch one')
        await button.callback(self.click())
        self.assertEqual(view.page, 'reveal')
        stale = self.click()
        await button.callback(stale)
        self.assertIn('older page', stale.followup.send.call_args.args[0])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        self.valid(view)

    async def test_bulk_modal_is_bounded_and_rechecks_level_on_submit(self):
        await storage.update_player(42, level=30)
        await storage.add_item(42, 'celestial_egg', 100)
        view = await self.view(egg_id='celestial_egg')
        click = self.click()
        await self.button(view, 'Hatch quantity…').callback(click)
        modal = click.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        self.assertEqual(modal._max, 25)
        await storage.update_player(42, level=29)
        await modal._callback(self.click(), 2)
        self.assertIn('trainer level 30', self.text(view))
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 100)
        self.assertEqual(len(await storage.get_player_beasts(42)), 0)

    async def test_batch_modal_preserves_original_egg_after_selection_changes(self):
        await storage.update_player(42, level=12)
        await storage.add_item(42, 'common_egg', 3)
        await storage.add_item(42, 'rare_egg', 1)
        view = await self.view(egg_id='common_egg')
        click = self.click()
        await self.button(view, 'Hatch quantity…').callback(click)
        modal = click.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        await view.egg_select.callback(self.click(value='rare_egg'))
        await modal._callback(self.click(), 2)
        self.assertEqual(len(view.grants), 2)
        self.assertTrue(all(g['egg_id'] == 'common_egg' for g in view.grants))
        self.assertEqual({r['item_id']: r['quantity'] for r in await storage.get_inventory(42)}, {'common_egg': 1, 'rare_egg': 1})

    async def test_purchase_request_replay_pays_and_tracks_progress_once(self):
        await storage.update_player(42, gold=1000)
        view = await self.view(egg_id='common_egg')
        click = self.click()
        await view.handle(click, 'buy')
        await view.handle(click, 'buy')
        self.assertEqual((await storage.get_player(42))['gold'], 800)
        self.assertEqual((await storage.get_inventory(42))[0]['quantity'], 1)
        rows = await self.rows("SELECT value FROM player_progress WHERE user_id=42 AND stat_key='spend_gold'")
        self.assertEqual(rows[0]['value'], 200)
        self.assertIn('800 gold', self.text(view))

    async def test_old_tend_command_prepares_saved_choice_then_one_claim(self):
        row = await self.ready()
        click = self.click()
        await World.tend.callback(World(None), click, row['id'])
        view = click.followup.send.call_args.kwargs['view']
        self.addCleanup(view.stop)
        self.assertEqual(view.page, 'offer')
        self.assertEqual(await storage.get_player_beasts(42), [])
        saved = deepcopy(view.offer['candidates'])
        again = await nursery.prepare_offer(42, row['id'])
        self.assertEqual(saved, again['candidates'])
        candidate = view.offer['candidates'][0]
        button = self.button(view, 'Choose ' + candidate['name'])
        await button.callback(self.click())
        await button.callback(self.click())
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)
        self.assertIn('Welcome,', self.text(view))
        self.valid(view)

    async def test_timed_research_uses_paid_offer_and_grade_floor(self):
        row = await self.ready()
        await self.execute('INSERT INTO nursery_research VALUES(?,?,?)', (42, research_key('sprout_pod'), RESEARCH_COST))
        offer = await nursery.prepare_offer(42, row['id'])
        view = await self.view(page='offer', offer=offer)
        await self.button(view, 'Use family research instead').callback(self.click())
        self.assertTrue(view.research_offer)
        self.assertTrue(view.targets)
        target = view.targets[0]['id']
        await view.target_select.callback(self.click(value=target))
        self.assertIn('this incubating egg', self.text(view))
        await self.button(view, 'Confirm researched hatch').callback(self.click())
        beast = (await storage.get_player_beasts(42))[0]
        self.assertEqual(beast['beast_id'], target)
        self.assertEqual(beast['rarity'], 'common')
        self.assertEqual((await self.rows('SELECT points FROM nursery_research'))[0]['points'], 0)
        self.assertIn('8 points spent', self.text(view))
        self.assertNotIn('+1 family research', self.text(view))

    async def test_portrait_reveal_has_original_art_and_text_fallback(self):
        await storage.add_item(42, 'common_egg')
        grant = (await nursery.hatch(42, 'common_egg', request_key='portrait'))[0]
        self.media.stop()
        with patch('utils.nursery_view.gear_art.render_portrait', new=AsyncMock(return_value=Portrait('p.png', b'image'))) as render:
            click = self.click()
            view = await open_nursery(click, page='reveal', grants=[grant])
            self.addCleanup(view.stop)
            render.assert_awaited_once_with(grant['beast_id'])
            self.assertTrue(click.followup.send.call_args.kwargs['files'])
            self.assertTrue(any(isinstance(i, discord.ui.Thumbnail) for i in view.walk_children()))
        with patch('utils.nursery_view.gear_art.render_portrait', new=AsyncMock(return_value=None)):
            fallback = await self.view(page='reveal', grants=[grant])
            self.assertFalse(any(isinstance(i, discord.ui.Thumbnail) for i in fallback.walk_children()))
            self.assertIn('Welcome,', self.text(fallback))
            self.valid(fallback)

    async def test_all_eggs_pools_and_many_options_fit_discord_limits(self):
        await storage.update_player(42, level=50)
        view = await self.view()
        for egg in view.state['eggs']:
            view.egg_id = egg['id']
            for page in ('eggs', 'pool'):
                view.page = page
                view.render()
                self.valid(view)
        expanded = deepcopy(view.state['eggs'])
        for index in range(30):
            expanded.append({**expanded[0], 'id': f'future_{index}', 'name': f'Future egg {index}'})
        view.state['eggs'], view.egg_page, view.page = expanded, 2, 'eggs'
        view.render()
        self.valid(view)
        self.assertLessEqual(len(view.egg_select.options), 25)

    async def test_expiry_closes_controls_but_keeps_saved_incubation(self):
        await self.ready()
        view = await self.view(page='incubations')
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        self.assertIn('closed', self.text(view))
        self.assertFalse(any(isinstance(i, (discord.ui.Button, discord.ui.Select)) for i in view.walk_children()))
        click = self.click()
        await view.handle(click, 'offer')
        click.response.send_message.assert_awaited_once()
        self.assertEqual(len((await nursery.snapshot(42))['incubations']), 1)
