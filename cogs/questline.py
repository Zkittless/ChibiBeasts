import random
import discord
from discord import app_commands
from discord.ext import commands
from utils.db import get_player_beasts, get_beast_data
from utils.theme import COLORS
from utils.story import relationship_display, load_questline, load_npcs, get_quest_state, get_relationship_level

class Questline(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='npc', description='Talk to a person of the Loom 💬')
    @app_commands.choices(name=[app_commands.Choice(name='Maren', value='maren'), app_commands.Choice(name='Cael', value='cael'), app_commands.Choice(name='Sable', value='sable'), app_commands.Choice(name='Orren', value='orren'), app_commands.Choice(name='The Archivist', value='the_archivist')])
    async def npc(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        npc = load_npcs()[name]
        state = await get_quest_state(interaction.user.id)
        chapter = load_questline()['chapters'].get(state['current_chapter'], {})
        rel = get_relationship_level(state, name)
        finished = 'chapter_10' in state['completed_chapters']
        first = int(npc['quest_giver'].split('_')[1])
        seen = len(state['completed_chapters']) + 1 >= first
        if finished:
            line = {'maren': "'The clasp opens easily now. So does the next page. Tell me what you saw today.'", 'cael': "'The thread is quieter. Not silent. I think that's better.'", 'sable': "'Maren sent her coat back. A new pocket, this time. She asked. I said yes.'", 'orren': "'There is room beside the new root. Sit. We have time.'", 'the_archivist': "'That chapter has an ending. I find it more interesting every time I read it.'"}[name]
        elif chapter.get('npc') == name:
            line = "'Come back when you are ready to finish this part together. Your journal has the details.'"
        elif not seen:
            line = npc['relationship_levels']['stranger']
        else:
            line = npc['relationship_levels'].get(rel, npc['relationship_levels']['stranger'])
        embed = discord.Embed(title=f"{npc['emoji']} {npc['name']}", description=f"*{npc['title']}* · {npc['location']}\n\n{npc['appearance']}\n\n{line}", color=COLORS['info'])
        if seen and (not finished):
            safe = [k for k in npc if k.startswith('on_') and (not any((word in k for word in ('sundering', 'questline', 'relics', 'starters'))))]
            if name == 'maren':
                owned = await get_player_beasts(interaction.user.id)
                starter = next((get_beast_data(b['beast_id']) for b in owned if (get_beast_data(b['beast_id']) or {}).get('starter_family')), None)
                if starter:
                    embed.add_field(name='Your companion', value=f"'How is {starter['name']}? A shape can change without a friendship getting smaller.'", inline=False)
            elif safe:
                embed.add_field(name='A moment together', value=npc[random.choice(safe)][:1000], inline=False)
        companion = get_beast_data(npc['beast_companion'])
        if companion:
            embed.set_thumbnail(url=companion['image_url'])
        embed.set_footer(text='Relationship: ' + relationship_display(rel) + ' · /journal → Story')
        await interaction.followup.send(embed=embed)

    @app_commands.command(name='meet', description='Meet the people of the Loom and find their homes 🗺️')
    async def meet(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = await get_quest_state(interaction.user.id)
        embed = discord.Embed(title='🗺️ People of the Loom', description='Five people, five ways of paying attention. Open /journal → Story to hear their stories.', color=COLORS['info'])
        for id, npc in load_npcs().items():
            embed.add_field(name=f"{npc['emoji']} {npc['name']}", value=f"{npc['title']}\n{npc['location']} · {relationship_display(get_relationship_level(state, id))}", inline=False)
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Questline(bot))
