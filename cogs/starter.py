# ── ChibiBeasts Starter Selection ───────────────────────────────────────────
# The /start command runs a cinematic multi-stage onboarding experience.
# Stage 1: World introduction — what is ChibiBeasts and why does it matter
# Stage 2: The four Architects — lore-grounded starter presentation
# Stage 3: Beast selection — four buttons, each reveals a different world
# Stage 4: Confirmation cinematic — the Architect responds to the choice
# Stage 5: Welcome — starter kit + clear "what to do first" guide

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
from utils.db import (
    get_or_create_player, get_player,
    add_beast_to_player, get_player_beasts, load_beasts
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI
from utils.progress import unlock_simple_achievement, record_bestiary_sighting

DB_PATH = "db/chibibeast.db"

STARTER_IDS = {"prismite", "twine", "gloop", "barkley"}

# ── Per-starter cinematic data ───────────────────────────────────────────────
STARTER_CINEMATICS = {
    "prismite": {
        "architect":    "Prism",
        "architect_desc": "the Architect of Form",
        "scene_title":  "🔷 Order Chooses Gently",
        "scene_lines": [
            "*Prism watches from somewhere beyond the edge of things.*",
            "*It has always believed that reality needs structure — edges, geometry, things that hold their shape.*",
            "*But structure without warmth is just a cage.*",
            "*So it made Prismite. A small porcelain kitten with diamond eyes.*",
            "*And sent it out to see whether order could be gentle.*",
            "",
            "*Today, Prismite found its answer.*",
        ],
        "confirmation": "*Prism nods — quietly, once. Order has chosen to be gentle today.*",
        "color": "rare",
    },
    "twine": {
        "architect":    "Twine",
        "architect_desc": "the Architect of Time",
        "scene_title":  "🧵 Time Chooses to Be Kind",
        "scene_lines": [
            "*Twine believes reality needs memory — a thread connecting what-was to what-will-be.*",
            "*It pulled a strand of itself: crimson, ticking, alive with the weight of all moments.*",
            "*And shaped it into a hamster with a tiny clock-gear shell.*",
            "*Sent it out to learn whether time could be kind.*",
            "",
            "*Somewhere upstream in time, Twine already knows how this goes.*",
            "*It seems pleased.*",
        ],
        "confirmation": "*Somewhere upstream in time, Twine already knows how this goes. It seems pleased.*",
        "color": "epic",
    },
    "gloop": {
        "architect":    "Aspect",
        "architect_desc": "the Architect of Change",
        "scene_title":  "🫧 Change Decides to Be Safe",
        "scene_lines": [
            "*Aspect believed reality needed to be soft enough to become anything.*",
            "*It let a piece of its own shifting, galaxy-colored self bubble free.*",
            "*A slime full of swirling starlight. Gloop.*",
            "*Sent to discover whether change could be safe.*",
            "",
            "*Aspect ripples with something that might be pride,*",
            "*if a cosmic force of change could feel pride.*",
        ],
        "confirmation": "*Aspect ripples with something that might be pride, if a cosmic force of change could feel pride. Change has decided to be safe, at least for now.*",
        "color": "legendary",
    },
    "barkley": {
        "architect":    "Pillar",
        "architect_desc": "the Architect of Foundation",
        "scene_title":  "🌿 Foundation Decides to Play",
        "scene_lines": [
            "*Pillar believed reality needed something that would not break.*",
            "*It cracked off a fragment of bark and ancient moss and stone.*",
            "*Shaped it into a small dragon hatchling wearing its own eggshell like a helmet.*",
            "*Barkley. Sent to prove that steadiness could still be playful.*",
            "",
            "*Pillar does not say anything. It never does.*",
            "*But somewhere, something very large and very old becomes marginally more certain the world will hold.*",
        ],
        "confirmation": "*Pillar does not say anything. It never does. But somewhere, something very large and very old becomes marginally more certain the world will hold.*",
        "color": "uncommon",
    },
}

# ── What to do first — shown after choosing ──────────────────────────────────
FIRST_STEPS = [
    ("📋", "/dailies", "Check your 4 daily quests — complete them for gold and shards"),
    ("🗺️", "/explore", "Explore the Whispering Woods to catch your first wild beast"),
    ("🥚", "/shop",    "Buy a Common Egg and hatch it for a new companion"),
    ("⚔️", "/sparr",   "Spar with an NPC to earn EXP and deepen your bond"),
    ("📖", "/questline","Begin the main story — follow the thread"),
    ("📚", "/help",    "Browse all commands any time"),
]


class StarterView(discord.ui.View):
    def __init__(self, cog, trainer_id: int, trainer_name: str, starters: dict):
        super().__init__(timeout=180)
        self.cog          = cog
        self.trainer_id   = trainer_id
        self.trainer_name = trainer_name
        self.starters     = starters
        self.chosen       = False

    async def _confirm(self, interaction: discord.Interaction, chosen_id: str):
        if interaction.user.id != self.trainer_id:
            return await interaction.response.send_message(
                "✦ This journey isn't yours to start!", ephemeral=True
            )
        if self.chosen:
            return await interaction.response.send_message(
                "✦ You've already chosen your starter!", ephemeral=True
            )
        self.chosen = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        starter  = self.starters[chosen_id]
        cinematic = STARTER_CINEMATICS[chosen_id]

        # ── Grant starter to player ────────────────────────────────────────
        beast_row_id = await add_beast_to_player(
            self.trainer_id, {**starter, "caught_from": "starter"}
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE player_beasts SET is_active = 1 WHERE id = ?", (beast_row_id,)
            )
            await db.commit()

        # ── Stage 1: Architect cinematic ───────────────────────────────────
        scene_embed = discord.Embed(
            title=cinematic["scene_title"],
            description="\n".join(cinematic["scene_lines"]),
            color=COLORS.get(cinematic["color"], COLORS["legendary"])
        )
        if starter.get("image_url"):
            scene_embed.set_image(url=starter["image_url"])
        scene_embed.set_footer(text="ChibiBeasts 🐾  •  The Loom is still weaving.")
        await interaction.followup.send(embed=scene_embed)

        # ── Stage 2: Beast reveal ──────────────────────────────────────────
        beast_embed = self.cog.build_starter_embed(starter)
        await interaction.followup.send(embed=beast_embed)

        # ── Stage 3: Welcome + what to do next ────────────────────────────
        steps_str = "\n".join(
            f"{emoji} **`{cmd}`** — {desc}"
            for emoji, cmd, desc in FIRST_STEPS
        )
        welcome_embed = discord.Embed(
            title=f"🐾 Welcome, {self.trainer_name}.",
            description=(
                f"{cinematic['confirmation']}\n\n"
                f"You've been given **500 gold** 💰 and **10 Celestial Shards** 🔮 to start with.\n\n"
                f"**Your first steps:**\n{steps_str}"
            ),
            color=COLORS["divine"]
        )
        welcome_embed.set_footer(
            text="ChibiBeasts 🐾  •  Collect, raise, battle. The Loom is watching."
        )
        await interaction.followup.send(embed=welcome_embed)

        # ── Achievements + bestiary ────────────────────────────────────────
        await unlock_simple_achievement(self.trainer_id, "first_steps")
        if interaction.guild:
            await record_bestiary_sighting(
                interaction.guild.id, starter["id"], self.trainer_id
            )

    @discord.ui.button(label="Prismite 🔷", style=discord.ButtonStyle.primary, row=0)
    async def pick_prismite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "prismite")

    @discord.ui.button(label="Twine 🧵", style=discord.ButtonStyle.primary, row=0)
    async def pick_twine(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "twine")

    @discord.ui.button(label="Gloop 🫧", style=discord.ButtonStyle.primary, row=0)
    async def pick_gloop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "gloop")

    @discord.ui.button(label="Barkley 🌿", style=discord.ButtonStyle.primary, row=0)
    async def pick_barkley(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._confirm(interaction, "barkley")


class Starter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_starter_embed(self, beast: dict) -> discord.Embed:
        type_emoji   = TYPE_EMOJI.get(beast.get("type", ""), "❓")
        rarity_emoji = RARITY_EMOJI.get(beast["rarity"], "⚪")
        color        = COLORS.get(beast["rarity"], COLORS["info"])

        embed = discord.Embed(
            title=f"🎁 {beast['name']} chose you back.",
            description=(
                f"### {rarity_emoji} **{beast['name']}** — *{beast['title']}*\n\n"
                f"*{beast['description']}*"
            ),
            color=color
        )
        embed.add_field(name=f"{type_emoji} Type",  value=beast["type"].capitalize(),                    inline=True)
        embed.add_field(name="✨ Rarity",            value=RARITY_LABEL.get(beast["rarity"], beast["rarity"]), inline=True)
        embed.add_field(name="🏛️ House",            value=beast.get("starter_house", "Unknown"),        inline=True)

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

        embed.set_footer(text="ChibiBeasts 🐾  •  Use /beastinfo to inspect anytime")
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
                        f"Use `/profile` to see your progress, or `/help` to browse all commands."
                    ),
                    color=COLORS["info"]
                ))

        await get_or_create_player(interaction.user.id, str(interaction.user))

        all_beasts = load_beasts()
        starters   = {sid: all_beasts[sid] for sid in STARTER_IDS if sid in all_beasts}

        # ── Stage 1: World intro ───────────────────────────────────────────
        intro_embed = discord.Embed(
            title="🌟 The Loom Wove Four Threads",
            description=(
                "*Before there were beasts, before there were trainers, before there was even a "
                "world to stand on — there was only the Loom.*\n\n"
                "*The Loom was not a place. It was the act of weaving itself: an endless process "
                "spinning raw possibility into shape.*\n\n"
                "*Then, in a single instant, the Loom wove four threads tighter than any others — "
                "and they woke up.*\n\n"
                "These four became the **Architects**: vast, curious ideas that each wanted one small "
                "companion to carry their question out into the world.\n\n"
                "**Today, that companion is you.**\n\n"
                "*Each starter comes from a different Architect. Your choice shapes not just your "
                "stats — but the kind of story you're walking into.*\n\n"
                "**Who will you bring with you?**"
            ),
            color=COLORS["divine"]
        )

        # Add each starter as a field
        HOUSE_EMOJI = {"prismite": "🔷", "twine": "🧵", "gloop": "🫧", "barkley": "🌿"}
        ORDER       = ["prismite", "twine", "gloop", "barkley"]
        for sid in ORDER:
            if sid not in starters:
                continue
            b      = starters[sid]
            emoji  = HOUSE_EMOJI.get(sid, "⚪")
            cin    = STARTER_CINEMATICS[sid]
            s      = b["base_stats"]
            intro_embed.add_field(
                name=f"{emoji} **{b['name']}** — *{cin['architect_desc'].title()}*",
                value=(
                    f"*{b.get('starter_flavor', b['description'])}*\n"
                    f"❤️`{s['hp']}` ⚔️`{s['attack']}` 🛡️`{s['defense']}` "
                    f"💨`{s['speed']}` 💠`{s['mana']}`"
                ),
                inline=False
            )

        intro_embed.set_footer(
            text="ChibiBeasts 🐾  •  The Loom is still weaving. Your thread starts now."
        )

        view = StarterView(self, interaction.user.id, interaction.user.display_name, starters)
        await interaction.followup.send(embed=intro_embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Starter(bot))
