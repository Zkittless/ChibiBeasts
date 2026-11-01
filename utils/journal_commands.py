"""The public command list and the journal's internal action handlers."""
import logging
import discord
from discord import app_commands


# These actions have complete journal controls. Keep their handlers callable
# for panel actions and compatibility tests; do not register duplicate slash
# commands. Global command sync removes their old Discord registrations.
PANEL_COMMANDS = frozenset({
    'collection', 'beastinfo', 'nickname', 'gear', 'equip', 'unequip', 'care',
    'train', 'evolve', 'nursery', 'hatch', 'incubate', 'eggs', 'tend', 'craft',
    'inventory', 'shop', 'daily', 'dailies', 'achievements', 'bestiary',
    'perks', 'perk_equip', 'perk_unequip', 'perk_use', 'history', 'party',
    'raidparty', 'challenge', 'leaderboard',
})

ACTION_COGS = {
    'daily': 'Utilities', 'inventory': 'Inventory', 'shop': 'Shop',
    'achievements': 'Progression', 'bestiary': 'Progression', 'leaderboard': 'Leaderboard',
}


class JournalCommandTree(app_commands.CommandTree):
    def add_command(self, command, /, **kwargs):
        if not isinstance(command, app_commands.Command) or command.name not in PANEL_COMMANDS:
            return super().add_command(command, **kwargs)


def action_command(bot, name):
    if not bot:
        return None
    cog = getattr(bot, 'get_cog', lambda _: None)(ACTION_COGS[name]) if name in ACTION_COGS else None
    return getattr(cog, name, None) if cog else bot.tree.get_command(name)


async def prune_guild_shortcuts(bot):
    """Remove old guild copies without overwriting guild-only developer tools."""
    logger = logging.getLogger('chibibeasts.commands')
    for guild in bot.guilds:
        try:
            registered = await bot.tree.fetch_commands(guild=guild)
            for command in registered:
                if command.type == discord.AppCommandType.chat_input and command.name in PANEL_COMMANDS:
                    await command.delete()
        except discord.HTTPException:
            logger.warning('Could not remove old journal shortcuts in guild %s; !sync guild can retry.', guild.id, exc_info=True)


def guide_categories():
    return {
        'Your Field Journal': '**/journal** opens one panel with three sections.\n\n'
            '**Adventure** · destination, exploration team, Tower Trials and battle history.\n'
            '**Beasts** · browse by rarity, search, gear, care, training, naming and evolution.\n'
            '**Camp** · Nursery, Workshop, supplies, story, rewards, perks and community.\n\n'
            'Use Back to return without opening another message. **/explore** opens adventure setup or your saved route.',
        'Adventure & combat': 'Adventure → Edit team sets your lead and two reserves.\n'
            'Travel options opens Tower Trials, Battle log, saved destination and seasonal rewards.\n'
            'Story choices use a dropdown. Battle moves remain buttons; Swap / Assist opens teammate actions.\n\n'
            '/sparr offers NPC practice. /battle and /ranked challenge other trainers. /ancient and /raid open group encounters.',
        'Beasts & care': 'Beasts starts with Common and shows all owned rarities. Use the page buttons to browse every companion.\n'
            'Open a beast, then choose Gear, Care & items, Train, Rename, Evolve / awaken or Team.\n'
            'The roster Care button shows only injured or knocked-out companions. A beast’s own Care & items page also offers treats.\n\n'
            '/play spends time with your lead. /use also supports trainer items such as elemental attunement tokens.',
        'Camp & progress': 'Camp → Nursery buys eggs, incubates, hatches, tends and lets you choose a hatchling.\n'
            'Camp → Workshop crafts equipment, runes and evolution items.\n'
            'Camp → Shop & inventory handles supplies.\n'
            'Camp → Story & goals contains chapters, daily objectives and pinned goals.\n'
            'Camp → Trainer contains your profile, perks, achievements and the bestiary. Ready daily rewards appear in Camp.',
        'Social & reference': '/profile can look up another trainer. /trade offers a beast to another trainer.\n'
            '/market · /list · /delist · /appraise · /sell · /release\n'
            '/guild_create · /guild · /guild_invite · /guild_promote · /guild_leave\n'
            '/contribute · /guild_shop · /sanctuary · /build\n'
            '/rank · /ranked_leaderboard · /title · /stats\n'
            '/codex · /typeinfo · /lore · /meet · /npc',
    }
