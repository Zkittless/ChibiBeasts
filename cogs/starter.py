# ── ChibiBeasts Starter Selection ───────────────────────────────────────────
# The /start command and StarterView are intentionally kept separate from
# hatch.py. Starters are not hatched from eggs — they are given at the
# beginning of the journey via a dedicated lore-grounded ceremony.
# Per LORE.md: "You are not picking a pet. You are continuing a four-sided
# conversation that started before the world had a floor to stand on."

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from utils.db import (
    get_or_create_player, get_player,
    add_beast_to_player, get_player_beasts, load_beasts
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import unlock_simple_achievement, record_bestiary_sighting

DB_PATH = "db/chibibeast.db"

STARTER_IDS  = {"prismite", "twine", "gloop", "barkley"}
HOUSE_EMOJI  = {"prismite": "🔷", "twine": "🧵", "gloop": "🫧", "barkley": "🌿"}

# One unique Architect response per starter — shown after the player chooses
CONFIRMATION_LINES = {
    "prismite": (
        "*Prism watches from somewhere beyond the edge of things and nods — quietly, "
        "once. Order has chosen to be gentle today.*"
    ),
    "twine": (
        "*Somewhere upstream in time, Twine already knows how this goes. "
        "It seems pleased.*"
    ),
    "gloop": (
        "*Aspect ripples with something that might be pride, "
        "if a cosmic force of change could feel pride. "
        "Change has decided to be safe, at least for now.*"
    ),
    "barkley": (
        "*Pillar does not say anything. It never does. "
        "But somewhere, something very large and very old "
        "becomes marginally more certain the world will hold.*"
    ),
}


class StarterView(discord.ui.View):
    """
    Four-button starter selection view. Lives here rather than inside the
    start() command body so it can be unit-tested, reused, and is not
    tangled up with the egg-hatching infrastructure in hatch.py.
    """
    def __init__(self, cog, trainer_id: int, trainer_name: str, starters: dict):
        super().__init__(timeout=120)
        self.cog         = cog
        self.trainer_id  = trainer_id
        self.trainer_name = trainer_name
        self.starters    = starters
        self.chosen      = False

    async def _confirm(self, interaction: discord.Interaction, chosen_id: str):
        if interaction.user.id != self.trainer_id:
            return await interaction.response.send_message(
                "This journey isn't yours to start!", ephemeral=True
            )
        if self.chosen:
            return await interaction.response.send_message(
                "You've already chosen your starter!", ephemeral=True
            )
        self.chosen = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        starter = self.starters[chosen_id]

        import aiosqlite as _aio
        beast_row_id = await add_beast_to_player(
            self.trainer_id, {**starter, "caught_from": "starter"}
        )
        async with _aio.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE player_beasts SET is_active = 1 WHERE id = ?", (beast_row_id,)
            )
            await db.commit()

        lore_line = CONFIRMATION_LINES.get(chosen_id, "*Your journey begins.*")

        welcome_embed = discord.Embed(
            title="🐾 Your Journey Begins!",
            description=(
                f"*Welcome, **{self.trainer_name}**.*\n\n"
                f"{lore_line}\n\n"
                f"You received **500 gold** 💰 and **10 Celestial Shards** 🔮 to get started.\n\n"
                f"**What you can do next:**\n"
                f"🥚 `/hatch` — Hatch eggs to find new beasts\n"
                f"🗺️ `/explore` — Discover wild beasts in the world\n"
                f"⚔️ `/battle` — Challenge other trainers\n"
                f"📋 `/dailies` — Check your daily quests\n"
                f"📖 `/profile` — View your trainer profile\n"
                f"🏪 `/shop` — Browse the shop\n"
                f"📚 `/help` — See all 50 commands\n"
            ),
            color=COLORS["divine"]
        )
        welcome_embed.set_footer(text="ChibiBeasts 🐾  •  Collect, Raise, Battle!")

        beast_embed = self.cog.build_starter_embed(starter)
        await interaction.followup.send(embeds=[welcome_embed, beast_embed])

        await unlock_simple_achievement(self.trainer_id, "first_steps")
        if interaction.guild:
            await record_bestiary_sighting(
                interaction.guild.id, starter["id"], self.trainer_id
            )

    @discord.ui.button(label="Prismite 🔷", style=discord.ButtonStyle.secondary)
    async def pick_prismite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "prismite")

    @discord.ui.button(label="Twine 🧵", style=discord.ButtonStyle.secondary)
    async def pick_twine(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "twine")

    @discord.ui.button(label="Gloop 🫧", style=discord.ButtonStyle.secondary)
    async def pick_gloop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "gloop")

    @discord.ui.button(label="Barkley 🌿", style=discord.ButtonStyle.secondary)
    async def pick_barkley(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "barkley")


