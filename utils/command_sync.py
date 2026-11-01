"""Publish the public and home-server command scopes after Discord login."""
import logging
import os

import discord

logger = logging.getLogger('chibibeasts.commands')


def developer_ids():
    values = []
    for name in ('OWNER_ID', 'GUILD_ID'):
        value = (os.getenv(name) or '0').strip() or '0'
        if not value.isdecimal():
            raise ValueError(f'{name} must be a numeric Discord ID.')
        values.append(int(value))
    return tuple(values)


async def sync_startup_commands(bot):
    """Global sync does not publish commands registered for a specific guild."""
    owner_id, guild_id = developer_ids()
    scopes = [('global', None)]
    if owner_id and guild_id:
        scopes.append(('developer', discord.Object(id=guild_id)))
    for scope, guild in scopes:
        try:
            synced = await bot.tree.sync(guild=guild)
        except discord.Forbidden:
            guidance = ('Check that GUILD_ID is the server ID and this bot is installed there '
                        'with application commands enabled.' if guild else
                        'Check the bot application and its application command access.')
            logger.exception('Command sync denied scope=%s guild=%s. %s',
                             scope, getattr(guild, 'id', None), guidance)
        except Exception:
            logger.exception('Command sync failed scope=%s guild=%s; owner-only !sync%s can retry.',
                             scope, getattr(guild, 'id', None), ' guild in that server' if guild else '')
        else:
            logger.info('Command sync complete scope=%s guild=%s top_level_commands=%s',
                        scope, getattr(guild, 'id', None), len(synced))


async def clear_guild_commands(tree, guild):
    """Clear remote registrations while retaining handlers for a later resync."""
    registered = tuple(tree.get_commands(guild=guild))
    tree.clear_commands(guild=guild)
    try:
        return await tree.sync(guild=guild)
    finally:
        for command in registered:
            tree.add_command(command, guild=guild, override=True)
