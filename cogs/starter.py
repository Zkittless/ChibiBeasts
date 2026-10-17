# ── ChibiBeasts Starter Selection ───────────────────────────────────────────
# The /start command runs a cinematic multi-stage onboarding experience.
# Stage 1: World introduction — what is ChibiBeasts and why does it matter
# Stage 2: Meet all four companions in the illustrated overview
# Stage 3: Browse portraits and moves, then explicitly choose a starter
# Stage 4: Confirmation cinematic — the Architect responds to the choice
# Stage 5: Welcome — starter kit + clear "what to do first" guide

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import logging
from utils.artwork import card_artwork_attachment, card_artwork_url
from utils.beast_cards import STARTER_ORDER, STARTER_ROLES, starter_overview_embed, starter_preview_embed
from utils.db import (
    get_or_create_player, get_player,
    add_beast_to_player, get_player_beasts, load_beasts
)
from utils.theme import COLORS
from utils.progress import unlock_simple_achievement, record_bestiary_sighting


STARTER_IDS = set(STARTER_ORDER)

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
    ("📚", "/guide",   "Browse all commands any time"),
]


class StarterView(discord.ui.View):
    def __init__(self, cog, trainer_id: int, trainer_name: str, starters: dict):
        super().__init__(timeout=180)
        self.cog          = cog
        self.trainer_id   = trainer_id
        self.trainer_name = trainer_name
        self.starters     = starters
        self.chosen       = False
        self.selected_id = None
        self.details_mode = False
        self.message = None
        self._published_art = None
        self.lock = asyncio.Lock()
        self.browse.options = [discord.SelectOption(label='Meet all four', value='overview', default=True)] + [
            discord.SelectOption(label=starters[key]['name'], value=key, description=STARTER_ROLES[key][0])
            for key in STARTER_ORDER if key in starters
        ]
        self._update_controls()

    def current_embed(self):
        if self.selected_id is None:
            return starter_overview_embed()
        return starter_preview_embed(self.starters[self.selected_id], details=self.details_mode)

    def _update_controls(self):
        selected = self.selected_id
        for option in self.browse.options:
            option.default = option.value == (selected or 'overview')
        self.browse.disabled = self.chosen
        self.details.disabled = self.chosen or selected is None
        self.details.label = 'Portrait' if self.details_mode else 'Moves & stats'
        self.choose.disabled = self.chosen or selected is None
        self.choose.label = f"Choose {self.starters[selected]['name']}" if selected else 'Choose a companion'

    async def _redraw(self, interaction):
        self._update_controls()
        async with asyncio.timeout(15):
            if self._published_art != self.selected_id:
                with card_artwork_attachment(self.selected_id) as artwork:
                    await interaction.edit_original_response(embed=self.current_embed(), view=self, attachments=[artwork])
                self._published_art = self.selected_id
            else:
                await interaction.edit_original_response(embed=self.current_embed(), view=self)

    async def show(self, interaction, *, selected=None, toggle=False):
        if interaction.user.id != self.trainer_id:
            return await interaction.response.send_message("This journey belongs to another trainer. Use /start for yours.", ephemeral=True)
        await interaction.response.defer()
        async with self.lock:
            if self.chosen or self.is_finished():
                return await interaction.followup.send('This selection has closed. Use /start to see your next step.', ephemeral=True)
            if toggle:
                self.details_mode = not self.details_mode if self.selected_id else False
            else:
                if selected is not None and selected not in self.starters:
                    return await interaction.followup.send('That companion is not available.', ephemeral=True)
                self.selected_id = selected
                self.details_mode = False
            await self._redraw(interaction)

    async def _confirm(self, interaction: discord.Interaction, chosen_id: str):
        if interaction.user.id != self.trainer_id:
            return await interaction.response.send_message(
                "✦ This journey isn't yours to start!", ephemeral=True
            )
        if self.chosen:
            return await interaction.response.send_message(
                "✦ You've already chosen your starter!", ephemeral=True
            )
        await interaction.response.defer()
        async with self.lock:
            if self.chosen or self.is_finished():
                return await interaction.followup.send('This selection has closed. Use /profile to see your companion.', ephemeral=True)
            if chosen_id is None:
                return await interaction.followup.send('Meet a companion first, then press Choose.', ephemeral=True)
            if chosen_id != self.selected_id or chosen_id != self._published_art:
                return await interaction.followup.send('Your selection changed. Review the portrait and press Choose again.', ephemeral=True)
            starter = self.starters[chosen_id]
            beast_row_id = await add_beast_to_player(self.trainer_id, {**starter, 'caught_from': 'starter'}, starter=True)
            self.chosen = True
            self._update_controls()
            if beast_row_id is None:
                await interaction.edit_original_response(view=self)
                self.stop()
                return await interaction.followup.send("✦ You've already chosen your starter. Use `/profile` to see your companion.", ephemeral=True)
            embed = self.cog.build_starter_embed(starter)
            embed.set_image(url=card_artwork_url(chosen_id))
            await interaction.edit_original_response(embed=embed, view=self)
            self.stop()
        cinematic = STARTER_CINEMATICS[chosen_id]

        # ── Stage 1: Architect cinematic ───────────────────────────────────
        scene_embed = discord.Embed(
            title=cinematic["scene_title"],
            description="\n".join(cinematic["scene_lines"]),
            color=COLORS.get(cinematic["color"], COLORS["legendary"])
        )
        scene_embed.set_footer(text="ChibiBeasts 🐾  •  The Loom is still weaving.")

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
        await interaction.followup.send(embeds=[scene_embed, welcome_embed])

        # ── Achievements + bestiary ────────────────────────────────────────
        await unlock_simple_achievement(self.trainer_id, "first_steps")
        if interaction.guild:
            await record_bestiary_sighting(
                interaction.guild.id, starter["id"], self.trainer_id
            )

    @discord.ui.select(placeholder='Meet a starter…', options=[discord.SelectOption(label='Meet all four', value='overview')], row=0)
    async def browse(self, interaction, select):
        value = interaction.data['values'][0]
        await self.show(interaction, selected=None if value == 'overview' else value)

    @discord.ui.button(label='Moves & stats', style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def details(self, interaction, button):
        await self.show(interaction, toggle=True)

    @discord.ui.button(label='Choose a companion', style=discord.ButtonStyle.success, row=1, disabled=True)
    async def choose(self, interaction, button):
        # Capture the portrait actually published when the button was pressed.
        # A concurrent selection change must not claim a different companion.
        await self._confirm(interaction, self._published_art)

    async def on_timeout(self):
        async with self.lock:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content='Your companions will wait. Use /start to reopen their portraits.', view=self)
                except discord.HTTPException:
                    pass

    async def on_error(self, interaction, error, item):
        logging.getLogger('chibibeasts.starter').error('Starter browser failed: trainer=%s companion=%s', self.trainer_id, self.selected_id, exc_info=error)
        message = 'The companion browser could not finish that request. Use /start to reopen it, or /profile if you already chose.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class Starter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_starter_embed(self, beast: dict) -> discord.Embed:
        embed = starter_preview_embed(beast)
        embed.title = f"{beast['name']} chose you back."
        embed.description = f"*{beast['title']}*\n\n{STARTER_CINEMATICS[beast['id']]['confirmation']}"
        if beast.get("image_url"):
            embed.set_image(url=beast["image_url"])
        embed.set_footer(text="ChibiBeasts · Your companion is ready · /beastinfo for stats and moves")
        return embed

    @app_commands.command(name="start", description="Begin your ChibiBeasts journey! 🐾")
    async def start(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_player(interaction.user.id)

        if player:
            existing = await get_player_beasts(interaction.user.id)
            if existing or player.get("starter_claimed"):
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        f"✦ You've already started your journey, **{interaction.user.display_name}**!\n"
                        f"Use `/profile` to see your progress, or `/guide` to browse all commands."
                    ),
                    color=COLORS["info"]
                ))

        await get_or_create_player(interaction.user.id, str(interaction.user))

        all_beasts = load_beasts()
        starters   = {sid: all_beasts[sid] for sid in STARTER_IDS if sid in all_beasts}

        view = StarterView(self, interaction.user.id, interaction.user.display_name, starters)
        with card_artwork_attachment() as artwork:
            message = await interaction.followup.send(embed=starter_overview_embed(), view=view, file=artwork, wait=True)
        view.message = interaction.channel.get_partial_message(message.id) if interaction.channel else message


async def setup(bot: commands.Bot):
    await bot.add_cog(Starter(bot))
