import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_player_beasts, get_active_beast, get_inventory,
    add_item, remove_item, load_beasts, load_items, load_perks,
    get_beast_data, calc_exp_for_level, calc_player_exp_for_level,
    get_perk_slots, apply_beast_levelup, get_beast_exp_for_level
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, hp_bar, exp_bar, fmt_stats, SPARKLE
from utils.progress import track_quest_event, notify_quest_completions
from utils.dispositions import disposition_display

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View your trainer profile 📖")
    @app_commands.describe(member="View another trainer's profile")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        await interaction.response.defer()
        player = await get_or_create_player(target.id, str(target))
        beasts = await get_player_beasts(target.id)
        active = await get_active_beast(target.id)

        exp_needed = calc_player_exp_for_level(player["level"])
        beast_count = len(beasts)
        rarity_counts = {}
        for b in beasts:
            rarity_counts[b["rarity"]] = rarity_counts.get(b["rarity"], 0) + 1

        collection_str = " ".join(
            f"{RARITY_EMOJI.get(r, '⚪')}`{c}`"
            for r, c in sorted(rarity_counts.items(), key=lambda x: ["common","uncommon","rare","epic","legendary","divine","altered_divine"].index(x[0]) if x[0] in ["common","uncommon","rare","epic","legendary","divine","altered_divine"] else 99)
        ) or "No beasts yet!"

        title_line = f"🏷️ **Title:** *{player.get('title', 'None')}*\n" if player.get('title') else ""

        embed = discord.Embed(
            title=f"🐾 {target.display_name}'s Trainer Profile",
            description=title_line,
            color=COLORS["divine"]
        )
        embed.add_field(
            name="📊 Trainer Stats",
            value=(
                f"⭐ **Level:** {player['level']}\n"
                f"✨ **EXP:** {exp_bar(player['exp'], exp_needed)}\n"
                f"💰 **Gold:** `{player['gold']:,}`\n"
                f"💎 **Celestial Shards:** `{player['celestial_shards']}` — *spend at `/shard_shop`*\n"
                f"🎟️ **Guild Tokens:** `{player['guild_tokens']}`"
            ),
            inline=False
        )
        embed.add_field(
            name="⚔️ Battle Record",
            value=f"✨ Victories: `{player['wins']}` | 💤 Lessons: `{player['losses']}`",
            inline=True
        )
        embed.add_field(
            name="🐾 Collection",
            value=f"**{beast_count}** beasts total\n{collection_str}",
            inline=True
        )
        if active:
            beast_data = get_beast_data(active["beast_id"])
            if beast_data:
                name = active["nickname"] or beast_data["name"]
                embed.add_field(
                    name="⚔️ Active Beast",
                    value=(
                        f"{RARITY_EMOJI.get(active['rarity'], '⚪')} **{name}** — Lv.{active['level']}\n"
                        f"❤️ {hp_bar(active['hp'], active['max_hp'])}"
                    ),
                    inline=False
                )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="ChibiBeasts 🐾  •  /collection to see all beasts")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="collection", description="View your ChibiBeast collection 🐾")
    @app_commands.describe(page="Page number", rarity="Filter by rarity")
    @app_commands.choices(rarity=[
        app_commands.Choice(name="All", value="all"),
        app_commands.Choice(name="⚪ Common", value="common"),
        app_commands.Choice(name="🟢 Uncommon", value="uncommon"),
        app_commands.Choice(name="🔵 Rare", value="rare"),
        app_commands.Choice(name="🟣 Epic", value="epic"),
        app_commands.Choice(name="🟡 Legendary", value="legendary"),
        app_commands.Choice(name="🌸 Divine", value="divine"),
        app_commands.Choice(name="⚠️ Altered Divine", value="altered_divine"),
    ])
    async def collection(self, interaction: discord.Interaction, page: int = 1, rarity: str = "all"):
        await interaction.response.defer()
        beasts = await get_player_beasts(interaction.user.id)
        if rarity != "all":
            beasts = [b for b in beasts if b["rarity"] == rarity]

        if not beasts:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No beasts found! Use `/hatch` or `/explore` to find some.",
                color=COLORS["info"]
            ))

        per_page = 8
        total_pages = max(1, (len(beasts) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        page_beasts = beasts[(page-1)*per_page : page*per_page]

        embed = discord.Embed(
            title=f"🐾 {interaction.user.display_name}'s Collection",
            description=f"**{len(beasts)}** beasts total | Page {page}/{total_pages}",
            color=COLORS["divine"]
        )
        for b in page_beasts:
            beast_data = get_beast_data(b["beast_id"])
            if not beast_data:
                continue
            name = b["nickname"] or beast_data["name"]
            rarity_emoji = RARITY_EMOJI.get(b["rarity"], "⚪")
            type_emoji = TYPE_EMOJI.get(beast_data["type"], "❓")
            active_tag = " ⚔️ **ACTIVE**" if b["is_active"] else ""
            fav_tag = " ⭐" if b["is_favorite"] else ""
            embed.add_field(
                name=f"{rarity_emoji} {name} — Lv.{b['level']}{active_tag}{fav_tag}",
                value=f"{type_emoji} {beast_data['type'].capitalize()} | ❤️ {b['hp']}/{b['max_hp']} | #{b['id']}",
                inline=True
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  /beastinfo <id> to view details")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="beastinfo", description="View detailed info about a specific beast 🔍")
    @app_commands.describe(beast_id="The ID of the beast from your collection")
    async def beastinfo(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as cursor:
                beast_row = await cursor.fetchone()

        if not beast_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Beast not found in your collection!", color=COLORS["error"]
            ))

        beast_row = dict(beast_row)
        beast_data = get_beast_data(beast_row["beast_id"])
        if not beast_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Beast data not found!", color=COLORS["error"]
            ))

        name = beast_row["nickname"] or beast_data["name"]
        rarity = beast_row["rarity"]
        exp_needed = calc_exp_for_level(beast_row["level"])

        embed = discord.Embed(
            title=f"{'⚠️ ALTERED ' if beast_row['is_altered_divine'] else ''}{RARITY_EMOJI.get(rarity,'⚪')} {name}",
            description=f"*{beast_data['title']}*\n{beast_data['description']}",
            color=COLORS.get(rarity, COLORS["info"])
        )
        embed.add_field(
            name="📊 Stats",
            value=fmt_stats(beast_row),
            inline=True
        )
        embed.add_field(
            name="📈 Progress",
            value=(
                f"⭐ Level: `{beast_row['level']}`\n"
                f"✨ EXP: {exp_bar(beast_row['exp'], exp_needed)}\n"
                f"😊 Happiness: `{beast_row['happiness']}/100`"
            ),
            inline=True
        )
        embed.add_field(
            name="⚡ Moves",
            value="\n".join(f"• {m}" for m in beast_data["moves"]) + f"\n🌟 **Ultimate:** {beast_data['ultimate']}",
            inline=False
        )
        embed.add_field(
            name="🎭 Disposition",
            value=disposition_display(beast_row.get("disposition")),
            inline=False
        )
        if beast_data.get("divine_passive"):
            dp = beast_data["divine_passive"]
            embed.add_field(
                name=f"✨ Divine Passive: **{dp['passive_name']}**",
                value=f"*{dp['passive_desc']}*",
                inline=False
            )
        if beast_data.get("starter"):
            embed.add_field(
                name="🏛️ Origin",
                value=f"*{beast_data.get('starter_house', 'Unknown House')} — {beast_data.get('starter_flavor', '')}*",
                inline=False
            )
        if beast_data.get("image_url"):
            embed.set_image(url=beast_data["image_url"])
        embed.set_footer(text=f"Beast ID: #{beast_row['id']} | Caught via: {beast_row['caught_from']}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="setactive", description="Set a beast as your active battle beast ⚔️")
    @app_commands.describe(beast_id="The ID of the beast to set as active")
    async def setactive(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            async with db.execute(
                "SELECT id FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as cursor:
                exists = await cursor.fetchone()
            if not exists:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ That beast isn't in your collection!", color=COLORS["error"]
                ))
            await db.execute("UPDATE player_beasts SET is_active = 0 WHERE user_id = ?", (interaction.user.id,))
            await db.execute("UPDATE player_beasts SET is_active = 1 WHERE id = ?", (beast_id,))
            await db.commit()
        await interaction.followup.send(embed=discord.Embed(
            description=f"✦ Beast `#{beast_id}` is now your active beast! ⚔️",
            color=COLORS["success"]
        ))

    @app_commands.command(name="nickname", description="Give your beast a nickname 💬")
    @app_commands.describe(beast_id="Beast ID", name="New nickname (max 20 chars)")
    async def nickname(self, interaction: discord.Interaction, beast_id: int, name: str):
        if len(name) > 20:
            return await interaction.response.send_message(embed=discord.Embed(
                description="✦ Nickname must be 20 characters or less!", color=COLORS["error"]
            ), ephemeral=True)
        async with aiosqlite.connect("db/chibibeast.db") as db:
            async with db.execute(
                "SELECT id FROM player_beasts WHERE id = ? AND user_id = ?",
                (beast_id, interaction.user.id)
            ) as c:
                exists = await c.fetchone()
            if not exists:
                return await interaction.response.send_message(embed=discord.Embed(
                    description="✦ Beast not found!", color=COLORS["error"]
                ), ephemeral=True)
            await db.execute("UPDATE player_beasts SET nickname = ? WHERE id = ?", (name, beast_id))
            await db.commit()
        await interaction.response.send_message(embed=discord.Embed(
            description=f"✦ Beast `#{beast_id}` has been named **{name}**! 💬",
            color=COLORS["success"]
        ))

class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="inventory", description="View your item inventory 🎒")
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer()
        inv = await get_inventory(interaction.user.id)
        items_data = load_items()

        if not inv:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Your inventory is empty! Visit the `/shop` to buy items.",
                color=COLORS["info"]
            ))

        embed = discord.Embed(title="🎒 Your Inventory", color=COLORS["info"])
        for entry in inv:
            item = items_data.get(entry["item_id"])
            if not item:
                continue
            rarity_emoji = RARITY_EMOJI.get(item["rarity"], "⚪")
            embed.add_field(
                name=f"{rarity_emoji} {item['name']} x{entry['quantity']}",
                value=item["description"][:80] + "...",
                inline=False
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  /use <item> to use an item")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="use", description="Use an item from your inventory 💊")
    @app_commands.describe(item_name="Name of the item to use")
    async def use(self, interaction: discord.Interaction, item_name: str):
        await interaction.response.defer()
        items_data = load_items()
        item_id = item_name.lower().replace(" ", "_").replace("-", "_")
        item = items_data.get(item_id)

        if not item:
            matches = [i for i in items_data.values() if item_name.lower() in i["name"].lower()]
            if matches:
                item = matches[0]
                item_id = item["id"]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Item `{item_name}` not found!", color=COLORS["error"]
                ))

        inv = await get_inventory(interaction.user.id)
        has_item = any(e["item_id"] == item_id and e["quantity"] > 0 for e in inv)
        if not has_item:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You don't have **{item['name']}** in your inventory!",
                color=COLORS["error"]
            ))

        active = await get_active_beast(interaction.user.id)
        if not active and item["type"] not in ["cooldown", "unlock", "reset"]:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast to use this item!", color=COLORS["error"]
            ))

        effect = item["effect"]
        result_lines = []

        async with aiosqlite.connect("db/chibibeast.db") as db:
            if "heal_percent" in effect and active:
                heal = int(active["max_hp"] * (effect["heal_percent"] / 100))
                new_hp = min(active["max_hp"], active["hp"] + heal)
                await db.execute("UPDATE player_beasts SET hp = ? WHERE id = ?", (new_hp, active["id"]))
                result_lines.append(f"❤️ Healed **{heal} HP** ({active['hp']} → {new_hp})")

            if "revive" in effect and active and active["hp"] <= 0:
                heal = int(active["max_hp"] * (effect.get("heal_percent", 50) / 100))
                await db.execute("UPDATE player_beasts SET hp = ? WHERE id = ?", (heal, active["id"]))
                result_lines.append(f"🔥 Revived with **{heal} HP**!")

            if "restore_mana_percent" in effect and active:
                restore = int(active["max_mana"] * (effect["restore_mana_percent"] / 100))
                new_mana = min(active["max_mana"], active["mana"] + restore)
                await db.execute("UPDATE player_beasts SET mana = ? WHERE id = ?", (new_mana, active["id"]))
                result_lines.append(f"💠 Restored **{restore} Mana**!")

            if "happiness" in effect and active:
                new_hap = min(100, active["happiness"] + effect["happiness"])
                await db.execute("UPDATE player_beasts SET happiness = ? WHERE id = ?", (new_hap, active["id"]))
                result_lines.append(f"😊 Happiness increased to **{new_hap}/100**!")

            if "exp" in effect and active:
                new_exp = active["exp"] + effect["exp"]
                new_level = active["level"]
                while new_exp >= get_beast_exp_for_level(active, new_level):
                    new_exp -= get_beast_exp_for_level(active, new_level)
                    new_level += 1
                await apply_beast_levelup(db, active, new_level, new_exp)
                result_lines.append(f"✨ Gained **{effect['exp']} EXP**!")
                if new_level > active["level"]:
                    result_lines.append(f"⬆️ **LEVEL UP!** Now Lv.{new_level}! Stats increased!")

            if "instant_levels" in effect and active:
                target_level = active["level"] + effect["instant_levels"]
                await apply_beast_levelup(db, active, target_level, 0)
                result_lines.append(f"⬆️ Leveled up **{effect['instant_levels']} levels** → Lv.{target_level}! Stats increased!")

            # Chrono-Biscuit: instantly ready the oldest incubating egg
            if item_id == "chrono_biscuit":
                async with db.execute(
                    "SELECT id, egg_name FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY started_at ASC LIMIT 1",
                    (interaction.user.id,)
                ) as c:
                    egg_row = await c.fetchone()
                if egg_row:
                    await db.execute(
                        "UPDATE incubating_eggs SET ready_at = datetime('now', '-1 minute') WHERE id = ?",
                        (egg_row[0],)
                    )
                    result_lines.append(f"⏰ **{egg_row[1]}** is now ready to hatch! Use `/hatchegg`.")
                else:
                    result_lines.append("⏰ No eggs currently incubating.")

            # Star-Candy Shards: +3 to a random combat stat permanently
            if "stat_boost" in effect and active:
                import random as _rand
                stat_choices = ["attack", "defense", "speed"]
                boosted_stat = _rand.choice(stat_choices)
                boost_amt = effect.get("stat_boost", 3)
                await db.execute(
                    f"UPDATE player_beasts SET {boosted_stat} = {boosted_stat} + ? WHERE id = ?",
                    (boost_amt, active["id"])
                )
                result_lines.append(f"⭐ **{boosted_stat.capitalize()}** permanently increased by **+{boost_amt}**!")


            # Aether Tonic — cure_all
            if "cure_all" in effect:
                result_lines.append("✨ All status conditions cleared! Effect persists into next battle.")

            # Sugarsprout Cupcake — temp speed boost flag
            if "speed_boost_percent" in effect and active:
                boost = int(active["speed"] * (effect["speed_boost_percent"] / 100))
                result_lines.append(f"💨 Speed boosted by **+{boost}** for your next battle!")
                await db.execute("UPDATE players SET brew_active = brew_active + 1 WHERE user_id = ?", (interaction.user.id,))

            # Spellbound Incense — encounter boost for 30 mins
            if "encounter_boost" in effect:
                import time as _time
                duration_mins = effect.get("duration_minutes", 30)
                until = _time.time() + (duration_mins * 60)
                await db.execute("UPDATE players SET incense_active_until = ? WHERE user_id = ?", (until, interaction.user.id))
                result_lines.append(f"🌿 Encounter boost active for **{duration_mins} minutes**! Uncommon and Rare beasts more likely in `/explore`.")

            # Krakenshale Brew — double defense next battle
            if "defense_multiplier" in effect:
                await db.execute("UPDATE players SET brew_active = brew_active + 2 WHERE user_id = ?", (interaction.user.id,))
                result_lines.append("🛡️ **Defense doubled** for your next battle! (Krakenshale Brew)")

            # Tear of the Leviathan — stat reset to base at current level
            if "reset_stats" in effect and active:
                from utils.db import load_beasts as _lb, calc_stat_growth as _csg
                all_b = _lb()
                bbase = all_b.get(active["beast_id"], {}).get("base_stats", {})
                if bbase:
                    growth = _csg(dict(active), active["level"] - 1)
                    await db.execute("""UPDATE player_beasts SET max_hp=?, hp=?, attack=?, defense=?, speed=?, mana=?, max_mana=? WHERE id=?""",
                        (bbase["hp"]+growth["hp"], bbase["hp"]+growth["hp"],
                         bbase["attack"]+growth["attack"], bbase["defense"]+growth["defense"],
                         bbase["speed"]+growth["speed"], bbase["mana"]+growth["mana"],
                         bbase["mana"]+growth["mana"], active["id"]))
                    result_lines.append("💎 **Stats fully reset** to optimal base values for current level!")

            # Genesis Fruit — unlock divine_trait_slot
            if "unlock_divine_trait" in effect and active:
                await db.execute("UPDATE player_beasts SET divine_trait = 'unlocked' WHERE id = ?", (active["id"],))
                result_lines.append("🌈 **Divine Trait Slot unlocked** on this beast!")
                result_lines.append("*Something stayed behind when the fruit disappeared.*")

            # Sunforge Core — evolve if level met
            if "evolution_trigger" in effect and active:
                from utils.db import load_beasts as _lb2, calc_stat_growth as _csg2
                all_b2 = _lb2()
                bdata = all_b2.get(active["beast_id"], {})
                evo = bdata.get("evolution")
                if evo and evo.get("method") == "sunforge_core":
                    lvl_req = evo.get("level_required", 1)
                    if active["level"] >= lvl_req:
                        tid = evo["evolves_to"]
                        tdata = all_b2.get(tid, {})
                        ns = tdata.get("base_stats", {})
                        g2 = _csg2({"rarity": tdata.get("rarity","common")}, active["level"]-1)
                        await db.execute("""UPDATE player_beasts SET beast_id=?,rarity=?,max_hp=?,hp=?,attack=?,defense=?,speed=?,mana=?,max_mana=? WHERE id=?""",
                            (tid, tdata.get("rarity","common"),
                             ns.get("hp",100)+g2["hp"], ns.get("hp",100)+g2["hp"],
                             ns.get("attack",50)+g2["attack"], ns.get("defense",50)+g2["defense"],
                             ns.get("speed",50)+g2["speed"], ns.get("mana",50)+g2["mana"],
                             ns.get("mana",50)+g2["mana"], active["id"]))
                        result_lines.append(f"🌟 **{bdata.get('name','Beast')}** evolved into **{tdata.get('name','?')}**!")
                        if tdata.get("divine_passive"):
                            result_lines.append(f"✨ New passive: **{tdata['divine_passive']['passive_name']}**!")
                    else:
                        result_lines.append(f"✦ Needs to be Level **{lvl_req}** to evolve. Currently Lv.{active['level']}.")
                elif evo:
                    result_lines.append(f"✦ This beast needs **{evo.get('method','?').replace('_',' ').title()}** to evolve, not a Sunforge Core.")
                else:
                    result_lines.append("✦ This beast has no evolution path.")

            await db.commit()

        await remove_item(interaction.user.id, item_id)
        result_text = "\n".join(result_lines) if result_lines else "Item used!"
        await interaction.followup.send(embed=discord.Embed(
            title=f"✦ Used {item['name']}",
            description=result_text,
            color=COLORS["success"]
        ))

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shop", description="Browse the ChibiBeasts shop 🏪")
    @app_commands.choices(category=[
        app_commands.Choice(name="🥚 Eggs", value="eggs"),
        app_commands.Choice(name="⚪ Common Items", value="common"),
        app_commands.Choice(name="🟢 Uncommon Items", value="uncommon"),
        app_commands.Choice(name="🔵 Rare Items", value="rare"),
    ])
    async def shop(self, interaction: discord.Interaction, category: str = "eggs"):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        items_data = load_items()

        embed = discord.Embed(
            title="🏪 ChibiBeasts Shop",
            description=f"💰 Your gold: `{player['gold']:,}` | 💎 Shards: `{player['celestial_shards']}`",
            color=COLORS["legendary"]
        )

        if category == "eggs":
            EGG_SHOP = [
                ("🥚 Common Egg",     200,    "Common → Uncommon. Fast. Low-stakes. Good for beginners."),
                ("🥚✨ Rare Egg",     1500,   "Uncommon → Legendary. Real odds at something impressive."),
                ("🌌🥚 Celestial Egg",8000,   "Epic → **Divine** (25% chance). The real hunt starts here."),
                ("🌊💎 Abyssal Egg",  25000,  "Legendary → **Divine** (55% chance). Mostly Divine. Patience rewarded."),
            ]
            embed.add_field(
                name="🥚 Hatch Eggs with `/hatch`",
                value="Instant hatching — no waiting. Odds shown below.",
                inline=False
            )
            for egg_name, price, desc in EGG_SHOP:
                embed.add_field(
                    name=f"{egg_name} — `{price:,} gold`",
                    value=desc,
                    inline=False
                )
            embed.add_field(
                name="🌱 Incubation Eggs",
                value="Named eggs (Sprout Pod, Prism Sphere, Volcanic Core, etc.) can be bought and placed in `/incubate` for timed hatching with different pools. Use `/incubate` after buying.",
                inline=False
            )
            embed.set_footer(text="Use /buy <egg name> to purchase • /hatch to hatch immediately • /incubate for timed eggs")
        else:
            shop_items = [i for i in items_data.values() if i["rarity"] == category and i["price"] > 0]
            for item in shop_items:
                rarity_emoji = RARITY_EMOJI.get(item["rarity"], "⚪")
                embed.add_field(
                    name=f"{rarity_emoji} {item['name']} — `{item['price']:,} gold`",
                    value=item["description"][:100],
                    inline=False
                )
            embed.set_footer(text="Use /buy <item name> to purchase")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="buy", description="Buy an item from the shop 💰")
    @app_commands.describe(item_name="Name of item to buy")
    async def buy(self, interaction: discord.Interaction, item_name: str):
        await interaction.response.defer()
        items_data = load_items()

        EGG_SHOP_MAP = {
            "common egg":          ("common_egg",          200),
            "rare egg":            ("rare_egg",            1500),
            "celestial egg":       ("celestial_egg",       8000),
            "abyssal egg":         ("abyssal_egg",         25000),
            "sprout pod":          ("sprout_pod",          300),
            "pebble shell":        ("pebble_shell",        300),
            "soot hatchling":      ("soot_hatchling",      300),
            "dewdrop bulb":        ("dewdrop_bulb",        1200),
            "gale nest":           ("gale_nest",           1200),
            "cavern core":         ("cavern_core",         1200),
            "prism sphere":        ("prism_sphere",        4000),
            "glow-spore cluster":  ("glow_spore",          4000),
            "eclipse pebble":      ("eclipse_pebble",      4000),
            "volcanic core":       ("volcanic_core",       12000),
            "nimbus cloud":        ("nimbus_cloud",        12000),
            "monolith relic":      ("monolith_relic",      12000),
            "abyssal trench orb":  ("abyssal_trench_orb",  50000),
            "dragon-hoard scale":  ("dragon_hoard_scale",  50000),
            "glacial monolith":    ("glacial_monolith",    50000),
        }
        INSTANT_HATCH_EGGS = {"common_egg", "rare_egg", "celestial_egg", "abyssal_egg"}

        normalized = item_name.lower().strip()
        egg_match = None
        for key, (egg_id, egg_price) in EGG_SHOP_MAP.items():
            if normalized in key or key in normalized:
                egg_match = (egg_id, egg_price, key.title())
                break

        # Resolve item and base price before opening any connection
        if egg_match:
            item_id, base_price, display_name = egg_match
            item_name_display = display_name
            is_egg = True
        else:
            item_id = item_name.lower().replace(" ", "_").replace("-", "_")
            item = items_data.get(item_id)
            if not item:
                matches = [i for i in items_data.values() if item_name.lower() in i["name"].lower()]
                if matches:
                    item = matches[0]
                    item_id = item["id"]
                else:
                    return await interaction.followup.send(embed=discord.Embed(
                        description=f"✦ Item `{item_name}` not found in the shop!",
                        color=COLORS["error"]
                    ))
            base_price = item["price"]
            item_name_display = item["name"]
            is_egg = False

        # ── Single atomic transaction: check perk + verify + deduct + grant ──
        # Using WHERE gold >= price as the race-condition guard.
        # If two concurrent requests both pass the Python-level check and race
        # to the DB, only the first UPDATE will match the WHERE clause and
        # return rowcount > 0. The second sees rowcount = 0 and aborts.
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row

            # Perk and balance read inside the same connection
            async with db.execute(
                "SELECT gold FROM players WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                player_row = await c.fetchone()
            if not player_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Player data not found. Use `/start` first.",
                    color=COLORS["error"]
                ))

            async with db.execute(
                "SELECT 1 FROM player_perks WHERE user_id = ? AND perk_id = 'whimsy_merchant' AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                has_merchant = await c.fetchone()

            price = int(base_price * 0.95) if has_merchant else base_price
            current_gold = player_row["gold"]

            if current_gold < price:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ You need `{price:,} gold` but only have `{current_gold:,}`!",
                    color=COLORS["error"]
                ))

            # Atomic deduct: WHERE gold >= price means the second of two
            # concurrent requests will see rowcount=0 and fail safely
            cursor = await db.execute(
                "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
                (price, interaction.user.id, price)
            )
            if cursor.rowcount == 0:
                await db.rollback()
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Purchase failed — your gold changed between clicks. Please try again.",
                    color=COLORS["error"]
                ))

            # Grant item within the same connection
            async with db.execute(
                "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, item_id)
            ) as c:
                inv_row = await c.fetchone()
            if inv_row:
                await db.execute(
                    "UPDATE player_inventory SET quantity = quantity + 1 WHERE id = ?",
                    (inv_row["id"],)
                )
            else:
                await db.execute(
                    "INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?, ?, 1)",
                    (interaction.user.id, item_id)
                )

            await db.commit()
            new_gold = current_gold - price

        merchant_tag = " *(Whimsy Merchant discount applied)*" if has_merchant else ""
        if is_egg and item_id in INSTANT_HATCH_EGGS:
            next_step = f"Use `/hatch` and select **{item_name_display}** to hatch immediately!"
        elif is_egg:
            next_step = f"Use `/incubate {item_name_display}` to start the incubation timer!"
        else:
            next_step = f"Check `/inventory` to use it."

        await interaction.followup.send(embed=discord.Embed(
            title=f"✦ Purchased {item_name_display}!{merchant_tag}",
            description=f"Spent `{price:,} gold` | Balance: `{new_gold:,} gold`\n{next_step}",
            color=COLORS["success"]
        ))

        spend_quests_completed = await track_quest_event(interaction.user.id, "spend_gold", amount=price)
        await notify_quest_completions(interaction.channel, spend_quests_completed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    await bot.add_cog(Inventory(bot))
    await bot.add_cog(Shop(bot))
