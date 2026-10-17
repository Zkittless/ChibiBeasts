"""Packaged artwork, real component routing and safe starter previews."""
import asyncio
import hashlib
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord.ui.view import ViewStore

from cogs.starter import Starter, StarterView
from cogs.world import World
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import db as storage
from utils.artwork import PROJECT_ROOT, card_artwork_manifest, card_artwork_path, card_artwork_url
from utils.beast_cards import STARTER_ORDER, codex_embed, starter_preview_embed


class PackagedCards(unittest.TestCase):
    def test_every_roster_entry_has_current_readable_card_art(self):
        roster = storage.load_beasts()
        manifest = card_artwork_manifest()
        self.assertEqual(set(manifest['beasts']), set(roster))
        for key, entry in manifest['beasts'].items():
            with self.subTest(beast=key):
                content = card_artwork_path(key).read_bytes()
                self.assertEqual(content[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', content[16:24]), (768, 448))
                self.assertLess(len(content), 1024 * 1024)
                self.assertEqual(hashlib.sha256(content).hexdigest(), entry['sha256'])
                self.assertEqual(hashlib.sha256((PROJECT_ROOT / 'data/sprites' / f'{key}.png').read_bytes()).hexdigest(), entry['source_sha256'])
        content = card_artwork_path().read_bytes()
        self.assertEqual(struct.unpack('>II', content[16:24]), (768, 640))
        self.assertEqual(hashlib.sha256(content).hexdigest(), manifest['starter_lineup']['sha256'])

    def test_all_codex_tabs_retain_art_and_stay_inside_discord_embed_limits(self):
        for key, beast in storage.load_beasts().items():
            for tab in ('overview', 'battle', 'lore'):
                with self.subTest(beast=key, tab=tab):
                    embed = codex_embed(beast, tab)
                    self.assertEqual(embed.title, beast['name'])
                    self.assertLessEqual(len(embed), 6000)
                    self.assertLessEqual(len(embed.fields), 25)
                    self.assertTrue(all(len(f.value) <= 1024 and len(f.name) <= 256 for f in embed.fields))
                    self.assertEqual(embed.image.url or embed.thumbnail.url, card_artwork_url(key))
                    if tab == 'battle':
                        fields = '\n'.join(field.name for field in embed.fields)
                        for move in beast['moves'] + [beast['ultimate']]:
                            self.assertIn(move, fields)


class ArtworkInteractions(DatabaseCase):
    def starters(self):
        return {key: storage.get_beast_data(key) for key in STARTER_ORDER}

    async def open_starters(self):
        command = interaction()
        message = SimpleNamespace(id=800, edit=AsyncMock())
        command.followup.send.return_value = message
        command.channel.get_partial_message = lambda _: message
        await Starter.start.callback(Starter(None), command)
        payload = command.followup.send.call_args.kwargs
        self.addCleanup(payload['view'].stop)
        return payload['view'], payload, message

    async def test_start_shows_all_four_before_claim_and_browsing_never_grants_one(self):
        view, payload, _ = await self.open_starters()
        self.assertEqual(payload['embed'].image.url, 'attachment://starter-lineup.png')
        self.assertEqual(payload['file'].filename, 'starter-lineup.png')
        self.assertTrue(view.choose.disabled)
        ids = [child.custom_id for child in view.children]
        for key in STARTER_ORDER:
            click = component(section=key)
            await view.browse.callback(click)
            self.assertEqual(click.edit_original_response.call_args.kwargs['embed'].image.url, card_artwork_url(key))
            self.assertEqual(len(click.edit_original_response.call_args.kwargs['attachments']), 1)
            self.assertEqual(view.choose.label, f'Choose {self.starters()[key]["name"]}')
            self.assertFalse(view.choose.disabled)
            details = component()
            await view.details.callback(details)
            self.assertNotIn('attachments', details.edit_original_response.call_args.kwargs)
            self.assertEqual(details.edit_original_response.call_args.kwargs['embed'].thumbnail.url, card_artwork_url(key))
            self.assertEqual([child.custom_id for child in view.children], ids)
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertEqual((await storage.get_player(42))['starter_claimed'], 0)

    async def test_confirm_grants_the_previewed_starter_once_and_closes_controls(self):
        view, _, _ = await self.open_starters()
        await view.browse.callback(component(section='twine'))
        click = component()
        await view.choose.callback(click)
        rows = await storage.get_player_beasts(42)
        self.assertEqual([(row['beast_id'], row['is_active']) for row in rows], [('twine', 1)])
        self.assertTrue(view.is_finished())
        self.assertTrue(all(child.disabled for child in view.children))
        self.assertEqual(click.edit_original_response.call_args.kwargs['embed'].image.url, card_artwork_url('twine'))
        await view.choose.callback(component())
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_real_select_dispatcher_keeps_starter_portraits_and_details_live(self):
        view, _, message = await self.open_starters()
        store = ViewStore(None)
        store.add_view(view, message.id)
        ids = [child.custom_id for child in view.children]
        for key in ('twine', 'gloop', 'overview', 'barkley'):
            done = asyncio.Event()

            async def edited(**kwargs):
                store.add_view(kwargs['view'], message.id)
                done.set()

            click = component(edit=edited)
            click.message = message
            click.data = {'component_type': 3, 'custom_id': ids[0], 'values': [key]}
            store.dispatch_view(3, ids[0], click)
            await asyncio.wait_for(done.wait(), 1)
            click.response.defer.assert_awaited_once()
            self.assertEqual(view.selected_id, None if key == 'overview' else key)
            self.assertEqual([child.custom_id for child in view.children], ids)
        self.assertEqual(await storage.get_player_beasts(42), [])

    async def test_stale_picker_does_not_claim_or_announce_a_second_starter(self):
        first, _, _ = await self.open_starters()
        second, _, _ = await self.open_starters()
        await first.browse.callback(component(section='twine'))
        await second.browse.callback(component(section='prismite'))
        await first.choose.callback(component())
        stale = component()
        await second.choose.callback(stale)
        self.assertNotIn('embed', stale.edit_original_response.call_args.kwargs)
        self.assertIn('already chosen', stale.followup.send.call_args.args[0])
        self.assertEqual([b['beast_id'] for b in await storage.get_player_beasts(42)], ['twine'])

    async def test_confirm_during_a_new_portrait_upload_cannot_claim_the_wrong_starter(self):
        view, _, _ = await self.open_starters()
        await view.browse.callback(component(section='twine'))
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(**kwargs):
            entered.set()
            await release.wait()

        browsing = component(section='gloop', edit=delayed)
        task = asyncio.create_task(view.browse.callback(browsing))
        confirm_task = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            confirm = component()
            confirm_task = asyncio.create_task(view.choose.callback(confirm))
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(task, confirm_task), 2)
            self.assertEqual(await storage.get_player_beasts(42), [])
            self.assertIn('selection changed', confirm.followup.send.call_args.args[0])
            await view.choose.callback(component())
            self.assertEqual([b['beast_id'] for b in await storage.get_player_beasts(42)], ['gloop'])
        finally:
            release.set()
            await asyncio.gather(task, *([confirm_task] if confirm_task else []), return_exceptions=True)

    async def test_codex_attaches_art_and_real_router_keeps_tabs_live(self):
        command = interaction()
        message = SimpleNamespace(id=801, edit=AsyncMock())
        command.followup.send.return_value = message
        command.channel.get_partial_message = lambda _: message
        await World.codex.callback(World(None), command, 'Twine')
        payload = command.followup.send.call_args.kwargs
        view = payload['view']
        self.addCleanup(view.stop)
        self.assertEqual(payload['file'].filename, 'twine.png')
        self.assertEqual(payload['embed'].image.url, card_artwork_url('twine'))
        store = ViewStore(None)
        store.add_view(view, message.id)
        ids = [child.custom_id for child in view.children]
        for index in (1, 2, 0, 2, 1):
            done = asyncio.Event()

            async def edited(**kwargs):
                self.assertNotIn('attachments', kwargs)
                store.add_view(kwargs['view'], message.id)
                done.set()

            click = component(edit=edited)
            click.message = message
            click.data = {'component_type': 2, 'custom_id': ids[index]}
            store.dispatch_view(2, ids[index], click)
            await asyncio.wait_for(done.wait(), 1)
            click.response.defer.assert_awaited_once()
            self.assertEqual([child.custom_id for child in view.children], ids)

    async def test_other_trainers_cannot_change_or_claim_the_selection(self):
        view, _, _ = await self.open_starters()
        click = component(section='twine', uid=77)
        await view.browse.callback(click)
        click.response.send_message.assert_awaited_once()
        self.assertIsNone(view.selected_id)
        self.assertEqual(await storage.get_player_beasts(42), [])
        await view._confirm(component(uid=77), 'twine')
        self.assertEqual(await storage.get_player_beasts(42), [])
