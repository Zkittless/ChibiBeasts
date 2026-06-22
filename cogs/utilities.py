import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import json
import random
from datetime import datetime, timezone, timedelta
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_active_beast, get_beast_data, load_beasts,
    add_item, remove_item, apply_beast_levelup, calc_exp_for_level
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import check_achievements, unlock_simple_achievement, notify_unlocks
from utils.sanctuary import get_user_sanctuary

DB_PATH = "db/chibibeast.db"

def load_equipment():
    with open("data/equipment.json") as f:
        d = json.load(f)
    return d["equipment"], d["runes"]

def load_materials():
    with open("data/materials.json") as f:
        return json.load(f)["materials"]

# ── Shard Shop inventory ───────────────────────────────────────────────────────
SHARD_SHOP = {
    "astral_reroll": {
        "name": "🌌 Astral Reroll",
        "desc": "Guarantees your next `/hatch` produces a specific element type of your choice.",
        "cost": 15,          # was 25 — core weekly item, should be accessible
        "weekly_limit": 1,
        "type": "reroll",
    },
    "divine_compass": {
        "name": "🧭 Divine Compass",
        "desc": "Boosts the divine encounter rate in the Celestial Loom to 20% for your next 3 explores.",
        "cost": 25,          # was 40
        "weekly_limit": 1,
        "type": "explore_boost",
    },
    "loom_fragment": {
        "name": "🧵 Loom Fragment",
        "desc": "Reduces the incubation time of your oldest egg by 6 hours.",
        "cost": 10,          # was 15
        "weekly_limit": 3,
        "type": "incubation_skip",
    },
    "prism_key": {
        "name": "🔑 Prism Key",
        "desc": "Grants access to a special /explore variant in the Celestial Loom with a 30% divine rate.",
        "cost": 40,          # was 60 — premium item, intentionally more expensive
        "weekly_limit": 1,
        "type": "key",
    },
    "beast_rename_token": {
        "name": "✏️ Rename Token",
        "desc": "Rename any beast — even with special characters.",
        "cost": 10,          # was 15 — cosmetic, low barrier
        "weekly_limit": 0,
        "type": "cosmetic",
    },
    "trainer_title_reset": {
        "name": "🏷️ Title Reset",
        "desc": "Clear your current trainer title and choose from all titles you've earned.",
        "cost": 5,           # was 10 — cosmetic, should be trivial
        "weekly_limit": 0,
        "type": "cosmetic",
    },
}


