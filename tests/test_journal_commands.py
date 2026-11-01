from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from utils.journal_commands import JournalCommandTree, PANEL_COMMANDS, action_command, guide_categories, prune_guild_shortcuts


class JournalCommandRegistry(IsolatedAsyncioTestCase):
    async def test_removed_commands_are_not_registered_but_panel_handlers_remain(self):
        async with commands.Bot(command_prefix='!', intents=discord.Intents.none(), tree_cls=JournalCommandTree) as bot:
            for name in ('journal', 'hatch', 'profile', 'utilities', 'social', 'world', 'economy', 'progression', 'care', 'battle'):
                await bot.load_extension('cogs.' + name)
            public = {command.name for command in bot.tree.get_commands()}
            self.assertFalse(public & PANEL_COMMANDS)
            self.assertTrue({'journal', 'explore', 'profile', 'battle', 'trade', 'use', 'play'} <= public)
            for name in ('daily', 'shop', 'inventory', 'achievements', 'bestiary', 'leaderboard'):
                command = action_command(bot, name)
                self.assertIsNotNone(command, name)
                self.assertIsNotNone(command.binding, name)
            bot.tree.copy_global_to(guild=discord.Object(id=123))
            self.assertFalse({command.name for command in bot.tree.get_commands(guild=discord.Object(id=123))} & PANEL_COMMANDS)

    async def test_guild_cleanup_deletes_only_retired_application_commands(self):
        old = SimpleNamespace(name='gear', type=discord.AppCommandType.chat_input, delete=AsyncMock())
        keep = SimpleNamespace(name='dev', type=discord.AppCommandType.chat_input, delete=AsyncMock())
        menu = SimpleNamespace(name='gear', type=discord.AppCommandType.user, delete=AsyncMock())
        bot = SimpleNamespace(guilds=[discord.Object(id=123)], tree=SimpleNamespace(fetch_commands=AsyncMock(return_value=[old, keep, menu])))
        await prune_guild_shortcuts(bot)
        old.delete.assert_awaited_once()
        keep.delete.assert_not_awaited()
        menu.delete.assert_not_awaited()

    async def test_help_does_not_advertise_removed_commands(self):
        import re
        listed = set(re.findall(r'/([a-z_]+)', '\n'.join(guide_categories().values())))
        self.assertFalse(listed & PANEL_COMMANDS)
