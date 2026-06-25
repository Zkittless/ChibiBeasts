import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from utils.db import init_db

# Ensure working directory is always the folder containing bot.py
os.chdir(os.path.dirname(os.path.abspath(__file__)))
print(f"📁 Working directory: {os.getcwd()}")
print(f"📂 Files visible: {os.listdir('.')[:8]}")

load_dotenv()

OWNER_ID   = int(os.getenv("OWNER_ID", 0))   # Your Discord user ID — add to Railway env vars
HOME_GUILD = int(os.getenv("GUILD_ID", 0))    # Your main server — keeps instant sync

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


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Instantly sync slash commands to any new server the bot joins."""
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Auto-synced {len(synced)} command(s) to new guild: {guild.name} ({guild.id})")
    except Exception as e:
        print(f"❌ Failed to auto-sync to {guild.name}: {e}")


@bot.command(name="sync")
async def sync_commands(ctx: commands.Context, scope: str = "global"):
    """
    !sync          — global sync (all servers, up to 1hr propagation) + instant home guild sync
    !sync guild    — instant sync to current server only
    !sync all      — sync to every server the bot is in (instant for each)
    Owner only.
    """
    if ctx.author.id != OWNER_ID:
        return await ctx.send("✦ Owner only.")

    await ctx.send("⏳ Syncing...")

    try:
        if scope == "guild":
            # Instant sync to current server only
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ Synced `{len(synced)}` command(s) to **{ctx.guild.name}** instantly.")

        elif scope == "all":
            # Instant sync to every server
            total = 0
            for guild in bot.guilds:
                try:
                    bot.tree.copy_global_to(guild=guild)
                    synced = await bot.tree.sync(guild=guild)
                    total += len(synced)
                except Exception as e:
                    print(f"❌ Failed to sync to {guild.name}: {e}")
            await ctx.send(f"✅ Synced to **{len(bot.guilds)}** server(s) — `{total // max(len(bot.guilds),1)}` command(s) each.")

        else:
            # Global sync + instant home guild sync
            global_synced = await bot.tree.sync()
            print(f"✅ Global sync: {len(global_synced)} command(s)")

            # Also instantly sync home guild so your server doesn't wait
            if HOME_GUILD:
                home = discord.Object(id=HOME_GUILD)
                bot.tree.copy_global_to(guild=home)
                guild_synced = await bot.tree.sync(guild=home)
                await ctx.send(
                    f"✅ Global sync queued — `{len(global_synced)}` command(s) "
                    f"(up to 1hr for new servers).\n"
                    f"✅ **{ctx.guild.name}** synced instantly — `{len(guild_synced)}` command(s)."
                )
            else:
                await ctx.send(f"✅ Global sync queued — `{len(global_synced)}` command(s) (up to 1hr for new servers).")

    except Exception as e:
        await ctx.send(f"❌ Sync failed: `{e}`")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global slash command error handler.
    Catches all unhandled errors and sends a friendly response instead of
    Discord's generic 'The application did not respond' message.
    """
    import logging
    log = logging.getLogger("chibibeasts.commands")
    cause = getattr(error, "original", error)
    log.error(
        "Unhandled error in /%s: %s",
        interaction.command.name if interaction.command else "unknown",
        cause,
        exc_info=cause,
    )

    # Friendlier, more specific error messages
    if isinstance(cause, discord.errors.Forbidden):
        message = "✦ I'm missing permissions to do that here. Make sure I have the right roles."
    elif isinstance(cause, discord.errors.NotFound):
        message = "✦ Couldn't find what you were looking for — it may have been deleted."
    elif "cooldown" in str(cause).lower():
        message = f"✦ Slow down! {cause}"
    else:
        message = (
            "✦ Something went wrong with that command. "
            "The error has been logged — try again in a moment!"
        )

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

        # ── Sync strategy ──────────────────────────────────────────────────
        # Global sync only on startup — copying to guild AND global causes duplicates.
        # Use !sync guild in your server for instant updates after deploys.
        try:
            global_synced = await bot.tree.sync()
            print(f"✅ Global sync: {len(global_synced)} command(s)")
        except Exception as e:
            print(f"❌ Global sync failed: {e}")

        await bot.connect()


asyncio.run(main())
