import discord
from discord import app_commands
from discord.ext import commands

from utils.care_view import open_care


class CompanionCare(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def beast_autocomplete(self, interaction: discord.Interaction, current: str):
        from utils.owned_roster import autocomplete
        return await autocomplete(interaction, current)

    @app_commands.command(name='care', description='Preview and use owned healing items or treats on a companion')
    @app_commands.describe(beast_number='Choose a beast by name or number; defaults to your active beast')
    @app_commands.autocomplete(beast_number=beast_autocomplete)
    async def care(self, interaction: discord.Interaction, beast_number: int = None):
        return await open_care(interaction, beast_number=beast_number)


async def setup(bot):
    await bot.add_cog(CompanionCare(bot))
