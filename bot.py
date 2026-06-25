import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from utils.db import init_db

os.chdir(os.path.dirname(os.path.abspath(__file__)))
print(f"📁 Working directory: {os.getcwd()}")
print(f"📂 Files visible: {os.listdir('.')[:8]}")

load_dotenv()

OWNER_ID = int(os.getenv("OWNER_ID", 0))

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"🐾 ChibiBeasts is online as {bot.user}")
    print(f"📡 Connected to {len(bot.guilds)} server(s)")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="ChibiBeasts 🐾 | /start"
        )
    )


@bot.command(name="sync")
async def sync_commands(ctx: commands.Context, scope: str = "global"):
    """
    !sync        — global sync (all servers)
    !sync guild  — sync everything to this server instantly including dev commands
    !sync clear  — wipe guild-scoped commands from this server
    Owner only.
    """
    if ctx.author.id != OWNER_ID:
        return await ctx.send("✦ Owner only.")

    if scope == "clear":
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        await ctx.send(
            f"✅ Guild commands cleared from **{ctx.guild.name}**.\n"
            f"Run `!sync guild` to restore."
        )
    elif scope == "guild":
        # Copy globals + sync guild-specific commands (includes dev group)
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"✅ Synced `{len(synced)}` command(s) to **{ctx.guild.name}** instantly.")
    else:
        # Global sync
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"✅ Global sync complete — `{len(synced)}` command(s) pushed.")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: `{e}`")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    import logging
    log = logging.getLogger("chibibeasts.commands")
    cause = getattr(error, "original", error)
    log.error(
        "Unhandled error in /%s: %s",
        interaction.command.name if interaction.command else "unknown",
        cause,
        exc_info=cause,
    )

    if isinstance(cause, discord.errors.Forbidden):
        message = "✦ I'm missing permissions to do that here."
    elif isinstance(cause, discord.errors.NotFound):
        message = "✦ Couldn't find what you were looking for — it may have been deleted."
    elif "cooldown" in str(cause).lower():
        message = f"✦ Slow down! {cause}"
    else:
        message = "✦ Something went wrong. The error has been logged — try again in a moment!"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


async def load_cogs():
    for filename in sorted(os.listdir("./cogs")):
        if filename.endswith(".py"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"✅ Loaded cog: {filename}")
            except Exception as e:
                print(f"❌ Failed to load {filename}: {e}")


async def main():
    async with bot:
        await init_db()
        await load_cogs()
        await bot.login(os.getenv("DISCORD_TOKEN"))

        home_id = os.getenv("GUILD_ID", "")
        home_guild = discord.Object(id=int(home_id)) if home_id else None

        # Remove dev commands from global tree so they never appear globally
        for cmd_name in ["dev", "give_ouroboros", "reset_shard_shop", "set_beast_level"]:
            cmd = bot.tree.get_command(cmd_name)
            if cmd:
                bot.tree.remove_command(cmd_name)
                print(f"🔒 Removed {cmd_name} from global tree")
                # Re-add to home guild only
                if home_guild:
                    bot.tree.add_command(cmd, guild=home_guild)

        # Global sync — dev commands are now excluded
        try:
            synced = await bot.tree.sync()
            print(f"✅ Global sync: {len(synced)} command(s)")
        except Exception as e:
            print(f"❌ Sync failed: {e}")

        # Sync dev commands to home guild
        if home_guild:
            try:
                guild_synced = await bot.tree.sync(guild=home_guild)
                print(f"✅ Home guild sync: {len(guild_synced)} command(s) (includes dev)")
            except Exception as e:
                print(f"❌ Home guild sync failed: {e}")

        await bot.connect()


asyncio.run(main())