class Starter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_starter_embed(self, beast: dict) -> discord.Embed:
        """Build the beast reveal embed for the chosen starter."""
        type_emoji  = TYPE_EMOJI.get(beast.get("type", ""), "❓")
        rarity_emoji = RARITY_EMOJI.get(beast["rarity"], "⚪")
        color = COLORS.get(beast["rarity"], COLORS["info"])

        embed = discord.Embed(
            title=f"🎁 {beast['name']} chose you back.",
            description=(
                f"### {rarity_emoji} **{beast['name']}** — *{beast['title']}*\n\n"
                f"*{beast['description']}*"
            ),
            color=color
        )
        embed.add_field(name=f"{type_emoji} Type", value=beast["type"].capitalize(), inline=True)
        embed.add_field(name="✨ Rarity", value=RARITY_LABEL.get(beast["rarity"], beast["rarity"]), inline=True)
        embed.add_field(name="🏛️ House", value=beast.get("starter_house", "Unknown"), inline=True)

        stats = beast["base_stats"]
        embed.add_field(
            name="📊 Base Stats",
            value=(
                f"❤️ HP: `{stats['hp']}` | ⚔️ ATK: `{stats['attack']}`\n"
                f"🛡️ DEF: `{stats['defense']}` | 💨 SPD: `{stats['speed']}`\n"
                f"💠 MANA: `{stats['mana']}`"
            ),
            inline=False
        )
        embed.add_field(
            name="⚡ Moves",
            value="\n".join(f"• {m}" for m in beast["moves"]) + f"\n🌟 **Ultimate:** {beast['ultimate']}",
            inline=False
        )
        flavor = beast.get("starter_flavor", "")
        if flavor:
            embed.add_field(name="💬 Personality", value=f"*{flavor}*", inline=False)

        if beast.get("image_url"):
            embed.set_image(url=beast["image_url"])

        embed.set_footer(text="ChibiBeasts 🐾  •  Use /profile to see your trainer stats")
        return embed

    @app_commands.command(name="start", description="Begin your ChibiBeasts journey! 🐾")
    async def start(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_player(interaction.user.id)

        if player:
            existing = await get_player_beasts(interaction.user.id)
            if existing:
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        f"✦ You've already started your journey, **{interaction.user.display_name}**!\n"
                        f"Use `/profile` to see your progress."
                    ),
                    color=COLORS["info"]
                ))

        await get_or_create_player(interaction.user.id, str(interaction.user))

        all_beasts = load_beasts()
        starters   = {sid: all_beasts[sid] for sid in STARTER_IDS if sid in all_beasts}

        # Fallback — should never fire in a correctly seeded deployment
        if not starters:
            starters = {
                b["id"]: b for b in all_beasts.values()
                if b["rarity"] == "common" and not b.get("starter")
            }

        intro_embed = discord.Embed(
            title="🌟 Welcome to ChibiBeasts",
            description=(
                "*Long before the world had a floor to stand on, the Loom wove four threads tighter "
                "than any others — and they woke up.*\n\n"
                "These four became the **Architects**: vast, curious ideas that each wanted one small "
                "companion to carry their question out into the world.\n\n"
                "**Today, that companion is yours to choose.**\n\n"
                "Each starter comes from a different Architect. "
                "Your choice shapes who you are as a trainer — not just in stats, "
                "but in the kind of story you're walking into.\n\n"
                "*Who will you bring with you?*"
            ),
            color=COLORS["divine"]
        )
        intro_embed.set_footer(
            text="ChibiBeasts 🐾  •  The Loom is still weaving. Your thread starts now."
        )

        # One embed per starter so each sprite sits cleanly next to its description
        starter_embeds = []
        for sid in STARTER_IDS:
            if sid not in starters:
                continue
            b      = starters[sid]
            emoji  = HOUSE_EMOJI.get(sid, "⚪")
            flavor = b.get("starter_flavor", b["description"])
            house  = b.get("starter_house", "Unknown House")
            s      = b["base_stats"]
            color  = COLORS.get(b["rarity"], COLORS["info"])

            se = discord.Embed(
                title=f"{emoji} {b['name']} — *{b['title']}*",
                description=f"🏛️ *{house}*\n\n{flavor}",
                color=color
            )
            se.add_field(
                name="📊 Stats",
                value=(
                    f"❤️`{s['hp']}` ⚔️`{s['attack']}` 🛡️`{s['defense']}` "
                    f"💨`{s['speed']}` 💠`{s['mana']}`"
                ),
                inline=False
            )
            if b.get("image_url"):
                se.set_thumbnail(url=b["image_url"])
            starter_embeds.append(se)

        view = StarterView(self, interaction.user.id, interaction.user.display_name, starters)
        await interaction.followup.send(embeds=[intro_embed] + starter_embeds, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Starter(bot))
