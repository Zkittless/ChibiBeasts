"""
ranked.py — Ranked PvP system
Commands: /ranked, /ranked_leaderboard, /season
Rating: ELO-based, 1000 starting, seasons reset every 4 weeks
Ranks: Unranked → Bronze → Silver → Gold → Platinum → Diamond
"""
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from datetime import datetime, timezone

from utils.db import (
    get_or_create_player, get_player, update_player,
    get_beast_data, DB_PATH
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI
from utils.progress import (
    track_quest_event, check_achievements, notify_unlocks,
    notify_quest_completions, unlock_simple_achievement
)

# ── Rank tiers ─────────────────────────────────────────────────────────────
RANKS = [
    ("unranked",  0,    None,   "⬜", "Play 5 placement matches to receive your rank."),
    ("bronze",    0,    999,    "🟫", "The beginning. Every champion started here."),
    ("silver",    1000, 1249,   "⬜", "You know what you're doing. Mostly."),
    ("gold",      1250, 1499,   "🟨", "A real threat. Opponents check your profile before accepting."),
    ("platinum",  1500, 1749,   "🩵", "The Loom noticed. It doesn't usually notice."),
    ("diamond",   1750, 99999,  "💎", "The best in the server. The Archivist would call this a data point."),
]

RANK_EMOJI = {r[0]: r[3] for r in RANKS}
RANK_LABEL = {r[0]: r[0].capitalize() for r in RANKS}

CURRENT_SEASON = 1  # bump this to reset season


def get_rank_for_rating(rating: int, placements_done: int) -> str:
    if placements_done < 5:
        return "unranked"
    for name, lo, hi, _, _ in RANKS[1:]:  # skip unranked
        if lo <= rating <= hi:
            return name
    return "diamond"


def calc_elo(winner_rating: int, loser_rating: int, k: int = 32):
    """Standard ELO — returns (winner_new, loser_new, winner_delta)."""
    expected_w = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    delta = int(k * (1 - expected_w))
    delta = max(8, min(delta, 48))  # floor/ceiling on swing
    return winner_rating + delta, loser_rating - delta, delta


PLACEMENT_COUNT = 5  # matches before ranked rating applies


class Ranked(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /ranked ────────────────────────────────────────────────────────────
    @app_commands.command(name="ranked", description="Challenge someone to a ranked PvP battle 🏅")
    @app_commands.describe(opponent="The trainer to challenge")
    async def ranked(self, interaction: discord.Interaction, opponent: discord.Member):
        await interaction.response.defer()

        if opponent.id == interaction.user.id:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't challenge yourself to a ranked match.",
                color=COLORS["error"]
            ))
        if opponent.bot:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Bots don't have rankings. Yet.",
                color=COLORS["error"]
            ))

        challenger = await get_or_create_player(interaction.user.id, str(interaction.user))
        opp_player = await get_or_create_player(opponent.id, str(opponent))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Get active beasts
            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1",
                (interaction.user.id,)
            ) as c:
                c_beast = await c.fetchone()
            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1",
                (opponent.id,)
            ) as c:
                o_beast = await c.fetchone()

        if not c_beast:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You don't have an active beast set! Use `/setactive`.",
                color=COLORS["error"]
            ))
        if not o_beast:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ {opponent.display_name} doesn't have an active beast set.",
                color=COLORS["error"]
            ))

        c_beast = dict(c_beast)
        o_beast = dict(o_beast)
        c_rating = challenger.get("pvp_rating", 1000)
        o_rating = opp_player.get("pvp_rating", 1000)
        c_rank   = challenger.get("pvp_rank", "unranked")
        o_rank   = opp_player.get("pvp_rank", "unranked")
        c_bd     = get_beast_data(c_beast["beast_id"]) or {}
        o_bd     = get_beast_data(o_beast["beast_id"]) or {}

        embed = discord.Embed(
            title="🏅 Ranked Challenge",
            description=(
                f"**{interaction.user.display_name}** {RANK_EMOJI.get(c_rank,'⬜')} `{c_rank.capitalize()}` ({c_rating} RP)\n"
                f"*{RARITY_EMOJI.get(c_beast['rarity'],'⚪')} {c_beast.get('nickname') or c_bd.get('name','?')} "
                f"Lv.{c_beast['level']}*\n\n"
                f"**vs**\n\n"
                f"**{opponent.display_name}** {RANK_EMOJI.get(o_rank,'⬜')} `{o_rank.capitalize()}` ({o_rating} RP)\n"
                f"*{RARITY_EMOJI.get(o_beast['rarity'],'⚪')} {o_beast.get('nickname') or o_bd.get('name','?')} "
                f"Lv.{o_beast['level']}*\n\n"
                f"*{opponent.display_name}, accept the challenge?*"
            ),
            color=COLORS["legendary"]
        )

        uid_c = interaction.user.id
        uid_o = opponent.id
        accepted = {"value": False}

        class RankedView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=60)

            @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="⚔️")
            async def accept(self_v, bi: discord.Interaction, btn: discord.ui.Button):
                if bi.user.id != uid_o:
                    return await bi.response.send_message("✦ Only the challenged player can accept.", ephemeral=True)
                accepted["value"] = True
                self_v.stop()
                await bi.response.defer()

            @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
            async def decline(self_v, bi: discord.Interaction, btn: discord.ui.Button):
                if bi.user.id not in (uid_o, uid_c):
                    return await bi.response.send_message("✦ This isn't your match.", ephemeral=True)
                self_v.stop()
                await bi.response.edit_message(embed=discord.Embed(
                    description=f"✦ {opponent.display_name} declined the ranked challenge.",
                    color=COLORS["error"]
                ), view=None)

        view = RankedView()
        msg  = await interaction.followup.send(embed=embed, view=view)
        await view.wait()

        if not accepted["value"]:
            if not view.is_finished():
                await msg.edit(embed=discord.Embed(
                    description="✦ Ranked challenge timed out.",
                    color=COLORS["error"]
                ), view=None)
            return

        # ── Simulate battle ────────────────────────────────────────────────
        # Use same stat-based combat model as /sparr but for ranked context
        from utils.type_chart import get_type_multiplier

        def quick_battle(b1: dict, b2: dict, bd1: dict, bd2: dict) -> tuple:
            """Returns (winner_id, loser_id, log_lines)."""
            s1 = {"hp": b1["hp"], "max_hp": b1["max_hp"], "atk": b1["attack"],
                  "def": b1["defense"], "spd": b1["speed"], "name": b1.get("nickname") or bd1.get("name","?"),
                  "type": bd1.get("type",""), "uid": uid_c}
            s2 = {"hp": b2["hp"], "max_hp": b2["max_hp"], "atk": b2["attack"],
                  "def": b2["defense"], "spd": b2["speed"], "name": b2.get("nickname") or bd2.get("name","?"),
                  "type": bd2.get("type",""), "uid": uid_o}
            log = []
            turn = 1
            goes_first = s1 if s1["spd"] >= s2["spd"] else s2
            goes_second = s2 if goes_first is s1 else s1
            while s1["hp"] > 0 and s2["hp"] > 0 and turn <= 20:
                for atk, dfn in [(goes_first, goes_second),(goes_second, goes_first)]:
                    if s1["hp"] <= 0 or s2["hp"] <= 0:
                        break
                    t_mult = get_type_multiplier(atk["type"], dfn["type"])
                    is_crit = random.random() < 0.10
                    raw = max(1, int(atk["atk"] * (100/(100+dfn["def"])) * t_mult * (1.5 if is_crit else 1.0)))
                    dfn["hp"] = max(0, dfn["hp"] - raw)
                    crit_tag = "⭐ " if is_crit else ""
                    eff = "⚡ Super effective! " if t_mult >= 2 else ("🛡️ Resisted. " if t_mult <= 0.5 else "")
                    log.append(f"{crit_tag}**{atk['name']}** → `{raw}` dmg! {eff}")
                turn += 1
            winner = s1 if s1["hp"] > s2["hp"] else s2
            loser  = s2 if winner is s1 else s1
            return winner["uid"], loser["uid"], log

        winner_id, loser_id, log = quick_battle(c_beast, o_beast, c_bd, o_bd)

        # ── Update ratings ─────────────────────────────────────────────────
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT pvp_rating, pvp_rank, pvp_wins, pvp_losses, pvp_season FROM players WHERE user_id = ?", (winner_id,)) as c:
                w_row = dict(await c.fetchone())
            async with db.execute("SELECT pvp_rating, pvp_rank, pvp_wins, pvp_losses, pvp_season FROM players WHERE user_id = ?", (loser_id,)) as c:
                l_row = dict(await c.fetchone())

            w_new_rating, l_new_rating, delta = calc_elo(
                w_row.get("pvp_rating", 1000),
                l_row.get("pvp_rating", 1000)
            )
            w_wins    = (w_row.get("pvp_wins", 0) or 0) + 1
            l_losses  = (l_row.get("pvp_losses", 0) or 0) + 1
            w_total_g = w_wins + (w_row.get("pvp_losses", 0) or 0)
            l_total_g = (l_row.get("pvp_wins", 0) or 0) + l_losses
            w_new_rank = get_rank_for_rating(w_new_rating, w_total_g)
            l_new_rank = get_rank_for_rating(l_new_rating, l_total_g)

            await db.execute(
                "UPDATE players SET pvp_rating=?, pvp_rank=?, pvp_wins=? WHERE user_id=?",
                (w_new_rating, w_new_rank, w_wins, winner_id)
            )
            await db.execute(
                "UPDATE players SET pvp_rating=?, pvp_rank=?, pvp_losses=? WHERE user_id=?",
                (l_new_rating, l_new_rank, l_losses, loser_id)
            )
            # Also increment regular wins for achievements
            await db.execute("UPDATE players SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
            await db.execute("UPDATE players SET losses = losses + 1 WHERE user_id = ?", (loser_id,))
            await db.commit()

        winner_name = interaction.user.display_name if winner_id == uid_c else opponent.display_name
        loser_name  = opponent.display_name if winner_id == uid_c else interaction.user.display_name
        w_prev_rank = c_rank if winner_id == uid_c else o_rank
        rank_up     = w_prev_rank != w_new_rank and w_new_rank != "unranked"

        log_str = "\n".join(log[-6:])  # last 6 turns
        result_embed = discord.Embed(
            title=f"🏅 Ranked Result — {winner_name} wins!",
            description=f"*...{log_str}*",
            color=COLORS["legendary"]
        )
        result_embed.add_field(
            name=f"🏆 {winner_name}",
            value=(
                f"{RANK_EMOJI.get(w_new_rank,'⬜')} `{w_new_rank.capitalize()}`\n"
                f"`{w_row.get('pvp_rating',1000)}` → `{w_new_rating}` RP (+{delta})\n"
                + (f"🎉 **Rank Up!** → {w_new_rank.capitalize()}!" if rank_up else "")
            ),
            inline=True
        )
        result_embed.add_field(
            name=f"💀 {loser_name}",
            value=(
                f"{RANK_EMOJI.get(l_new_rank,'⬜')} `{l_new_rank.capitalize()}`\n"
                f"`{l_row.get('pvp_rating',1000)}` → `{l_new_rating}` RP (-{delta})"
            ),
            inline=True
        )
        result_embed.set_footer(text=f"Season {CURRENT_SEASON} · /ranked_leaderboard to see standings")
        await msg.edit(embed=result_embed, view=None)

        # ── Quest & achievement tracking ───────────────────────────────────
        w_quests = await track_quest_event(winner_id, "battle_win")
        await track_quest_event(winner_id, "battle_win")  # counts for regular quests too
        if w_total_g == 1:  # first ranked win
            await unlock_simple_achievement(winner_id, "first_ranked_win")
        w_unlocked = await check_achievements(winner_id)
        l_unlocked = await check_achievements(loser_id)
        if w_quests and interaction.channel:
            await notify_quest_completions(interaction.channel, w_quests)
        winner_member = interaction.user if winner_id == uid_c else opponent
        loser_member  = opponent if winner_id == uid_c else interaction.user
        if w_unlocked and interaction.channel:
            await notify_unlocks(interaction.channel, winner_member, w_unlocked)
        if l_unlocked and interaction.channel:
            await notify_unlocks(interaction.channel, loser_member, l_unlocked)

    # ── /ranked_leaderboard ────────────────────────────────────────────────
    @app_commands.command(name="ranked_leaderboard", description="View the ranked PvP leaderboard 🏆")
    async def ranked_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT username, pvp_rating, pvp_rank, pvp_wins, pvp_losses "
                "FROM players WHERE pvp_rank != 'unranked' AND pvp_wins + pvp_losses >= 5 "
                "ORDER BY pvp_rating DESC LIMIT 15"
            ) as c:
                rows = [dict(r) for r in await c.fetchall()]

        if not rows:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No ranked players yet this season. Use `/ranked` to start!",
                color=COLORS["info"]
            ))

        embed = discord.Embed(
            title=f"🏅 Ranked Leaderboard — Season {CURRENT_SEASON}",
            color=COLORS["legendary"]
        )
        medals = ["🥇","🥈","🥉"]
        lines = []
        for i, row in enumerate(rows):
            medal   = medals[i] if i < 3 else f"`{i+1}.`"
            r_emoji = RANK_EMOJI.get(row["pvp_rank"], "⬜")
            w, l    = row["pvp_wins"] or 0, row["pvp_losses"] or 0
            wr      = f"{int(w/(w+l)*100)}%" if w+l > 0 else "—"
            lines.append(
                f"{medal} **{row['username']}** {r_emoji} `{row['pvp_rank'].capitalize()}`\n"
                f"   `{row['pvp_rating']} RP` · {w}W/{l}L ({wr})"
            )
        embed.description = "\n".join(lines)
        embed.set_footer(text="Season resets every 4 weeks · /ranked to play")
        await interaction.followup.send(embed=embed)

    # ── /rank ──────────────────────────────────────────────────────────────
    @app_commands.command(name="rank", description="View your current ranked PvP rating and stats 📊")
    @app_commands.describe(player="Player to look up (leave blank for yourself)")
    async def rank(self, interaction: discord.Interaction, player: discord.Member = None):
        await interaction.response.defer()
        target    = player or interaction.user
        p_data    = await get_or_create_player(target.id, str(target))
        rating    = p_data.get("pvp_rating", 1000)
        rank_name = p_data.get("pvp_rank", "unranked")
        wins      = p_data.get("pvp_wins", 0) or 0
        losses    = p_data.get("pvp_losses", 0) or 0
        total     = wins + losses
        wr        = f"{int(wins/total*100)}%" if total > 0 else "—"
        placements_left = max(0, PLACEMENT_COUNT - total)
        r_emoji   = RANK_EMOJI.get(rank_name, "⬜")

        # Find rank info
        rank_info = next((r for r in RANKS if r[0] == rank_name), RANKS[0])
        _, lo, hi, _, flavor = rank_info

        embed = discord.Embed(
            title=f"{r_emoji} {target.display_name} — {rank_name.capitalize()}",
            description=f"*{flavor}*",
            color=COLORS["legendary"]
        )
        embed.add_field(name="Rating",   value=f"`{rating} RP`",       inline=True)
        embed.add_field(name="Record",   value=f"`{wins}W / {losses}L`", inline=True)
        embed.add_field(name="Win Rate", value=f"`{wr}`",               inline=True)

        if placements_left > 0:
            embed.add_field(
                name="📋 Placements",
                value=f"*{placements_left} match{'es' if placements_left != 1 else ''} left to receive your rank.*",
                inline=False
            )
        elif rank_name != "diamond" and hi != 99999:
            next_rank = RANKS[RANKS.index(rank_info) + 1]
            rp_needed = next_rank[1] - rating
            embed.add_field(
                name=f"Next Rank: {next_rank[3]} {next_rank[0].capitalize()}",
                value=f"`{rp_needed} RP` needed",
                inline=False
            )
        else:
            embed.add_field(name="✨ Peak", value="*You're at the top.*", inline=False)

        embed.set_footer(text=f"Season {CURRENT_SEASON} · /ranked to battle · /ranked_leaderboard for standings")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ranked(bot))
