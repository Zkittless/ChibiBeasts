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
    add_item, remove_item, apply_beast_levelup, calc_exp_for_level,
    get_beast_by_player_number, get_raid_party, set_raid_slot, clear_raid_slot,
    knockout_beast, revive_beast, is_knocked_out, ko_time_remaining
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import check_achievements, unlock_simple_achievement, notify_unlocks
from utils.sanctuary import get_user_sanctuary

# ── Evolution Cinematics ────────────────────────────────────────────────────
EVOLUTION_SCENES = {
    "radiant_kitsune": {
        "title": "🦊 All Nine, At Once",
        "lines": [
            "Other Kitsune earn their tails one at a time. Centuries of mastery, patience, growth — each tail a chapter.",
            "The Sunforge does not have centuries. It has one moment, and it uses it completely.",
            "All nine tails arrived simultaneously. The Kitsune stood still for a long time afterward, processing something the language of foxes does not have a word for.",
            "The Kitsune_9 you can encounter in the wild earned theirs the old way. This one earned all of them in a single breath.",
            "*The difference is visible. The other foxes know. They don't discuss it.*",
        ],
        "color": "divine",
    },
    "radiant_goblin": {
        "title": "🔥 The Forge Speaks",
        "lines": [
            "The Sunforge did not change the Goblin.",
            "It held the Goblin until the Goblin understood what it already was.",
            "The stubbornness that got it hit too many times is the same stubbornness that made it walk out.",
            "*The Forge Fury was always there. The Sunforge just gave it something to answer to.*",
        ],
        "color": "epic",
    },
    "radiant_imp": {
        "title": "🌑 Darker Than It Went In",
        "lines": [
            "The Sunforge takes most things and makes them shine.",
            "The Imp went in and came out wrong — not broken, just occupying a different relationship with light than it had before.",
            "The shadows around it lean in now. They recognize something.",
            "*The Loom filed a report. The report has been filed under: does not need to be understood, only respected.*",
        ],
        "color": "rare",
    },
    "radiant_hydra": {
        "title": "🐍 The Heads Forget What Heads Are For",
        "lines": [
            "The Sunforge touched the Hydra and two heads grew back for each one it took.",
            "Eventually it gave up and gave the Hydra something it hadn't asked for.",
            "The heads that grew back were not heads. The Hydra did not seem to notice. The Hydra has never cared particularly what it was made of, only that there was more of it.",
            "*Endless Regen was not a gift. It was the Sunforge admitting defeat. The Hydra accepts both with equal indifference.*",
        ],
        "color": "divine",
    },
    "ascended_slime": {
        "title": "🌊 The Test Passes",
        "lines": [
            "The Loom made the Slime as an experiment.",
            "The question was: can resilience, taken far enough, become something divine?",
            "The Genesis Fruit touched it. Nothing visible changed.",
            "Then everything around it looked slightly smaller.",
            "*The answer was yes. The Loom has not decided yet whether to be proud or unsettled. The Slime does not care either way. The Slime absorbs that feeling too.*",
        ],
        "color": "divine",
    },
    "ascended_unicorn": {
        "title": "✨ The Grace Becomes the Thing Itself",
        "lines": [
            "There is a light that exists before light has a name.",
            "The Unicorn always moved toward it. The Genesis Fruit was the last step.",
            "It did not ascend so much as arrive — at something it had been walking toward since the first time it touched a wound and the wound closed.",
            "The horn no longer heals. It does something older than healing.",
            "*It does not distinguish between giving and taking anymore. Sacred Mending does not ask whether you deserve it. That is the point.*",
        ],
        "color": "divine",
    },
    "ascended_pegasus": {
        "title": "🌪️ Past the Edge, and Then Further",
        "lines": [
            "The Pegasus found the boundary between the world and what the world is resting inside.",
            "Most things stop there. The Pegasus visited twice before breakfast.",
            "The Genesis Fruit was not a door. It was confirmation that the Pegasus had already stopped asking permission.",
            "It came back faster than it left. It always does, now.",
            "*Boundary Break is not a passive. It is a habit the Pegasus developed when it realised the edge of the world was just a suggestion.*",
        ],
        "color": "divine",
    },
    "ascended_phoenix": {
        "title": "🔥 The Last Sunrise. And Then Another.",
        "lines": [
            "Every Phoenix dies.",
            "The Genesis Fruit burned in its talons and no ash fell. Something had changed about the relationship between fire and ending.",
            "It stood in the light of its own pyre and looked at the flames and decided, quietly, that dying had become a habit.",
            "Habits can be broken.",
            "*Deathless Flame does not make it invincible. It makes it unwilling. There is a difference, and the difference matters enormously to whoever is standing across from it.*",
        ],
        "color": "divine",
    },
}

