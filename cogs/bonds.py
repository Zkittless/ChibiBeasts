import discord
from discord import app_commands
from discord.ext import commands
from utils.db import get_active_beast, get_beast_by_player_number, get_beast_data
from utils.theme import COLORS
from utils.gameplay import BOND_LEVEL, BOND_REQUIRED

class Bonds(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='bond', description="Your companion's bond and next awakening milestone 🌿")
    async def bond(self, interaction: discord.Interaction, beast_number: int=None):
        await interaction.response.defer()
        row = await get_beast_by_player_number(interaction.user.id, beast_number) if beast_number else await get_active_beast(interaction.user.id)
        if not row:
            return await interaction.followup.send('Choose your first companion with /start, or set an active beast with /setactive.')
        data = get_beast_data(row['beast_id'])
        evo = data.get('evolution')
        embed = discord.Embed(title=f"🌿 {row.get('nickname') or data['name']} · Your bond", description=f"Bond **{row['bond']}/100** · Happiness **{row['happiness']}/100** · Level **{row['level']}**\n\n/play gives 3 bond, a battle win gives 2, and training gives 1. Bond stays with the individual companion.", color=COLORS['divine'] if data.get('starter_family') else COLORS['info'])
        if evo and evo.get('form') == 'bond':
            target = get_beast_data(evo['evolves_to'])
            embed.add_field(name=f"Awakening: {target['name']}", value=f"Beast level {BOND_LEVEL} · {BOND_REQUIRED} bond · 70 happiness\n{target['description']}\nWhen ready: `/evolve {row['player_number']}`. No item is needed.", inline=False)
        elif data.get('starter_family'):
            embed.add_field(name='An awakened friendship', value='Your bond keeps growing. Every bond point adds 0.1% to combat attack, defense and speed, up to 10% at 100.', inline=False)
        else:
            embed.add_field(name='A shared journey', value='Any companion can build a bond. The four Architect starters also have unique bond awakenings.', inline=False)
        embed.set_image(url=data['image_url'])
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Bonds(bot))
