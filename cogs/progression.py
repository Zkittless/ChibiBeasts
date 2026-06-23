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
    async def bestiary(self, interaction: discord.Interaction):
        rarity = None
        await interaction.response.defer()
        all_beasts    = load_beasts()
        guild_id      = interaction.guild.id if interaction.guild else 0
        discovered    = await get_bestiary_progress(guild_id)

        # Load global catch counts
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT beast_id, catch_count FROM global_catch_counts") as c:
                catch_counts = {r["beast_id"]: r["catch_count"] for r in await c.fetchall()}

        TAB_RARITIES = ["all","common","uncommon","rare","epic","legendary","divine","special"]
        TAB_LABELS   = {
            "all":      "📋 All",
            "common":   "⚪ Common",
            "uncommon": "🟢 Uncommon",
            "rare":     "🔵 Rare",
            "epic":     "🟣 Epic",
            "legendary":"🟠 Legendary",
            "divine":   "✨ Divine",
            "special":  "✦ Special",
        }
        SPECIAL = {"altered_divine","corrupted","ancient","dev"}
        RARITY_COLORS = {
            "all":"info","common":"common","uncommon":"uncommon","rare":"rare",
            "epic":"epic","legendary":"legendary","divine":"divine","special":"legendary",
        }

        uid = interaction.user.id
        per_page = 12

        def filter_beasts(tab: str) -> list:
            if tab == "all":
                pool = list(all_beasts.values())
            elif tab == "special":
                pool = [b for b in all_beasts.values() if b["rarity"] in SPECIAL]
            else:
                pool = [b for b in all_beasts.values() if b["rarity"] == tab]
            return sorted(pool, key=lambda b: (RARITY_ORDER.index(b["rarity"]) if b["rarity"] in RARITY_ORDER else 99, b["name"]))

        def has_tab(tab: str) -> bool:
            return len(filter_beasts(tab)) > 0

        def catch_bar(count: int) -> str:
            """Visual catch counter — compact dots."""
            if count == 0:   return ""
            if count < 5:    return f"{'◆' * count}{'◇' * (5-count)}"
            if count < 25:   return f"✦ `{count}`"
            if count < 100:  return f"✦✦ `{count}`"
            return               f"✦✦✦ `{count}`"

        def build_embed(tab: str, page: int):
            beasts = filter_beasts(tab)
            total_beasts  = len(beasts)
            total_found   = sum(1 for b in beasts if b["id"] in discovered)
            total_pages   = max(1, (total_beasts + per_page - 1) // per_page)
            page          = max(1, min(page, total_pages))
            page_beasts   = beasts[(page-1)*per_page : page*per_page]

            color_key = RARITY_COLORS.get(tab, "info")
            embed = discord.Embed(
                title=f"📖 {interaction.guild.name}'s Bestiary" if interaction.guild else "📖 Bestiary",
                description=(
                    f"**{TAB_LABELS.get(tab, tab)}** — `{total_found}/{total_beasts}` discovered · Page {page}/{total_pages}"
                ),
                color=COLORS.get(color_key, COLORS["info"])
            )

            for b in page_beasts:
                r_emoji   = RARITY_EMOJI.get(b["rarity"], "⚪")
                t_emoji   = TYPE_EMOJI.get(b["type"], "❓")
                count     = catch_counts.get(b["id"], 0)
                bar       = catch_bar(count)

                if b["id"] in discovered:
                    sighting    = discovered[b["id"]]
                    finder      = interaction.guild.get_member(sighting["first_caught_by"]) if interaction.guild else None
                    finder_name = finder.display_name if finder else "Unknown Trainer"
                    name_line   = f"{r_emoji} **{b['name']}** {t_emoji}"
                    val_line    = f"First: *{finder_name}*"
                    if bar:
                        val_line += f" · {bar}"
                else:
                    name_line = f"❔ **???** {t_emoji}"
                    val_line  = "*undiscovered*"

                embed.add_field(name=name_line, value=val_line, inline=True)

            embed.set_footer(text="◆ = 1 catch · ✦ = 5+ · ✦✦ = 25+ · ✦✦✦ = 100+ globally")
            return embed, total_pages

        current_tab  = "all"
        current_page = 1

        class BestiaryView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=180)
                self_v.tab  = current_tab
                self_v.page = current_page
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                visible = [t for t in TAB_RARITIES if has_tab(t)]
                # Row 0 — rarity select
                select = discord.ui.Select(
                    placeholder="📖 Filter by rarity…",
                    options=[
                        discord.SelectOption(label=TAB_LABELS[t], value=t, default=t==self_v.tab)
                        for t in visible
                    ],
                    row=0
                )
                async def _on_select(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your bestiary view!", ephemeral=True)
                    self_v.tab  = inter.data["values"][0]
                    self_v.page = 1
                    self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                select.callback = _on_select
                self_v.add_item(select)
                # Row 1 — pagination
                _, total = build_embed(self_v.tab, self_v.page)
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=1, disabled=self_v.page<=1)
                async def _prev(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your bestiary view!", ephemeral=True)
                    self_v.page -= 1; self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                prev.callback = _prev
                self_v.add_item(prev)
                self_v.add_item(discord.ui.Button(label=f"{self_v.page}/{total}", style=discord.ButtonStyle.secondary, row=1, disabled=True))
                nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=1, disabled=self_v.page>=total)
                async def _next(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your bestiary view!", ephemeral=True)
                    self_v.page += 1
                    self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                nxt.callback = _next
                self_v.add_item(nxt)

        emb, _ = build_embed(current_tab, current_page)
        view = BestiaryView()
        await interaction.followup.send(embed=emb, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Progression(bot))