# ── Evolution Item → Form Label ─────────────────────────────────────────────
EVOLUTION_FORM_LABELS = {
    "radiant":  ("🌟 Radiant Form",   "The Sunforge has spoken."),
    "ascended": ("✨ Ascended Form",   "The Genesis Fruit has chosen."),
}

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
    # ── Page 1: Utility items ────────────────────────────────────────────
    "astral_reroll": {
        "name": "🌌 Astral Reroll",
        "desc": "Guarantees your next `/hatch` produces a specific element type of your choice.",
        "cost": 15,
        "weekly_limit": 1,
        "type": "reroll",
    },
    "divine_compass": {
        "name": "🧭 Divine Compass",
        "desc": "Boosts the divine encounter rate in the Celestial Loom to 20% for your next 3 explores.",
        "cost": 25,
        "weekly_limit": 1,
        "type": "explore_boost",
    },
    "loom_fragment": {
        "name": "🧵 Loom Fragment",
        "desc": "Reduces the incubation time of your oldest egg by 6 hours.",
        "cost": 10,
        "weekly_limit": 3,
        "type": "incubation_skip",
    },
    # ── Page 2: Cosmetics & access ───────────────────────────────────────
    "prism_key": {
        "name": "🔑 Prism Key",
        "desc": "Grants access to a special /explore variant in the Celestial Loom with a 30% divine rate.",
        "cost": 40,
        "weekly_limit": 1,
        "type": "key",
    },
    "beast_rename_token": {
        "name": "✏️ Rename Token",
        "desc": "Rename any beast — even with special characters.",
        "cost": 10,
        "weekly_limit": 0,
        "type": "cosmetic",
    },
    "trainer_title_reset": {
        "name": "🏷️ Title Reset",
        "desc": "Clear your current trainer title and choose from all titles you've earned.",
        "cost": 5,
        "weekly_limit": 0,
        "type": "cosmetic",
    },
    # ── Page 3: Ancient Summon Items ─────────────────────────────────────
    "epoch_shard": {
        "name": "⏳ Epoch Shard",
        "desc": "Calls Ancient Chronos to the altar. Time stutters around it. Also drops from Corrupted Fenrir.",
        "cost": 150,
        "weekly_limit": 1,
        "type": "grant_item",
        "grant_item_id": "epoch_shard",
    },
    "firstborn_ember": {
        "name": "🔥 Firstborn Ember",
        "desc": "Calls Ancient Genesis to the altar. The flame that started everything, still burning. Also drops from Corrupted Dragon.",
        "cost": 150,
        "weekly_limit": 1,
        "type": "grant_item",
        "grant_item_id": "firstborn_ember",
    },
    "void_prism": {
        "name": "🌑 Void Prism",
        "desc": "Calls Ancient Abyss to the altar. Absorbs all light. The silence around it is wrong. Also drops from Corrupted Leviathan.",
        "cost": 150,
        "weekly_limit": 1,
        "type": "grant_item",
        "grant_item_id": "void_prism",
    },
}


