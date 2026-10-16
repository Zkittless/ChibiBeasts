import random
import discord
from discord import app_commands
from discord.ext import commands
from utils.db import get_or_create_player, get_player_beasts, get_beast_data
from utils.theme import COLORS
from utils.story import RELATIONSHIP_LEVELS, relationship_display, relationship_order, load_questline, load_npcs, get_quest_state, save_quest_state, get_relationship_level, advance_quest_step, check_chapter_unlock, journal, claim_chapter, complete

def build_progress_bar(current, target, length=10):
    filled = min(length, int(current / max(1, target) * length))
    return '🟩' * filled + '⬛' * (length - filled) + f' {min(current, target)}/{target}'

def build_chapter_embed(chapter, state, player=None):
    npc = load_npcs()[chapter['npc']]
    embed = discord.Embed(title=f"📜 {chapter['name']}", description=f"{npc['emoji']} **{npc['name']}** · {chapter['location']}", color=COLORS['legendary'])
    p = state['step_progress'].get(chapter['id'], {})
    for step in chapter['steps']:
        done = p.get(step['id'], 0)
        target = step.get('target', 1)
        value = '✅ Complete' if done >= target else build_progress_bar(done, target)
        if step['type'] == 'relic_collect' and done < target:
            value += f"\nClues: {p.get(step['id'] + '_search', 0)}/3. Choose this destination with /explore."
        if step['type'] == 'material_collect_specific':
            value += '\n' + ' · '.join((f"{mid.replace('_', ' ').title()} {p.get(step['id'] + '_' + mid, 0)}/{qty}" for mid, qty in step['materials'].items()))
        embed.add_field(name=step['description'], value=value, inline=False)
    embed.set_footer(text='/questline to report back · /guide for your next step')
    return embed

class Questline(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def finish(self, interaction, chapter, choice=None):
        uid = interaction.user.id
        gid = interaction.guild.id if interaction.guild else 0
        try:
            state, reward = await claim_chapter(uid, chapter['id'], gid, choice)
        except ValueError as error:
            return await interaction.followup.send(str(error), ephemeral=True)
        lines = [chapter['choices'][choice]['scene']] if choice else []
        lines += chapter['completion_dialogue']
        embed = discord.Embed(title=f"✅ {chapter['name']} — Complete", description='\n\n'.join(lines)[:4000], color=COLORS['success'])
        rewards = [f"{reward.get('gold', 0):,} gold", f"{reward.get('exp', 0)} EXP", f"{reward.get('celestial_shards', 0)} shards"]
        rewards += [f"{qty}× {iid.replace('_', ' ').title()}" for group in ('items', 'materials') for iid, qty in reward.get(group, {}).items()]
        if reward.get('relic'):
            rewards.append(reward['relic'].replace('_', ' ').title())
        if choice:
            rewards.append('Title: ' + chapter['choices'][choice]['title'])
        embed.add_field(name='Rewards', value=' · '.join(rewards), inline=False)
        embed.set_footer(text='/questline to meet your next chapter' if state['current_chapter'] else 'The hollow is open. Revisit /npc or /explore.')
        await interaction.followup.send(embed=embed)

    @app_commands.command(name='questline', description='Your story journal: objectives, conversations and choices 📜')
    async def questline_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await get_or_create_player(interaction.user.id, str(interaction.user))
        try:
            state, chapter, introduce = await journal(interaction.user.id, interaction.guild.id if interaction.guild else 0)
        except ValueError as error:
            return await interaction.followup.send(str(error))
        if not chapter:
            return await interaction.followup.send(embed=discord.Embed(title='🌱 The Sundering of the Loom — Complete', description="The protective stitch has loosened. A new root grows beside an open path. Your companions and the people of the Loom remember what you did together.\n\nRevisit /npc, grow your starter's /bond, or help another trainer through a raid.", color=COLORS['divine']))
        if introduce:
            embed = discord.Embed(title=f"📜 {chapter['name']}", description='\n\n'.join(chapter['intro'])[:4000], color=COLORS['legendary'])
            embed.add_field(name='Your objectives', value='\n'.join(('• ' + s['description'] for s in chapter['steps'])), inline=False)
            embed.set_footer(text='/questline shows progress and lets you report back')
            return await interaction.followup.send(embed=embed)
        if complete(chapter, state):
            if not chapter.get('choices'):
                return await self.finish(interaction, chapter)
            view = discord.ui.View(timeout=180)
            uid = interaction.user.id
            for key, answer in chapter['choices'].items():
                button = discord.ui.Button(label=answer['label'], style=discord.ButtonStyle.success)

                async def choose(bi, selected=key):
                    if bi.user.id != uid:
                        return await bi.response.send_message("This is your fellow trainer's choice.", ephemeral=True)
                    await bi.response.defer()
                    for child in view.children:
                        child.disabled = True
                    await bi.edit_original_response(view=view)
                    view.stop()
                    await self.finish(bi, chapter, selected)
                button.callback = choose
                view.add_item(button)
            return await interaction.followup.send(embed=discord.Embed(title='🌿 What will you bring to the hollow?', description='Care, curiosity or courage. Choose how you and your companion will loosen the old stitch. Each path completes the story and earns its own title.', color=COLORS['divine']), view=view)
        await interaction.followup.send(embed=build_chapter_embed(chapter, state))

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
        embed.set_footer(text='Relationship: ' + relationship_display(rel) + ' · /questline')
        await interaction.followup.send(embed=embed)

    @app_commands.command(name='meet', description='Meet the people of the Loom and find their homes 🗺️')
    async def meet(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = await get_quest_state(interaction.user.id)
        embed = discord.Embed(title='🗺️ People of the Loom', description='Five people, five ways of paying attention. Follow /questline to hear their stories.', color=COLORS['info'])
        for id, npc in load_npcs().items():
            embed.add_field(name=f"{npc['emoji']} {npc['name']}", value=f"{npc['title']}\n{npc['location']} · {relationship_display(get_relationship_level(state, id))}", inline=False)
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Questline(bot))
