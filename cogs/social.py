import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_beast_data, load_perks, get_perk_slots
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement, notify_unlocks, notify_quest_completions
)

class Trading(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="trade", description="Offer a beast trade to another trainer 🤝")
    @app_commands.describe(
        member="Trainer to trade with",
        your_beast_id="Your beast ID to offer",
        their_beast_id="Their beast ID you want (optional)",
        gold_offer="Gold to include in the offer"
    )
    async def trade(
        self, interaction: discord.Interaction,
        member: discord.Member,
        your_beast_id: int,
        their_beast_id: int = None,
        gold_offer: int = 0
    ):
        await interaction.response.defer()

        if member.id == interaction.user.id:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't trade with yourself!", color=COLORS["error"]
            ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (your_beast_id, interaction.user.id)
            ) as c:
                your_beast = await c.fetchone()

            if not your_beast:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ That beast isn't in your collection!", color=COLORS["error"]
                ))

            your_beast = dict(your_beast)

            # Can't trade altered divines
            if your_beast.get("is_altered_divine"):
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ **Altered Divine** beasts are soulbound and cannot be traded!",
                    color=COLORS["error"]
                ))

            their_beast = None
            if their_beast_id:
                async with db.execute(
                    "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                    (their_beast_id, member.id)
                ) as c:
                    their_beast = await c.fetchone()
                if not their_beast:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ **{member.display_name}** doesn't have that beast!", color=COLORS["error"]
                    ))
                their_beast = dict(their_beast)

        player = await get_player(interaction.user.id)
        if gold_offer > 0 and player["gold"] < gold_offer:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You don't have enough gold! You have `{player['gold']:,}`.", color=COLORS["error"]
            ))

        your_beast_data = get_beast_data(your_beast["beast_id"])
        your_name = your_beast.get("nickname") or your_beast_data["name"]

        desc = (
            f"**{interaction.user.display_name}** wants to trade with **{member.display_name}**!\n\n"
            f"**Offering:** {RARITY_EMOJI.get(your_beast['rarity'],'⚪')} {your_name} (Lv.{your_beast['level']})"
            + (f" + `{gold_offer:,}` 💰" if gold_offer > 0 else "") + "\n"
        )

        if their_beast:
            their_beast_data = get_beast_data(their_beast["beast_id"])
            their_name = their_beast.get("nickname") or their_beast_data["name"]
            desc += f"**Wants:** {RARITY_EMOJI.get(their_beast['rarity'],'⚪')} {their_name} (Lv.{their_beast['level']})\n"
        else:
            desc += "**Wants:** Any beast in return\n"

        desc += f"\n*{member.mention}, do you accept?*"

        class TradeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.message: discord.Message | None = None

            def _disable_all(self):
                for item in self.children:
                    item.disabled = True

            async def on_timeout(self):
                self._disable_all()
                if self.message:
                    try:
                        await self.message.edit(
                            embed=discord.Embed(
                                description="⌛ This trade offer expired. Use `/trade` to send a new one.",
                                color=COLORS["info"],
                            ),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

            async def on_error(self, interaction: discord.Interaction, error: Exception, item):
                import logging
                logging.getLogger("chibibeasts.trade").exception("TradeView error", exc_info=error)
                msg = "✦ Something went wrong with this trade — please try again."
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(msg, ephemeral=True)
                except discord.HTTPException:
                    pass

            @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success, emoji="🤝")
            async def accept(self, inv_interaction: discord.Interaction, button: discord.ui.Button):
                if inv_interaction.user.id != member.id:
                    return await inv_interaction.response.send_message("This trade isn't for you!", ephemeral=True)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv_interaction.response.edit_message(view=self)

                # Re-validate everything fresh, inside one connection, right before
                # committing — prevents double-spend / double-click / stale-state exploits
                # where gold or beasts moved in the time between the offer and the accept.
                async with aiosqlite.connect("db/chibibeast.db") as db:
                    db.row_factory = aiosqlite.Row

                    # Re-check the offered beast is still owned by the sender and untraded
                    async with db.execute(
                        "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                        (your_beast_id, interaction.user.id)
                    ) as c:
                        fresh_your_beast = await c.fetchone()
                    if not fresh_your_beast:
                        return await inv_interaction.followup.send(embed=discord.Embed(
                            description="✦ This trade is no longer valid — the offered beast has moved!",
                            color=COLORS["error"]
                        ))

                    # Re-check the requested beast (if any) is still owned by the receiver
                    if their_beast_id:
                        async with db.execute(
                            "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                            (their_beast_id, member.id)
                        ) as c:
                            fresh_their_beast = await c.fetchone()
                        if not fresh_their_beast:
                            return await inv_interaction.followup.send(embed=discord.Embed(
                                description="✦ This trade is no longer valid — the requested beast has moved!",
                                color=COLORS["error"]
                            ))

                    # Re-check sender's gold balance fresh, right before spending it
                    if gold_offer > 0:
                        async with db.execute(
                            "SELECT gold FROM players WHERE user_id = ?", (interaction.user.id,)
                        ) as c:
                            sender_row = await c.fetchone()
                        if not sender_row or sender_row["gold"] < gold_offer:
                            return await inv_interaction.followup.send(embed=discord.Embed(
                                description=f"✦ Trade failed — **{interaction.user.display_name}** no longer has enough gold!",
                                color=COLORS["error"]
                            ))

                    # All checks passed — perform the transfer atomically
                    await db.execute(
                        "UPDATE player_beasts SET user_id = ?, is_active = 0 WHERE id = ? AND user_id = ?",
                        (member.id, your_beast_id, interaction.user.id)
                    )
                    if their_beast_id:
                        await db.execute(
                            "UPDATE player_beasts SET user_id = ?, is_active = 0 WHERE id = ? AND user_id = ?",
                            (interaction.user.id, their_beast_id, member.id)
                        )
                    if gold_offer > 0:
                        await db.execute(
                            "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
                            (gold_offer, interaction.user.id, gold_offer)
                        )
                        await db.execute(
                            "UPDATE players SET gold = gold + ? WHERE user_id = ?",
                            (gold_offer, member.id)
                        )
                    await db.commit()

                await inv_interaction.followup.send(embed=discord.Embed(
                    title="🤝 Trade Complete!",
                    description=f"**{your_name}** has been transferred to **{member.display_name}**!",
                    color=COLORS["success"]
                ))

                # ── Progress tracking: quests + achievements for both traders ──
                sender_quests = await track_quest_event(interaction.user.id, "trade")
                receiver_quests = await track_quest_event(member.id, "trade")
                sender_unlocked = await unlock_simple_achievement(interaction.user.id, "first_trade")
                receiver_unlocked = await unlock_simple_achievement(member.id, "first_trade")
                # Trading changes beast counts for both parties, which can also
                # cross stat-based achievement thresholds (e.g. collector_10/25)
                sender_stat_unlocked = await check_achievements(interaction.user.id)
                receiver_stat_unlocked = await check_achievements(member.id)
                await notify_quest_completions(inv_interaction.channel, sender_quests + receiver_quests)
                if sender_unlocked:
                    await notify_unlocks(inv_interaction.channel, interaction.user, ["first_trade"])
                if receiver_unlocked:
                    await notify_unlocks(inv_interaction.channel, member, ["first_trade"])
                await notify_unlocks(inv_interaction.channel, interaction.user, sender_stat_unlocked)
                await notify_unlocks(inv_interaction.channel, member, receiver_stat_unlocked)

            @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
            async def decline(self, inv_interaction: discord.Interaction, button: discord.ui.Button):
                if inv_interaction.user.id != member.id:
                    return await inv_interaction.response.send_message("This isn't for you!", ephemeral=True)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv_interaction.response.edit_message(
                    embed=discord.Embed(description="❌ Trade declined.", color=COLORS["error"]),
                    view=self
                )

        embed = discord.Embed(title="🤝 Trade Offer!", description=desc, color=COLORS["legendary"])
        embed.set_footer(text="Trade expires in 2 minutes")
        view = TradeView()
        view.message = await interaction.followup.send(embed=embed, view=view)


