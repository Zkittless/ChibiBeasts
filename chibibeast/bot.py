import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from utils.db import init_db

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🐾 ChibiBeasts is online as {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="ChibiBeasts 🐾 | /start"
        )
    )
    try:
        guild = discord.Object(id=int(os.getenv("GUILD_ID", 0)))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global slash command error handler.
    Without this, any unhandled exception shows Discord's generic
    'The application did not respond' message — confusing for players.
    This catches all errors, logs them for debugging, and sends a friendly
    response so the player knows something went wrong rather than nothing.
    """
    import traceback
    import logging

    log = logging.getLogger("chibibeasts.commands")

    # Unwrap CommandInvokeError to get the real cause
    cause = getattr(error, "original", error)

    log.error(
        "Unhandled error in /%s: %s",
        interaction.command.name if interaction.command else "unknown",
        cause,
        exc_info=cause,
    )

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
        pass  # If we can't respond at all, there's nothing further to do

async def load_cogs():
    for filename in os.listdir("./cogs"):
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
        await bot.start(os.getenv("DISCORD_TOKEN"))

asyncio.run(main())
