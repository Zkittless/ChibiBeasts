from utils import db as storage
from utils import gear_art
import asyncio
import io
import logging
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


def load_equipment():
    from utils.catalog import load_catalog
    data=load_catalog('equipment.json')
    return data['equipment'],data['runes']

def load_materials():
    from utils.catalog import load_catalog
    return load_catalog('materials.json')['materials']

# ── Shard Shop inventory ───────────────────────────────────────────────────────
SHARD_SHOP = {
    # ── Page 1: Utility items ────────────────────────────────────────────
    "astral_reroll": {
        "name": "🌌 Astral Reroll",
        "desc": "Attune an instant hatch to your chosen element at its rolled rarity. Saved until a matching form is available.",
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
        "min_level": 10,
    },
    "loom_fragment": {
        "name": "🧵 Loom Fragment",
        "desc": "Reduces the incubation time of your oldest egg by 6 hours.",
        "cost": 5,
        "weekly_limit": 2,
        "type": "incubation_skip",
    },
    # ── Page 2: Cosmetics & access ───────────────────────────────────────
    "prism_key": {
        "name": "🔑 Prism Key",
        "desc": "Gives one Celestial Loom exploration a 30% divine rate. Used before a Divine Compass charge.",
        "cost": 15,
        "weekly_limit": 1,
        "type": "key",
        "min_level": 10,
    },
    "beast_rename_token": {
        "name": "✏️ Rename Token",
        "desc": "Give a companion an extended nickname of up to 40 characters (free /nickname allows 20).",
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
        "cost": 60,
        "weekly_limit": 1,
        "type": "grant_item",
        "min_level": 10,
        "grant_item_id": "epoch_shard",
    },
    "firstborn_ember": {
        "name": "🔥 Firstborn Ember",
        "desc": "Calls Ancient Genesis to the altar. The flame that started everything, still burning. Also drops from Corrupted Dragon.",
        "cost": 60,
        "weekly_limit": 1,
        "type": "grant_item",
        "min_level": 10,
        "grant_item_id": "firstborn_ember",
    },
    "void_prism": {
        "name": "🌑 Void Prism",
        "desc": "Calls Ancient Abyss to the altar. Absorbs all light. The silence around it is wrong. Also drops from Corrupted Leviathan.",
        "cost": 60,
        "weekly_limit": 1,
        "type": "grant_item",
        "min_level": 10,
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
        async with storage.connect() as db:
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

        async with storage.connect() as db:
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
                "SELECT equipment_id FROM player_equipment WHERE user_id = ? AND beast_row_id = ? ORDER BY id",
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
        # Legacy rune rows may share this table; the visual uses the armor slot
        # only, consistently with combat preparation and the workshop.
        armor_id = next((gid for gid in armor_rows if all_gear.get(gid, {}).get('slot') == 'armor'), '')
        rune_id = rune_id or next((gid for gid in armor_rows if all_gear.get(gid, {}).get('slot') == 'rune'), None)

        embed = discord.Embed(
            title=f"🛡️ Gear — {r_emoji} {name} `#{beast_id}`",
            description=f"Lv.{beast_row['level']} · `{beast_row['hp']}/{beast_row['max_hp']}HP`",
            color=COLORS.get(beast_row["rarity"], COLORS["info"])
        )

        # Equipped armor
        if armor_id:
            for aid in (armor_id,):
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
        available_armor = [gid for gid in inv_gear if all_gear.get(gid,{}).get("slot") == "armor"]
        available_runes = [gid for gid in inv_gear if all_gear.get(gid,{}).get("slot") == "rune"]

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

        if not available_armor and not available_runes and not armor_id and not rune_id:
            embed.add_field(
                name="🔍 No Gear Available",
                value="Open `/craft` to browse recipes, check materials and craft gear.",
                inline=False
            )

        embed.set_footer(text="✦ /equip <item> <beast_id> to equip · /unequip <beast_id> to remove")
        portrait = await gear_art.render_portrait(beast_row['beast_id'], armor_id)
        if portrait is None:
            return await interaction.followup.send(embed=embed)
        with io.BytesIO(portrait.data) as buffer:
            file = discord.File(buffer, filename=portrait.filename, description=f'{name} equipment appearance.')
            embed.set_image(url=f'attachment://{portrait.filename}')
            try:
                async with asyncio.timeout(15):
                    await interaction.followup.send(embed=embed, file=file)
            except discord.HTTPException as error:
                if isinstance(error, discord.NotFound):
                    raise
                logging.getLogger('chibibeasts.gear.art').warning('Equipment portrait rejected (%s); showing gear text', error.status)
                embed.set_image(url=None)
                async with asyncio.timeout(15):
                    await interaction.followup.send(embed=embed)
            finally:
                file.close()

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
                    description=f"✦ `{item_name}` not found. Check `/craft` for craftable armor and runes.",
                    color=COLORS["error"]
                ))

        from utils.equipment import equip as equip_owned
        try:beast_row,gear=await equip_owned(interaction.user.id,beast_id,gear_id)
        except ValueError as error:return await interaction.followup.send(str(error))
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
        from utils.progress import track_quest_event as _eqtqe, notify_quest_completions as _eqnqc
        _eq_done = await _eqtqe(interaction.user.id, "equip")
        if _eq_done and interaction.channel:
            await _eqnqc(interaction.channel, _eq_done)
        await unlock_simple_achievement(interaction.user.id, "first_equip")
        if gear.get("rarity")=="legendary":await unlock_simple_achievement(interaction.user.id,"first_legendary_gear")
        await interaction.followup.send(embed=embed)

    # ── /unequip ──────────────────────────────────────────────────────────
    @app_commands.command(name="unequip", description="Remove equipment from a beast 🛡️")
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def unequip(self,interaction:discord.Interaction,beast_id:int):
        await interaction.response.defer()
        from utils.equipment import unequip as remove_gear
        try:removed=await remove_gear(interaction.user.id,beast_id)
        except ValueError as error:return await interaction.followup.send(str(error))
        await interaction.followup.send(embed=discord.Embed(title="🛡️ Equipment removed",description="\n".join(removed) or "This companion has no gear to remove.",color=COLORS["success"]))

    # ── /sell ─────────────────────────────────────────────────────────────
    async def sell_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show items and materials in inventory that can be sold."""
        items_data = load_items()
        with open("data/materials.json", encoding="utf-8") as f:
            import json as _j
            mats_data = _j.load(f)["materials"]
        async with storage.connect() as db:
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

        items_data = storage.load_items()
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
        key_column="material_id" if is_material else "item_id"
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT id, quantity FROM {table} WHERE user_id = ? AND {key_column} = ?",
                (interaction.user.id, item_id)
            ) as c:
                inv_row = await c.fetchone()

        if not inv_row or inv_row["quantity"] < 1:
            return await interaction.response.send_message(embed=discord.Embed(
                description=f"✦ You don't have any **{item['name']}** to sell.",
                color=COLORS["error"]
            ), ephemeral=True)

        from utils.economy_balance import MATERIAL_SELL_VALUES as MATERIAL_PRICES, item_sell_price

        async def do_sell(modal_interaction: discord.Interaction, quantity: int):
            await modal_interaction.response.defer(ephemeral=True)
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT 1 FROM player_perks WHERE user_id = ? AND perk_id = 'whimsy_merchant' AND equipped = 1",
                    (modal_interaction.user.id,)
                ) as c:
                    has_merchant = await c.fetchone()
                async with db.execute(
                    f"SELECT id, quantity FROM {table} WHERE user_id = ? AND {key_column} = ?",
                    (modal_interaction.user.id, item_id)
                ) as c:
                    fresh = await c.fetchone()
                if not fresh or fresh["quantity"] < quantity:
                    return await modal_interaction.followup.send(
                        f"✦ You only have `{fresh['quantity'] if fresh else 0}` now.", ephemeral=True
                    )
                base_price = MATERIAL_PRICES.get(item.get("rarity","common"), 20) if is_material else item_sell_price(item)
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
                description="✦ You can't release your active beast. Open `/journal` → Companions and make another active first.",
                color=COLORS["error"]
            ))

        # Confirm with a button
        from utils.economy_balance import RELEASE_VALUES as REFUND
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
                if inv.user.id != interaction.user.id:
                    return await inv.response.send_message('Not your choice!', ephemeral=True)
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
                f"You'll receive **{refund:,} gold** as a parting gift. Equipped armor and runes return to your inventory.\n"
                f"*This cannot be undone.*"
            ),
            color=COLORS.get(rarity, COLORS["info"])
        ), view=view)
        await view.wait()

        if not view.confirmed:
            return

        from utils.transfers import release_companion
        try:
            refund = await release_companion(interaction.user.id, actual_id, expected_species=beast_row['beast_id'])
        except ValueError as error:
            return await interaction.followup.send(str(error), ephemeral=True)

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
    async def evolve(self,interaction:discord.Interaction,beast_id:int):
        await interaction.response.defer()
        from utils.gameplay import evolve_beast
        try:before,after,stats=await evolve_beast(interaction.user.id,beast_id)
        except ValueError as error:return await interaction.followup.send(str(error))
        embed=discord.Embed(title=f"✨ {before['name']} became {after['name']}!",description=after['description']+"\n\nYour levels, nickname, bond and earned stat investments continue with your companion.",color=COLORS["divine"])
        scene=after.get("awakening_scene") or EVOLUTION_CINEMATICS.get(after["id"],{}).get("lines")
        if scene:embed.add_field(name="A familiar friend",value="\n\n".join(scene)[:1024],inline=False)
        if after.get("divine_passive"):embed.add_field(name=after["divine_passive"]["passive_name"],value=after["divine_passive"]["passive_desc"],inline=False)
        evolved = await storage.get_beast_by_player_number(interaction.user.id, beast_id)
        armor = await gear_art.equipped_armor_ids(interaction.user.id)
        await gear_art.send_owned_portrait(interaction, embed, after['id'], armor.get(evolved['id'], '') if evolved else '')
        if interaction.guild:
            from utils.progress import record_bestiary_sighting
            await record_bestiary_sighting(interaction.guild.id,after["id"],interaction.user.id)
        from cogs.hatch import check_collection_completion
        await check_collection_completion(interaction,after,load_beasts())


    # ── /shard_shop ───────────────────────────────────────────────────────

    # ── /daily ────────────────────────────────────────────────────────────
    @app_commands.command(name="daily", description="Claim your daily reward and apply sanctuary bonuses 🌅")
    async def daily(self,interaction:discord.Interaction):
        await interaction.response.defer()
        uid=interaction.user.id
        player=await get_or_create_player(uid,str(interaction.user))
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sanctuary=await get_user_sanctuary(uid)
        from utils.rewards import shards
        async with storage.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            claim=await db.execute("INSERT OR IGNORE INTO daily_quests(user_id,quest_id,progress,completed,date) VALUES(?,'daily_claim',1,1,?)",(uid,today))
            if not claim.rowcount:return await interaction.followup.send("Today's reward has already been claimed. Resets at midnight UTC.")
            from utils.economy_balance import gold_reward_level
            gold=100+gold_reward_level(player["level"])*15
            actual_shards=await shards(db,uid,2)
            await db.execute("UPDATE players SET gold=gold+? WHERE user_id=?",(gold,uid))
            if sanctuary.get("fairy_garden"):
                await db.execute("UPDATE player_beasts SET happiness=MIN(100,happiness+5) WHERE user_id=? AND is_active=0",(uid,))
            await db.commit()
        description=f"+**{gold} gold** 💰\n+**{actual_shards} Celestial Shards** 🔮"
        if sanctuary.get("fairy_garden"):description+="\n🌸 Fairy Garden: +5 happiness to benched companions."
        await interaction.followup.send(embed=discord.Embed(title="🌅 Daily reward claimed",description=description,color=COLORS["success"]))

    # ── /stats ────────────────────────────────────────────────────────────
    @app_commands.command(name="stats", description="View server-wide ChibiBeasts statistics 📊")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild.id if interaction.guild else 0

        async with storage.connect() as db:
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

    async def _command_guide(self, interaction, *, already_deferred=False):
        if not already_deferred:
            await interaction.response.defer()
        from utils.habitats import BIOMES
        player=await get_or_create_player(interaction.user.id,str(interaction.user))
        from utils.story import get_quest_state,load_questline
        state=await get_quest_state(interaction.user.id)
        next_ch=next((c for k,c in load_questline()["chapters"].items() if k not in state["completed_chapters"]),None)
        embed=discord.Embed(title="🌱 Your next step",description=("Choose your first companion with /start." if not player.get("starter_claimed") else f"Story: {next_ch['name'] if next_ch else 'The hollow is restored'} · /journal → Story.\n/journal → Companions shows bonds, active selection and awakening milestones."),color=COLORS["info"])
        categories={
            "Your Field Journal": "/journal — Adventure, Companions, Workshop, Story and Activities\nSwitch active companions and review bond awakenings in Companions; open your material bag in Workshop.",
            "Adventure": "/explore — begin or resume a saved expedition with choices; one charge every 30 minutes, banking up to three\n/challenge — wild battle, every 20 minutes\n/sparr — one spar per NPC per day\n/npc · /meet · /lore",
            "Companions": "/collection · /beastinfo · /nickname\n/play · /train · /evolve\n/codex · /bestiary · /typeinfo",
            "Eggs and items": "/shop — eggs, consumables and shard purchases\n/hatch — instant eggs\n/incubate · /eggs · /tend — timed eggs\n/inventory · /use [beast_number]",
            "Builds and trade": "/craft · /gear\n/equip · /unequip · /perks · /perk_equip · /perk_unequip · /perk_use\n/market · /list · /delist · /appraise · /sell · /release · /trade",
            "Guilds and endgame": "/guild_create · /guild · /guild_invite · /guild_promote · /guild_leave\n/contribute · /guild_shop · /sanctuary · /build\n/raidparty — set three companions before a raid\n/raid · /ancient — attack, guard and support with the raid buttons\n/battle · /ranked · /rank · /ranked_leaderboard",
            "Progress": "/daily · /dailies · /achievements · /profile\n/title · /history · /stats · /leaderboard · /party",
        }
        for label,value in categories.items():embed.add_field(name=label,value=value,inline=False)
        embed.add_field(name="Combat basics",value="Attack restores mana. Attack, skill and guard each earn one ultimate charge; an ultimate spends three charges and 50 mana. Raids start at zero charges. Prepared raids aim for 5–8 minutes: equip gear, tend allies, swap wounded companions and answer Weave patterns. Speed shortens action recovery; bosses resist repeated stuns.",inline=False)
        embed.add_field(name="Building your kit",value="Return home from an expedition to bank one material, even if the creature escapes. /craft or the journal's Workshop opens recipes, costs, sources, materials, crafting and equip/evolve buttons. Your Forge discount is included. Raids and /contribute earn personal Guild Tickets for /guild_shop; the guild's shared tokens fund /build and raid entry.",inline=False)
        embed.add_field(name="Available destinations",value=" · ".join(b["name"] for b in BIOMES.values() if player["level"]>=b["min_level"]),inline=False)
        await interaction.followup.send(embed=embed)

    # ── /title ────────────────────────────────────────────────────────────
    @app_commands.command(name="title", description="Set your active trainer title 🏷️")
    @app_commands.describe(new_title="The title to display (must be one you've earned)")
    async def title_cmd(self,interaction:discord.Interaction,new_title:str=None):
        await interaction.response.defer()
        from cogs.hatch import COLLECTION_REWARDS
        from utils.story import load_questline
        async with storage.connect() as db:
            async with db.execute("SELECT achievement_id FROM achievements WHERE user_id=?",(interaction.user.id,)) as c:earned={r[0] for r in await c.fetchall()}
            async with db.execute("SELECT choice FROM story_choices WHERE user_id=? AND chapter_id='chapter_10'",(interaction.user.id,)) as c:choice=await c.fetchone()
            titles=[r["title"] for collection,r in COLLECTION_REWARDS.items() if "collection_"+collection.lower().replace(" ","_") in earned]
            if "loom_witness" in earned:titles.append("Witness to the Loom")
            if "guild_heart" in earned:titles.append("Guild Heart")
            if choice:titles.append(load_questline()["chapters"]["chapter_10"]["choices"][choice[0]]["title"])
            if not new_title:return await interaction.followup.send("Earned titles:\n"+"\n".join(titles) if titles else "Complete stories, collections or earn a Guild Crest to unlock a title.")
            title=next((t for t in titles if t.lower()==new_title.lower()),None)
            if not title:return await interaction.followup.send("You have not earned that title. Use /title to see yours.")
            await db.execute("UPDATE players SET title=? WHERE user_id=?",(title,interaction.user.id));await db.commit()
        await interaction.followup.send(f"Title equipped: {title}.")

    # ── /play ─────────────────────────────────────────────────────────────
    @app_commands.command(name="play", description="Spend time with your active beast to boost their happiness 😊")
    async def play(self, interaction: discord.Interaction):
        await interaction.response.defer()
        import random as _r
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            # 2 hour cooldown on /play (tracked via play_last_at)
            import time as _t
            _now = _t.time()
            async with db.execute("SELECT play_last_at FROM players WHERE user_id = ?", (interaction.user.id,)) as c:
                _prow = await c.fetchone()
            _play_last = float(_prow["play_last_at"]) if _prow and _prow["play_last_at"] else 0
            from utils.rewards import cooldown
            _remaining = await cooldown(db,interaction.user.id,7200) - (_now - _play_last)
            if _remaining > 0:
                _m, _s = divmod(int(_remaining), 60)
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Your beast needs a little rest first. Ready in **{_m}m {_s}s**.",
                    color=COLORS["info"]
                ))

            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1",
                (interaction.user.id,)
            ) as c:
                active = await c.fetchone()

            if not active:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ You don't have an active beast! Open `/journal` → Companions → Make active first.",
                    color=COLORS["error"]
                ))

            active = dict(active)
            from utils.db import get_beast_data as _gbd
            beast_data = _gbd(active["beast_id"]) or {}
            name = active.get("nickname") or beast_data.get("name", "your beast")
            rarity = active.get("rarity", "common")

            # Happiness gain
            PLAY_GAIN = 15
            new_happiness = min(100, active["happiness"] + PLAY_GAIN)

            # Small EXP gain (scales with rarity)
            EXP_GAIN = {"common": 8, "uncommon": 10, "rare": 13, "epic": 16, "legendary": 20, "divine": 25}.get(rarity, 8)
            new_exp = active.get("exp", 0) + EXP_GAIN

            # Random item find — small chance to find something
            ITEM_FIND_CHANCE = 0.12
            found_item = None
            FINDABLE_ITEMS = [
                ("brambleberries", "🫐 Brambleberries"),
                ("star_candy_shards", "🍬 Star-Candy Shards"),
                ("spellbound_incense", "🌿 Spellbound Incense"),
            ]
            if _r.random() < ITEM_FIND_CHANCE:
                found_item = _r.choice(FINDABLE_ITEMS)
                async with db.execute(
                    "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                    (interaction.user.id, found_item[0])
                ) as c:
                    inv_row = await c.fetchone()
                if inv_row:
                    await db.execute("UPDATE player_inventory SET quantity = quantity + 1 WHERE id = ?", (inv_row["id"],))
                else:
                    await db.execute("INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?, ?, 1)", (interaction.user.id, found_item[0]))

            from utils.db import apply_beast_levelup,get_beast_exp_for_level
            new_level=active["level"]
            while new_exp>=get_beast_exp_for_level(active,new_level):
                new_exp-=get_beast_exp_for_level(active,new_level);new_level+=1
            await apply_beast_levelup(db,active,new_level,new_exp)
            await db.execute("UPDATE player_beasts SET happiness=? WHERE id=?",(new_happiness,active["id"]))
            await db.execute("UPDATE players SET play_last_at = ? WHERE user_id = ?", (_now, interaction.user.id))
            await db.commit()

        # Flavor lines by rarity tier
        PLAY_LINES = {
            "common": [
                f"*{name} chases something that isn't there, then pretends it wasn't doing that.*",
                f"*You sit with {name} for a while. It doesn't move much. That seems to be the point.*",
                f"*{name} leans against you for exactly three seconds, then walks away like it didn't happen.*",
                f"*You bring {name} somewhere new. It sniffs everything. Twice.*",
                f"*{name} does something small and perfect that you immediately want to tell someone about.*",
            ],
            "uncommon": [
                f"*{name} leads you somewhere and then acts like it was your idea.*",
                f"*{name} makes a sound you haven't heard before. You spend a while trying to figure out what it meant.*",
                f"*You throw something for {name}. It retrieves it, considers it, and keeps it.*",
            ],
            "rare": [
                f"*{name} shows you something. You're not sure what it is, but it feels significant.*",
                f"*{name} does something that shouldn't be possible for a beast its size. You choose not to mention it.*",
                f"*The air around {name} hums slightly while you're with it. You don't notice until it stops.*",
            ],
            "epic": [
                f"*{name} looks at you like it knows something you don't. It probably does.*",
                f"*You spend time with {name} and come away feeling like something was exchanged, though you can't say what.*",
                f"*{name} does something that reminds you why you chose it.*",
            ],
            "legendary": [
                f"*{name} tolerates your presence in a way that feels, somehow, like high praise.*",
                f"*Being near {name} is like standing next to something the world is still deciding how to categorize.*",
                f"*{name} regards you with ancient patience. You feel, briefly, like you're part of something larger.*",
            ],
            "divine": [
                f"*Time moves strangely when you're with {name}. You leave feeling like you've been somewhere.*",
                f"*{name} does nothing in particular. The nothing feels full.*",
                f"*You sit with {name} and the Loom hums faintly in the distance. {name} doesn't seem surprised.*",
            ],
        }
        tier = rarity if rarity in PLAY_LINES else "common"
        play_line = _r.choice(PLAY_LINES[tier])

        desc = (
            f"{play_line}\n\n"
            f"😊 **+{PLAY_GAIN} happiness** → `{new_happiness}/100`\n"
            f"✨ **+{EXP_GAIN} EXP**"
        )
        if found_item:
            desc += f"\n🎁 **Found:** {found_item[1]}! Added to your inventory."
        if new_happiness < 50:
            desc += "\n\n*Your beast could use more time together. Try `/shop` for happiness items.*"

        embed = discord.Embed(title=f"🐾 Time with {name}", description=desc, color=COLORS["success"])
        embed.set_footer(text="ChibiBeasts 🐾  •  /play again in 2 hours")
        armor = await gear_art.equipped_armor_ids(interaction.user.id)
        await gear_art.send_owned_portrait(interaction, embed, active['beast_id'], armor.get(active['id'], ''), thumbnail=True)

        # Track quest event
        from utils.progress import track_quest_event as _tqe, unlock_simple_achievement as _ua
        await _tqe(interaction.user.id, "play",beast_row_id=active["id"])
        await _ua(interaction.user.id, "first_play") if not _play_last else None

    # ── /history ──────────────────────────────────────────────────────────
    @app_commands.command(name="history", description="View your recent battle, raid, and trade history 📜")
    async def history(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = interaction.user.id
        category = "battles"  # default; overridden by select

        async with storage.connect() as db:
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
                    async with storage.connect() as _db:
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


async def command_guide(interaction):
    """The journal's command reference retains the detailed existing guide."""
    await Utilities(None)._command_guide(interaction, already_deferred=True)


async def _handle_shard_item(db,user_id,sid,shop_item):
    from utils.shops import shard_effect
    return await shard_effect(db,user_id,sid,shop_item)
