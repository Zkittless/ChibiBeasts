import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from utils.artwork import beast_image_url
from utils import db as storage
from utils.raids import active_raids as active_ancient_raids
ANCIENT_BOSSES = [{'id': 'ancient_chronos', 'name': 'Ancient Chronos', 'type': 'ancient', 'max_hp': 150000, 'attack': 2000, 'image_url': beast_image_url('ancient_chronos'), 'description': "The primordial form of Chronos, existing before time itself had a name. It doesn't govern time. It is time.", 'loot_table': ['tear_of_leviathan', 'genesis_fruit', 'ambrosia_tart', 'sunforge_core']}, {'id': 'ancient_genesis', 'name': 'Ancient Genesis', 'type': 'ancient', 'max_hp': 160000, 'attack': 2200, 'image_url': beast_image_url('ancient_genesis'), 'description': "The original Origin Phoenix, carrying the flame of the universe's first moment. Everything alive exists because this beast once burned.", 'loot_table': ['genesis_fruit', 'singularity_soda', 'tear_of_leviathan']}, {'id': 'ancient_abyss', 'name': 'Ancient Abyss', 'type': 'ancient', 'max_hp': 140000, 'attack': 2100, 'image_url': beast_image_url('ancient_abyss'), 'description': 'The Dark Matter Panther in its oldest form — the void that existed before the universe had anything to put in it.', 'loot_table': ['tear_of_leviathan', 'ambrosia_tart', 'genesis_fruit']}]
SUMMON_ITEMS = {'epoch_shard': 'ancient_chronos', 'firstborn_ember': 'ancient_genesis', 'void_prism': 'ancient_abyss'}
SUMMON_ANIMATIONS = {'ancient_chronos': ['*The Epoch Shard splinters. Time around the altar stutters — a moment repeats once, then again, then stops.*', "*Somewhere behind you, a sound occurs that hasn't happened yet. You turn. Nothing is there. It hasn't happened yet.*", '*Ancient Chronos was already here. It has always been here. It is choosing, now, to be seen.*'], 'ancient_genesis': ['*The Firstborn Ember touches the altar and ignites it with a light that predates color.*', '*The flame does not burn the stone. It reminds it of when it was something else.*', '*The light does not fade when it arrives. Ancient Genesis does not appear — it simply becomes true.*'], 'ancient_abyss': ['*The Void Prism is placed on the altar. The light in the room does not go out — it simply stops arriving.*', '*The silence changes. It becomes the kind of silence that existed before sound was invented.*', '*Something vast and patient has noticed. The darkness does not approach — it simply becomes the room.*']}

class Ancient(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def summon_item_autocomplete(self, interaction, current):
        async with storage.connect() as db:
            async with db.execute('SELECT item_id,quantity FROM player_inventory WHERE user_id=? AND quantity>0', (interaction.user.id,)) as c:
                owned = dict(await c.fetchall())
        return [app_commands.Choice(name=f"{id.replace('_', ' ').title()} ×{owned[id]}", value=id) for id in SUMMON_ITEMS if owned.get(id) and current.lower() in id.replace('_', ' ').lower()]

    @app_commands.command(name='ancient', description='Use a summon relic to gather an Ancient raid 🏛️')
    @app_commands.autocomplete(summon_item=summon_item_autocomplete)
    async def ancient(self, interaction: discord.Interaction, summon_item: str):
        await interaction.response.defer()
        await storage.get_or_create_player(interaction.user.id, str(interaction.user))
        if summon_item not in SUMMON_ITEMS:
            return await interaction.followup.send('Choose an Epoch Shard, Firstborn Ember or Void Prism.')
        boss = next((b for b in ANCIENT_BOSSES if b['id'] == SUMMON_ITEMS[summon_item]))
        from utils.raids import open_raid
        try:
            await open_raid(interaction, boss, summon_item=summon_item)
        except ValueError as error:
            await interaction.followup.send(str(error))

    @app_commands.command(name='ancient_attack', description='Find your current Ancient raid and its action buttons')
    async def ancient_attack(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raid = next((r for r in active_ancient_raids.values() if not r.guild_id and interaction.user.id in r.users), None)
        await interaction.followup.send(f'Use the raid buttons: {raid.message.jump_url}' if raid and raid.message else 'Join an /ancient lobby first.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(Ancient(bot))
