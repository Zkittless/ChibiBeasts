"""Exercise startup publication with the real scoped command tree, offline."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import discord


class DeveloperCommandSync(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        environment = patch.dict(os.environ, {'OWNER_ID': '42', 'GUILD_ID': '7', 'DISCORD_TOKEN': 'offline-test'})
        environment.start()
        self.addCleanup(environment.stop)
        logging_setup = patch('logging.basicConfig')
        logging_setup.start()
        self.addCleanup(logging_setup.stop)
        original_directory = Path.cwd()
        self.addCleanup(os.chdir, original_directory)
        spec = importlib.util.spec_from_file_location('chibi_startup_test', Path(__file__).resolve().parents[1] / 'bot.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch('dotenv.load_dotenv'), patch('builtins.print'):
            spec.loader.exec_module(self.module)
        self.bot = self.module.bot
        self.addAsyncCleanup(self.bot.close)
        self.published = []

        async def sync(*, guild=None):
            commands = self.bot.tree.get_commands(guild=guild)
            self.published.append((getattr(guild, 'id', None), {command.name for command in commands}))
            return commands

        self.bot.tree.sync = AsyncMock(side_effect=sync)
        self.bot.login = AsyncMock()
        self.bot.connect = AsyncMock()
        self.module.init_db = AsyncMock()
        self.module.load_cogs = self.load_commands

    async def load_commands(self):
        await self.bot.load_extension('cogs.dev')

        @self.bot.tree.command(name='public_test', description='Offline public command')
        async def public_test(interaction: discord.Interaction):
            pass

    async def test_startup_publishes_globals_and_configured_developer_guild(self):
        await self.module.main()
        self.assertEqual(self.published, [(None, {'public_test'}),
            (7, {'dev', 'give_ouroboros', 'reset_shard_shop', 'set_beast_level'})])
        self.bot.login.assert_awaited_once_with('offline-test')
        self.bot.connect.assert_awaited_once()

    async def test_clear_then_guild_sync_keeps_loaded_developer_commands(self):
        async with self.bot:
            await self.load_commands()
            context = SimpleNamespace(author=SimpleNamespace(id=42), guild=SimpleNamespace(id=7, name='Dev server'), send=AsyncMock())
            await self.module.sync_commands.callback(context, 'clear')
            await self.module.sync_commands.callback(context, 'guild')
        self.assertEqual(self.published[0], (7, set()))
        self.assertEqual(self.published[1], (7, {'public_test', 'dev', 'give_ouroboros', 'reset_shard_shop', 'set_beast_level'}))

    async def test_global_sync_failure_does_not_skip_the_developer_server(self):
        original = self.bot.tree.sync.side_effect

        async def sync(*, guild=None):
            if guild is None:
                raise RuntimeError('Global publication failed')
            return await original(guild=guild)

        self.bot.tree.sync.side_effect = sync
        with self.assertLogs('chibibeasts.commands', level='ERROR') as logs:
            await self.module.main()
        self.assertEqual(self.published[0][0], 7)
        self.assertIn('dev', self.published[0][1])
        self.assertIn('scope=global', '\n'.join(logs.output))
        self.bot.connect.assert_awaited_once()

    async def test_missing_server_access_reports_the_configured_guild(self):
        original = self.bot.tree.sync.side_effect

        async def sync(*, guild=None):
            if guild:
                raise discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                        {'code': 50001, 'message': 'Missing Access'})
            return await original(guild=guild)

        self.bot.tree.sync.side_effect = sync
        with self.assertLogs('chibibeasts.commands', level='ERROR') as logs:
            await self.module.main()
        self.assertEqual(self.published, [(None, {'public_test'})])
        self.assertIn('scope=developer guild=7', '\n'.join(logs.output))
        self.assertIn('GUILD_ID is the server ID', '\n'.join(logs.output))
        self.bot.connect.assert_awaited_once()

    async def disabled(self, name):
        with patch.dict(os.environ, {name: '0'}), self.assertLogs('chibibeasts.commands', level='WARNING') as logs:
            await self.module.main()
        self.assertEqual(self.published, [(None, {'public_test'})])
        self.assertIn('Developer commands disabled', '\n'.join(logs.output))

    async def test_missing_owner_disables_developer_registration_and_publication(self):
        await self.disabled('OWNER_ID')

    async def test_missing_guild_disables_developer_registration_and_publication(self):
        await self.disabled('GUILD_ID')

    async def test_whitespace_in_hosting_variables_uses_the_same_ids(self):
        with patch.dict(os.environ, {'OWNER_ID': ' 42 ', 'GUILD_ID': ' 7\n'}):
            await self.module.main()
        self.assertIn((7, {'dev', 'give_ouroboros', 'reset_shard_shop', 'set_beast_level'}), self.published)

    async def test_sync_in_another_server_does_not_copy_developer_commands(self):
        async with self.bot:
            await self.load_commands()
            context = SimpleNamespace(author=SimpleNamespace(id=42), guild=SimpleNamespace(id=8, name='Public server'), send=AsyncMock())
            await self.module.sync_commands.callback(context, 'guild')
            self.assertIsNotNone(self.bot.tree.get_command('dev', guild=discord.Object(id=7)))
        self.assertEqual(self.published, [(8, {'public_test'})])

    async def test_non_owner_and_dm_guild_sync_do_not_publish(self):
        for owner, guild in ((43, SimpleNamespace(id=7, name='Dev server')), (42, None)):
            context = SimpleNamespace(author=SimpleNamespace(id=owner), guild=guild, send=AsyncMock())
            await self.module.sync_commands.callback(context, 'guild')
            context.send.assert_awaited_once()
        self.bot.tree.sync.assert_not_awaited()

    async def test_failed_clear_restores_local_handlers_for_retry(self):
        async with self.bot:
            await self.load_commands()
            context = SimpleNamespace(author=SimpleNamespace(id=42), guild=SimpleNamespace(id=7, name='Dev server'), send=AsyncMock())
            self.bot.tree.sync.side_effect = RuntimeError('Offline publication failed')
            with self.assertRaises(RuntimeError):
                await self.module.sync_commands.callback(context, 'clear')
            self.assertEqual({c.name for c in self.bot.tree.get_commands(guild=context.guild)},
                             {'dev', 'give_ouroboros', 'reset_shard_shop', 'set_beast_level'})

    async def test_every_published_developer_action_still_checks_owner(self):
        async with self.bot:
            await self.load_commands()
            roots = self.bot.tree.get_commands(guild=discord.Object(id=7))
            leaves = [leaf for root in roots for leaf in
                      (root.walk_commands() if isinstance(root, discord.app_commands.Group) else [root])]
            self.assertEqual(len(leaves), 21)
            for command in leaves:
                self.assertTrue(command.checks, command.name)
                for check in command.checks:
                    self.assertTrue(await check(SimpleNamespace(user=SimpleNamespace(id=42))))
                    other = SimpleNamespace(user=SimpleNamespace(id=43), response=SimpleNamespace(send_message=AsyncMock()))
                    with self.assertRaises(discord.app_commands.CheckFailure):
                        await check(other)


if __name__ == '__main__':
    import unittest
    unittest.main()