class Utilities(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /equip ────────────────────────────────────────────────────────────
    @app_commands.command(name="equip", description="Equip armor or a rune to a beast ⚔️")
    @app_commands.describe(
        item_name="Name of the equipment or rune to equip",
        beast_id="ID of the beast to equip it on (from /collection)"
    )
    async def equip(self, interaction: discord.Interaction, item_name: str, beast_id: int):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        all_gear = {**equipment, **runes}

        # Match gear
        gear_id = item_name.lower().replace(" ", "_").replace("-", "_")
        gear = all_gear.get(gear_id)
        if not gear:
            matches = [(k, v) for k, v in all_gear.items() if item_name.lower() in v["name"].lower()]
            if matches:
                gear_id, gear = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ `{item_name}` not found. Check `/recipes` for craftable gear or `/shop` for runes.",
                    color=COLORS["error"]
                ))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Verify beast ownership
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                beast_row = await c.fetchone()
            if not beast_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Beast not found in your collection!", color=COLORS["error"]
                ))
            beast_row = dict(beast_row)

            # Verify ownership of gear (in player_equipment or rune in inventory)
            is_rune = gear.get("slot") == "rune"
            if is_rune:
                # Runes are bought and stored in player_inventory
                async with db.execute(
                    "SELECT quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                    (interaction.user.id, gear_id)
                ) as c:
                    inv_row = await c.fetchone()
                if not inv_row or inv_row["quantity"] < 1:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ You don't own a **{gear['name']}**! Buy one from `/shop` or earn it from raids.",
                        color=COLORS["error"]
                    ))
                # Each beast can only have one rune
                if beast_row.get("rune_id"):
                    old_rune = all_gear.get(beast_row["rune_id"], {})
                    return await interaction.followup.send(embed=discord.Embed(
                        description=(
                            f"✦ This beast already has **{old_rune.get('name', beast_row['rune_id'])}** equipped.\n"
                            f"Use `/unequip {beast_id}` first."
                        ),
                        color=COLORS["error"]
                    ))
                # Equip rune — remove from inventory, set on beast
                await db.execute(
                    "UPDATE player_inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_id = ?",
                    (interaction.user.id, gear_id)
                )
                await db.execute(
                    "DELETE FROM player_inventory WHERE user_id = ? AND item_id = ? AND quantity <= 0",
                    (interaction.user.id, gear_id)
                )
                await db.execute(
                    "UPDATE player_beasts SET rune_id = ? WHERE id = ?",
                    (gear_id, beast_id)
                )
            else:
                # Armor: check player_equipment table for ownership
                async with db.execute(
                    "SELECT id FROM player_equipment WHERE user_id = ? AND equipment_id = ? AND beast_row_id IS NULL",
                    (interaction.user.id, gear_id)
                ) as c:
                    gear_row = await c.fetchone()
                if not gear_row:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ You don't own **{gear['name']}**! Craft it with `/craft`.",
                        color=COLORS["error"]
                    ))
                # Unequip from any other beast first
                await db.execute(
                    "UPDATE player_equipment SET beast_row_id = NULL WHERE user_id = ? AND equipment_id = ?",
                    (interaction.user.id, gear_id)
                )
                await db.execute(
                    "UPDATE player_equipment SET beast_row_id = ? WHERE id = ?",
                    (beast_id, gear_row["id"])
                )

            await db.commit()

        beast_data = get_beast_data(beast_row["beast_id"]) or {}
        beast_name = beast_row.get("nickname") or beast_data.get("name", "Beast")

        effect = gear.get("effect", {})
        effect_str = " | ".join(
            f"+{v} {k.replace('_',' ').title()}" if isinstance(v, int) else str(k)
            for k, v in effect.items()
            if isinstance(v, (int, float)) and v > 0
        ) or gear.get("desc", "Effect active")

        embed = discord.Embed(
            title=f"⚔️ Equipped: {gear['name']}",
            description=(
                f"*{gear['description']}*\n\n"
                f"**Equipped on:** {beast_name}\n"
                f"**Effect:** {effect_str}\n\n"
                f"*{gear.get('lore', '')}*"
            ),
            color=COLORS.get(gear.get("rarity", "common"), COLORS["info"])
        )
        embed.set_footer(text=f"ChibiBeasts 🐾  •  Use /unequip {beast_id} to remove this gear")
        await interaction.followup.send(embed=embed)

    # ── /unequip ──────────────────────────────────────────────────────────
    @app_commands.command(name="unequip", description="Remove equipment from a beast 🛡️")
    @app_commands.describe(beast_id="ID of the beast to unequip (from /collection)")
    async def unequip(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        all_gear = {**equipment, **runes}

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                beast_row = await c.fetchone()
            if not beast_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Beast not found!", color=COLORS["error"]
                ))
            beast_row = dict(beast_row)

            removed = []

            # Unequip rune → return to inventory
            if beast_row.get("rune_id"):
                rune_id = beast_row["rune_id"]
                rune = all_gear.get(rune_id, {})
                async with db.execute(
                    "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                    (interaction.user.id, rune_id)
                ) as c:
                    inv = await c.fetchone()
                if inv:
                    await db.execute(
                        "UPDATE player_inventory SET quantity = quantity + 1 WHERE id = ?", (inv["id"],)
                    )
                else:
                    await db.execute(
                        "INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?, ?, 1)",
                        (interaction.user.id, rune_id)
                    )
                await db.execute("UPDATE player_beasts SET rune_id = NULL WHERE id = ?", (beast_id,))
                removed.append(f"🔮 {rune.get('name', rune_id)} returned to inventory")

            # Unequip armor
            async with db.execute(
                "SELECT equipment_id FROM player_equipment WHERE beast_row_id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                armor_rows = [dict(r) for r in await c.fetchall()]
            for ar in armor_rows:
                equip_data = all_gear.get(ar["equipment_id"], {})
                await db.execute(
                    "UPDATE player_equipment SET beast_row_id = NULL WHERE beast_row_id = ? AND equipment_id = ?",
                    (beast_id, ar["equipment_id"])
                )
                removed.append(f"⚔️ {equip_data.get('name', ar['equipment_id'])} unequipped")

            await db.commit()

        if not removed:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ This beast has no equipment to remove.", color=COLORS["info"]
            ))

        beast_data = get_beast_data(beast_row["beast_id"]) or {}
        beast_name = beast_row.get("nickname") or beast_data.get("name", "Beast")
        await interaction.followup.send(embed=discord.Embed(
            title=f"🛡️ Equipment Removed",
            description=f"**{beast_name}** unequipped:\n" + "\n".join(removed),
            color=COLORS["success"]
        ))

    # ── /sell ─────────────────────────────────────────────────────────────
    @app_commands.command(name="sell", description="Sell items or materials for gold 💰")
    @app_commands.describe(
        item_name="Item or material name to sell",
        quantity="How many to sell (default 1)"
    )
    async def sell(self, interaction: discord.Interaction, item_name: str, quantity: int = 1):
        await interaction.response.defer()
        if quantity < 1:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Quantity must be at least 1.", color=COLORS["error"]
            ))

        with open("data/items.json") as f:
            items_data = json.load(f)["items"]
        materials = load_materials()

        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Try to match item first, then material
        item_id = item_name.lower().replace(" ", "_").replace("-", "_")
        item = items_data.get(item_id)
        is_material = False

        if not item:
            mat = materials.get(item_id)
            if mat:
                item = mat
                is_material = True
            else:
                # Fuzzy match both
                for iid, iv in items_data.items():
                    if item_name.lower() in iv["name"].lower():
                        item, item_id = iv, iid
                        break
                if not item:
                    for mid, mv in materials.items():
                        if item_name.lower() in mv["name"].lower():
                            item, item_id, is_material = mv, mid, True
                            break

        if not item:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ `{item_name}` not found.", color=COLORS["error"]
            ))

        # Check Whimsy Merchant for sell bonus
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT 1 FROM player_perks WHERE user_id = ? AND perk_id = 'whimsy_merchant' AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                has_merchant = await c.fetchone()

            table = "player_materials" if is_material else "player_inventory"
            async with db.execute(
                f"SELECT id, quantity FROM {table} WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, item_id)
            ) as c:
                inv_row = await c.fetchone()

        if not inv_row or inv_row["quantity"] < quantity:
            have = inv_row["quantity"] if inv_row else 0
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You only have `{have}` of that item.", color=COLORS["error"]
            ))

        # Sell price — materials use rarity-based pricing
        MATERIAL_PRICES = {
            "common": 20, "uncommon": 60, "rare": 150, "epic": 400,
            "legendary": 1000, "altered_divine": 3000
        }
        if is_material:
            base_price = MATERIAL_PRICES.get(item.get("rarity", "common"), 20)
        else:
            base_price = max(5, int(item.get("price", 0) * 0.35))

        if has_merchant:
            base_price = int(base_price * 1.20)  # Whimsy Merchant: +20% sell price

        total = base_price * quantity

        async with aiosqlite.connect(DB_PATH) as db:
            if inv_row["quantity"] == quantity:
                await db.execute(f"DELETE FROM {table} WHERE id = ?", (inv_row["id"],))
            else:
                await db.execute(
                    f"UPDATE {table} SET quantity = quantity - ? WHERE id = ?",
                    (quantity, inv_row["id"])
                )
            await db.execute("UPDATE players SET gold = gold + ? WHERE user_id = ?",
                             (total, interaction.user.id))
            await db.commit()

        merchant_tag = " *(Whimsy Merchant bonus applied!)*" if has_merchant else ""
        await interaction.followup.send(embed=discord.Embed(
            title="💰 Sold!",
            description=(
                f"Sold `{quantity}x` **{item['name']}** for **{total:,} gold**{merchant_tag}\n"
                f"Remaining balance: `{player['gold'] + total:,} gold`"
            ),
            color=COLORS["success"]
        ))

    # ── /release ──────────────────────────────────────────────────────────
    @app_commands.command(name="release", description="Release a beast back into the wild 🌿")
    @app_commands.describe(beast_id="ID of the beast to release (from /collection)")
    async def release(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                beast_row = await c.fetchone()

        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Beast not found in your collection!", color=COLORS["error"]
            ))
        beast_row = dict(beast_row)
        beast_data = get_beast_data(beast_row["beast_id"]) or {}
        beast_name = beast_row.get("nickname") or beast_data.get("name", "Unknown")
        rarity = beast_row["rarity"]

        if beast_row.get("is_active"):
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't release your active beast. Use `/setactive` to choose another first.",
                color=COLORS["error"]
            ))

        # Confirm with a button
        REFUND = {"common": 50, "uncommon": 150, "rare": 500, "epic": 1200,
                  "legendary": 3000, "divine": 8000, "altered_divine": 20000}
        refund = REFUND.get(rarity, 50)

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.confirmed = False

            @discord.ui.button(label="Release", style=discord.ButtonStyle.danger, emoji="🌿")
            async def confirm(self, inv: discord.Interaction, btn: discord.ui.Button):
                if inv.user.id != interaction.user.id:
                    return await inv.response.send_message("Not your choice!", ephemeral=True)
                self.confirmed = True
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv.response.edit_message(view=self)

            @discord.ui.button(label="Keep", style=discord.ButtonStyle.secondary, emoji="💙")
            async def cancel(self, inv: discord.Interaction, btn: discord.ui.Button):
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv.response.edit_message(
                    embed=discord.Embed(description=f"✦ Kept **{beast_name}**.", color=COLORS["info"]),
                    view=self
                )

        view = ConfirmView()
        await interaction.followup.send(embed=discord.Embed(
            title=f"🌿 Release {beast_name}?",
            description=(
                f"*{beast_data.get('description', '')}*\n\n"
                f"{RARITY_EMOJI.get(rarity, '⚪')} **{RARITY_LABEL.get(rarity, rarity)}**\n\n"
                f"You'll receive **{refund:,} gold** as a parting gift.\n"
                f"*This cannot be undone.*"
            ),
            color=COLORS.get(rarity, COLORS["info"])
        ), view=view)
        await view.wait()

        if not view.confirmed:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM player_beasts WHERE id = ?", (beast_id,))
            await db.execute("UPDATE players SET gold = gold + ? WHERE user_id = ?",
                             (refund, interaction.user.id))
            await db.commit()

        # Lore-flavored release message
        RELEASE_LINES = {
            "divine": f"*The Loom receives {beast_name} back gently. Somewhere, Orren nods.*",
            "legendary": f"*{beast_name} turns once before it goes. That was a goodbye.*",
            "epic": f"*{beast_name} doesn't look back. That's fine.*",
        }
        lore_line = RELEASE_LINES.get(rarity, f"*{beast_name} returns to the world.*")

        await interaction.followup.send(embed=discord.Embed(
            title="🌿 Released",
            description=f"{lore_line}\n\n+**{refund:,} gold** returned to your wallet.",
            color=COLORS["success"]
        ))

    # ── /evolve ───────────────────────────────────────────────────────────
    @app_commands.command(name="evolve", description="Evolve a beast using the right item 🌟")
    @app_commands.describe(beast_id="ID of the beast to evolve (from /collection)")
    async def evolve(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        all_beasts = load_beasts()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                beast_row = await c.fetchone()

        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Beast not found!", color=COLORS["error"]
            ))
        beast_row = dict(beast_row)
        beast_data = all_beasts.get(beast_row["beast_id"])
        if not beast_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Beast data not found!", color=COLORS["error"]
            ))

        evolution = beast_data.get("evolution")
        if not evolution:
            return await interaction.followup.send(embed=discord.Embed(
                description=(
                    f"✦ **{beast_data['name']}** has no known evolution.\n"
                    f"*Not everything the Loom wove was meant to become something else.*"
                ),
                color=COLORS["info"]
            ))

        level_req = evolution.get("level_required", 1)
        if beast_row["level"] < level_req:
            return await interaction.followup.send(embed=discord.Embed(
                description=(
                    f"✦ **{beast_data['name']}** needs to be **Level {level_req}** to evolve.\n"
                    f"Current level: `{beast_row['level']}`\n\n"
                    f"*{evolution['description']}*"
                ),
                color=COLORS["error"]
            ))

        method = evolution.get("method", "sunforge_core")
        target_id = evolution.get("evolves_to")
        target_data = all_beasts.get(target_id)
        if not target_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Evolution target data missing. Please report this.", color=COLORS["error"]
            ))

        # Check inventory for the required item
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, method)
            ) as c:
                item_row = await c.fetchone()

        with open("data/items.json") as f:
            items_data = json.load(f)["items"]
        method_item = items_data.get(method, {})

        if not item_row or item_row["quantity"] < 1:
            return await interaction.followup.send(embed=discord.Embed(
                title=f"🌟 {beast_data['name']} Wants to Evolve...",
                description=(
                    f"*{evolution['description']}*\n\n"
                    f"**Required:** 1x **{method_item.get('name', method.replace('_',' ').title())}**\n"
                    f"**Evolves into:** {RARITY_EMOJI.get(target_data['rarity'],'⚪')} **{target_data['name']}**\n\n"
                    f"✦ You don't have the required item. Find it in the shop or from raids."
                ),
                color=COLORS["epic"]
            ))

        # Perform evolution
        async with aiosqlite.connect(DB_PATH) as db:
            # Consume item
            if item_row["quantity"] == 1:
                await db.execute("DELETE FROM player_inventory WHERE id = ?", (item_row["id"],))
            else:
                await db.execute(
                    "UPDATE player_inventory SET quantity = quantity - 1 WHERE id = ?",
                    (item_row["id"],)
                )

            # Update beast_id, rarity, and reset stats to new beast's base
            new_stats = target_data["base_stats"]
            # Scale stats by the difference in levels the beast already has
            from utils.db import calc_stat_growth
            levels_above_base = max(0, beast_row["level"] - 1)
            growth = calc_stat_growth({"rarity": target_data["rarity"]}, levels_above_base)
            await db.execute("""
                UPDATE player_beasts SET
                    beast_id = ?, rarity = ?,
                    max_hp = ?, hp = ?,
                    attack = ?, defense = ?, speed = ?,
                    mana = ?, max_mana = ?
                WHERE id = ?
            """, (
                target_id, target_data["rarity"],
                new_stats["hp"] + growth["hp"], new_stats["hp"] + growth["hp"],
                new_stats["attack"] + growth["attack"],
                new_stats["defense"] + growth["defense"],
                new_stats["speed"] + growth["speed"],
                new_stats["mana"] + growth["mana"],
                new_stats["mana"] + growth["mana"],
                beast_id
            ))
            await db.commit()

        rarity_emoji = RARITY_EMOJI.get(target_data["rarity"], "⚪")
        color = COLORS.get(target_data["rarity"], COLORS["legendary"])

        embed = discord.Embed(
            title=f"🌟 Evolution!",
            description=(
                f"**{beast_data['name']}** evolved into **{target_data['name']}**!\n\n"
                f"{rarity_emoji} *{target_data['title']}*\n\n"
                f"*{target_data['description']}*\n\n"
                f"*{evolution['description']}*"
            ),
            color=color
        )
        if target_data.get("divine_passive"):
            dp = target_data["divine_passive"]
            embed.add_field(
                name=f"✨ New Divine Passive: {dp['passive_name']}",
                value=dp["passive_desc"],
                inline=False
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  Check /beastinfo to see updated stats")
        await interaction.followup.send(embed=embed)

        # Check achievements after evolution
        unlocked = await check_achievements(interaction.user.id)
        if unlocked:
            from utils.progress import notify_unlocks
            await notify_unlocks(interaction.channel, interaction.user, unlocked)

    # ── /shard_shop ───────────────────────────────────────────────────────
    @app_commands.command(name="shard_shop", description="Spend Celestial Shards on exclusive items 🔮")
    @app_commands.describe(item="Item to buy (leave blank to browse)")
    @app_commands.choices(item=[
        app_commands.Choice(name="🌌 Astral Reroll (25 shards) — guaranteed element next hatch", value="astral_reroll"),
        app_commands.Choice(name="🧭 Divine Compass (40 shards) — 3 boosted explores",          value="divine_compass"),
        app_commands.Choice(name="🧵 Loom Fragment (15 shards) — skip 6hrs of incubation",       value="loom_fragment"),
        app_commands.Choice(name="🔑 Prism Key (60 shards) — 30% divine explore",                value="prism_key"),
        app_commands.Choice(name="✏️ Rename Token (15 shards) — rename any beast",               value="beast_rename_token"),
        app_commands.Choice(name="🏷️ Title Reset (10 shards) — choose from earned titles",       value="trainer_title_reset"),
    ])
    async def shard_shop(self, interaction: discord.Interaction, item: str = None):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        shards = player.get("celestial_shards", 0)

        if not item:
            embed = discord.Embed(
                title="🔮 Celestial Shard Shop",
                description=(
                    f"*The Loom accepts shards as a kind of acknowledgment — proof that you've been paying attention.*\n\n"
                    f"**Your balance:** `{shards} 🔮`\n\n"
                    "Shards are earned from achievements, raids, and questline completion."
                ),
                color=COLORS["divine"]
            )
            for sid, shop_item in SHARD_SHOP.items():
                limit_str = f" (limit {shop_item['weekly_limit']}/week)" if shop_item["weekly_limit"] else ""
                embed.add_field(
                    name=f"{shop_item['name']} — `{shop_item['cost']} 🔮`{limit_str}",
                    value=shop_item["desc"],
                    inline=False
                )
            embed.set_footer(text="ChibiBeasts 🐾  •  Use /shard_shop <item> to buy")
            return await interaction.followup.send(embed=embed)

        shop_item = SHARD_SHOP.get(item)
        if not shop_item:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Unknown item.", color=COLORS["error"]
            ))

        if shards < shop_item["cost"]:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Need `{shop_item['cost']} 🔮` shards, you have `{shards}`.",
                color=COLORS["error"]
            ))

        # Weekly limit check
        if shop_item["weekly_limit"] > 0:
            week_str = datetime.now(timezone.utc).strftime("%Y-W%W")
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT shard_shop_week FROM players WHERE user_id = ?", (interaction.user.id,)
                ) as c:
                    row = await c.fetchone()
            week_data = {}
            if row and row["shard_shop_week"]:
                try:
                    week_data = json.loads(row["shard_shop_week"])
                except Exception:
                    week_data = {}
            if week_data.get("week") == week_str:
                bought = week_data.get(item, 0)
                if bought >= shop_item["weekly_limit"]:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ You've already bought **{shop_item['name']}** this week. Come back next week.",
                        color=COLORS["error"]
                    ))
            else:
                week_data = {"week": week_str}
            week_data[item] = week_data.get(item, 0) + 1

        # Apply purchase
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE players SET celestial_shards = celestial_shards - ? WHERE user_id = ?",
                (shop_item["cost"], interaction.user.id)
            )
            if shop_item["weekly_limit"] > 0:
                await db.execute(
                    "UPDATE players SET shard_shop_week = ? WHERE user_id = ?",
                    (json.dumps(week_data), interaction.user.id)
                )

            # Apply item effects
            item_type = shop_item["type"]
            result_desc = ""

            if item_type == "explore_boost":
                # Set divine compass flag — 3 boosted explores stored as player flag
                import time
                boost_until = time.time() + (3 * 3600)  # expires in 3 hours if unused, tracked separately
                await db.execute(
                    "UPDATE players SET incense_active_until = ? WHERE user_id = ?",
                    (boost_until, interaction.user.id)
                )
                result_desc = "Your next 3 `/explore` runs have boosted Divine odds in the Celestial Loom!"

            elif item_type == "incubation_skip":
                async with db.execute(
                    "SELECT id, egg_name, ready_at FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY started_at ASC LIMIT 1",
                    (interaction.user.id,)
                ) as c:
                    egg = await c.fetchone()
                if egg:
                    new_ready = datetime.strptime(egg["ready_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) - timedelta(hours=6)
                    await db.execute(
                        "UPDATE incubating_eggs SET ready_at = ? WHERE id = ?",
                        (new_ready.strftime("%Y-%m-%d %H:%M:%S"), egg["id"])
                    )
                    result_desc = f"**{egg['egg_name']}** incubation reduced by 6 hours!"
                else:
                    result_desc = "No eggs currently incubating — but the fragment is yours to keep in spirit."

            elif item_type == "key":
                # Prism Key — store a flag for enhanced explore
                await db.execute(
                    "UPDATE players SET brew_active = brew_active + 1 WHERE user_id = ?",
                    (interaction.user.id,)
                )
                result_desc = "A Prism Key has been added. Your next `/explore` in the Celestial Loom will have a 30% Divine rate."

            elif item_type in ["cosmetic", "reroll"]:
                result_desc = f"**{shop_item['name']}** is now yours. Check `/profile` to apply it."

            await db.commit()

        embed = discord.Embed(
            title=f"🔮 Purchased: {shop_item['name']}",
            description=(
                f"*{shop_item['desc']}*\n\n"
                f"{result_desc}\n\n"
                f"Remaining shards: `{shards - shop_item['cost']} 🔮`"
            ),
            color=COLORS["divine"]
        )
        await interaction.followup.send(embed=embed)

    # ── /daily ────────────────────────────────────────────────────────────
    @app_commands.command(name="daily", description="Claim your daily reward and apply sanctuary bonuses 🌅")
    async def daily(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Daily reset check using date string in DB
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT quest_id, date FROM daily_quests WHERE user_id = ? AND quest_id = 'daily_claim' ORDER BY date DESC LIMIT 1",
                (interaction.user.id,)
            ) as c:
                last_claim = await c.fetchone()

        if last_claim and last_claim["date"] == today:
            next_reset = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            remaining = next_reset - datetime.now(timezone.utc)
            hrs, rem = divmod(int(remaining.total_seconds()), 3600)
            mins = rem // 60
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You've already claimed today's daily reward!\n⏳ Next reset in `{hrs}h {mins}m`.",
                color=COLORS["info"]
            ))

        # Base reward — 2 shards flat, scaling gold with level
        # Flat 2 shards (not level-gated) so new players can reach the shard
        # shop in a reasonable time. Level-scaling gold keeps the economy
        # meaningful as trainers grow.
        level = player.get("level", 1)
        gold_reward  = 100 + (level * 15)
        shard_reward = 2   # flat base — scales via quest completion bonus

        lines = [f"+**{gold_reward:,} gold** 💰", f"+**{shard_reward} Celestial Shards** 🔮"]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE players SET gold = gold + ?, celestial_shards = celestial_shards + ? WHERE user_id = ?",
                (gold_reward, shard_reward, interaction.user.id)
            )

            # Apply Fairy Garden happiness bonus to benched beasts
            sanctuary = await get_user_sanctuary(interaction.user.id)
            happiness_added = 0
            if sanctuary.get("fairy_garden"):
                await db.execute(
                    "UPDATE player_beasts SET happiness = MIN(100, happiness + 5) WHERE user_id = ? AND is_active = 0 AND happiness < 100",
                    (interaction.user.id,)
                )
                async with db.execute(
                    "SELECT COUNT(*) FROM player_beasts WHERE user_id = ? AND is_active = 0",
                    (interaction.user.id,)
                ) as c:
                    count = (await c.fetchone())[0]
                if count:
                    happiness_added = count
                    lines.append(f"🌸 **Fairy Garden:** +5 happiness to {count} benched beast{'s' if count != 1 else ''}")

            # Mark claim
            await db.execute(
                "INSERT OR REPLACE INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?, 'daily_claim', 1, 1, ?)",
                (interaction.user.id, today)
            )
            await db.commit()

        embed = discord.Embed(
            title="🌅 Daily Reward Claimed!",
            description="\n".join(lines),
            color=COLORS["success"]
        )
        embed.set_footer(text="ChibiBeasts 🐾  •  Beasts lose happiness daily — build a Fairy Garden to offset it")
        embed.set_footer(text=f"ChibiBeasts 🐾  •  Come back tomorrow! Level {level} trainer bonus applied.")
        await interaction.followup.send(embed=embed)

    # ── /stats ────────────────────────────────────────────────────────────
    @app_commands.command(name="stats", description="View server-wide ChibiBeasts statistics 📊")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild.id if interaction.guild else 0

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Total trainers
            async with db.execute("SELECT COUNT(*) FROM players") as c:
                total_trainers = (await c.fetchone())[0]

            # Total beasts caught (server bestiary)
            async with db.execute(
                "SELECT COUNT(*) FROM bestiary WHERE guild_id = ?", (guild_id,)
            ) as c:
                server_discovered = (await c.fetchone())[0]

            # Total beasts owned globally
            async with db.execute("SELECT COUNT(*) FROM player_beasts") as c:
                total_beasts = (await c.fetchone())[0]

            # Total divine beasts owned
            async with db.execute(
                "SELECT COUNT(*) FROM player_beasts WHERE rarity = 'divine'"
            ) as c:
                total_divines = (await c.fetchone())[0]

            # Total battles fought (pvp + pve + sparr, all recorded in battles table)
            async with db.execute("SELECT COUNT(*) FROM battles WHERE status = 'completed'") as c:
                total_battles = (await c.fetchone())[0]

            # Total raids completed
            async with db.execute("SELECT COUNT(*) FROM raids WHERE current_hp <= 0") as c:
                raids_completed = (await c.fetchone())[0]

            # Top trainer by level
            async with db.execute(
                "SELECT username, level, wins FROM players ORDER BY level DESC, wins DESC LIMIT 1"
            ) as c:
                top_trainer = await c.fetchone()

            # Most common beast owned
            async with db.execute(
                "SELECT beast_id, COUNT(*) as cnt FROM player_beasts GROUP BY beast_id ORDER BY cnt DESC LIMIT 1"
            ) as c:
                common_beast = await c.fetchone()

            # Rarest achievement holder
            async with db.execute(
                "SELECT achievement_id, COUNT(*) as cnt FROM achievements GROUP BY achievement_id ORDER BY cnt ASC LIMIT 1"
            ) as c:
                rarest_ach = await c.fetchone()

        from utils.db import load_beasts as _load_beasts
        all_b = _load_beasts()
        top_beast_name = all_b.get(common_beast["beast_id"], {}).get("name", "?") if common_beast else "?"
        total_species = len(all_b)

        from utils.progress import ACHIEVEMENTS
        rarest_name = ACHIEVEMENTS.get(rarest_ach["achievement_id"], {}).get("name", "?") if rarest_ach else "?"

        embed = discord.Embed(
            title=f"📊 ChibiBeasts Server Stats",
            description=f"*The Loom keeps its own records. These are the legible ones.*",
            color=COLORS["legendary"]
        )
        embed.add_field(name="👥 Trainers", value=f"`{total_trainers:,}`", inline=True)
        embed.add_field(name="🐾 Beasts Owned", value=f"`{total_beasts:,}`", inline=True)
        embed.add_field(name="🌸 Divines Found", value=f"`{total_divines:,}`", inline=True)
        embed.add_field(
            name="📖 Server Bestiary",
            value=f"`{server_discovered}/{total_species}` species discovered",
            inline=True
        )
        embed.add_field(name="⚔️ Battles Fought", value=f"`{total_battles:,}`", inline=True)
        embed.add_field(name="💀 Raids Defeated", value=f"`{raids_completed:,}`", inline=True)
        if top_trainer:
            embed.add_field(
                name="🏆 Top Trainer",
                value=f"**{top_trainer['username']}** — Lv.{top_trainer['level']} | {top_trainer['wins']} wins",
                inline=False
            )
        if common_beast:
            embed.add_field(name="🐾 Most Common Beast", value=f"**{top_beast_name}** ({common_beast['cnt']} owned)", inline=True)
        if rarest_ach:
            embed.add_field(name="✨ Rarest Achievement", value=f"**{rarest_name}** ({rarest_ach['cnt']} earned)", inline=True)
        embed.set_footer(text="ChibiBeasts 🐾  •  /leaderboard for individual rankings")
        await interaction.followup.send(embed=embed)

    # ── /help ─────────────────────────────────────────────────────────────
    @app_commands.command(name="help", description="Browse all ChibiBeasts commands 📚")
    @app_commands.describe(category="Which category to browse")
    @app_commands.choices(category=[
        app_commands.Choice(name="🌱 Getting Started", value="start"),
        app_commands.Choice(name="🐾 Beasts & Collection", value="beasts"),
        app_commands.Choice(name="⚔️ Battle", value="battle"),
        app_commands.Choice(name="🌍 Exploration & Eggs", value="explore"),
        app_commands.Choice(name="⚒️ Crafting & Equipment", value="craft"),
        app_commands.Choice(name="🏰 Guilds & Raids", value="guild"),
        app_commands.Choice(name="📋 Quests & Progression", value="progress"),
        app_commands.Choice(name="💰 Economy & Trading", value="economy"),
        app_commands.Choice(name="📖 Lore & World", value="lore"),
    ])
    async def help_cmd(self, interaction: discord.Interaction, category: str = "start"):
        await interaction.response.defer()

        HELP = {
            "start": {
                "title": "🌱 Getting Started",
                "desc": "New to ChibiBeasts? Start here.",
                "commands": [
                    ("/start", "Begin your journey — choose your starter beast (Prismite, Twine, Gloop, or Barkley)"),
                    ("/profile", "View your trainer profile, stats, and balance"),
                    ("/daily", "Claim your daily gold and shard reward"),
                    ("/lore", "Read the world's creation myth and story"),
                    ("/meet", "See all the NPCs and where to find them"),
                    ("/questline", "Track your story progress"),
                    ("/help <category>", "Browse commands by category"),
                ]
            },
            "beasts": {
                "title": "🐾 Beasts & Collection",
                "desc": "Manage your beast collection.",
                "commands": [
                    ("/collection", "View all your beasts"),
                    ("/beastinfo <id>", "Detailed stats, moves, and disposition for a beast"),
                    ("/setactive <id>", "Set your active battle beast"),
                    ("/nickname <id> <name>", "Give a beast a nickname"),
                    ("/release <id>", "Release a beast for a gold refund"),
                    ("/evolve <id>", "Evolve a beast using the required item"),
                    ("/codex <name>", "Look up any beast's lore and stats"),
                    ("/bestiary", "See what your server has discovered"),
                ]
            },
            "battle": {
                "title": "⚔️ Battle",
                "desc": "PvP combat, wild battles, and NPC spars.",
                "commands": [
                    ("/battle @trainer",  "Challenge another trainer to a PvP beast battle"),
                    ("/challenge <biome>","Fight a wild beast in a biome — win to catch it"),
                    ("/sparr <npc>",      "Spar with an NPC — deepen your bond and earn shards (once per NPC/day)"),
                    ("/leaderboard",      "Server rankings by victories"),
                    ("/typeinfo <type>",  "Look up type matchups and advantages"),
                ]
            },
            "explore": {
                "title": "🌍 Exploration & Eggs",
                "desc": "Explore biomes and hatch beasts.",
                "commands": [
                    ("/explore", "Explore a biome — find wild beasts and materials (1hr cooldown)"),
                    ("/hatch", "Instantly hatch an egg (Common/Rare/Celestial/Abyssal)"),
                    ("/shop eggs",        "Browse and buy instant-hatch eggs"),
                    ("/shop incubation",  "Browse and buy incubation eggs"),
                    ("/incubate <egg name>", "Place a named egg in incubation (timed)"),
                    ("/eggs", "Check your incubating eggs and timers"),
                    ("/hatchegg", "Hatch a ready incubated egg"),
                ]
            },
            "craft": {
                "title": "⚒️ Crafting & Equipment",
                "desc": "Materials, gear, and beast equipment.",
                "commands": [
                    ("/materials", "View your crafting material stash"),
                    ("/recipes", "Browse all craftable armor and rune recipes"),
                    ("/craft <item>", "Craft an armor set from materials"),
                    ("/equip <item> <beast_id>", "Equip armor or a rune to a beast"),
                    ("/unequip <beast_id>", "Remove all equipment from a beast"),
                    ("/sell <item>", "Sell items or materials for gold"),
                ]
            },
            "guild": {
                "title": "🏰 Guilds & Raids",
                "desc": "Build a guild and take on raids.",
                "commands": [
                    ("/guild_create <name>", "Found a new guild (costs 2,000 gold)"),
                    ("/guild", "View your guild info"),
                    ("/guild_invite @member", "Invite someone to your guild"),
                    ("/raid", "Trigger a raid boss (guild officers only)"),
                    ("/raid_attack", "Deal damage to the active raid boss"),
                    ("/sanctuary", "View your guild's Sanctuary upgrades"),
                    ("/build <upgrade>", "Build a Sanctuary tier (Fairy Garden / Gnome Forge / Observatory)"),
                ]
            },
            "progress": {
                "title": "📋 Quests & Progression",
                "desc": "Daily quests, achievements, and story.",
                "commands": [
                    ("/dailies", "View your daily quest progress"),
                    ("/daily", "Claim daily reward"),
                    ("/achievements", "View your achievement collection"),
                    ("/questline", "Track and advance the main story questline"),
                    ("/npc <name>", "Talk to an NPC"),
                    ("/meet", "Overview of all NPCs and locations"),
                ]
            },
            "economy": {
                "title": "💰 Economy & Trading",
                "desc": "Gold, shards, shop, and trading.",
                "commands": [
                    ("/shop", "Browse the item and egg shop"),
                    ("/shop items",    "Browse and buy items"),
                    ("/use <item>", "Use an item from your inventory"),
                    ("/inventory", "View your item inventory"),
                    ("/sell <item>", "Sell items or materials for gold"),
                    ("/trade @trainer", "Offer a beast/gold trade to another player"),
                    ("/perks", "View your perks"),
                    ("/perk_equip <perk>", "Equip a perk"),
                    ("/shard_shop", "Spend Celestial Shards on exclusive items"),
                ]
            },
            "lore": {
                "title": "📖 Lore & World",
                "desc": "The story and the world behind it.",
                "commands": [
                    ("/lore <chapter>", "Read lore chapters: creation, sundering, starters, etc."),
                    ("/codex <beast>", "In-game beast encyclopedia with type lore"),
                    ("/typeinfo <type>", "Elemental type matchup chart"),
                    ("/stats", "Server-wide statistics"),
                    ("/bestiary", "Server's beast discovery log"),
                ]
            },
        }

        cat = HELP.get(category, HELP["start"])
        embed = discord.Embed(
            title=cat["title"],
            description=cat["desc"],
            color=COLORS["info"]
        )
        for cmd, desc in cat["commands"]:
            embed.add_field(name=f"`{cmd}`", value=desc, inline=False)
        embed.set_footer(text="ChibiBeasts 🐾  •  /help <category> for other sections")
        await interaction.followup.send(embed=embed)

    # ── /title ────────────────────────────────────────────────────────────
    @app_commands.command(name="title", description="Set your active trainer title 🏷️")
    @app_commands.describe(new_title="The title to display (must be one you've earned)")
    async def title_cmd(self, interaction: discord.Interaction, new_title: str = None):
        await interaction.response.defer()

        # Get all earned titles from achievements and questline
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT achievement_id FROM achievements WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                earned_ach = [r["achievement_id"] for r in await c.fetchall()]

            async with db.execute(
                "SELECT title FROM players WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                row = await c.fetchone()
            current_title = row["title"] if row else None

        from utils.progress import ACHIEVEMENTS
        from cogs.hatch import COLLECTION_REWARDS

        TITLE_SOURCES = {}
        for aid in earned_ach:
            ach = ACHIEVEMENTS.get(aid, {})
            pass  # Achievements don't have titles yet; questline and collections do

        # Collection titles
        collection_titles = {r["title"]: r["title"] for r in COLLECTION_REWARDS.values()}
        # Questline title
        if "loom_witness" in earned_ach:
            collection_titles["Witness to the Loom"] = "Witness to the Loom"

        all_titles = list(collection_titles.values())

        if not new_title:
            embed = discord.Embed(
                title="🏷️ Your Trainer Titles",
                description=f"**Current title:** *{current_title or 'None'}*\n\nUse `/title <name>` to equip one.",
                color=COLORS["info"]
            )
            if all_titles:
                embed.add_field(name="Earned titles", value="\n".join(f"• {t}" for t in all_titles), inline=False)
            else:
                embed.add_field(name="No titles yet", value="Complete questlines and collections to earn titles.", inline=False)
            return await interaction.followup.send(embed=embed)

        # Check if they own this title
        matching = [t for t in all_titles if new_title.lower() in t.lower()]
        if not matching:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You haven't earned the title **{new_title}** yet.",
                color=COLORS["error"]
            ))

        chosen = matching[0]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE players SET title = ? WHERE user_id = ?",
                (chosen, interaction.user.id)
            )
            await db.commit()

        await interaction.followup.send(embed=discord.Embed(
            description=f"✦ Title set to **{chosen}**. It will appear in your `/profile`.",
            color=COLORS["success"]
        ))

    # ── /play ─────────────────────────────────────────────────────────────
    @app_commands.command(name="play", description="Spend time with your active beast to boost their happiness 😊")
    async def play(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # One play session per day
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            async with db.execute(
                "SELECT 1 FROM daily_quests WHERE user_id = ? AND quest_id = 'play_session' AND date = ?",
                (interaction.user.id, today)
            ) as c:
                already_played = await c.fetchone()

            if already_played:
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        "✦ You've already played with your beast today.\n"
                        "*They're happy — come back tomorrow.*"
                    ),
                    color=COLORS["info"]
                ))

            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1",
                (interaction.user.id,)
            ) as c:
                active = await c.fetchone()

            if not active:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ You don't have an active beast! Use `/setactive` first.",
                    color=COLORS["error"]
                ))

            active = dict(active)
            PLAY_GAIN = 15
            new_happiness = min(100, active["happiness"] + PLAY_GAIN)
            already_full = active["happiness"] >= 100

            await db.execute(
                "UPDATE player_beasts SET happiness = ? WHERE id = ?",
                (new_happiness, active["id"])
            )
            await db.execute(
                "INSERT INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?, 'play_session', 1, 1, ?)",
                (interaction.user.id, today)
            )
            await db.commit()

        from utils.db import get_beast_data as _gbd
        beast_data = _gbd(active["beast_id"]) or {}
        name = active.get("nickname") or beast_data.get("name", "your beast")

        PLAY_LINES = [
            f"*{name} chases something that isn't there, then pretends it wasn't doing that.*",
            f"*You sit with {name} for a while. It doesn't move much. That seems to be the point.*",
            f"*{name} does something you can't quite describe. You feel like you both understood something.*",
            f"*{name} leans against you for exactly three seconds, then walks away like it didn't happen.*",
            f"*You bring {name} somewhere it hasn't been. It sniffs everything. Twice.*",
        ]
        import random as _r
        play_line = _r.choice(PLAY_LINES)

        if already_full:
            desc = f"*{name} is already as happy as can be — but they don't mind the company.*\n\n😊 Happiness: `100/100`"
        else:
            desc = (
                f"{play_line}\n\n"
                f"😊 **+{PLAY_GAIN} happiness** → `{new_happiness}/100`"
                + ("\n\n*Use `/shop` to buy Brambleberries or Sugarsprout Cupcakes for more happiness boosts!*"
                   if new_happiness < 50 else "")
            )

        await interaction.followup.send(embed=discord.Embed(
            title=f"🐾 Playing with {name}",
            description=desc,
            color=COLORS["success"]
        ))

    # ── /history ──────────────────────────────────────────────────────────
    @app_commands.command(name="history", description="View your recent battle, raid, and trade history 📜")
    @app_commands.describe(category="Which history to view")
    @app_commands.choices(category=[
        app_commands.Choice(name="⚔️ Battles", value="battles"),
        app_commands.Choice(name="💀 Raids",   value="raids"),
        app_commands.Choice(name="🤝 Trades",  value="trades"),
    ])
    async def history(self, interaction: discord.Interaction, category: str = "battles"):
        await interaction.response.defer()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            if category == "battles":
                async with db.execute("""
                    SELECT b.battle_type, b.winner_id, b.challenger_id, b.opponent_id,
                           b.created_at,
                           p1.username AS challenger_name,
                           p2.username AS opponent_name
                    FROM battles b
                    LEFT JOIN players p1 ON b.challenger_id = p1.user_id
                    LEFT JOIN players p2 ON b.opponent_id   = p2.user_id
                    WHERE b.challenger_id = ? OR b.opponent_id = ?
                    ORDER BY b.created_at DESC LIMIT 15
                """, (interaction.user.id, interaction.user.id)) as c:
                    rows = [dict(r) for r in await c.fetchall()]

                embed = discord.Embed(
                    title="⚔️ Battle History",
                    description=f"*Your last {len(rows)} battles.*" if rows else "*No battles recorded yet.*",
                    color=COLORS["epic"]
                )
                for r in rows:
                    btype = r["battle_type"] or "pvp"
                    if btype == "pvp":
                        opponent_name = r["opponent_name"] or "Unknown"
                        if r["winner_id"] == interaction.user.id:
                            result = "✅ Win"
                        elif r["winner_id"] is None:
                            result = "🤝 Draw"
                        else:
                            result = "💤 Loss"
                        label = f"vs {opponent_name}"
                    elif btype == "sparr":
                        result = "✅ Win" if r["winner_id"] == interaction.user.id else "💤 Loss"
                        label = "NPC Spar"
                    else:
                        result = "✅ Win" if r["winner_id"] == interaction.user.id else "💤 Loss"
                        label = "Wild Battle"
                    ts = r["created_at"][:10] if r["created_at"] else "?"
                    embed.add_field(
                        name=f"{result} — {label}",
                        value=f"*{btype.upper()} · {ts}*",
                        inline=True
                    )

            elif category == "raids":
                async with db.execute("""
                    SELECT r.boss_name, r.boss_type, r.status, r.started_at, r.ended_at,
                           rp.damage_dealt
                    FROM raid_participants rp
                    JOIN raids r ON rp.raid_id = r.id
                    WHERE rp.user_id = ?
                    ORDER BY r.started_at DESC LIMIT 15
                """, (interaction.user.id,)) as c:
                    rows = [dict(r) for r in await c.fetchall()]

                async with db.execute("""
                    SELECT beast_id, altered_name, caught_at
                    FROM altered_divines
                    WHERE caught_by = ?
                    ORDER BY caught_at DESC LIMIT 5
                """, (interaction.user.id,)) as c:
                    divines = [dict(r) for r in await c.fetchall()]

                embed = discord.Embed(
                    title="💀 Raid History",
                    description=f"*Your last {len(rows)} raids.*" if rows else "*No raids participated in yet.*",
                    color=COLORS["legendary"]
                )
                for r in rows:
                    status_icon = "🏆" if r["status"] == "completed" else "⏰"
                    ts = r["started_at"][:10] if r["started_at"] else "?"
                    embed.add_field(
                        name=f"{status_icon} {r['boss_name']}",
                        value=f"`{r['damage_dealt']:,}` dmg · {r['boss_type'].capitalize()} · {ts}",
                        inline=True
                    )
                if divines:
                    from utils.db import get_beast_data as _gbd
                    divine_lines = []
                    for d in divines:
                        bd = _gbd(d["beast_id"])
                        name = bd["name"] if bd else d["beast_id"]
                        ts = d["caught_at"][:10] if d["caught_at"] else "?"
                        divine_lines.append(f"🌸 **{d['altered_name']}** → {name} · {ts}")
                    embed.add_field(
                        name="✨ Altered Divines Caught",
                        value="\n".join(divine_lines),
                        inline=False
                    )

            else:  # trades
                async with db.execute("""
                    SELECT t.*, p1.username AS sender_name, p2.username AS receiver_name,
                           pb1.beast_id AS sent_beast_id, pb2.beast_id AS received_beast_id,
                           pb1.rarity   AS sent_rarity,    pb2.rarity   AS received_rarity
                    FROM trades t
                    LEFT JOIN players   p1  ON t.sender_id          = p1.user_id
                    LEFT JOIN players   p2  ON t.receiver_id         = p2.user_id
                    LEFT JOIN player_beasts pb1 ON t.sender_beast_id   = pb1.id
                    LEFT JOIN player_beasts pb2 ON t.receiver_beast_id = pb2.id
                    WHERE t.sender_id = ? OR t.receiver_id = ?
                    ORDER BY t.created_at DESC LIMIT 15
                """, (interaction.user.id, interaction.user.id)) as c:
                    rows = [dict(r) for r in await c.fetchall()]

                embed = discord.Embed(
                    title="🤝 Trade History",
                    description=f"*Your last {len(rows)} trades.*" if rows else "*No completed trades yet.*",
                    color=COLORS["success"]
                )
                from utils.db import get_beast_data as _gbd
                for r in rows:
                    sent_bd = _gbd(r["sent_beast_id"]) if r.get("sent_beast_id") else None
                    recv_bd = _gbd(r.get("received_beast_id")) if r.get("received_beast_id") else None
                    sent_name = sent_bd["name"] if sent_bd else "?"
                    recv_name = recv_bd["name"] if recv_bd else "anything"
                    sent_r = RARITY_EMOJI.get(r.get("sent_rarity"), "⚪")
                    recv_r = RARITY_EMOJI.get(r.get("received_rarity"), "⚪") if recv_bd else ""
                    direction = "📤 Sent" if r["sender_id"] == interaction.user.id else "📥 Received"
                    other = r["receiver_name"] if r["sender_id"] == interaction.user.id else r["sender_name"]
                    gold_note = f" + `{r['gold_offered']:,}` 💰" if r.get("gold_offered") else ""
                    ts = r["created_at"][:10] if r.get("created_at") else "?"
                    embed.add_field(
                        name=f"{direction} with {other or '?'}",
                        value=f"{sent_r} {sent_name}{gold_note} ↔ {recv_r} {recv_name} · {ts}",
                        inline=False
                    )

        embed.set_footer(text="ChibiBeasts 🐾  •  /history battles | raids | trades")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utilities(bot))
