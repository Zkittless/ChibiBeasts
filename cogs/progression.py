import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from utils.db import get_or_create_player, load_beasts
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import (
    ACHIEVEMENTS, get_daily_quests, check_achievements, get_bestiary_progress
)

DB_PATH = "db/chibibeast.db"
TIER_ORDER = ["bronze", "silver", "gold", "platinum"]
RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "divine", "altered_divine", "corrupted", "ancient", "dev"]


class Progression(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /dailies ─────────────────────────────────────────────────────────
    @app_commands.command(name="dailies", description="View your daily quests 📋")
    async def dailies(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await get_or_create_player(interaction.user.id, str(interaction.user))
        quests = await get_daily_quests(interaction.user.id)

        embed = discord.Embed(
            title="📋 Daily Quests",
            description=(
                "*The Loom keeps weaving whether you're watching or not. "
                "These are the threads it's laid out for you today.*\n"
                "*(Quests reset at 00:00 UTC. Complete them for bonus gold and EXP.)*"
            ),
            color=COLORS["info"]
        )

        completed_count = sum(1 for q in quests if q["completed"])
        for q in quests:
            status = "✅" if q["completed"] else "⏳"
            bar_len = 12
            filled = int((q["progress"] / q["target"]) * bar_len) if q["target"] else bar_len
            filled = min(filled, bar_len)
            bar = "🟦" * filled + "⬛" * (bar_len - filled)
            embed.add_field(
                name=f"{status} {q['emoji']} {q['name']}",
                value=(
                    f"*{q['desc'].format(target=q['target'])}*\n"
                    f"{bar} `{q['progress']}/{q['target']}`\n"
                    f"Reward: +{q['reward_gold']:,} 💰 | +{q['reward_exp']} EXP"
                ),
                inline=False
            )

        embed.set_footer(text=f"ChibiBeasts 🐾  •  {completed_count}/{len(quests)} completed today")
        await interaction.followup.send(embed=embed)

    # ── /achievements ────────────────────────────────────────────────────
    @app_commands.command(name="achievements", description="View your trainer achievements 🏆")
    @app_commands.describe(member="View another trainer's achievements")
    async def achievements(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        target = member or interaction.user
        await get_or_create_player(target.id, str(target))

        # Re-check stat-based achievements live so the list is always current
        await check_achievements(target.id)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT achievement_id, earned_at FROM achievements WHERE user_id = ?", (target.id,)
            ) as c:
                earned = {r["achievement_id"]: r["earned_at"] for r in await c.fetchall()}

        total = len(ACHIEVEMENTS)
        unlocked = len(earned)

        embed = discord.Embed(
            title=f"🏆 {target.display_name}'s Achievements",
            description=(
                f"**Progress:** `{unlocked}/{total}` unlocked\n"
                f"*The Architects are watching. Every catch, every battle, every raid — "
                f"it all becomes part of the weave.*"
            ),
            color=COLORS["legendary"]
        )

        by_tier = {t: [] for t in TIER_ORDER}
        for aid, ach in ACHIEVEMENTS.items():
            by_tier[ach["tier"]].append((aid, ach))

        for tier in TIER_ORDER:
            entries = by_tier[tier]
            if not entries:
                continue
            lines = []
            for aid, ach in entries:
                if aid in earned:
                    lines.append(f"{ach['emoji']} **{ach['name']}** — *{ach['desc']}* ✅")
                else:
                    lines.append(f"{ach['emoji']} ~~{ach['name']}~~ — *{ach['desc']}*")
            embed.add_field(
                name=f"{tier.capitalize()} Tier",
                value="\n".join(lines),
                inline=False
            )

        embed.set_footer(text="ChibiBeasts 🐾  •  Achievements unlock automatically as you play!")
        await interaction.followup.send(embed=embed)

    # ── /bestiary ────────────────────────────────────────────────────────
    @app_commands.command(name="bestiary", description="View the server's beast discovery log 📖")
    @app_commands.describe(rarity="Filter by rarity")
    @app_commands.choices(rarity=[
        app_commands.Choice(name="⚪ Common", value="common"),
        app_commands.Choice(name="🟢 Uncommon", value="uncommon"),
        app_commands.Choice(name="🔵 Rare", value="rare"),
        app_commands.Choice(name="🟣 Epic", value="epic"),
        app_commands.Choice(name="🟡 Legendary", value="legendary"),
        app_commands.Choice(name="🌸 Divine", value="divine"),
    ])
    async def bestiary(self, interaction: discord.Interaction, rarity: str = None):
        await interaction.response.defer()
        all_beasts = load_beasts()
        guild_id = interaction.guild.id if interaction.guild else 0
        discovered = await get_bestiary_progress(guild_id)

        beasts_to_show = list(all_beasts.values())
        if rarity:
            beasts_to_show = [b for b in beasts_to_show if b["rarity"] == rarity]

        beasts_to_show.sort(key=lambda b: (RARITY_ORDER.index(b["rarity"]), b["name"]))

        total = len(beasts_to_show)
        found = sum(1 for b in beasts_to_show if b["id"] in discovered)

        embed = discord.Embed(
            title=f"📖 {interaction.guild.name}'s Bestiary" if interaction.guild else "📖 Bestiary",
            description=(
                f"**Discovered:** `{found}/{total}`"
                + (f" *(filtered to {RARITY_LABEL.get(rarity, rarity)})*" if rarity else "")
            ),
            color=COLORS.get(rarity, COLORS["info"]) if rarity else COLORS["info"]
        )

        # Group by rarity for readability
        grouped = {}
        for b in beasts_to_show:
            grouped.setdefault(b["rarity"], []).append(b)

        for r in RARITY_ORDER:
            if r not in grouped:
                continue
            lines = []
            for b in grouped[r]:
                type_emoji = TYPE_EMOJI.get(b["type"], "❓")
                if b["id"] in discovered:
                    sighting = discovered[b["id"]]
                    finder = interaction.guild.get_member(sighting["first_caught_by"]) if interaction.guild else None
                    finder_name = finder.display_name if finder else "Unknown Trainer"
                    lines.append(f"{type_emoji} **{b['name']}** — *first caught by {finder_name}*")
                else:
                    lines.append(f"❔ **???** — *undiscovered*")
            # Discord embed field value limit is 1024 chars; chunk if needed
            chunk = []
            chunk_len = 0
            field_idx = 0
            for line in lines:
                if chunk_len + len(line) + 1 > 1000:
                    field_idx += 1
                    embed.add_field(
                        name=f"{RARITY_EMOJI.get(r,'⚪')} {RARITY_LABEL.get(r, r.capitalize())}" + (f" (cont.)" if field_idx > 1 else ""),
                        value="\n".join(chunk),
                        inline=False
                    )
                    chunk = []
                    chunk_len = 0
                chunk.append(line)
                chunk_len += len(line) + 1
            if chunk:
                field_idx += 1
                embed.add_field(
                    name=f"{RARITY_EMOJI.get(r,'⚪')} {RARITY_LABEL.get(r, r.capitalize())}" + (f" (cont.)" if field_idx > 1 else ""),
                    value="\n".join(chunk),
                    inline=False
                )

        embed.set_footer(text="ChibiBeasts 🐾  •  Catch beasts to fill in the bestiary for everyone!")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Progression(bot))
