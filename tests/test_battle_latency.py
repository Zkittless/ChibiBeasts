"""Attack latency improvements preserve visuals, live state and replay guards."""
from concurrent.futures import ThreadPoolExecutor
import json
import random
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageChops, ImageDraw

from scripts.preview_adventure_team import cases
from tests.test_gameplay import DatabaseCase, interaction
from utils import battle_art, db as storage, expeditions, field_journal as ui
from utils import journal_adventure, journal_state, journey_combat, journey_service, trial_combat
from utils.journal import JournalView


class TextRendering(unittest.TestCase):
    def test_cache_preserves_every_team_transition_and_resource_change(self):
        for name, before, after, action in cases():
            with self.subTest(clip=name):
                times = (0, .49, .6, 1)
                expected = [battle_art.frame(before, after, 'woods', action, t) for t in times]
                with ui.text_cache():
                    for t, original in zip(times, expected):
                        actual = battle_art.frame(before, after, 'woods', action, t)
                        self.assertIsNone(ImageChops.difference(original, actual).getbbox())

    def test_text_reuses_glyphs_but_releases_them_even_after_failure(self):
        image = Image.new('RGB', (200, 100))
        draw = ImageDraw.Draw(image)
        with patch.object(ui, '_glyph', wraps=ui._glyph) as glyph:
            with self.assertRaises(RuntimeError), ui.text_cache():
                first = ui.text(draw, 'Twine 100/120', (5, 5, 190, 30))
                with ui.text_cache():
                    repeated = ui.text(draw, 'Twine 100/120', (5, 5, 190, 30))
                self.assertEqual(first, repeated)
                self.assertEqual(glyph.call_count, 1)
                raise RuntimeError('Interrupted clip')
            with ui.text_cache():
                ui.text(draw, 'Twine 100/120', (5, 5, 190, 30))
            self.assertEqual(glyph.call_count, 2)

    def test_concurrent_render_workers_keep_their_own_fonts_and_masks(self):
        def render(size):
            image = Image.new('RGB', (300, 100))
            with ui.text_cache():
                for offset in (0, 35):
                    ui.text(ImageDraw.Draw(image), 'Gloop 100/120', (0, offset, 300, offset+35), size=size)
            return image.tobytes()
        expected = [render(size) for size in (12, 24)]
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(list(workers.map(render, (12, 24))), expected)


class BattleInputs(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.beast('twine', starter=True)
        trip = await expeditions.begin(42, 'woods', rng=random.Random(9), adventure_version=3)
        self.assertEqual(trip['status'], 'battle')
        journey_combat.start(trip['payload'], journey_combat.plan('woods', 4, 'single', 2))
        for fighter in [*trip['payload']['combat']['party'], trip['payload']['combat']['enemy']]:
            fighter['hp'] = fighter['max_hp'] = 100000
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(trip['payload']), trip['id']))
        self.panel = JournalView(42, await journal_state.snapshot(42), page='adventure')
        self.panel.art_enabled = False
        self.addCleanup(self.panel.stop)

    def action(self, verb='journey_act'):
        trip = self.panel.trip
        return f"{verb}:{trip['id']}:{trip['version']}" + (':0' if verb == 'journey_act' else '')

    async def test_attack_reads_current_trip_without_loading_camp_or_inventory(self):
        version = self.panel.trip['version']
        click = interaction()
        with patch.object(journal_state, 'snapshot', wraps=journal_state.snapshot) as snapshot, \
             patch.object(expeditions, 'current', wraps=expeditions.current) as current, \
             patch.object(journal_adventure, 'present', new=AsyncMock()) as present:
            await self.panel.handle(click, self.action())
        click.response.defer.assert_awaited_once()
        current.assert_awaited_once_with(42)
        snapshot.assert_not_awaited()
        self.assertEqual(self.panel.trip['version'], version + 1)
        self.assertIs(self.panel.state.trip, self.panel.trip)
        present.assert_awaited_once_with(self.panel, click, animate=True)

    async def test_stale_attack_refreshes_current_state_without_reapplying_damage(self):
        action = self.action()
        trip = self.panel.trip
        choice = trial_combat.legal_actions(trip['payload']['combat'])[0]['choice']
        newer = await journey_service.battle_action(42, trip['id'], trip['version'], choice)
        with patch.object(journal_state, 'snapshot', wraps=journal_state.snapshot) as snapshot, \
             patch.object(journey_service, 'battle_action', wraps=journey_service.battle_action) as mutate, \
             patch.object(journal_adventure, 'present', new=AsyncMock()) as present:
            await self.panel.handle(interaction(), action)
        mutate.assert_not_awaited()
        snapshot.assert_awaited_once()
        self.assertEqual(self.panel.trip, newer)
        self.assertFalse(present.call_args.kwargs['animate'])
        self.assertIn('refreshed', self.panel.notice)

    async def test_racing_attack_is_rechecked_inside_transaction(self):
        original = journey_service.battle_action
        saved = []

        async def race(*args):
            saved.append(await original(*args))
            return await original(*args)

        with patch.object(journey_service, 'battle_action', side_effect=race), \
             patch.object(journal_adventure, 'present', new=AsyncMock()):
            await self.panel.handle(interaction(), self.action())
        self.assertEqual(self.panel.trip, saved[0])
        self.assertIn('older page', self.panel.notice)
        self.assertFalse(self.panel.mutating)

    async def test_retreat_refreshes_settled_player_and_resources(self):
        with patch.object(journal_state, 'snapshot', wraps=journal_state.snapshot) as snapshot, \
             patch.object(journal_adventure, 'present', new=AsyncMock()):
            await self.panel.handle(interaction(), self.action('journey_return'))
        snapshot.assert_awaited_once()
        self.assertEqual(self.panel.trip['status'], 'complete')
        self.assertIsNone(self.panel.state.trip)
        player = (await self.rows('SELECT * FROM players WHERE user_id=42'))[0]
        self.assertEqual(self.panel.state.player['gold'], player['gold'])
        self.assertEqual(self.panel.state.player['exp'], player['exp'])

    async def test_encounter_victory_refreshes_collection_before_next_scene(self):
        trip = self.panel.trip
        combat = trip['payload']['combat']
        combat['enemy']['hp'] = 1
        for fighter in combat['party']:
            fighter.update(attack=100000, base_attack=100000, speed=100000, base_speed=100000)
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?', (json.dumps(trip['payload']), trip['id']))
        with patch.object(journal_state, 'snapshot', wraps=journal_state.snapshot) as snapshot, \
             patch.object(journal_adventure, 'present', new=AsyncMock()):
            await self.panel.handle(interaction(), self.action())
        self.assertEqual(self.panel.trip['status'], 'active')
        snapshot.assert_awaited_once()
        self.assertEqual(self.panel.state.trip, self.panel.trip)

    async def test_navigation_after_attack_refreshes_changed_camp_state(self):
        with patch.object(journal_adventure, 'present', new=AsyncMock()):
            await self.panel.handle(interaction(), self.action())
        await storage.update_player(42, gold=12345)
        with patch.object(self.panel, 'publish', new=AsyncMock()), \
             patch.object(journal_state, 'snapshot', wraps=journal_state.snapshot) as snapshot:
            await self.panel.handle(interaction(), 'page:overview')
        self.assertGreaterEqual(snapshot.await_count, 1)
        self.assertEqual(self.panel.state.player['gold'], 12345)


if __name__ == '__main__':
    unittest.main()
