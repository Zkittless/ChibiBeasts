"""Owner tools fast-forward incubation through the same saved atomic claim."""
from datetime import datetime, timedelta, timezone
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.dev import Dev
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage, nursery
from utils.nursery_catalog import research_key


class NurseryDeveloperTools(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await storage.update_player(42, level=50)
        self.member = SimpleNamespace(id=42, display_name='Target trainer')

    async def incubate(self, *, user_id=42, future=False):
        await storage.add_item(user_id, 'prism_sphere')
        clock = datetime.now(timezone.utc) + timedelta(days=20 if future else -20)
        return await nursery.incubate(user_id, 'prism_sphere', request_key=f'start:{user_id}', now=clock)

    def click(self, key=1234):
        click = interaction(1)
        click.id = key
        return click

    async def test_saved_offer_is_reused_with_duplicate_research_and_progress(self):
        row = await self.incubate()
        offer = await nursery.prepare_offer(42, row['id'], rng=random.Random(11))
        first = offer['candidates'][0]
        await storage.add_beast_to_player(42, storage.get_beast_data(first['beast_id']))
        click = self.click()
        await Dev.hatch_egg.callback(Dev(None), click, self.member, row['id'])
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        grants = (await nursery.snapshot(42))['recent']
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]['beast_id'], first['beast_id'])
        self.assertEqual(grants[0]['level'], first['level'])
        self.assertTrue(grants[0]['duplicate'])
        points = await self.rows('SELECT points FROM nursery_research WHERE user_id=? AND family=?', (42, research_key('prism_sphere')))
        self.assertEqual(points[0]['points'], 1)
        saved = await nursery.prepare_offer(42, row['id'])
        self.assertEqual(saved['candidates'], offer['candidates'])
        self.assertTrue(saved['claimed'])
        self.assertIn(first['name'], click.followup.send.call_args.args[0])

    async def test_repeated_admin_request_never_grants_an_egg_twice(self):
        row = await self.incubate()
        click = self.click()
        await Dev.hatch_egg.callback(Dev(None), click, self.member, row['id'])
        await Dev.hatch_egg.callback(Dev(None), click, self.member, row['id'])
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM nursery_grants')), 1)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE user_id=42 AND stat_key='hatch'"))[0]['value'], 1)
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 1)

    async def test_oldest_retry_does_not_prepare_the_next_future_incubation(self):
        first = await self.incubate(future=True)
        await storage.add_item(42, 'prism_sphere')
        second = await nursery.incubate(42, 'prism_sphere', request_key='second-incubator',
            now=datetime.now(timezone.utc) + timedelta(days=30))
        click = self.click()
        await Dev.hatch_egg.callback(Dev(None), click, self.member)
        original_reply = click.followup.send.call_args.args[0]
        await Dev.hatch_egg.callback(Dev(None), click, self.member)
        self.assertEqual(click.followup.send.call_args.args[0], original_reply)
        offers = await self.rows('SELECT * FROM nursery_offers')
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]['incubation_id'], first['id'])
        self.assertEqual(len(await self.rows('SELECT * FROM nursery_grants')), 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)
        remaining = await self.rows('SELECT * FROM incubating_eggs WHERE id=?', (second['id'],))
        self.assertEqual(remaining[0]['hatched'], 0)
        self.assertEqual(await self.rows('SELECT * FROM nursery_offers WHERE incubation_id=?', (second['id'],)), [])

    async def test_force_future_egg_keeps_grade_level_and_real_growth(self):
        row = await self.incubate(future=True)
        click = self.click()
        await Dev.hatch_egg.callback(Dev(None), click, self.member)
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(len(beasts), 1)
        beast = beasts[0]
        self.assertTrue(14 <= beast['level'] <= 18)
        self.assertGreater(beast['max_hp'], storage.get_beast_data(beast['beast_id'])['base_stats']['hp'])
        self.assertEqual(beast['caught_from'], 'incubation:prism_sphere')
        self.assertIn('Lv.' + str(beast['level']), click.followup.send.call_args.args[0])
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs WHERE id=?', (row['id'],)))[0]['hatched'], 1)

    async def test_specific_id_cannot_claim_another_targets_incubation(self):
        await storage.get_or_create_player(99, 'Another trainer')
        await storage.update_player(99, level=50)
        foreign = await self.incubate(user_id=99)
        click = self.click()
        await Dev.hatch_egg.callback(Dev(None), click, self.member, foreign['id'])
        self.assertIn('no incubating eggs', click.followup.send.call_args.args[0])
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual(await storage.get_player_beasts(99), [])
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 0)
        self.assertEqual(await self.rows('SELECT * FROM nursery_offers'), [])

    async def test_atomic_service_rejection_is_shown_without_marking_hatched(self):
        row = await self.incubate()
        click = self.click()
        with patch('utils.nursery.claim_offer', new=AsyncMock(side_effect=ValueError('Your team is in battle.'))):
            await Dev.hatch_egg.callback(Dev(None), click, self.member, row['id'])
        self.assertIn('Your team is in battle.', click.followup.send.call_args.args[0])
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual((await self.rows('SELECT hatched FROM incubating_eggs'))[0]['hatched'], 0)

    async def test_confirmed_reset_clears_all_nursery_state_for_only_target(self):
        started = {}
        for user_id in (42, 99):
            await storage.get_or_create_player(user_id, f'Trainer {user_id}')
            await storage.update_player(user_id, level=50)
            await storage.add_item(user_id, 'common_egg')
            await nursery.hatch(user_id, 'common_egg', request_key=f'first:{user_id}')
            row = await self.incubate(user_id=user_id)
            started[user_id] = row['id']
            await nursery.prepare_offer(user_id, row['id'], rng=random.Random(user_id))
            await self.execute('INSERT INTO nursery_research VALUES(?,?,8)', (user_id, research_key('prism_sphere')))
        before = {}
        user_tables = ('nursery_requests', 'nursery_offers', 'nursery_research', 'nursery_seen', 'nursery_grants')
        for table in user_tables:
            before[table] = await self.rows(f'SELECT * FROM {table} WHERE user_id=99')
            self.assertTrue(before[table], table)
        metadata_before = await self.rows('SELECT * FROM nursery_incubations WHERE incubation_id=?', (started[99],))
        self.assertTrue(metadata_before)
        opened = self.click()
        await Dev.reset.callback(Dev(None), opened, self.member)
        view = opened.response.send_message.call_args.kwargs['view']
        self.addCleanup(view.stop)
        confirmed = self.click(4321)
        await view.children[0].callback(confirmed)
        for table in user_tables:
            self.assertEqual(await self.rows(f'SELECT * FROM {table} WHERE user_id=42'), [], table)
            self.assertEqual(await self.rows(f'SELECT * FROM {table} WHERE user_id=99'), before[table], table)
        self.assertEqual(await self.rows('SELECT * FROM nursery_incubations WHERE incubation_id=?', (started[42],)), [])
        self.assertEqual(await self.rows('SELECT * FROM nursery_incubations WHERE incubation_id=?', (started[99],)), metadata_before)
        self.assertIn('fully reset', confirmed.followup.send.call_args.args[0])
