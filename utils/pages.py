"""Small owner-controlled page view for Discord embeds."""
import discord

class EmbedPages(discord.ui.View):

    def __init__(self, owner, pages):
        super().__init__(timeout=180)
        self.owner = owner
        self.pages = pages
        self.index = 0
        self.update_buttons()

    def update_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.pages) - 1

    async def show(self, interaction, change):
        if interaction.user.id != self.owner:
            return await interaction.response.send_message('Open your own journal to turn its pages.', ephemeral=True)
        self.index = max(0, min(len(self.pages) - 1, self.index + change))
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        await self.show(interaction, -1)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        await self.show(interaction, 1)

def achievement_pages(target, definitions, earned):
    entries = sorted(definitions.items(), key=lambda pair: ('bronze', 'silver', 'gold', 'platinum').index(pair[1]['tier']))
    pages = []
    for start in range(0, len(entries), 10):
        embed = discord.Embed(title=f"🏆 {target.display_name}'s achievements", description=f'{len(set(earned) & set(definitions))}/{len(definitions)} earned · collecting, care, craft and shared adventures.', color=14265428)
        for key, achievement in entries[start:start + 10]:
            embed.add_field(name=f"{('✅' if key in earned else '◇')} {achievement['emoji']} {achievement['name']} · {achievement['tier']}", value=achievement['desc'], inline=False)
        embed.set_footer(text=f'Page {len(pages) + 1}/{(len(entries) + 9) // 10} · Achievements unlock as you play')
        pages.append(embed)
    return pages
