"""Regression checks for artwork delivery, startup, and starter claims."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import discord
from discord.ext import commands
from utils import db as storage
from utils.artwork import PROJECT_ROOT, artwork_manifest, beast_image_url

class Artwork(unittest.TestCase):
    def test_every_beast_has_one_versioned_alpha_png(self):
        roster = storage.load_beasts()
        manifest = artwork_manifest()
        self.assertEqual(len(roster), 86)
        self.assertEqual(set(roster), set(manifest))
        self.assertEqual(
            {p.name for p in (PROJECT_ROOT / "data/sprites").glob("*.png")},
            {f"{beast_id}.png" for beast_id in roster},
        )
        for beast_id, beast in roster.items():
            with self.subTest(beast=beast_id):
                entry = manifest[beast_id]
                self.assertEqual(entry["file"], f"{beast_id}.png")
                content = (PROJECT_ROOT / "data/sprites" / entry["file"]).read_bytes()
                self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
                width, height, depth, color = struct.unpack(">IIBB", content[16:26])
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                self.assertEqual(color, 6, "Sprites must retain their RGBA channel")
                self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"])
                self.assertIn(f"/{beast_id}.png?v={entry['sha256'][:16]}", beast["image_url"])

    def test_a_custom_image_host_keeps_content_versions(self):
        with patch.dict(os.environ, {"BEAST_IMAGE_BASE_URL": "https://art.example/beasts/"}):
            url = beast_image_url("prismite")
        self.assertTrue(url.startswith("https://art.example/beasts/prismite.png?v="))

    def test_invalid_and_unknown_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            beast_image_url("../private")
        with self.assertRaises(KeyError):
            beast_image_url("unknown_beast")

    def test_loaders_work_outside_the_project_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertEqual(storage.get_beast_data("prismite")["name"], "Prismite")
                self.assertIn("brambleberries", storage.load_items())
                self.assertTrue(storage.load_perks())
            finally:
                os.chdir(previous)

    def test_all_generation_prompts_are_saved(self):
        prompts = json.loads((PROJECT_ROOT / "docs/art-prompts.json").read_text(encoding="utf-8"))
        self.assertEqual({entry["id"] for entry in prompts["characters"]}, set(storage.load_beasts()))
        self.assertEqual(len(prompts["characters"]), 86)

    def test_raid_art_uses_the_same_resolver(self):
        from cogs.guilds import RAID_BOSSES
        from cogs.ancient import ANCIENT_BOSSES
        for boss in [boss for group in RAID_BOSSES.values() for boss in group] + ANCIENT_BOSSES:
            self.assertEqual(boss["image_url"], beast_image_url(boss["id"]))

class StarterClaims(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = storage.DB_PATH
        storage.DB_PATH = str(Path(self.temp.name) / "nested/player.db")
        await storage.init_db()
        await storage.get_or_create_player(42, "test-trainer")

    async def asyncTearDown(self):
        storage.DB_PATH = self.previous_db
        self.temp.cleanup()

    async def test_two_selection_messages_only_grant_one_active_starter(self):
        first = {**storage.get_beast_data("prismite"), "caught_from": "starter"}
        second = {**storage.get_beast_data("barkley"), "caught_from": "starter"}
        grants = await asyncio.gather(
            storage.add_beast_to_player(42, first, starter=True),
            storage.add_beast_to_player(42, second, starter=True),
        )
        self.assertEqual(sum(grant is not None for grant in grants), 1)
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(len(beasts), 1)
        self.assertEqual(beasts[0]["is_active"], 1)
        self.assertEqual(beasts[0]["caught_from"], "starter")
        self.assertEqual(beasts[0]["player_number"], 1)

    async def test_an_existing_collection_cannot_claim_another_starter(self):
        slime = storage.get_beast_data("slime")
        await storage.add_beast_to_player(42, slime)
        self.assertIsNone(await storage.add_beast_to_player(42, slime, starter=True))
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_concurrent_catches_receive_distinct_collection_numbers(self):
        slime = storage.get_beast_data("slime")
        await asyncio.gather(*(storage.add_beast_to_player(42, slime) for _ in range(6)))
        beasts = await storage.get_player_beasts(42)
        self.assertEqual(sorted(beast["player_number"] for beast in beasts), list(range(1, 7)))

    async def test_simultaneous_player_creation_is_idempotent(self):
        players = await asyncio.gather(
            storage.get_or_create_player(77, "new-trainer"),
            storage.get_or_create_player(77, "new-trainer"),
        )
        self.assertTrue(all(player["user_id"] == 77 for player in players))

    async def test_trading_away_the_starter_does_not_reset_its_claim(self):
        import aiosqlite
        starter = {**storage.get_beast_data("prismite"), "caught_from": "starter"}
        await storage.add_beast_to_player(42, starter, starter=True)
        await storage.get_or_create_player(77, "recipient")
        async with aiosqlite.connect(storage.DB_PATH) as connection:
            await connection.execute("UPDATE player_beasts SET user_id = 77 WHERE user_id = 42")
            await connection.commit()
        self.assertEqual(await storage.get_player_beasts(42), [])
        self.assertIsNone(await storage.add_beast_to_player(42, starter, starter=True))

    async def test_existing_accounts_keep_their_collection_during_migration(self):
        await storage.add_beast_to_player(42, storage.get_beast_data("slime"))
        await storage.init_db()
        self.assertEqual((await storage.get_player(42))["starter_claimed"], 1)
        self.assertEqual(len(await storage.get_player_beasts(42)), 1)

    async def test_legacy_achievement_preserves_a_claim_with_an_empty_collection(self):
        import aiosqlite
        async with aiosqlite.connect(storage.DB_PATH) as connection:
            await connection.execute(
                "INSERT INTO achievements (user_id, achievement_id) VALUES (42, 'first_steps')"
            )
            await connection.commit()
        await storage.init_db()
        self.assertEqual((await storage.get_player(42))["starter_claimed"], 1)
        self.assertIsNone(await storage.add_beast_to_player(
            42, storage.get_beast_data("prismite"), starter=True
        ))

class Extensions(unittest.IsolatedAsyncioTestCase):
    async def test_all_extensions_load_and_dev_commands_stay_in_the_home_guild(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
        with patch.dict(os.environ, {"OWNER_ID": "123", "GUILD_ID": "456"}):
            async with bot:
                for path in sorted((PROJECT_ROOT / "cogs").glob("*.py")):
                    await bot.load_extension(f"cogs.{path.stem}")
                self.assertEqual(len(bot.extensions), 17)
                self.assertIsNone(bot.tree.get_command("dev"))
                group = bot.tree.get_command("dev", guild=discord.Object(id=456))
                self.assertIsNotNone(group)
                for command in group.walk_commands():
                    self.assertTrue(command.checks, f"/dev {command.name} lacks its owner check")
                self.assertIsNotNone(bot.tree.get_command("guide"))
                import inspect
                import re
                from cogs.utilities import Utilities
                guide_commands=set(re.findall(r"/([a-z_]+)",inspect.getsource(Utilities.help_cmd.callback) + inspect.getsource(Utilities._command_guide)))
                registered={command.name for command in bot.tree.get_commands()}
                self.assertFalse(guide_commands-registered,f"Guide references missing commands: {guide_commands-registered}")
                from cogs.starter import FIRST_STEPS, Starter
                self.assertIn("/guide", [step[1] for step in FIRST_STEPS])
                embed = Starter(bot).build_starter_embed(storage.get_beast_data("prismite"))
                self.assertEqual(embed.image.url, beast_image_url("prismite"))

    async def test_dev_commands_are_disabled_without_configuration(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
        with patch.dict(os.environ, {"OWNER_ID": "0", "GUILD_ID": "0"}):
            async with bot:
                await bot.load_extension("cogs.dev")
                self.assertIsNone(bot.get_cog("Dev"))
                self.assertIsNone(bot.tree.get_command("dev"))

if __name__ == "__main__":
    unittest.main()
