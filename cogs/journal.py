import discord
from discord import app_commands
from discord.ext import commands

from utils.journal import open_journal


class Journal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='journal', description='Your Field Journal: adventures, companions, crafting and story')
    async def journal(self, interaction: discord.Interaction):
        await open_journal(interaction, bot=self.bot)


async def setup(bot):
    await bot.add_cog(Journal(bot))
