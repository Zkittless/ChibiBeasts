"""Profile selection passes through Discord's real argument conversion."""
from types import SimpleNamespace

import discord
from discord import app_commands

from cogs.profile import Profile
from tests.test_gameplay import DatabaseCase, interaction
from utils import db as storage


def user_data(uid, name):
    return dict(id=str(uid), username=name, global_name=name, discriminator='0', avatar=None)


def request(*, target=None, member=False, guild=True):
    click = interaction()
    state = SimpleNamespace()
    state.create_user = state.store_user = lambda data, **kwargs: discord.User(state=state, data=data)
    click._state = state
    click.guild = SimpleNamespace(id=1001) if guild else None
    click.guild_id = click.guild.id if click.guild else None
    click.user = state.create_user(user_data(42, 'Viewer'))
    resolved, options = {}, []
    if target:
        resolved['users'] = {str(target): user_data(target, 'OtherTrainer')}
        if member:
            resolved['members'] = {str(target): dict(roles=[], joined_at=None, nick='Server nickname', flags=0)}
        options = [dict(name='member', type=discord.AppCommandOptionType.user.value, value=str(target))]
    return click, app_commands.Namespace(click, resolved, options)


class ProfileLookup(DatabaseCase):
    async def test_non_member_profile_works_from_another_server(self):
        await storage.get_or_create_player(84, 'OtherTrainer')
        await storage.update_player(84, level=12, gold=4321)
        await self.beast('twine', uid=84, starter=True)
        click, namespace = request(target=84)
        self.assertIsInstance(namespace.member, discord.User)
        self.assertNotIsInstance(namespace.member, discord.Member)
        await Profile(None).profile._invoke_with_namespace(click, namespace)
        click.response.defer.assert_awaited_once()
        embed = click.followup.send.call_args.kwargs['embed']
        self.assertIn('OtherTrainer', embed.title)
        text = '\n'.join(field.value for field in embed.fields)
        self.assertIn('4,321', text)
        self.assertIn('**1** beasts total', text)
        self.assertEqual((await storage.get_player(42))['level'], 1)

    async def test_current_server_member_keeps_nickname_and_profile(self):
        await storage.get_or_create_player(84, 'OtherTrainer')
        click, namespace = request(target=84, member=True)
        self.assertIsInstance(namespace.member, discord.Member)
        await Profile(None).profile._invoke_with_namespace(click, namespace)
        click.response.defer.assert_awaited_once()
        self.assertIn('Server nickname', click.followup.send.call_args.kwargs['embed'].title)

    async def test_omitted_target_keeps_own_profile_in_a_dm(self):
        click, namespace = request(guild=False)
        await Profile(None).profile._invoke_with_namespace(click, namespace)
        click.response.defer.assert_awaited_once()
        embeds = click.followup.send.call_args.kwargs['embeds']
        self.assertIn('Viewer', embeds[0].title)
        self.assertTrue(any(embed.title == 'Where to Start' for embed in embeds))