class Utilities(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /equip ────────────────────────────────────────────────────────────
    async def equip_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show equipment and runes from the player's inventory."""
        equipment, runes = load_equipment()
        all_gear = {**equipment, **runes}
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT item_id, quantity FROM player_inventory WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                inv = {r["item_id"]: r["quantity"] for r in await c.fetchall()}
        choices = []
        for gid, gear in all_gear.items():
            if gid in inv and inv[gid] > 0 and current.lower() in gear["name"].lower():
                choices.append(app_commands.Choice(name=gear["name"], value=gid))
        return choices[:25]

    @app_commands.command(name="gear", description="View a beast's equipped gear and what you have available 🛡️")
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def gear(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        all_gear = {**equipment, **runes}

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND player_number = ?",
                (interaction.user.id, beast_id)
            ) as c:
                beast_row = await c.fetchone()
            if not beast_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ No beast found with number `#{beast_id}`.",
                    color=COLORS["error"]
                ))
            beast_row = dict(beast_row)
            # Equipped armor
            async with db.execute(
                "SELECT equipment_id FROM player_equipment WHERE user_id = ? AND beast_row_id = ?",
                (interaction.user.id, beast_row["id"])
            ) as c:
                armor_rows = [r["equipment_id"] for r in await c.fetchall()]
            # Inventory gear (unequipped)
            async with db.execute(
                "SELECT equipment_id FROM player_equipment WHERE user_id = ? AND beast_row_id IS NULL",
                (interaction.user.id,)
            ) as c:
                inv_gear = [r["equipment_id"] for r in await c.fetchall()]

        bd       = get_beast_data(beast_row["beast_id"]) or {}
        r_emoji  = RARITY_EMOJI.get(beast_row["rarity"], "⚪")
        name     = beast_row.get("nickname") or bd.get("name", "?")
        rune_id  = beast_row.get("rune_id")

        embed = discord.Embed(
            title=f"🛡️ Gear — {r_emoji} {name} `#{beast_id}`",
            description=f"Lv.{beast_row['level']} · `{beast_row['hp']}/{beast_row['max_hp']}HP`",
            color=COLORS.get(beast_row["rarity"], COLORS["info"])
        )

        # Equipped armor
        if armor_rows:
            for aid in armor_rows:
                g = all_gear.get(aid, {})
                gr = RARITY_EMOJI.get(g.get("rarity","common"), "⚪")
                eff_str = " · ".join(
                    f"+{v}{'%' if 'percent' in k or 'chance' in k else ''} {k.replace('_',' ').replace(' percent','').replace(' chance','')}"
                    if isinstance(v, (int,float)) else k.replace('_',' ')
                    for k,v in g.get("effect",{}).items()
                )
                embed.add_field(name=f"🔰 {gr} {g.get('name','?')}", value=eff_str or "Special", inline=False)
        else:
            embed.add_field(name="🔰 Armor", value="*None equipped — use `/equip <armor> #{beast_id}`*", inline=False)

        # Equipped rune
        if rune_id:
            g  = all_gear.get(rune_id, {})
            gr = RARITY_EMOJI.get(g.get("rarity","common"), "⚪")
            eff_str = " · ".join(
                f"+{v} {k.replace('_',' ')}" if isinstance(v,(int,float)) else k.replace('_',' ')
                for k,v in g.get("effect",{}).items()
            )
            embed.add_field(name=f"💎 {gr} {g.get('name','?')}", value=eff_str or "Special", inline=False)
        else:
            embed.add_field(name="💎 Rune", value="*None equipped — use `/equip <rune> #{beast_id}`*", inline=False)

        # Available in inventory
        available_armor = [gid for gid in inv_gear if all_gear.get(gid,{}).get("slot","armor") == "armor"]
        available_runes = [gid for gid in inv_gear if all_gear.get(gid,{}).get("slot","rune") == "rune"]

        if available_armor:
            avail_str = "\n".join(
                f"  {RARITY_EMOJI.get(all_gear[g].get('rarity','common'),'⚪')} {all_gear[g]['name']}"
                for g in available_armor if g in all_gear
            )
            embed.add_field(name="📦 Armor in Inventory", value=avail_str, inline=False)
        if available_runes:
            avail_str = "\n".join(
                f"  {RARITY_EMOJI.get(all_gear[g].get('rarity','common'),'⚪')} {all_gear[g]['name']}"
                for g in available_runes if g in all_gear
            )
            embed.add_field(name="📦 Runes in Inventory", value=avail_str, inline=False)

        if not available_armor and not available_runes and not armor_rows and not rune_id:
            embed.add_field(
                name="🔍 No Gear Available",
                value="Craft gear with `/craft` · Browse recipes with `/recipes`",
                inline=False
            )

        embed.set_footer(text="✦ /equip <item> <beast_id> to equip · /unequip <beast_id> to remove")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="equip", description="Equip armor or a rune to a beast ⚔️")
    @app_commands.describe(
        item_name="Equipment or rune to equip (from your inventory)",
        beast_id="Your beast number from /collection"
    )
    @app_commands.autocomplete(item_name=equip_autocomplete)
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

            # Resolve beast by player_number
            beast_row_data = await get_beast_by_player_number(interaction.user.id, beast_id)
            if not beast_row_data:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Beast `#{beast_id}` not found in your collection!", color=COLORS["error"]
                ))
            beast_row = beast_row_data
            actual_id = beast_row["id"]

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
                    (gear_id, actual_id)
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
                    (actual_id, gear_row["id"])
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
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def unequip(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        all_gear = {**equipment, **runes}

        beast_row = await get_beast_by_player_number(interaction.user.id, beast_id)
        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Beast `#{beast_id}` not found!", color=COLORS["error"]
            ))
        actual_id = beast_row["id"]

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
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
                await db.execute("UPDATE player_beasts SET rune_id = NULL WHERE id = ?", (actual_id,))
                removed.append(f"🔮 {rune.get('name', rune_id)} returned to inventory")

            # Unequip armor
            async with db.execute(
                "SELECT equipment_id FROM player_equipment WHERE beast_row_id = ? AND user_id = ?",
                (actual_id, interaction.user.id)
            ) as c:
                armor_rows = [dict(r) for r in await c.fetchall()]
            for ar in armor_rows:
                equip_data = all_gear.get(ar["equipment_id"], {})
                await db.execute(
                    "UPDATE player_equipment SET beast_row_id = NULL WHERE beast_row_id = ? AND equipment_id = ?",
                    (actual_id, ar["equipment_id"])
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
    async def sell_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show items and materials in inventory that can be sold."""
        items_data = load_items()
        with open("data/materials.json") as f:
            import json as _j
            mats_data = _j.load(f)["materials"]
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT item_id, quantity FROM player_inventory WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                inv_items = {r["item_id"]: r["quantity"] for r in await c.fetchall()}
            async with db.execute(
                "SELECT material_id, quantity FROM player_materials WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                inv_mats = {r["material_id"]: r["quantity"] for r in await c.fetchall()}
        choices = []
        for iid, qty in inv_items.items():
            item = items_data.get(iid)
            if item and current.lower() in item["name"].lower():
                choices.append(app_commands.Choice(name=f"{item['name']} (x{qty})", value=iid))
        for mid, qty in inv_mats.items():
            mat = mats_data.get(mid)
            if mat and current.lower() in mat["name"].lower():
                choices.append(app_commands.Choice(name=f"{mat['name']} (x{qty})", value=mid))
        return choices[:25]

    @app_commands.command(name="sell", description="Sell items or materials for gold 💰")
    @app_commands.describe(item_name="Item or material to sell")
    @app_commands.autocomplete(item_name=sell_autocomplete)
    async def sell(self, interaction: discord.Interaction, item_name: str):
        from utils.modals import QuantityModal

        with open("data/items.json") as f:
            items_data = json.load(f)["items"]
        materials = load_materials()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        item_id = item_name.lower().replace(" ", "_").replace("-", "_")
        item = items_data.get(item_id)
        is_material = False

        if not item:
            mat = materials.get(item_id)
            if mat:
                item = mat
                is_material = True
            else:
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
            return await interaction.response.send_message(embed=discord.Embed(
                description=f"✦ `{item_name}` not found.", color=COLORS["error"]
            ), ephemeral=True)

        table = "player_materials" if is_material else "player_inventory"
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT id, quantity FROM {table} WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, item_id)
            ) as c:
                inv_row = await c.fetchone()

        if not inv_row or inv_row["quantity"] < 1:
            return await interaction.response.send_message(embed=discord.Embed(
                description=f"✦ You don't have any **{item['name']}** to sell.",
                color=COLORS["error"]
            ), ephemeral=True)

        MATERIAL_PRICES = {
            "common": 20, "uncommon": 60, "rare": 150, "epic": 400,
            "legendary": 1000, "altered_divine": 3000
        }

        async def do_sell(modal_interaction: discord.Interaction, quantity: int):
            await modal_interaction.response.defer(ephemeral=True)
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT 1 FROM player_perks WHERE user_id = ? AND perk_id = 'whimsy_merchant' AND equipped = 1",
                    (modal_interaction.user.id,)
                ) as c:
                    has_merchant = await c.fetchone()
                async with db.execute(
                    f"SELECT id, quantity FROM {table} WHERE user_id = ? AND item_id = ?",
                    (modal_interaction.user.id, item_id)
                ) as c:
                    fresh = await c.fetchone()
                if not fresh or fresh["quantity"] < quantity:
                    return await modal_interaction.followup.send(
                        f"✦ You only have `{fresh['quantity'] if fresh else 0}` now.", ephemeral=True
                    )
                base_price = MATERIAL_PRICES.get(item.get("rarity","common"), 20) if is_material else max(5, int(item.get("price",0)*0.35))
                if has_merchant:
                    base_price = int(base_price * 1.20)
                total = base_price * quantity
                if fresh["quantity"] == quantity:
                    await db.execute(f"DELETE FROM {table} WHERE id = ?", (fresh["id"],))
                else:
                    await db.execute(f"UPDATE {table} SET quantity = quantity - ? WHERE id = ?", (quantity, fresh["id"]))
                await db.execute("UPDATE players SET gold = gold + ? WHERE user_id = ?", (total, modal_interaction.user.id))
                await db.commit()
            merchant_tag = " *(Whimsy Merchant bonus!)*" if has_merchant else ""
            await modal_interaction.followup.send(embed=discord.Embed(
                title="💰 Sold!",
                description=f"Sold `{quantity}x` **{item['name']}** for **{total:,} gold**{merchant_tag}\nBalance: `{player['gold'] + total:,} gold`",
                color=COLORS["success"]
            ), ephemeral=True)

        await interaction.response.send_modal(QuantityModal(
            title=f"Sell {item['name']}",
            item_name=item["name"],
            max_quantity=inv_row["quantity"],
            callback=do_sell
        ))

    # ── /release ──────────────────────────────────────────────────────────
    @app_commands.command(name="release", description="Release a beast back into the wild 🌿")
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def release(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()

        beast_row = await get_beast_by_player_number(interaction.user.id, beast_id)
        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Beast `#{beast_id}` not found in your collection!", color=COLORS["error"]
            ))
        actual_id = beast_row["id"]
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
            await db.execute("DELETE FROM player_beasts WHERE id = ?", (actual_id,))
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
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def evolve(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        all_beasts = load_beasts()

        beast_row = await get_beast_by_player_number(interaction.user.id, beast_id)
        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Beast `#{beast_id}` not found!", color=COLORS["error"]
            ))
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
                beast_row["id"]
            ))
            await db.commit()

        rarity_emoji = RARITY_EMOJI.get(target_data["rarity"], "⚪")
        color = COLORS.get(target_data["rarity"], COLORS["legendary"])
        form  = evolution.get("form", "")

        # ── Cinematic scene (if one exists for this target) ────────────────
        scene = EVOLUTION_SCENES.get(target_id)
        if scene:
            scene_embed = discord.Embed(
                title=scene["title"],
                description="\n\n".join(scene["lines"]),
                color=COLORS.get(scene["color"], color)
            )
            if target_data.get("image_url"):
                scene_embed.set_image(url=target_data["image_url"])
            await interaction.followup.send(embed=scene_embed)

        # ── Result embed ───────────────────────────────────────────────────
        form_title, form_tagline = EVOLUTION_FORM_LABELS.get(form, ("🌟 Evolution!", ""))

        embed = discord.Embed(
            title=form_title,
            description=(
                f"{rarity_emoji} **{beast_data['name']}** → **{target_data['name']}**\n"
                f"*{target_data['title']}*\n\n"
                f"{target_data['description']}\n\n"
                + (f"*{form_tagline}*" if form_tagline else "")
            ),
            color=color
        )

        # Stat summary
        from utils.db import calc_stat_growth
        levels_done = max(0, beast_row["level"] - 1)
        growth = calc_stat_growth({"rarity": target_data["rarity"]}, levels_done)
        new_stats = target_data["base_stats"]
        embed.add_field(
            name="📊 New Stats",
            value=(
                f"❤️ `{new_stats['hp'] + growth['hp']}HP` · "
                f"⚔️ `{new_stats['attack'] + growth['attack']}ATK` · "
                f"🛡️ `{new_stats['defense'] + growth['defense']}DEF` · "
                f"💨 `{new_stats['speed'] + growth['speed']}SPD`"
            ),
            inline=False
        )

        if target_data.get("divine_passive"):
            dp = target_data["divine_passive"]
            embed.add_field(
                name=f"✨ New Divine Passive: **{dp['passive_name']}**",
                value=dp["passive_desc"],
                inline=False
            )

        embed.set_footer(text="Use /beastinfo to inspect · /collection to see your full roster")
        if target_data.get("image_url"):
            embed.set_thumbnail(url=target_data["image_url"])
        await interaction.followup.send(embed=embed)

        # Check achievements after evolution
        unlocked = await check_achievements(interaction.user.id)
        if unlocked:
            from utils.progress import notify_unlocks
            await notify_unlocks(interaction.channel, interaction.user, unlocked)


    # ── /shard_shop ───────────────────────────────────────────────────────
async def _handle_shard_item(db, user_id: int, sid: str, shop_item: dict) -> str:
    """Execute the side-effect of a shard shop purchase. Returns result description string."""
    import time as _time
    item_type = shop_item["type"]
    result_desc = ""
    if item_type == "explore_boost":
        boost_until = _time.time() + (3 * 3600)
        await db.execute("UPDATE players SET incense_active_until = ? WHERE user_id = ?", (boost_until, user_id))
        result_desc = "Your next 3 `/explore` runs have boosted Divine odds in the Celestial Loom!"
    elif item_type == "incubation_skip":
        from datetime import datetime, timezone, timedelta
        async with db.execute(
            "SELECT id, egg_name, ready_at FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY started_at ASC LIMIT 1",
            (user_id,)
        ) as c:
            egg = await c.fetchone()
        if egg:
            new_ready = datetime.strptime(egg["ready_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) - timedelta(hours=6)
            await db.execute("UPDATE incubating_eggs SET ready_at = ? WHERE id = ?", (new_ready.strftime("%Y-%m-%d %H:%M:%S"), egg["id"]))
            result_desc = f"**{egg['egg_name']}** incubation reduced by 6 hours!"
        else:
            result_desc = "No eggs currently incubating — the fragment is yours to use later."
    elif item_type == "key":
        await db.execute("UPDATE players SET brew_active = brew_active + 1 WHERE user_id = ?", (user_id,))
        result_desc = "Prism Key added. Your next `/explore` in the Celestial Loom will have a 30% Divine rate."
    elif item_type in ["cosmetic", "reroll"]:
        result_desc = f"**{shop_item['name']}** is now yours."
    elif item_type == "grant_item":
        grant_id = shop_item["grant_item_id"]
        async with db.execute("SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?", (user_id, grant_id)) as c:
            inv_row = await c.fetchone()
        if inv_row:
            await db.execute("UPDATE player_inventory SET quantity = quantity + 1 WHERE id = ?", (inv_row["id"],))
        else:
            await db.execute("INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?, ?, 1)", (user_id, grant_id))
        result_desc = "Added to your inventory. Use `/ancient` to summon."
    return result_desc


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
    async def help_cmd(self, interaction: discord.Interaction):
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

        uid = interaction.user.id

        HELP_OPTIONS = [
            ("start",    "🌱", "Getting Started"),
            ("beasts",   "🐾", "Beasts & Collection"),
            ("battle",   "⚔️", "Battle"),
            ("explore",  "🌍", "Exploration & Eggs"),
            ("craft",    "⚒️", "Crafting & Equipment"),
            ("guild",    "🏰", "Guilds & Raids"),
            ("progress", "📋", "Quests & Progression"),
            ("economy",  "💰", "Economy & Trading"),
            ("lore",     "📖", "Lore & World"),
        ]

        def build_help_embed(category: str) -> discord.Embed:
            cat = HELP.get(category, HELP["start"])
            embed = discord.Embed(title=cat["title"], description=cat["desc"], color=COLORS["info"])
            for cmd, desc in cat["commands"]:
                embed.add_field(name=f"`{cmd}`", value=desc, inline=False)
            embed.set_footer(text="ChibiBeasts 🐾")
            return embed

        class HelpView(discord.ui.View):
            def __init__(self_v, section="start"):
                super().__init__(timeout=180)
                self_v.section = section
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                select = discord.ui.Select(
                    placeholder="📚 Browse a category…",
                    options=[
                        discord.SelectOption(label=f"{emoji} {name}", value=key, default=key==self_v.section)
                        for key, emoji, name in HELP_OPTIONS
                    ],
                    row=0
                )
                async def _on_select(bi):
                    if bi.user.id != uid:
                        return await bi.response.send_message("✦ This isn't your help menu!", ephemeral=True)
                    self_v.section = bi.data["values"][0]
                    self_v._rebuild()
                    await bi.response.edit_message(embed=build_help_embed(self_v.section), view=self_v)
                select.callback = _on_select
                self_v.add_item(select)

        await interaction.followup.send(embed=build_help_embed("start"), view=HelpView("start"))

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
    async def history(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = interaction.user.id
        category = "battles"  # default; overridden by select

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

        embed.set_footer(text="ChibiBeasts 🐾")

        HIST_OPTIONS = [
            ("battles", "⚔️", "Battles"),
            ("raids",   "💀", "Raids"),
            ("trades",  "🤝", "Trades"),
        ]

        async def build_hist_embed(cat: str) -> discord.Embed:
            return await self.history.__wrapped__(self, interaction, cat) if False else embed

        class HistView(discord.ui.View):
            def __init__(self_v, section, first_embed):
                super().__init__(timeout=120)
                self_v.section = section
                self_v.last_embed = first_embed
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                select = discord.ui.Select(
                    placeholder="📜 Switch history…",
                    options=[
                        discord.SelectOption(label=f"{emoji} {name}", value=key, default=key==self_v.section)
                        for key, emoji, name in HIST_OPTIONS
                    ],
                    row=0
                )
                async def _on_select(bi):
                    if bi.user.id != uid:
                        return await bi.response.send_message("✦ This isn't your history!", ephemeral=True)
                    await bi.response.defer()
                    new_cat = bi.data["values"][0]
                    # Rebuild embed for new category
                    async with aiosqlite.connect(DB_PATH) as _db:
                        _db.row_factory = aiosqlite.Row
                        new_emb = await _fetch_history_embed(_db, bi.user.id, new_cat)
                    new_emb.set_footer(text="ChibiBeasts 🐾")
                    self_v.section = new_cat
                    self_v._rebuild()
                    await bi.edit_original_response(embed=new_emb, view=self_v)
                select.callback = _on_select
                self_v.add_item(select)

        async def _fetch_history_embed(db, user_id, cat):
            if cat == "battles":
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
                """, (user_id, user_id)) as c:
                    rows = [dict(r) for r in await c.fetchall()]
                emb = discord.Embed(title="⚔️ Battle History",
                    description=f"*Your last {len(rows)} battles.*" if rows else "*No battles recorded yet.*",
                    color=COLORS["epic"])
                for r in rows:
                    btype = r["battle_type"] or "pvp"
                    if btype == "pvp":
                        opponent_name = r["opponent_name"] or "Unknown"
                        result = "✅ Win" if r["winner_id"] == user_id else ("🤝 Draw" if r["winner_id"] is None else "💤 Loss")
                        label = f"vs {opponent_name}"
                    elif btype == "sparr":
                        result = "✅ Win" if r["winner_id"] == user_id else "💤 Loss"
                        label = "NPC Spar"
                    else:
                        result = "✅ Win" if r["winner_id"] == user_id else "💤 Loss"
                        label = "Wild Battle"
                    ts = r["created_at"][:10] if r["created_at"] else "?"
                    emb.add_field(name=f"{result} — {label}", value=f"*{btype.upper()} · {ts}*", inline=True)
                return emb

            elif cat == "raids":
                async with db.execute("""
                    SELECT r.boss_name, r.boss_type, r.status, r.started_at,
                           rp.damage_dealt
                    FROM raid_participants rp
                    JOIN raids r ON rp.raid_id = r.id
                    WHERE rp.user_id = ?
                    ORDER BY r.started_at DESC LIMIT 15
                """, (user_id,)) as c:
                    rows = [dict(r) for r in await c.fetchall()]
                emb = discord.Embed(title="💀 Raid History",
                    description=f"*Your last {len(rows)} raids.*" if rows else "*No raids yet.*",
                    color=COLORS["legendary"])
                for r in rows:
                    icon = "🏆" if r["status"] == "completed" else "⏰"
                    ts   = r["started_at"][:10] if r["started_at"] else "?"
                    emb.add_field(name=f"{icon} {r['boss_name']}", value=f"`{r['damage_dealt']:,}` dmg · {ts}", inline=True)
                return emb

            else:  # trades
                async with db.execute("""
                    SELECT t.*, p1.username AS sender_name, p2.username AS receiver_name,
                           pb1.beast_id AS sent_beast_id, pb2.beast_id AS received_beast_id,
                           pb1.rarity AS sent_rarity, pb2.rarity AS received_rarity
                    FROM trades t
                    LEFT JOIN players p1 ON t.sender_id = p1.user_id
                    LEFT JOIN players p2 ON t.receiver_id = p2.user_id
                    LEFT JOIN player_beasts pb1 ON t.sender_beast_id = pb1.id
                    LEFT JOIN player_beasts pb2 ON t.receiver_beast_id = pb2.id
                    WHERE t.sender_id = ? OR t.receiver_id = ?
                    ORDER BY t.created_at DESC LIMIT 15
                """, (user_id, user_id)) as c:
                    rows = [dict(r) for r in await c.fetchall()]
                from utils.db import get_beast_data as _gbd
                emb = discord.Embed(title="🤝 Trade History",
                    description=f"*Your last {len(rows)} trades.*" if rows else "*No trades yet.*",
                    color=COLORS["success"])
                for r in rows:
                    sent_bd = _gbd(r["sent_beast_id"]) if r.get("sent_beast_id") else None
                    recv_bd = _gbd(r.get("received_beast_id")) if r.get("received_beast_id") else None
                    sent_name = sent_bd["name"] if sent_bd else "?"
                    recv_name = recv_bd["name"] if recv_bd else "anything"
                    sent_r = RARITY_EMOJI.get(r.get("sent_rarity"), "⚪")
                    recv_r = RARITY_EMOJI.get(r.get("received_rarity"), "⚪") if recv_bd else ""
                    direction = "📤 Sent" if r["sender_id"] == user_id else "📥 Received"
                    other = r["receiver_name"] if r["sender_id"] == user_id else r["sender_name"]
                    gold_note = f" + `{r['gold_offered']:,}` 💰" if r.get("gold_offered") else ""
                    ts = r["created_at"][:10] if r.get("created_at") else "?"
                    emb.add_field(name=f"{direction} with {other or '?'}",
                        value=f"{sent_r} {sent_name}{gold_note} ↔ {recv_r} {recv_name} · {ts}", inline=False)
                return emb

        await interaction.followup.send(embed=embed, view=HistView("battles", embed))


    @app_commands.command(name="party", description="Quick view of your raid party status 🐾")
    async def party(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from utils.db import get_raid_party, is_knocked_out, ko_time_remaining, get_beast_data as _gbd
        from utils.theme import RARITY_EMOJI as _RE
        uid = interaction.user.id
        party = await get_raid_party(uid)
        if not any(party):
            return await interaction.followup.send(
                "✦ No raid party set up. Use `/raidparty` to assign your 3 beasts.", ephemeral=True
            )
        embed = discord.Embed(title="⚔️ Raid Party", color=COLORS.get("legendary", 0xFFD700))
        slot_labels = ["🥇 Slot 1", "🥈 Slot 2", "🥉 Slot 3"]
        ready = 0
        for i, beast in enumerate(party):
            if beast:
                bd = _gbd(beast["beast_id"]) or {}
                emoji = _RE.get(beast["rarity"], "⚪")
                name = beast.get("nickname") or bd.get("name", "?")
                ko = is_knocked_out(beast)
                if ko:
                    val = f"💀 **Knocked out** — `{ko_time_remaining(beast)}` remaining"
                else:
                    val = f"❤️ `{beast['hp']}/{beast['max_hp']}HP` · Lv.{beast['level']} · `{beast['attack']}ATK`"
                    ready += 1
                embed.add_field(name=f"{slot_labels[i]}: {emoji} {name}", value=val, inline=False)
            else:
                embed.add_field(name=slot_labels[i], value="*Empty*", inline=False)
        filled = sum(1 for b in party if b)
        if filled < 3:
            status = f"⚠️ {filled}/3 filled"
        elif ready < 3:
            status = f"💀 {filled-ready} recovering — raids locked"
        else:
            status = "✅ Party ready!"
        embed.set_footer(text=status + " · /raidparty to edit")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="raidparty", description="Set up your 3-beast raid party ⚔️")
    async def raidparty(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = interaction.user.id
        await get_or_create_player(uid, str(interaction.user))

        async def build_embed(party: list) -> discord.Embed:
            embed = discord.Embed(
                title="⚔️ Raid Party",
                description=(
                    "Your raid party is the team of 3 beasts that fight together in all raids.\n"
                    "All 3 slots must be filled before you can join or attack in a raid.\n\n"
                    "*Select a slot below to assign or change a beast.*"
                ),
                color=COLORS.get("legendary", 0xFFD700)
            )
            slot_labels = ["🥇 Slot 1 — Front", "🥈 Slot 2 — Mid", "🥉 Slot 3 — Bench"]
            ready = 0
            for i, beast in enumerate(party):
                if beast:
                    bd    = get_beast_data(beast["beast_id"]) or {}
                    emoji = RARITY_EMOJI.get(beast["rarity"], "⚪")
                    name  = beast.get("nickname") or bd.get("name", "?")
                    ko    = is_knocked_out(beast)
                    timer = ko_time_remaining(beast)
                    if ko:
                        status_line = f"💀 **Knocked out** — recovers in `{timer}`\n*Use a Phoenix Elixir to revive instantly*"
                    else:
                        status_line = f"`{beast['hp']}/{beast['max_hp']}HP` · `{beast['attack']}ATK` · `{beast['defense']}DEF`"
                        ready += 1
                    embed.add_field(
                        name=slot_labels[i],
                        value=(
                            f"{emoji} **{name}** `#{beast['player_number']}` · Lv.{beast['level']}\n"
                            f"{status_line}"
                        ),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name=slot_labels[i],
                        value="*Empty — click to assign*",
                        inline=False
                    )
            filled = sum(1 for b in party if b)
            if filled < 3:
                status = f"⚠️ {filled}/3 slots filled — raids locked until full"
            elif ready < 3:
                ko_count = filled - ready
                status = f"💀 {ko_count} beast{'s' if ko_count>1 else ''} recovering — raids locked until revived"
            else:
                status = "✅ Party ready!"
            embed.set_footer(text=status)
            return embed

        class SlotModal(discord.ui.Modal, title="Assign Beast to Slot"):
            beast_num = discord.ui.TextInput(
                label="Beast Number (e.g. 5)",
                placeholder="Enter your beast's #number from /collection",
                min_length=1, max_length=6
            )
            def __init__(self, slot: int, view: "PartyView"):
                super().__init__()
                self.slot = slot
                self.party_view = view

            async def on_submit(self, modal_interaction: discord.Interaction):
                raw = self.beast_num.value.strip().lstrip("#")
                if not raw.isdigit():
                    return await modal_interaction.response.send_message(
                        "✦ Enter a valid beast number.", ephemeral=True
                    )
                from utils.db import get_beast_by_player_number
                beast_row = await get_beast_by_player_number(uid, int(raw))
                if not beast_row:
                    return await modal_interaction.response.send_message(
                        f"✦ Beast `#{raw}` not found in your collection.", ephemeral=True
                    )
                await set_raid_slot(uid, beast_row["id"], self.slot)
                new_party = await get_raid_party(uid)
                # Rebuild buttons first, then edit — one atomic update
                self.party_view.party = new_party
                self.party_view._build_buttons()
                await modal_interaction.response.edit_message(
                    embed=await build_embed(new_party),
                    view=self.party_view
                )

        class PartyView(discord.ui.View):
            def __init__(self, party: list):
                super().__init__(timeout=120)
                self.party = party
                self._build_buttons()

            def _build_buttons(self):
                self.clear_items()
                slot_emojis = ["🥇", "🥈", "🥉"]
                for i, beast in enumerate(self.party):
                    label = f"Set Slot {i+1}" if not beast else f"Change Slot {i+1}"
                    btn = discord.ui.Button(
                        label=label, emoji=slot_emojis[i],
                        style=discord.ButtonStyle.primary if not beast else discord.ButtonStyle.secondary,
                        row=0
                    )
                    async def _assign(inter, slot=i+1, v=self):
                        await inter.response.send_modal(SlotModal(slot, v))
                    btn.callback = _assign
                    self.add_item(btn)

                    if beast:
                        clear_btn = discord.ui.Button(
                            label=f"Clear {i+1}", emoji="✖️",
                            style=discord.ButtonStyle.danger,
                            row=1
                        )
                        async def _clear(inter, slot=i+1, v=self):
                            await clear_raid_slot(uid, slot)
                            new_party = await get_raid_party(uid)
                            v.party = new_party
                            v._build_buttons()
                            await inter.response.edit_message(embed=await build_embed(new_party), view=v)
                        clear_btn.callback = _clear
                        self.add_item(clear_btn)

        party = await get_raid_party(uid)
        view  = PartyView(party)
        await interaction.followup.send(embed=await build_embed(party), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utilities(bot))
