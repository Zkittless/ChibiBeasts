from utils import db as storage
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
        your_beast_id="Your collection number to offer",
        their_beast_id="Their collection number you want (omit to give a gift)",
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

        if gold_offer < 0:
            return await interaction.followup.send('Gold offered cannot be negative.', ephemeral=True)

        if member.id == interaction.user.id:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't trade with yourself!", color=COLORS["error"]
            ))

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE player_number = ? AND user_id = ?",
                (your_beast_id, interaction.user.id)
            ) as c:
                your_beast = await c.fetchone()

            if not your_beast:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ That beast isn't in your collection!", color=COLORS["error"]
                ))

            your_beast = dict(your_beast)

            # Can't trade altered divines
            if your_beast.get("is_altered_divine") or your_beast["rarity"] == "altered_divine":
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ **Altered Divine** beasts are soulbound and cannot be traded!",
                    color=COLORS["error"]
                ))

            their_beast = None
            if their_beast_id:
                async with db.execute(
                    "SELECT * FROM player_beasts WHERE player_number = ? AND user_id = ?",
                    (their_beast_id, member.id)
                ) as c:
                    their_beast = await c.fetchone()
                if not their_beast:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ **{member.display_name}** doesn't have that beast!", color=COLORS["error"]
                    ))
                their_beast = dict(their_beast)

            from utils.transfers import _transferable
            try:
                await _transferable(db, interaction.user.id, your_beast['id'])
                if their_beast:
                    await _transferable(db, member.id, their_beast['id'])
            except ValueError as error:
                return await interaction.followup.send(str(error), ephemeral=True)

        player = await get_player(interaction.user.id)
        if not player or (gold_offer > 0 and player["gold"] < gold_offer):
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You don't have enough gold! You have `{(player or {}).get('gold', 0):,}`.", color=COLORS["error"]
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
            desc += "**Wants:** Nothing in return — this is a gift\n"

        desc += f"\nEquipped armor and bound runes travel with each companion.\n*{member.mention}, do you accept?*"

        class TradeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.message: discord.Message | None = None
                self.handled = False

            def _disable_all(self):
                for item in self.children:
                    item.disabled = True

            async def on_timeout(self):
                self.handled = True
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
                if self.handled:
                    return await inv_interaction.response.send_message('This offer is already closed.', ephemeral=True)
                self.handled = True
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv_interaction.response.edit_message(view=self)

                from utils.transfers import complete_trade
                try:
                    result = await complete_trade(
                        interaction.user.id, member.id, your_beast['id'],
                        their_beast['id'] if their_beast else None, gold_offer,
                        offer_key=f'trade-offer:{interaction.id}',
                    )
                except ValueError as error:
                    return await inv_interaction.followup.send(str(error), ephemeral=True)

                await inv_interaction.followup.send(embed=discord.Embed(
                    title="🤝 Trade Complete!",
                    description=f"**{your_name}** has been transferred to **{member.display_name}**!",
                    color=COLORS["success"]
                ))

                # ── Progress tracking: quests + achievements for both traders ──
                sender_quests = await track_quest_event(interaction.user.id, "trade", event_key=f"trade:{result['id']}:sender")
                receiver_quests = await track_quest_event(member.id, "trade", event_key=f"trade:{result['id']}:recipient")
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
                if self.handled:
                    return await inv_interaction.response.send_message('This offer is already closed.', ephemeral=True)
                self.handled = True
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
        from utils.perks_view import open_perks
        return await open_perks(interaction)

    async def perk_equip_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show perks the player owns but hasn't equipped yet."""
        perks_data = load_perks()
        all_perks = perks_data["perks"]
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT perk_id, equipped FROM player_perks WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                owned = {r["perk_id"]: r["equipped"] for r in await c.fetchall()}
        choices = []
        for pid, perk in all_perks.items():
            if pid in owned and not owned[pid] and current.lower() in perk.get("name","").lower():
                choices.append(app_commands.Choice(name=perk["name"], value=pid))
        return choices[:25]

    @app_commands.command(name="perk_equip", description="Equip a perk 🎯")
    @app_commands.describe(perk_name="Perk to equip (from your collection)")
    @app_commands.autocomplete(perk_name=perk_equip_autocomplete)
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

        from utils.gameplay import equip_perk
        try:used,total=await equip_perk(interaction.user.id,perk_id)
        except ValueError as error:return await interaction.followup.send(str(error))
        await interaction.followup.send(f"{perk['name']} equipped. {used}/{total} slots used.")

        perk_unlocked = await unlock_simple_achievement(interaction.user.id, "first_perk")
        if perk_unlocked:
            await notify_unlocks(interaction.channel, interaction.user, ["first_perk"])

    async def perk_unequip_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show only currently equipped perks."""
        perks_data = load_perks()
        all_perks = perks_data["perks"]
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT perk_id FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                equipped = {r["perk_id"] for r in await c.fetchall()}
        choices = []
        for pid in equipped:
            perk = all_perks.get(pid)
            if perk and current.lower() in perk.get("name","").lower():
                choices.append(app_commands.Choice(name=perk["name"], value=pid))
        return choices[:25]

    @app_commands.command(name="perk_unequip", description="Unequip a perk 🎯")
    @app_commands.describe(perk_name="Perk to unequip")
    @app_commands.autocomplete(perk_name=perk_unequip_autocomplete)
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

        from utils.perks_view import unequip_perk
        try:
            await unequip_perk(interaction.user.id, perk_id)
        except ValueError as error:
            return await interaction.followup.send(str(error))

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

        async with storage.connect() as db:
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


class PerkUse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="perk_use", description="Activate a perk's weekly ability 🎯")
    @app_commands.describe(perk_name="The perk to activate (e.g. Astral Resonance)")
    async def perk_use(self,interaction:discord.Interaction,perk_name:str):
        await interaction.response.defer(ephemeral=True)
        from utils.perks_view import activate_astral
        if 'astral' not in perk_name.lower():
            return await interaction.followup.send('Astral Resonance is the perk with a weekly active ability.', ephemeral=True)
        try:
            result = await activate_astral(interaction.user.id)
        except ValueError as error:
            result = str(error)
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Trading(bot))
    await bot.add_cog(Perks(bot))
    await bot.add_cog(PerkUse(bot))
    await bot.add_cog(Leaderboard(bot))
