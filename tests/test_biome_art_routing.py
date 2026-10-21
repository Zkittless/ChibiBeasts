"""Destination identity must survive every visual surface and saved legacy name."""
import asyncio
from dataclasses import replace
import hashlib
import importlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from scripts.prepare_battle_realms import prepare
from tests.test_gameplay import DatabaseCase, interaction
from utils import battle_art as art, db as storage, expeditions, journal_state, journal_team_art, raid_art
from utils.beast_workspace import BeastTeamView
from utils.habitats import BIOMES, biome_key
from utils.journal import JournalView


DISTINCT = {'woods_deep': 'Moonveil Marsh', 'ember_deep': 'Sunforge Dunes',
            'glacial_deep': 'Stormglass Peaks', 'trenches_deep': 'Cinderreef Caldera',
            'loom_deep': 'Starfall Gardens'}


class BiomeArtworkIdentity(unittest.TestCase):
    def test_exact_destinations_names_and_old_depth_labels_precede_family_aliases(self):
        self.assertEqual(set(art.REALMS), set(BIOMES))
        for key, region in BIOMES.items():
            for value in (key, key.upper(), region['name'], art.REALMS[key],
                          art.REALMS[key].removeprefix('The ')):
                with self.subTest(value=value):
                    self.assertEqual(art.realm_key(value), key)
            if key in DISTINCT:
                self.assertIn(DISTINCT[key], art.REALMS[key])
                family = art.REALMS[region['base']]
                for label in (family, family.removeprefix('The ')):
                    self.assertEqual(art.realm_key(label + ' · Depths'), key)
                    self.assertEqual(art.realm_key('Journey: ' + label + ' · Depths'), key)

    def test_family_resources_npc_homes_and_pvp_element_fallback_stay_stable(self):
        for key in DISTINCT:
            family = BIOMES[key]['base']
            self.assertEqual(biome_key(key), family)
            self.assertEqual(art.realm_key(art.REALMS[family].removeprefix('The ')), family)
        self.assertEqual(art.realm_key(states=[{'beast_type': 'water'}]), 'trenches')
        self.assertEqual(art.realm_key('unlisted arena', [{'beast_type': 'fire'}]), 'ember')
        self.assertEqual(art.realm_key('blooming meadow', [{'beast_type': 'fire'}]), 'ember')
        self.assertEqual(art.realm_key('woods_deep', [{'beast_type': 'cosmic'}]), 'woods_deep')
        self.assertEqual(art.realm_key(), 'woods')
        self.assertTrue(set(raid_art.REALMS.values()) <= {'woods', 'ember', 'glacial', 'trenches', 'loom'})
        self.assertGreaterEqual(art.background.cache_parameters()['maxsize'], len(BIOMES))

    def test_all_destinations_have_distinct_packaged_backgrounds_headers_and_hashes(self):
        manifest = json.loads((art.PROJECT_ROOT / 'data' / 'battle-artwork.json').read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['realms']), set(BIOMES))
        backgrounds, headers = set(), set()
        for key in BIOMES:
            record = manifest['realms'][key]
            data = (art.PROJECT_ROOT / 'data' / record['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])
            background = art.background(key)
            self.assertEqual(background.size, (640, 360))
            backgrounds.add(hashlib.sha256(background.tobytes()).hexdigest())
            header = art.PROJECT_ROOT / 'data' / 'journal' / f'journal-{key}.png'
            with Image.open(header) as image:
                self.assertEqual(image.size, (640, 126))
                image.verify()
            headers.add(hashlib.sha256(header.read_bytes()).hexdigest())
        self.assertEqual(len(backgrounds), len(BIOMES))
        self.assertEqual(len(headers), len(BIOMES))

    def test_animation_and_final_still_keep_exact_biome(self):
        from tests.test_battle_art import fighters
        before = art.snapshot(fighters())
        after = (before[0], replace(before[1], hp=0))
        for key in DISTINCT:
            with self.subTest(biome=key), patch.object(art, 'frame', wraps=art.frame) as frame:
                clip = art.render(before, after, art.realm_key(key), art.Action(0, 'Strike', damage=before[1].hp))
                still = art.render(after, after, art.realm_key(key))
                self.assertTrue(all(call.args[2] == key for call in frame.call_args_list))
                with Image.open(io.BytesIO(clip.data)) as image:
                    self.assertGreater(image.n_frames, 1)
                    self.assertNotIn('loop', image.info)
                with Image.open(io.BytesIO(still.data)) as image:
                    self.assertEqual(image.format, 'PNG')
                    self.assertEqual(image.size, (art.WIDTH, art.TOTAL_HEIGHT))


class BiomeArtworkFlows(DatabaseCase):
    async def ready(self):
        self.row_id = await self.beast('twine', starter=True)
        await storage.update_player(42, level=50)

    async def test_journal_selection_active_trip_and_team_share_full_visual_destination(self):
        await self.ready()
        state = await journal_state.snapshot(42)
        view = JournalView(42, state, page='adventure')
        self.addCleanup(view.stop)
        for key in BIOMES:
            view.realm = key
            self.assertEqual(view.art_realm, key)
            self.assertEqual(view.header.name, f'journal-{key}.png')
        view.realm, view.page = 'ember', 'team'
        view.trip = {'realm': 'woods_deep', 'status': 'active', 'payload': {'party_ids': [self.row_id]}}
        view.render = Mock()
        view.message = SimpleNamespace(attachments=[])
        stage = journal_team_art.TeamArtwork('selected-biome-stage.png', b'stage')
        with patch.object(journal_team_art, 'render_team', new=AsyncMock(return_value=stage)) as render:
            # The uploaded stage must use the active trip rather than the
            # unrelated destination selected in the menu.
            view.art_enabled = True
            await view.publish(interaction())
        self.assertEqual(render.await_args.args[1], 'woods_deep')
        view.trip['status'] = 'complete'
        self.assertEqual(view.art_realm, 'ember')

    async def test_beast_workspace_team_uses_saved_destination_then_active_trip(self):
        await self.ready()
        await journal_state.save_explore_default(42, 'loom_deep')
        nav = SimpleNamespace(user_id=42, lock=asyncio.Lock(), message=None, art_enabled=True,
                              decorate=Mock(), publish_embed=AsyncMock())
        row = (await storage.get_player_beasts(42))[0]
        view = BeastTeamView(nav, row)
        self.addCleanup(view.stop)
        await view.load()
        with patch.object(journal_team_art, 'render_team', new=AsyncMock(return_value=None)) as render:
            await view.publish(interaction())
            self.assertEqual(render.await_args.args[1], 'loom_deep')
            await expeditions.begin(42, 'glacial_deep')
            await view.load()
            await view.publish(interaction())
            self.assertEqual(render.await_args.args[1], 'glacial_deep')

    async def test_tower_keeps_every_distinct_destination_in_battle_presentation(self):
        from utils import trials
        from utils.trial_panel import TrialPanel
        await self.ready()
        await self.execute('UPDATE player_beasts SET hp=10000,max_hp=10000,defense=10000 WHERE user_id=42')
        for key in DISTINCT:
            await storage.update_player(42, challenge_last_at=0)
            await self.execute('DELETE FROM trial_cooldowns WHERE user_id=42')
            run = await trials.begin(42, key, seed=14)
            run = await trials.next_wave(42, run['id'], run['version'])
            panel = TrialPanel(42, destination='woods')
            self.addCleanup(panel.stop)
            panel.mode, panel.run = 'run', run
            with self.subTest(biome=key), patch.object(art.BattlePresentation, 'art', new=AsyncMock(return_value=None)) as renderer:
                await panel.prepare_media()
            self.assertEqual(panel.presentation.realm, key)
            self.assertEqual(panel.destination, key)
            self.assertEqual(renderer.await_args.args[0][1].species, run['payload']['enemy']['beast_id'])
            await trials.retreat(42, run['id'], run['version'])

    async def test_saved_expedition_passes_full_destination_to_battle_presentation(self):
        await self.ready()
        for key in DISTINCT:
            await self.execute('INSERT OR REPLACE INTO exploration_energy VALUES(42,3,10000)')
            trip = await expeditions.begin(42, key)
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks')
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'challenge')
            with self.subTest(biome=key), patch('cogs.battle.run_pve_battle', new=AsyncMock()) as runner:
                trip = await expeditions.run_battle(interaction(), trip)
            self.assertEqual(runner.await_args.kwargs['realm'], key)
            await expeditions.choose(42, trip['id'], trip['version'], 'return_early')


class BiomeArtworkTeamStages(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        journal_team_art.clear_cache()

    async def test_equipped_team_renders_in_every_distinct_destination(self):
        rows = [{'beast_id': 'twine', 'armor_id': 'gnome_iron_plating', 'hp': 10},
                {'beast_id': 'prismite', 'armor_id': '', 'hp': 10}]
        names, pixels = set(), set()
        for key in BIOMES:
            with self.subTest(biome=key):
                stage = await journal_team_art.render_team(rows, key)
                self.assertIsNotNone(stage)
                names.add(stage.name)
                with Image.open(io.BytesIO(stage.data)) as image:
                    self.assertEqual(image.size, (640, 300))
                    pixels.add(hashlib.sha256(image.tobytes()).hexdigest())
        self.assertEqual(len(names), len(BIOMES))
        self.assertEqual(len(pixels), len(BIOMES))


class BiomeArtworkPackaging(unittest.TestCase):
    def test_new_sources_merge_without_rewriting_existing_approved_assets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'data' / 'battle_realms'
            target.mkdir(parents=True)
            original = b'approved original bytes'
            (target / 'woods.png').write_bytes(original)
            old = {'file': 'battle_realms/woods.png', 'prompt': 'approved woodland', 'sha256': hashlib.sha256(original).hexdigest()}
            (root / 'data' / 'battle-artwork.json').write_text(json.dumps({'mode': 'built-in image_gen', 'realms': {'woods': old}}), encoding='utf-8')
            Image.new('RGB', (32, 18), '#376d67').save(root / 'marsh.png')
            source = root / 'source.json'
            source.write_text(json.dumps({'realms': {'woods_deep': {'workspace_file': 'marsh.png', 'prompt': 'marsh prompt'}}}), encoding='utf-8')
            self.assertEqual(set(prepare(source, root=root)), {'woods_deep'})
            manifest = json.loads((root / 'data' / 'battle-artwork.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['realms']['woods'], old)
            self.assertEqual((target / 'woods.png').read_bytes(), original)
            record = manifest['realms']['woods_deep']
            self.assertEqual(record['sha256'], hashlib.sha256((target / 'woods_deep.png').read_bytes()).hexdigest())
            self.assertEqual(record['source_sha256'], hashlib.sha256((root / 'marsh.png').read_bytes()).hexdigest())
            with Image.open(target / 'woods_deep.png') as image:
                self.assertEqual(image.size, (640, 360))

    def test_bad_manifest_or_missing_source_cannot_publish_partial_assets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (32, 18), '#376d67').save(root / 'marsh.png')
            source = root / 'source.json'
            for realms, error in (({'../outside': {'workspace_file': 'marsh.png', 'prompt': 'bad'}}, ValueError),
                                  ({'woods_deep': {'workspace_file': 'marsh.png', 'prompt': 'good'},
                                    'ember_deep': {'workspace_file': 'missing.png', 'prompt': 'missing'}}, FileNotFoundError)):
                source.write_text(json.dumps({'realms': realms}), encoding='utf-8')
                with self.assertRaises(error):
                    prepare(source, root=root)
                self.assertFalse((root / 'data').exists())