class Perks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="perks", description="View and manage your trainer perks 🎯")
    async def perks(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        perks_data = load_perks()
        all_perks = perks_data["perks"]

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ?", (interaction.user.id,)
            ) as c:
                owned_perks = {r["perk_id"]: dict(r) for r in await c.fetchall()}

        total_slots = get_perk_slots(player["level"])
        used_slots = sum(
            all_perks[p["perk_id"]]["slot_cost"]
            for p in owned_perks.values()
            if p["equipped"] and p["perk_id"] in all_perks
        )

        embed = discord.Embed(
            title="🎯 Your Perks",
            description=f"**Slots:** `{used_slots}/{total_slots}` used | Unlock more by leveling up!",
            color=COLORS["epic"]
        )

        if not owned_perks:
            embed.add_field(
                name="No Perks Yet!",
                value="Perks can be obtained from the shop, events, and achievements.",
                inline=False
            )
        else:
            for perk_id, owned in owned_perks.items():
                perk = all_perks.get(perk_id)
                if not perk:
                    continue
                rarity_emoji = RARITY_EMOJI.get(perk["rarity"], "⚪")
                equipped = "✅ **EQUIPPED**" if owned["equipped"] else "➖ Unequipped"
                embed.add_field(
                    name=f"{rarity_emoji} {perk['name']} [{perk['slot_cost']} slots] — {equipped}",
                    value=f"*{perk['flavor']}*\n{perk['description']}",
                    inline=False
                )

        embed.set_footer(text="Use /perk_equip <name> or /perk_unequip <name> to manage")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="perk_equip", description="Equip a perk 🎯")
    @app_commands.describe(perk_name="Name of the perk to equip")
    async def perk_equip(self, interaction: discord.Interaction, perk_name: str):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        perks_data = load_perks()
        all_perks = perks_data["perks"]

        perk_id = perk_name.lower().replace(" ", "_").replace("'", "").replace("-", "_")
        perk = all_perks.get(perk_id)
        if not perk:
            matches = [
                (key, p) for key, p in all_perks.items()
                if perk_name.lower() in p.get("name", "").lower()
            ]
            if matches:
                # Use the dict key as the canonical perk_id rather than trusting an
                # "id" field inside the perk object, since load_perks()'s schema may
                # not always nest an id — this avoids a KeyError on a fuzzy match.
                perk_id, perk = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Perk `{perk_name}` not found!", color=COLORS["error"]
                ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND perk_id = ?",
                (interaction.user.id, perk_id)
            ) as c:
                owned = await c.fetchone()

            if not owned:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ You don't own this perk!", color=COLORS["error"]
                ))

            async with db.execute(
                "SELECT perk_id FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                equipped_perks = [dict(r) for r in await c.fetchall()]

        total_slots = get_perk_slots(player["level"])
        used_slots = sum(all_perks[p["perk_id"]]["slot_cost"] for p in equipped_perks if p["perk_id"] in all_perks)
        if used_slots + perk["slot_cost"] > total_slots:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Not enough perk slots! Used: `{used_slots}/{total_slots}`. This perk costs `{perk['slot_cost']}` slots.",
                color=COLORS["error"]
            ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute(
                "UPDATE player_perks SET equipped = 1 WHERE user_id = ? AND perk_id = ?",
                (interaction.user.id, perk_id)
            )
            await db.commit()

        await interaction.followup.send(embed=discord.Embed(
            description=f"✦ **{perk['name']}** equipped! `{used_slots + perk['slot_cost']}/{total_slots}` slots used.",
            color=COLORS["success"]
        ))

        perk_unlocked = await unlock_simple_achievement(interaction.user.id, "first_perk")
        if perk_unlocked:
            await notify_unlocks(interaction.channel, interaction.user, ["first_perk"])

    @app_commands.command(name="perk_unequip", description="Unequip a perk 🎯")
    @app_commands.describe(perk_name="Name of the perk to unequip")
    async def perk_unequip(self, interaction: discord.Interaction, perk_name: str):
        await interaction.response.defer()
        perks_data = load_perks()
        all_perks = perks_data["perks"]

        perk_id = perk_name.lower().replace(" ", "_").replace("'", "").replace("-", "_")
        perk = all_perks.get(perk_id)
        if not perk:
            matches = [
                (key, p) for key, p in all_perks.items()
                if perk_name.lower() in p.get("name", "").lower()
            ]
            if matches:
                perk_id, perk = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Perk `{perk_name}` not found!", color=COLORS["error"]
                ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute(
                "UPDATE player_perks SET equipped = 0 WHERE user_id = ? AND perk_id = ?",
                (interaction.user.id, perk_id)
            )
            await db.commit()

        await interaction.followup.send(embed=discord.Embed(
            description=f"✦ **{perk['name']}** unequipped!",
            color=COLORS["success"]
        ))


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="View the ChibiBeasts leaderboards 🏆")
    @app_commands.choices(category=[
        app_commands.Choice(name="🏆 Top Trainers (Level)", value="level"),
        app_commands.Choice(name="⚔️ Best Battlers (Wins)", value="wins"),
        app_commands.Choice(name="💰 Richest Trainers (Gold)", value="gold"),
        app_commands.Choice(name="🐾 Best Collectors (Beasts)", value="beasts"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, category: str = "level"):
        await interaction.response.defer()

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            if category == "level":
                query = "SELECT username, level, exp FROM players ORDER BY level DESC, exp DESC LIMIT 10"
                title = "🏆 Top Trainers by Level"
            elif category == "wins":
                query = "SELECT username, wins, losses FROM players ORDER BY wins DESC LIMIT 10"
                title = "⚔️ Best Battlers"
            elif category == "gold":
                query = "SELECT username, gold FROM players ORDER BY gold DESC LIMIT 10"
                title = "💰 Richest Trainers"
            else:
                query = """
                    SELECT p.username, COUNT(pb.id) as beast_count
                    FROM players p LEFT JOIN player_beasts pb ON p.user_id = pb.user_id
                    GROUP BY p.user_id ORDER BY beast_count DESC LIMIT 10
                """
                title = "🐾 Best Collectors"

            async with db.execute(query) as c:
                rows = [dict(r) for r in await c.fetchall()]

        embed = discord.Embed(title=title, color=COLORS["legendary"])
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            if category == "level":
                lines.append(f"{medal} **{row['username']}** — Level `{row['level']}`")
            elif category == "wins":
                lines.append(f"{medal} **{row['username']}** — `{row['wins']}` victories / `{row['losses']}` lessons")
            elif category == "gold":
                lines.append(f"{medal} **{row['username']}** — `{row['gold']:,}` 💰")
            else:
                lines.append(f"{medal} **{row['username']}** — `{row.get('beast_count', 0)}` beasts")

        embed.description = "\n".join(lines) if lines else "No data yet!"
        embed.set_footer(text="ChibiBeasts 🐾  •  Updated in real time")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Trading(bot))
    await bot.add_cog(Perks(bot))
    await bot.add_cog(Leaderboard(bot))
