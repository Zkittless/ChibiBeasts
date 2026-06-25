import os
"""
Developer commands for ChibiBeasts.

All commands in this cog require the role ID set in the DEV_ROLE_ID
environment variable. Set it in Railway (or .env locally) to your
tester/admin role ID. If DEV_ROLE_ID is not set, the commands are
disabled entirely.

These commands are intentionally not listed in /help so they stay
invisible to regular players.
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import json
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_beast_data, load_beasts, load_items, add_item,
    calc_player_exp_for_level, add_beast_to_player,
    apply_beast_levelup, get_beast_by_player_number
)
from utils.theme import COLORS, RARITY_EMOJI

DB_PATH = "db/chibibeast.db"


def dev_only():
    """Check: interaction user must be the bot owner (OWNER_ID env var)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        owner_id_str = os.getenv("OWNER_ID", "")
        if not owner_id_str:
            await interaction.response.send_message(
                "✦ Dev commands are disabled — set `OWNER_ID` in environment variables.",
                ephemeral=True
            )
            return False
        try:
            owner_id = int(owner_id_str)
        except ValueError:
            await interaction.response.send_message(
                "✦ `OWNER_ID` is not a valid integer.", ephemeral=True
            )
            return False
        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "✦ Dev commands are owner-only.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


class Dev(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    dev_group = app_commands.Group(
        name="dev",
        description="Developer tools 🛠️"
    )

    # ── /dev give_gold ────────────────────────────────────────────────────
    @dev_group.command(name="give_gold", description="Give gold to a player")
    @app_commands.describe(member="Target player", amount="Amount of gold")
    @dev_only()
    async def give_gold(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=True)
        player = await get_or_create_player(member.id, str(member))
        await update_player(member.id, gold=player["gold"] + amount)
        await interaction.followup.send(
            f"✅ Gave **{amount:,} gold** to **{member.display_name}**. "
            f"New balance: `{player['gold'] + amount:,}`",
            ephemeral=True
        )

    # ── /dev give_item ────────────────────────────────────────────────────
    @dev_group.command(name="give_item", description="Give an item to a player")
    @app_commands.describe(member="Target player", item_id="Item ID (e.g. brambleberries)", quantity="Quantity")
    @dev_only()
    async def give_item(self, interaction: discord.Interaction, member: discord.Member, item_id: str, quantity: int = 1):
        await interaction.response.defer(ephemeral=True)
        items_data = load_items()
        item = items_data.get(item_id)
        if not item:
            # Try fuzzy match
            matches = [i for i in items_data.values() if item_id.lower() in i["name"].lower()]
            if matches:
                item = matches[0]
                item_id = item["id"]
            else:
                return await interaction.followup.send(
                    f"✦ Item `{item_id}` not found. Check `/shop` for valid IDs.", ephemeral=True
                )
        await get_or_create_player(member.id, str(member))
        await add_item(member.id, item_id, quantity)
        await interaction.followup.send(
            f"✅ Gave **{quantity}x {item['name']}** to **{member.display_name}**.",
            ephemeral=True
        )

    # ── /dev give_material ────────────────────────────────────────────────
    @dev_group.command(name="give_material", description="Give a crafting material to a player")
    @app_commands.describe(member="Target player", material_id="Material ID (e.g. pixie_silk)", quantity="Quantity")
    @dev_only()
    async def give_material(self, interaction: discord.Interaction, member: discord.Member, material_id: str, quantity: int = 1):
        await interaction.response.defer(ephemeral=True)
        with open("data/materials.json") as f:
            mats = json.load(f)["materials"]
        mat = mats.get(material_id)
        if not mat:
            matches = [m for m in mats.values() if material_id.lower() in m["name"].lower()]
            if matches:
                mat = matches[0]
                material_id = mat["id"]
            else:
                return await interaction.followup.send(
                    f"✦ Material `{material_id}` not found. Check `/recipes` for valid IDs.", ephemeral=True
                )
        await get_or_create_player(member.id, str(member))
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, quantity FROM player_materials WHERE user_id = ? AND material_id = ?",
                (member.id, material_id)
            ) as c:
                existing = await c.fetchone()
            if existing:
                await db.execute(
                    "UPDATE player_materials SET quantity = quantity + ? WHERE id = ?",
                    (quantity, existing[0])
                )
            else:
                await db.execute(
                    "INSERT INTO player_materials (user_id, material_id, quantity) VALUES (?, ?, ?)",
                    (member.id, material_id, quantity)
                )
            await db.commit()
        await interaction.followup.send(
            f"✅ Gave **{quantity}x {mat['name']}** to **{member.display_name}**.",
            ephemeral=True
        )

    # ── /dev give_beast ───────────────────────────────────────────────────
    @dev_group.command(name="give_beast", description="Add any beast to a player's collection")
    @app_commands.describe(member="Target player", beast_id="Beast ID (e.g. prismite, twine)")
    @dev_only()
    async def give_beast(self, interaction: discord.Interaction, member: discord.Member, beast_id: str):
        await interaction.response.defer(ephemeral=True)
        all_beasts = load_beasts()
        beast = all_beasts.get(beast_id)
        if not beast:
            matches = [(k, b) for k, b in all_beasts.items() if beast_id.lower() in b["name"].lower()]
            if matches:
                beast_id, beast = matches[0]
            else:
                return await interaction.followup.send(
                    f"✦ Beast `{beast_id}` not found. Check `/codex` for valid IDs.", ephemeral=True
                )
        await get_or_create_player(member.id, str(member))
        await add_beast_to_player(member.id, {**beast, "caught_from": "dev"})
        rarity_emoji = RARITY_EMOJI.get(beast["rarity"], "⚪")
        await interaction.followup.send(
            f"✅ Added {rarity_emoji} **{beast['name']}** to **{member.display_name}**'s collection.",
            ephemeral=True
        )

    # ── /dev give_ouroboros ───────────────────────────────────────────────
    @app_commands.command(name="give_ouroboros", description="[DEV] Grant Desync the Infinite — your personal beast 👑")
    @app_commands.describe(member="Target player (should be you)")
    @dev_only()
    async def give_ouroboros(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        all_beasts = load_beasts()
        beast = all_beasts.get("ouroboros")
        if not beast:
            return await interaction.followup.send("✦ Ouroboros not found in beast data!", ephemeral=True)
        await get_or_create_player(member.id, str(member))
        await add_beast_to_player(member.id, {**beast, "caught_from": "dev"})
        await interaction.followup.send(
            embed=discord.Embed(
                title="👑 The Loop Is Complete",
                description=(
                    f"*The sky tears open without warning.*\n\n"
                    f"**Desync the Infinite** has been granted to **{member.display_name}**.\n\n"
                    f"*It simply was always here. The world is only now noticing.*"
                ),
                color=0xFF0055
            ),
            ephemeral=True
        )
    @dev_group.command(name="give_shards", description="Give Celestial Shards to a player")
    @app_commands.describe(member="Target player", amount="Amount of shards")
    @dev_only()
    async def give_shards(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=True)
        player = await get_or_create_player(member.id, str(member))
        await update_player(member.id, celestial_shards=player["celestial_shards"] + amount)
        await interaction.followup.send(
            f"✅ Gave **{amount} Celestial Shards** to **{member.display_name}**.",
            ephemeral=True
        )

    # ── /dev give_guild_tokens ────────────────────────────────────────────
    @dev_group.command(name="give_guild_tokens", description="Add guild tokens to a player's guild")
    @app_commands.describe(member="Any member of the target guild", amount="Tokens to add")
    @dev_only()
    async def give_guild_tokens(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.guild_id, g.name, g.guild_tokens FROM guild_members gm "
                "JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (member.id,)
            ) as c:
                row = await c.fetchone()
            if not row:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** is not in a guild.", ephemeral=True
                )
            new_total = row["guild_tokens"] + amount
            await db.execute(
                "UPDATE guilds SET guild_tokens = guild_tokens + ? WHERE id = ?",
                (amount, row["guild_id"])
            )
            await db.commit()
        await interaction.followup.send(
            f"✅ Added **{amount} guild tokens** to **{row['name']}**. "
            f"New total: `{new_total}`",
            ephemeral=True
        )

    # ── /dev set_guild_tokens ─────────────────────────────────────────────
    @dev_group.command(name="set_guild_tokens", description="Set a guild's token count directly")
    @app_commands.describe(member="Any member of the target guild", amount="Token amount to set")
    @dev_only()
    async def set_guild_tokens(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.guild_id, g.name FROM guild_members gm "
                "JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (member.id,)
            ) as c:
                row = await c.fetchone()
            if not row:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** is not in a guild.", ephemeral=True
                )
            await db.execute(
                "UPDATE guilds SET guild_tokens = ? WHERE id = ?",
                (amount, row["guild_id"])
            )
            await db.commit()
        await interaction.followup.send(
            f"✅ Set **{row['name']}**'s guild tokens to **{amount}**.",
            ephemeral=True
        )

    # ── /dev reset_sanctuary ──────────────────────────────────────────────
    @dev_group.command(name="reset_sanctuary", description="Reset a guild's sanctuary upgrades")
    @app_commands.describe(member="Any member of the target guild")
    @dev_only()
    async def reset_sanctuary(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.guild_id, g.name FROM guild_members gm "
                "JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (member.id,)
            ) as c:
                row = await c.fetchone()
            if not row:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** is not in a guild.", ephemeral=True
                )
            await db.execute(
                "UPDATE guild_sanctuary SET fairy_garden = 0, gnome_forge = 0, celestial_observatory = 0 "
                "WHERE guild_id = ?",
                (row["guild_id"],)
            )
            await db.commit()
        await interaction.followup.send(
            f"✅ Reset all sanctuary upgrades for **{row['name']}**.",
            ephemeral=True
        )

    # ── /dev hatch_egg ────────────────────────────────────────────────────
    @dev_group.command(name="hatch_egg", description="Instantly hatch a player's oldest incubating egg")
    @app_commands.describe(member="Target player", egg_id="Specific egg ID to hatch (leave blank for oldest)")
    @dev_only()
    async def hatch_egg(self, interaction: discord.Interaction, member: discord.Member, egg_id: int = None):
        await interaction.response.defer(ephemeral=True)
        from cogs.world import EGGS, roll_egg_rarity, pick_beast_for_rarity
        from utils.db import add_beast_to_player
        from utils.theme import RARITY_LABEL, TYPE_EMOJI

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if egg_id:
                async with db.execute(
                    "SELECT * FROM incubating_eggs WHERE id = ? AND user_id = ? AND hatched = 0",
                    (egg_id, member.id)
                ) as c:
                    row = await c.fetchone()
            else:
                async with db.execute(
                    "SELECT * FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY ready_at ASC LIMIT 1",
                    (member.id,)
                ) as c:
                    row = await c.fetchone()

            if not row:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** has no incubating eggs.", ephemeral=True
                )
            row = dict(row)

            egg_def = EGGS.get(row["egg_type"], {})
            rarity  = roll_egg_rarity(row["egg_type"])
            beast   = pick_beast_for_rarity(rarity, egg_def)

            if not beast:
                return await interaction.followup.send(
                    "✦ Couldn't roll a beast for this egg type.", ephemeral=True
                )

            beast_row_id = await add_beast_to_player(
                member.id, {**beast, "caught_from": "incubation"}
            )
            await db.execute("UPDATE incubating_eggs SET hatched = 1 WHERE id = ?", (row["id"],))
            await db.commit()

        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        type_emoji   = TYPE_EMOJI.get(beast.get("type", ""), "❓")
        await interaction.followup.send(
            f"✅ Hatched **{row['egg_name']}** for **{member.display_name}**:\n"
            f"{rarity_emoji} **{beast['name']}** — {type_emoji} {beast.get('type','?').capitalize()} "
            f"({RARITY_LABEL.get(rarity, rarity)}) — Beast ID #{beast_row_id}",
            ephemeral=True
        )

    # ── /dev set_level ────────────────────────────────────────────────────
    @dev_group.command(name="set_level", description="Set a player's trainer level")
    @app_commands.describe(member="Target player", level="Target level")
    @dev_only()
    async def set_level(self, interaction: discord.Interaction, member: discord.Member, level: int):
        await interaction.response.defer(ephemeral=True)
        if level < 1 or level > 100:
            return await interaction.followup.send("✦ Level must be between 1 and 100.", ephemeral=True)
        await get_or_create_player(member.id, str(member))
        await update_player(member.id, level=level, exp=0)
        await interaction.followup.send(
            f"✅ Set **{member.display_name}**'s trainer level to **{level}**.",
            ephemeral=True
        )

    # ── /dev reset ────────────────────────────────────────────────────────
    @dev_group.command(name="reset", description="Fully reset a player's account (cannot be undone)")
    @app_commands.describe(member="Player to reset")
    @dev_only()
    async def reset(self, interaction: discord.Interaction, member: discord.Member):
        # Don't defer — need to send the confirmation view with a non-ephemeral response
        # so the buttons work. Use ephemeral anyway for safety.

        class ConfirmReset(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.confirmed = False

            @discord.ui.button(label="Yes, wipe account", style=discord.ButtonStyle.danger, emoji="💥")
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("Not for you.", ephemeral=True)
                self.confirmed = True
                self.stop()
                for item in self.children:
                    item.disabled = True
                await btn_interaction.response.edit_message(view=self)

                async with aiosqlite.connect(DB_PATH) as db:
                    uid = member.id
                    # If player was a guild leader, clean up the guild entirely
                    # (or transfer if other members exist)
                    async with db.execute(
                        "SELECT id, member_count FROM guilds WHERE leader_id = ?", (uid,)
                    ) as c:
                        led_guild = await c.fetchone()
                    if led_guild:
                        guild_id = led_guild[0]
                        member_count = led_guild[1]
                        if member_count <= 1:
                            # Only member — disband
                            await db.execute("DELETE FROM guilds WHERE id = ?", (guild_id,))
                            await db.execute("DELETE FROM guild_sanctuary WHERE guild_id = ?", (guild_id,))
                        else:
                            # Transfer to most senior remaining member
                            async with db.execute(
                                "SELECT user_id FROM guild_members WHERE guild_id = ? AND user_id != ? "
                                "ORDER BY CASE rank WHEN 'officer' THEN 0 ELSE 1 END LIMIT 1",
                                (guild_id, uid)
                            ) as c:
                                next_member = await c.fetchone()
                            if next_member:
                                await db.execute(
                                    "UPDATE guilds SET leader_id = ?, member_count = member_count - 1 WHERE id = ?",
                                    (next_member[0], guild_id)
                                )
                                await db.execute(
                                    "UPDATE guild_members SET rank = 'leader' WHERE guild_id = ? AND user_id = ?",
                                    (guild_id, next_member[0])
                                )
                            else:
                                await db.execute("DELETE FROM guilds WHERE id = ?", (guild_id,))
                                await db.execute("DELETE FROM guild_sanctuary WHERE guild_id = ?", (guild_id,))
                    # Decrement member count if they were a regular member
                    async with db.execute(
                        "SELECT guild_id FROM guild_members WHERE user_id = ?", (uid,)
                    ) as c:
                        gm = await c.fetchone()
                    if gm and not led_guild:
                        await db.execute(
                            "UPDATE guilds SET member_count = member_count - 1 WHERE id = ?",
                            (gm[0],)
                        )
                    # Wipe all player data
                    for table, col in [
                        ("player_beasts",    "user_id"),
                        ("player_inventory", "user_id"),
                        ("player_perks",     "user_id"),
                        ("player_materials", "user_id"),
                        ("player_equipment", "user_id"),
                        ("player_questline", "user_id"),
                        ("daily_quests",     "user_id"),
                        ("achievements",     "user_id"),
                        ("guild_members",    "user_id"),
                        ("raid_participants","user_id"),
                        ("altered_divines",  "caught_by"),
                        ("incubating_eggs",  "user_id"),
                        ("battles",          "challenger_id"),
                        ("battles",          "opponent_id"),
                        ("trades",           "sender_id"),
                        ("trades",           "receiver_id"),
                    ]:
                        await db.execute(f"DELETE FROM {table} WHERE {col} = ?", (uid,))
                    # Reset player row to fresh state
                    await db.execute(
                        "UPDATE players SET level=1, exp=0, gold=500, celestial_shards=10, "
                        "guild_id=NULL, guild_tokens=0, wins=0, losses=0, title=NULL, "
                        "explore_last_at=0, total_catches=0, total_gold_earned=0, "
                        "incense_active_until=0, brew_active=0, damage_multiplier=1.0, "
                        "shard_shop_week=NULL WHERE user_id = ?",
                        (uid,)
                    )
                    await db.commit()

                await btn_interaction.followup.send(
                    f"💥 **{member.display_name}**'s account has been fully reset. "
                    f"They can use `/start` to begin again.",
                    ephemeral=True
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("Not for you.", ephemeral=True)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await btn_interaction.response.edit_message(
                    content="Reset cancelled.", view=self
                )

        await interaction.response.send_message(
            f"⚠️ This will **permanently wipe all data** for **{member.display_name}** — "
            f"beasts, gold, progress, achievements, everything. Are you sure?",
            view=ConfirmReset(),
            ephemeral=True
        )

    # ── /dev reset_progress ───────────────────────────────────────────────
    @dev_group.command(name="reset_progress", description="Reset only daily quests and cooldowns (keep beasts/gold)")
    @app_commands.describe(member="Target player")
    @dev_only()
    async def reset_progress(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM daily_quests WHERE user_id = ?", (member.id,))
            await db.execute(
                "UPDATE players SET explore_last_at = 0, incense_active_until = 0 WHERE user_id = ?",
                (member.id,)
            )
            await db.commit()
        await interaction.followup.send(
            f"✅ Reset daily quests and cooldowns for **{member.display_name}**. "
            f"Beasts, gold, and progress are untouched.",
            ephemeral=True
        )

    # ── /dev info ─────────────────────────────────────────────────────────
    @dev_group.command(name="info", description="View raw account data for a player")
    @app_commands.describe(member="Target player")
    @dev_only()
    async def info(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM players WHERE user_id = ?", (member.id,)) as c:
                player = await c.fetchone()
            if not player:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** has no account yet.", ephemeral=True
                )
            player = dict(player)
            async with db.execute(
                "SELECT COUNT(*) FROM player_beasts WHERE user_id = ?", (member.id,)
            ) as c:
                beast_count = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM achievements WHERE user_id = ?", (member.id,)
            ) as c:
                ach_count = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM battles WHERE challenger_id = ? OR opponent_id = ?",
                (member.id, member.id)
            ) as c:
                battle_count = (await c.fetchone())[0]

        embed = discord.Embed(
            title=f"🛠️ Dev Info — {member.display_name}",
            color=COLORS["info"]
        )
        embed.add_field(
            name="Trainer",
            value=(
                f"Level: `{player['level']}` | EXP: `{player['exp']}`\n"
                f"Gold: `{player['gold']:,}` | Shards: `{player['celestial_shards']}`\n"
                f"Wins: `{player['wins']}` | Losses: `{player['losses']}`\n"
                f"Guild ID: `{player.get('guild_id') or 'None'}`"
            ),
            inline=False
        )
        embed.add_field(
            name="Counts",
            value=(
                f"Beasts: `{beast_count}` | Achievements: `{ach_count}`\n"
                f"Battles logged: `{battle_count}`"
            ),
            inline=False
        )
        embed.add_field(
            name="Flags",
            value=(
                f"Explore last at: `{player.get('explore_last_at', 0):.0f}`\n"
                f"Brew active: `{player.get('brew_active', 0)}`\n"
                f"Dmg multiplier: `{player.get('damage_multiplier', 1.0)}`"
            ),
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="reset_shard_shop", description="[DEV] Reset a player's shard shop weekly cooldown")
    @app_commands.describe(member="Player to reset")
    @dev_only()
    async def reset_shard_shop(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute("UPDATE players SET shard_shop_week = NULL WHERE user_id = ?", (member.id,))
            await db.commit()
        await interaction.followup.send(
            f"✅ Shard shop cooldown reset for **{member.display_name}**.", ephemeral=True
        )

    @app_commands.command(name="set_beast_level", description="[DEV] Set a beast's level directly")
    @app_commands.describe(member="Player who owns the beast", beast_number="Beast #number from /collection", level="Target level (1-50)")
    @dev_only()
    async def set_beast_level(self, interaction: discord.Interaction, member: discord.Member, beast_number: int, level: int):
        await interaction.response.defer(ephemeral=True)

        if not 1 <= level <= 50:
            return await interaction.followup.send("✦ Level must be between 1 and 50.", ephemeral=True)

        beast_row = await get_beast_by_player_number(member.id, beast_number)
        if not beast_row:
            return await interaction.followup.send(
                f"✦ Beast `#{beast_number}` not found for **{member.display_name}**.", ephemeral=True
            )

        beast_data = get_beast_data(beast_row["beast_id"]) or {}
        old_level = beast_row["level"]

        from utils.db import calc_stat_growth, DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if level > old_level:
                # Level up — apply growth
                await apply_beast_levelup(db, dict(beast_row), level, 0)
            else:
                # Level down — reset to base stats then apply growth from 1
                base = beast_data.get("base_stats", {})
                growth = calc_stat_growth({"rarity": beast_row["rarity"], "caught_from": beast_row.get("caught_from", "wild")}, max(0, level - 1))
                new_hp  = base.get("hp", 80)  + growth.get("hp", 0)
                new_atk = base.get("attack", 30) + growth.get("attack", 0)
                new_def = base.get("defense", 25) + growth.get("defense", 0)
                new_spd = base.get("speed", 30)  + growth.get("speed", 0)
                new_mana= base.get("mana", 10)   + growth.get("mana", 0)
                await db.execute("""
                    UPDATE player_beasts SET
                        level=?, exp=0, hp=?, max_hp=?,
                        attack=?, defense=?, speed=?, mana=?, max_mana=?
                    WHERE id=?
                """, (level, new_hp, new_hp, new_atk, new_def, new_spd, new_mana, new_mana, beast_row["id"]))
            await db.commit()

        name = beast_row.get("nickname") or beast_data.get("name", beast_row["beast_id"])
        emoji = RARITY_EMOJI.get(beast_row["rarity"], "⚪")
        await interaction.followup.send(
            f"✅ {emoji} **{name}** `#{beast_number}` ({member.display_name}) → Lv.{old_level} → **Lv.{level}**",
            ephemeral=True
        )

    # ── /dev reset_cooldowns ──────────────────────────────────────────────
    @dev_group.command(name="reset_cooldowns", description="[DEV] Reset explore and challenge cooldowns for a player")
    @app_commands.describe(member="Target player (defaults to you)")
    @dev_only()
    async def reset_cooldowns(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE players SET explore_last_at = 0, challenge_last_at = 0 WHERE user_id = ?",
                (target.id,)
            )
            await db.commit()
        await interaction.followup.send(
            f"✅ Explore and challenge cooldowns reset for **{target.display_name}**. Both show as ready in `/profile`.",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    import discord as _d
    home_id = os.getenv("GUILD_ID", "")
    if home_id:
        guild = _d.Object(id=int(home_id))
        await bot.add_cog(Dev(bot), guilds=[guild])
    else:
        await bot.add_cog(Dev(bot))
