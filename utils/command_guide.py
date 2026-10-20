"""A browsable guide that teaches panels before optional command shortcuts."""
import discord
from utils.theme import COLORS


class GuideView(discord.ui.View):
    def __init__(self, user_id, categories):
        super().__init__(timeout=300)
        self.user_id, self.categories = user_id, categories
        self.category = next(iter(categories))
        self.picker = discord.ui.Select(placeholder='Choose a guide topic', options=[
            discord.SelectOption(label=key, value=key) for key in categories])
        self.picker.callback = self.choose
        self.add_item(self.picker)

    def render(self):
        for option in self.picker.options:
            option.default = option.value == self.category
        return discord.Embed(title=self.category, description=self.categories[self.category], color=COLORS['info'])

    async def choose(self, interaction):
        if interaction.user.id != self.user_id or self.is_finished():
            return await interaction.response.send_message('Open the guide from your own journal.', ephemeral=True)
        key = interaction.data['values'][0]
        if key not in self.categories:
            return await interaction.response.send_message('Choose a current guide topic.', ephemeral=True)
        self.category = key
        await interaction.response.edit_message(embed=self.render(), view=self)
