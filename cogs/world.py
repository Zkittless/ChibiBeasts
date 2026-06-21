import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import json
import random
from datetime import datetime, timezone, timedelta
from utils.db import (
    get_or_create_player, get_player, update_player,
    add_beast_to_player, load_beasts, get_beast_data
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.dispositions import roll_disposition
from utils.type_chart import TYPE_LORE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement,
    record_bestiary_sighting, notify_unlocks, notify_quest_completions
)
from utils.sanctuary import get_user_sanctuary, apply_craft_discount

DB_PATH = "db/chibibeast.db"

# ── Data loaders ──────────────────────────────────────────────────────────────
def load_materials():
    with open("data/materials.json") as f:
        return json.load(f)["materials"]

def load_equipment():
    with open("data/equipment.json") as f:
        d = json.load(f)
        return d["equipment"], d["runes"]

# ── Egg definitions ───────────────────────────────────────────────────────────
EGGS = {
    # Common eggs — fast, lower odds
    "sprout_pod":    {"name": "Sprout Pod",    "rarity": "common",    "emoji": "🌱", "incubation_hours": 1,
                      "pool": {"common": 0.80, "uncommon": 0.20},
                      "flavor": "A soft leafy green capsule that smells like fresh rain.",
                      "lore": "The Loom made these first, when it was still learning what shapes beasts come in."},
    "pebble_shell":  {"name": "Pebble Shell",  "rarity": "common",    "emoji": "🪨", "incubation_hours": 1,
                      "pool": {"common": 0.75, "uncommon": 0.25},
                      "flavor": "A rough stone-textured egg that looks like a common river rock.",
                      "lore": "Barkley once used one as a pillow. It hatched into a Goblin. They're still friends."},
    "soot_hatchling":{"name": "Soot Hatchling","rarity": "common",    "emoji": "🖤", "incubation_hours": 1,
                      "pool": {"common": 0.70, "uncommon": 0.25, "rare": 0.05},
                      "flavor": "A tiny warm egg covered in dark ash. Something impatient is inside.",
                      "lore": "Imp-adjacent. Best hatched somewhere fireproof."},
    # Uncommon eggs
    "dewdrop_bulb":  {"name": "Dewdrop Bulb",  "rarity": "uncommon",  "emoji": "💧", "incubation_hours": 4,
                      "pool": {"uncommon": 0.55, "rare": 0.35, "epic": 0.10},
                      "flavor": "A translucent water-filled egg that lightly ripples when touched.",
                      "lore": "Found near Kelpie territory. Handle carefully — it sloshes."},
    "gale_nest":     {"name": "Gale Nest",     "rarity": "uncommon",  "emoji": "🌬️", "incubation_hours": 4,
                      "pool": {"uncommon": 0.50, "rare": 0.38, "epic": 0.12},
                      "flavor": "A lightweight feathery egg that hovers an inch off the ground.",
                      "lore": "Has to be weighted down during hatching or it drifts away. Always upward."},
    "cavern_core":   {"name": "Cavern Core",   "rarity": "uncommon",  "emoji": "💎", "incubation_hours": 4,
                      "pool": {"uncommon": 0.55, "rare": 0.35, "epic": 0.10},
                      "flavor": "A dense egg embedded with glowing raw crystals.",
                      "lore": "Found in cave networks that don't appear on any map."},
    # Rare eggs
    "prism_sphere":  {"name": "Prism Sphere",  "rarity": "rare",      "emoji": "🔮", "incubation_hours": 8,
                      "pool": {"rare": 0.60, "epic": 0.30, "legendary": 0.10},
                      "flavor": "A crystal egg that refracts light into brilliant rainbows.",
                      "lore": "Prismite is drawn to these. It stares at them for hours."},
    "glow_spore":    {"name": "Glow-Spore Cluster","rarity": "rare",  "emoji": "🍄", "incubation_hours": 8,
                      "pool": {"rare": 0.55, "epic": 0.35, "legendary": 0.10},
                      "flavor": "A bioluminescent egg covered in glowing mushrooms.",
                      "lore": "The mushrooms grow during incubation. They're part of the beast. Don't pick them."},
    "eclipse_pebble":{"name": "Eclipse Pebble","rarity": "rare",      "emoji": "🌗", "incubation_hours": 10,
                      "pool": {"rare": 0.50, "epic": 0.38, "legendary": 0.12},
                      "flavor": "A smooth grey stone that alternates between ice-cold and burning hot.",
                      "lore": "Holds two things that don't agree with each other. Something will break the tie."},
    # Epic eggs
    "volcanic_core": {"name": "Volcanic Core", "rarity": "epic",      "emoji": "🌋", "incubation_hours": 18,
                      "pool": {"epic": 0.55, "legendary": 0.35, "divine": 0.10},
                      "flavor": "A heavy obsidian egg with bright orange magma veins across the shell.",
                      "lore": "Requires heat to incubate correctly. Store in the Ember Wastes if possible."},
    "nimbus_cloud":  {"name": "Nimbus Cloud",  "rarity": "epic",      "emoji": "⛈️", "incubation_hours": 18,
                      "pool": {"epic": 0.55, "legendary": 0.35, "divine": 0.10},
                      "flavor": "A fluffy stormy cloud wrapped tightly into an egg shape, crackling with mini lightning.",
                      "lore": "Thunderbird-adjacent. Keep away from electronics during incubation."},
    "monolith_relic":{"name": "Monolith Relic","rarity": "epic",      "emoji": "🗿", "incubation_hours": 24,
                      "pool": {"epic": 0.50, "legendary": 0.40, "divine": 0.10},
                      "flavor": "An ancient stone tablet egg carved with glowing golden hieroglyphics.",
                      "lore": "Atlas is vaguely familiar with these. It won't say how."},
    # Legendary eggs
    "abyssal_trench_orb":  {"name": "Abyssal Trench Orb", "rarity": "legendary", "emoji": "🌊", "incubation_hours": 48,
                            "pool": {"legendary": 0.70, "divine": 0.30},
                            "flavor": "Covered in ancient barnacles and dark glowing runes, pressure-sealed by the deep sea.",
                            "lore": "The Sunken Abyssal Trenches give these up rarely. They do not give them up gently."},
    "dragon_hoard_scale":  {"name": "Dragon-Hoard Scale","rarity": "legendary","emoji": "🐉", "incubation_hours": 48,
                            "pool": {"legendary": 0.75, "divine": 0.25},
                            "flavor": "A massive diamond-hard egg made entirely of overlapping crimson and gold dragon scales.",
                            "lore": "These are technically stolen. The Dragon is aware. The Dragon is patient."},
    "glacial_monolith":    {"name": "Glacial Monolith",  "rarity": "legendary","emoji": "🧊", "incubation_hours": 48,
                            "pool": {"legendary": 0.70, "divine": 0.30},
                            "flavor": "Solid unmelting black ice with a massive dark silhouette frozen inside.",
                            "lore": "The silhouette is always moving when you're not watching it directly."},
    # Divine eggs (collection-specific)
    "genesis_matrix":      {"name": "Genesis Matrix",    "rarity": "divine",   "emoji": "🏛️", "incubation_hours": 96,
                            "pool": {"divine": 1.0},
                            "divine_pool": ["genesis", "terminus", "paradox"],
                            "flavor": "A perfectly flawless white geometric cube, floating and slowly rotating.",
                            "lore": "The Architects made this last. Or first. Paradox is involved."},
    "constellation_spool": {"name": "Constellation Spool","rarity": "divine",  "emoji": "⏳", "incubation_hours": 96,
                            "pool": {"divine": 1.0},
                            "divine_pool": ["zodiac", "karma", "horizon"],
                            "flavor": "A swirling vortex of midnight-blue space dust wound with glowing red lines.",
                            "lore": "Karma wove the first one of these. It hasn't said why."},
    "singularity_core":    {"name": "Singularity Core",  "rarity": "divine",   "emoji": "🎴", "incubation_hours": 96,
                            "pool": {"divine": 1.0},
                            "divine_pool": ["supernova", "nebula", "abyss"],
                            "flavor": "A pitch-black miniature black hole in a fragile cage of neon-purple stellar gas.",
                            "lore": "Obtained from Altered Divine raid drops. The Loom made this as a warning."},
    "world_tree_seed":     {"name": "World-Tree Seed",   "rarity": "divine",   "emoji": "🏮", "incubation_hours": 96,
                            "pool": {"divine": 1.0},
                            "divine_pool": ["atlas", "asgard", "nirvana"],
                            "flavor": "An ancient golden seed with tiny roots made of light breaking through the shell.",
                            "lore": "The Mythological Pillars remember a time before this was an egg. They don't discuss it."},
}

EGG_PRICES = {
    "common": 300, "uncommon": 1200, "rare": 4000, "epic": 12000, "legendary": 50000, "divine": 0
}

# ── Sanctuary upgrade definitions ─────────────────────────────────────────────
SANCTUARY_UPGRADES = {
    "fairy_garden": {
        "name": "🌸 Fairy Garden",
        "tier": 1,
        "cost_tokens": 50,
        "description": "Increases passive happiness gain for all members' benched beasts by 5%.",
        "lore": "The Fairies help because they want to. That's more unsettling than if they were paid.",
        "db_column": "fairy_garden",
    },
    "gnome_forge": {
        "name": "⚒️ Gnome Forge",
        "tier": 2,
        "cost_tokens": 150,
        "description": "Reduces crafting material costs for all guild members by 10%.",
        "lore": "The Gnomes insisted on designing the logo themselves. Nobody is allowed to comment on the logo.",
        "db_column": "gnome_forge",
        "requires": "fairy_garden",
    },
    "celestial_observatory": {
        "name": "🔭 Celestial Observatory",
        "tier": 3,
        "cost_tokens": 350,
        "description": "Grants all guild members a passive +2% encounter rate for Epic and Legendary beasts.",
        "lore": "From here you can see the Celestial Loom directly, if you're patient and the night is clear. "
                "Prismite likes to sit up here alone. Nobody asks why.",
        "db_column": "celestial_observatory",
        "requires": "gnome_forge",
    },
}


def roll_egg_rarity(egg_id: str) -> str:
    egg = EGGS.get(egg_id)
    if not egg:
        return "common"
    pool = egg["pool"]
    roll = random.random()
    cumulative = 0.0
    for rarity, chance in pool.items():
        cumulative += chance
        if roll <= cumulative:
            return rarity
    return list(pool.keys())[-1]


def pick_beast_for_rarity(rarity: str, egg: dict) -> dict | None:
    all_beasts = load_beasts()
    # Divine eggs have curated pools
    if "divine_pool" in egg and rarity == "divine":
        pool_ids = egg["divine_pool"]
        pool = [all_beasts[bid] for bid in pool_ids if bid in all_beasts]
    else:
        STARTER_IDS = {"prismite", "twine", "gloop", "barkley"}
        pool = [b for b in all_beasts.values()
                if b["rarity"] == rarity and b["id"] not in STARTER_IDS]
    return random.choice(pool) if pool else None


class World(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /incubate ──────────────────────────────────────────────────────────
    @app_commands.command(name="incubate", description="Place an egg to incubate 🥚")
    @app_commands.describe(egg_name="The egg to start incubating (from your inventory)")
    async def incubate(self, interaction: discord.Interaction, egg_name: str):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Match egg name to EGGS
        egg_id = egg_name.lower().replace(" ", "_").replace("-", "_")
        egg = EGGS.get(egg_id)
        if not egg:
            matches = [(k, e) for k, e in EGGS.items()
                       if egg_name.lower() in e["name"].lower()]
            if matches:
                egg_id, egg = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Egg `{egg_name}` not found! Check `/eggs` for available eggs.",
                    color=COLORS["error"]
                ))

        # Check inventory
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_inventory WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, egg_id)
            ) as c:
                inv_row = await c.fetchone()

            if not inv_row or inv_row["quantity"] < 1:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ You don't have a **{egg['name']}** in your inventory!",
                    color=COLORS["error"]
                ))

            # Check egg slots (max 3 at once)
            async with db.execute(
                "SELECT COUNT(*) FROM incubating_eggs WHERE user_id = ? AND hatched = 0",
                (interaction.user.id,)
            ) as c:
                count = (await c.fetchone())[0]
            if count >= 3:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ You already have 3 eggs incubating! Hatch one first with `/hatchegg`.",
                    color=COLORS["error"]
                ))

            hours = egg["incubation_hours"]
            ready_at = datetime.now(timezone.utc) + timedelta(hours=hours)

            # Deduct from inventory
            if inv_row["quantity"] == 1:
                await db.execute("DELETE FROM player_inventory WHERE id = ?", (inv_row["id"],))
            else:
                await db.execute(
                    "UPDATE player_inventory SET quantity = quantity - 1 WHERE id = ?",
                    (inv_row["id"],)
                )
            await db.execute(
                "INSERT INTO incubating_eggs (user_id, egg_type, egg_name, rarity, ready_at) VALUES (?,?,?,?,?)",
                (interaction.user.id, egg_id, egg["name"], egg["rarity"],
                 ready_at.strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()

        embed = discord.Embed(
            title=f"{egg['emoji']} Egg Incubating: {egg['name']}",
            description=(
                f"*{egg['flavor']}*\n\n"
                f"*{egg['lore']}*\n\n"
                f"⏳ Ready in **{hours} hour{'s' if hours != 1 else ''}**\n"
                f"Use `/hatchegg` once it's ready!"
            ),
            color=COLORS.get(egg["rarity"], COLORS["info"])
        )
        embed.set_footer(text="ChibiBeasts 🐾  •  The Loom is still weaving. Be patient.")
        await interaction.followup.send(embed=embed)

    # ── /eggs ──────────────────────────────────────────────────────────────
    @app_commands.command(name="eggs", description="View your incubating eggs 🥚")
    async def eggs(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY ready_at ASC",
                (interaction.user.id,)
            ) as c:
                rows = [dict(r) for r in await c.fetchall()]

        if not rows:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You have no eggs incubating. Pick one up in the `/shop` and use `/incubate`!",
                color=COLORS["info"]
            ))

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="🥚 Your Incubating Eggs",
            description="*The Loom weaves slowly. Good things take time.*",
            color=COLORS["info"]
        )
        for row in rows:
            egg = EGGS.get(row["egg_type"], {})
            ready_at = datetime.strptime(row["ready_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if now >= ready_at:
                status = "✅ **Ready to hatch!** Use `/hatchegg`"
            else:
                remaining = ready_at - now
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                minutes = rem // 60
                status = f"⏳ `{hours}h {minutes}m` remaining"
            embed.add_field(
                name=f"{egg.get('emoji','🥚')} {row['egg_name']} (ID: #{row['id']})",
                value=f"{status}\n*{RARITY_LABEL.get(row['rarity'], row['rarity'])}*",
                inline=False
            )
        await interaction.followup.send(embed=embed)

    # ── /hatchegg ──────────────────────────────────────────────────────────
    @app_commands.command(name="hatchegg", description="Hatch a ready incubated egg! 🐣")
    @app_commands.describe(egg_id="The incubation ID from /eggs (leave blank to hatch oldest ready egg)")
    async def hatchegg(self, interaction: discord.Interaction, egg_id: int = None):
        await interaction.response.defer()
        await get_or_create_player(interaction.user.id, str(interaction.user))
        now = datetime.now(timezone.utc)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if egg_id:
                async with db.execute(
                    "SELECT * FROM incubating_eggs WHERE id = ? AND user_id = ? AND hatched = 0",
                    (egg_id, interaction.user.id)
                ) as c:
                    row = await c.fetchone()
            else:
                async with db.execute(
                    "SELECT * FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY ready_at ASC LIMIT 1",
                    (interaction.user.id,)
                ) as c:
                    row = await c.fetchone()

            if not row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ No ready eggs found! Check `/eggs` to see what's incubating.",
                    color=COLORS["error"]
                ))

            row = dict(row)
            ready_at = datetime.strptime(row["ready_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if now < ready_at:
                remaining = ready_at - now
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                minutes = rem // 60
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        f"✦ **{row['egg_name']}** isn't ready yet!\n"
                        f"⏳ `{hours}h {minutes}m` remaining.\n"
                        f"*Use a Chrono-Biscuit to skip the wait!*"
                    ),
                    color=COLORS["error"]
                ))

            egg_def = EGGS.get(row["egg_type"], {})
            rarity = roll_egg_rarity(row["egg_type"])
            beast = pick_beast_for_rarity(rarity, egg_def)

            if not beast:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ The egg hatched but something went wrong. Try again!",
                    color=COLORS["error"]
                ))

            beast_row_id = await add_beast_to_player(
                interaction.user.id, {**beast, "caught_from": "incubation"}
            )
            await db.execute("UPDATE incubating_eggs SET hatched = 1 WHERE id = ?", (row["id"],))
            await db.commit()

        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        type_emoji = TYPE_EMOJI.get(beast.get("type", ""), "❓")
        color = COLORS.get(rarity, COLORS["info"])

        embed = discord.Embed(
            title=f"🐣 {row['egg_name']} Hatched!",
            description=(
                f"*The Loom finishes one more stitch.*\n\n"
                f"{rarity_emoji} **{beast['name']}** emerged — *{beast['title']}*\n\n"
                f"{type_emoji} Type: **{beast.get('type','?').capitalize()}** | "
                f"Rarity: **{RARITY_LABEL.get(rarity, rarity)}**\n\n"
                f"*{beast['description']}*"
            ),
            color=color
        )
        embed.set_footer(text=f"ChibiBeasts 🐾  •  Beast ID #{beast_row_id} added to your collection!")
        await interaction.followup.send(embed=embed)

        completed_quests = await track_quest_event(interaction.user.id, "hatch")
        unlocked = await check_achievements(interaction.user.id)
        if interaction.guild:
            await record_bestiary_sighting(interaction.guild.id, beast["id"], interaction.user.id)
        await notify_quest_completions(interaction.channel, completed_quests)
        await notify_unlocks(interaction.channel, interaction.user, unlocked)

    # ── /sanctuary ────────────────────────────────────────────────────────
    @app_commands.command(name="sanctuary", description="View and upgrade your guild's Sanctuary 🏰")
    async def sanctuary(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.rank, gm.guild_id, g.name, g.level, g.guild_tokens "
                "FROM guild_members gm JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (interaction.user.id,)
            ) as c:
                guild_row = await c.fetchone()

            if not guild_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ You need to be in a guild to view the Sanctuary! Use `/guild_create` or `/guild_invite`.",
                    color=COLORS["error"]
                ))
            guild_row = dict(guild_row)

            async with db.execute(
                "SELECT * FROM guild_sanctuary WHERE guild_id = ?", (guild_row["guild_id"],)
            ) as c:
                sanctuary = await c.fetchone()
            sanctuary = dict(sanctuary) if sanctuary else {
                "fairy_garden": 0, "gnome_forge": 0, "celestial_observatory": 0
            }

        embed = discord.Embed(
            title=f"🏰 {guild_row['name']}'s Sanctuary",
            description=(
                "*Every guild is a small echo of what the Architects did at the start — "
                "weaving a stable space together.*\n\n"
                "Upgrade your Sanctuary to unlock powerful passive bonuses for all members."
            ),
            color=COLORS["legendary"]
        )
        for key, upgrade in SANCTUARY_UPGRADES.items():
            col = upgrade["db_column"]
            built = bool(sanctuary.get(col, 0))
            req = upgrade.get("requires")
            req_met = not req or bool(sanctuary.get(req, 0))
            if built:
                status = "✅ Built"
            elif not req_met:
                req_name = SANCTUARY_UPGRADES[req]["name"]
                status = f"🔒 Requires {req_name}"
            else:
                status = f"🔨 Cost: {upgrade['cost_tokens']} 🎟️ tokens" + (
                    f" *(requires {SANCTUARY_UPGRADES[req]['name']})*" if req else "")
            embed.add_field(
                name=f"{upgrade['name']} (Tier {upgrade['tier']}) — {status}",
                value=f"{upgrade['description']}\n*{upgrade['lore']}*",
                inline=False
            )
        embed.set_footer(text=f"ChibiBeasts 🐾  •  Use /build <upgrade> to construct a Sanctuary upgrade")
        await interaction.followup.send(embed=embed)

    # ── /build ────────────────────────────────────────────────────────────
    @app_commands.command(name="build", description="Build a Sanctuary upgrade for your guild ⚒️")
    @app_commands.describe(upgrade="Which upgrade to build: fairy_garden, gnome_forge, or celestial_observatory")
    @app_commands.choices(upgrade=[
        app_commands.Choice(name="🌸 Fairy Garden (Tier 1)", value="fairy_garden"),
        app_commands.Choice(name="⚒️ Gnome Forge (Tier 2)", value="gnome_forge"),
        app_commands.Choice(name="🔭 Celestial Observatory (Tier 3)", value="celestial_observatory"),
    ])
    async def build(self, interaction: discord.Interaction, upgrade: str):
        await interaction.response.defer()
        up = SANCTUARY_UPGRADES.get(upgrade)
        if not up:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Unknown upgrade.", color=COLORS["error"]
            ))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.rank, gm.guild_id, g.name, g.guild_tokens "
                "FROM guild_members gm JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (interaction.user.id,)
            ) as c:
                guild_row = await c.fetchone()

            if not guild_row or guild_row["rank"] not in ["leader", "officer"]:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Only Guild Leaders and Officers can build Sanctuary upgrades!",
                    color=COLORS["error"]
                ))
            guild_row = dict(guild_row)
            gid = guild_row["guild_id"]

            async with db.execute("SELECT * FROM guild_sanctuary WHERE guild_id = ?", (gid,)) as c:
                sanctuary = await c.fetchone()
            sanctuary = dict(sanctuary) if sanctuary else {}

            col = up["db_column"]
            if sanctuary.get(col, 0):
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ **{up['name']}** is already built!", color=COLORS["error"]
                ))

            req = up.get("requires")
            if req and not sanctuary.get(req, 0):
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ You need to build **{SANCTUARY_UPGRADES[req]['name']}** first!",
                    color=COLORS["error"]
                ))

            if up["cost_tokens"] and guild_row["guild_tokens"] < up["cost_tokens"]:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Not enough guild tokens! Need **{up['cost_tokens']}**, have **{guild_row['guild_tokens']}**.",
                    color=COLORS["error"]
                ))

            if up["cost_tokens"]:
                await db.execute("UPDATE guilds SET guild_tokens = guild_tokens - ? WHERE id = ?",
                                 (up["cost_tokens"], gid))
            if sanctuary:
                await db.execute(f"UPDATE guild_sanctuary SET {col} = 1 WHERE guild_id = ?", (gid,))
            else:
                await db.execute(
                    f"INSERT INTO guild_sanctuary (guild_id, {col}) VALUES (?, 1)", (gid,)
                )
            await db.commit()

        await interaction.followup.send(embed=discord.Embed(
            title=f"✅ {up['name']} Built!",
            description=(
                f"*{up['lore']}*\n\n"
                f"**Effect:** {up['description']}\n\n"
                f"All guild members now benefit from this upgrade."
            ),
            color=COLORS["success"]
        ))

    # ── /craft ────────────────────────────────────────────────────────────
    @app_commands.command(name="craft", description="Craft equipment or runes from materials ⚒️")
    @app_commands.describe(item_name="The equipment or rune to craft")
    async def craft(self, interaction: discord.Interaction, item_name: str):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        all_craftable = {**equipment}

        item_id = item_name.lower().replace(" ", "_").replace("-", "_")
        item = all_craftable.get(item_id)
        if not item:
            matches = [(k, v) for k, v in all_craftable.items()
                       if item_name.lower() in v["name"].lower()]
            if matches:
                item_id, item = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ `{item_name}` isn't a craftable item. Check `/recipes` for the full list.",
                    color=COLORS["error"]
                ))

        # Check material holdings
        materials = load_materials()
        recipe = item.get("recipe", {})
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT material_id, quantity FROM player_materials WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                player_mats = {r["material_id"]: r["quantity"] for r in await c.fetchall()}

            # Check guild forge discount via sanctuary utility
            user_sanctuary = await get_user_sanctuary(interaction.user.id)
            actual_recipe = apply_craft_discount(recipe, user_sanctuary)
            forge_discount = user_sanctuary.get("gnome_forge", 0) == 1

            missing = []
            for mat_id, actual_qty in actual_recipe.items():
                have = player_mats.get(mat_id, 0)
                if have < actual_qty:
                    mat_name = materials.get(mat_id, {}).get("name", mat_id)
                    missing.append(f"**{mat_name}**: need {actual_qty}, have {have}")

            if missing:
                return await interaction.followup.send(embed=discord.Embed(
                    title="✦ Missing Materials",
                    description="You're short on:\n" + "\n".join(missing),
                    color=COLORS["error"]
                ))

            # Deduct materials using discounted recipe
            for mat_id, actual_qty in actual_recipe.items():
                await db.execute(
                    "UPDATE player_materials SET quantity = quantity - ? WHERE user_id = ? AND material_id = ?",
                    (actual_qty, interaction.user.id, mat_id)
                )
                await db.execute(
                    "DELETE FROM player_materials WHERE user_id = ? AND material_id = ? AND quantity <= 0",
                    (interaction.user.id, mat_id)
                )

            # Add item to player_equipment (unequipped)
            await db.execute(
                "INSERT INTO player_equipment (user_id, beast_row_id, equipment_id) VALUES (?, NULL, ?)",
                (interaction.user.id, item_id)
            )
            await db.commit()

        embed = discord.Embed(
            title=f"⚒️ Crafted: {item['name']}!",
            description=(
                f"*{item['description']}*\n\n"
                f"_{item['lore']}_\n\n"
                + ("*Gnome Forge discount applied!* " if forge_discount else "")
                + f"\nUse `/equip {item['name']} <beast_id>` to put it on a beast."
            ),
            color=COLORS.get(item["rarity"], COLORS["info"])
        )
        await interaction.followup.send(embed=embed)

    # ── /recipes ──────────────────────────────────────────────────────────
    @app_commands.command(name="recipes", description="Browse all craftable equipment recipes 📜")
    @app_commands.describe(category="Armor recipes or material sources")
    @app_commands.choices(category=[
        app_commands.Choice(name="⚔️ Armor",           value="armor"),
        app_commands.Choice(name="💎 Runes",            value="runes"),
        app_commands.Choice(name="🪨 Material Sources", value="sources"),
    ])
    async def recipes(self, interaction: discord.Interaction, category: str = "armor"):
        await interaction.response.defer()
        equipment, runes = load_equipment()
        materials_data = load_materials()

        RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "altered_divine"]

        # ── Material sources map ───────────────────────────────────────────
        # Materials drop from /explore catches at 60% chance, rarity-matched
        # to the beast caught. Altered Divine mats have no current drop source.
        MAT_SOURCES = {
            "common":         "🗺️ `/explore` — catch any **Common** beast (60% drop chance)",
            "uncommon":       "🗺️ `/explore` — catch any **Uncommon** beast (60% drop chance)",
            "rare":           "🗺️ `/explore` — catch any **Rare** beast (60% drop chance)",
            "epic":           "🗺️ `/explore` — catch any **Epic** beast (60% drop chance)",
            "legendary":      "🗺️ `/explore` — catch any **Legendary** beast (60% drop chance)",
            "altered_divine": "⚠️ *Not currently obtainable through gameplay — coming soon*",
        }

        if category == "sources":
            embed = discord.Embed(
                title="🪨 Material Sources",
                description=(
                    "*All materials drop from catching beasts during `/explore`. "
                    "Higher-rarity biomes yield higher-rarity materials.*\n\n"
                    "*Use `/materials` to see what you currently have.*"
                ),
                color=COLORS["info"]
            )
            # Group materials by rarity
            by_rarity = {}
            for mid, mat in materials_data.items():
                r = mat.get("rarity", "common")
                by_rarity.setdefault(r, []).append(mat)

            for rarity in RARITY_ORDER:
                mats = by_rarity.get(rarity, [])
                if not mats:
                    continue
                rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
                mat_list = " · ".join(f"{m['emoji']} **{m['name']}**" for m in mats)
                source = MAT_SOURCES.get(rarity, "")
                embed.add_field(
                    name=f"{rarity_emoji} {RARITY_LABEL.get(rarity, rarity.title())}",
                    value=f"{mat_list}\n{source}",
                    inline=False
                )
            embed.set_footer(text="ChibiBeasts 🐾  •  /craft <name> to forge · /materials to check your stock")
            return await interaction.followup.send(embed=embed)

        # ── Armor or Runes — paginated ─────────────────────────────────────
        if category == "runes":
            items = sorted(runes.values(), key=lambda x: RARITY_ORDER.index(x["rarity"]) if x["rarity"] in RARITY_ORDER else 99)
            title = "💎 Rune Recipes"
        else:
            items = sorted(equipment.values(), key=lambda x: RARITY_ORDER.index(x["rarity"]) if x["rarity"] in RARITY_ORDER else 99)
            title = "⚔️ Armor Recipes"

        # Fetch the player's current material stock for the checklist
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT material_id, quantity FROM player_materials WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                inv = {r["material_id"]: r["quantity"] for r in await c.fetchall()}

        per_page = 4
        total_pages = max(1, (len(items) + per_page - 1) // per_page)

        def build_recipe_page(page: int) -> discord.Embed:
            page_items = items[(page - 1) * per_page : page * per_page]
            embed = discord.Embed(
                title=title,
                description=(
                    f"*Use `/craft <name>` to forge. Materials drop from `/explore` catches.*\n"
                    f"*See `/recipes sources` for the full material source guide.*"
                ),
                color=COLORS["info"]
            )
            for item in page_items:
                recipe = item.get("recipe", {})
                rarity_emoji = RARITY_EMOJI.get(item["rarity"], "⚪")

                # Build checklist — ✅ if player has enough, ❌ with deficit if not
                checklist_lines = []
                can_craft = True
                for mid, qty_needed in recipe.items():
                    mat = materials_data.get(mid, {})
                    mat_name = mat.get("name", mid)
                    mat_emoji = mat.get("emoji", "⚪")
                    have = inv.get(mid, 0)
                    if have >= qty_needed:
                        checklist_lines.append(f"✅ {mat_emoji} {qty_needed}x {mat_name} `({have} owned)`")
                    else:
                        missing = qty_needed - have
                        checklist_lines.append(f"❌ {mat_emoji} {qty_needed}x {mat_name} `(need {missing} more)`")
                        can_craft = False

                craft_status = "🔨 **Ready to craft!**" if can_craft and recipe else ""
                checklist = "\n".join(checklist_lines) or "*No materials required*"

                embed.add_field(
                    name=f"{rarity_emoji} {item['name']}" + (" ✅" if can_craft and recipe else ""),
                    value=(
                        f"{item['description']}\n"
                        f"{checklist}"
                        + (f"\n{craft_status}" if craft_status else "")
                    ),
                    inline=False
                )
            embed.set_footer(text=f"ChibiBeasts 🐾  •  Page {page}/{total_pages} · /craft <name> to forge")
            return embed

        if total_pages == 1:
            return await interaction.followup.send(embed=build_recipe_page(1))

        class RecipeView(discord.ui.View):
            def __init__(self, current: int):
                super().__init__(timeout=120)
                self.page = current
                self._update_buttons()

            def _update_buttons(self):
                self.prev_btn.disabled = self.page <= 1
                self.next_btn.disabled = self.page >= total_pages
                self.prev_btn.label = f"◀ Page {self.page - 1}" if self.page > 1 else "◀"
                self.next_btn.label = f"Page {self.page + 1} ▶" if self.page < total_pages else "▶"

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't yours!", ephemeral=True)
                self.page -= 1
                self._update_buttons()
                await btn_interaction.response.edit_message(embed=build_recipe_page(self.page), view=self)

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't yours!", ephemeral=True)
                self.page += 1
                self._update_buttons()
                await btn_interaction.response.edit_message(embed=build_recipe_page(self.page), view=self)

        await interaction.followup.send(embed=build_recipe_page(1), view=RecipeView(1))

    # ── /materials ────────────────────────────────────────────────────────
    @app_commands.command(name="materials", description="View your crafting materials 🪨")
    async def materials_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        all_mats = load_materials()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT material_id, quantity FROM player_materials WHERE user_id = ? ORDER BY quantity DESC",
                (interaction.user.id,)
            ) as c:
                rows = [dict(r) for r in await c.fetchall()]

        if not rows:
            return await interaction.followup.send(embed=discord.Embed(
                description=(
                    "✦ You have no crafting materials yet!\n"
                    "*Materials drop from exploring biomes — especially mid and high-tier zones.*"
                ),
                color=COLORS["info"]
            ))

        embed = discord.Embed(
            title="🪨 Your Crafting Materials",
            description="*Everything the Loom left behind, repurposed.*",
            color=COLORS["info"]
        )
        for row in rows:
            mat = all_mats.get(row["material_id"], {})
            embed.add_field(
                name=f"{mat.get('emoji','⚪')} {mat.get('name', row['material_id'])}",
                value=f"x{row['quantity']} — *{mat.get('description','')}*",
                inline=True
            )
        await interaction.followup.send(embed=embed)

    # ── /codex ────────────────────────────────────────────────────────────
    @app_commands.command(name="codex", description="Look up lore and details on any beast 📖")
    @app_commands.describe(beast_name="Beast name to look up")
    async def codex(self, interaction: discord.Interaction, beast_name: str):
        await interaction.response.defer()
        all_beasts = load_beasts()

        beast_id = beast_name.lower().replace(" ", "_").replace("-", "_")
        beast = all_beasts.get(beast_id)
        if not beast:
            matches = [(k, b) for k, b in all_beasts.items()
                       if beast_name.lower() in b["name"].lower()]
            if matches:
                beast_id, beast = matches[0]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ `{beast_name}` not found in the Codex.",
                    color=COLORS["error"]
                ))

        rarity = beast["rarity"]
        type_name = beast.get("type", "")
        type_emoji = TYPE_EMOJI.get(type_name, "❓")
        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        color = COLORS.get(rarity, COLORS["info"])

        embed = discord.Embed(
            title=f"{rarity_emoji} {beast['name']} — *{beast['title']}*",
            description=beast["description"],
            color=color
        )
        embed.add_field(name=f"{type_emoji} Type", value=type_name.capitalize(), inline=True)
        embed.add_field(name="✨ Rarity", value=RARITY_LABEL.get(rarity, rarity), inline=True)
        if beast.get("collection"):
            embed.add_field(name="📚 Collection", value=beast["collection"], inline=True)

        stats = beast["base_stats"]
        embed.add_field(
            name="📊 Base Stats",
            value=(
                f"❤️ HP: `{stats['hp']}` | ⚔️ ATK: `{stats['attack']}`\n"
                f"🛡️ DEF: `{stats['defense']}` | 💨 SPD: `{stats['speed']}`\n"
                f"💠 MANA: `{stats['mana']}`"
            ),
            inline=False
        )
        embed.add_field(
            name="⚡ Moves",
            value="\n".join(f"• {m}" for m in beast["moves"]) + f"\n🌟 **Ultimate:** {beast['ultimate']}",
            inline=False
        )

        # Type lore
        if type_name in TYPE_LORE:
            embed.add_field(name=f"{type_emoji} Type Lore", value=f"*{TYPE_LORE[type_name]}*", inline=False)

        # Divine passive
        if beast.get("divine_passive"):
            dp = beast["divine_passive"]
            embed.add_field(
                name=f"✨ Divine Passive: **{dp['passive_name']}**",
                value=f"*{dp['passive_desc']}*",
                inline=False
            )

        if beast.get("starter"):
            embed.add_field(
                name="🏛️ Starter Beast",
                value=f"*{beast.get('starter_house')} — {beast.get('starter_flavor','')}*",
                inline=False
            )

        embed.set_footer(text="ChibiBeasts 🐾  •  /bestiary to see what your server has discovered")
        await interaction.followup.send(embed=embed)

    # ── /typeinfo ─────────────────────────────────────────────────────────
    @app_commands.command(name="typeinfo", description="Look up type matchups and weaknesses 🔥💧")
    @app_commands.describe(type_name="The element type to look up")
    @app_commands.choices(type_name=[
        app_commands.Choice(name="🔥 Fire",   value="fire"),
        app_commands.Choice(name="💧 Water",  value="water"),
        app_commands.Choice(name="🌿 Nature", value="nature"),
        app_commands.Choice(name="🌍 Earth",  value="earth"),
        app_commands.Choice(name="🌪️ Wind",   value="wind"),
        app_commands.Choice(name="❄️ Ice",    value="ice"),
        app_commands.Choice(name="✨ Arcane", value="arcane"),
        app_commands.Choice(name="🌑 Shadow", value="shadow"),
        app_commands.Choice(name="☀️ Light",  value="light"),
        app_commands.Choice(name="🌌 Cosmic", value="cosmic"),
    ])
    async def typeinfo(self, interaction: discord.Interaction, type_name: str):
        await interaction.response.defer()
        from utils.type_chart import TYPE_CHART, TYPE_LORE

        type_emoji = TYPE_EMOJI.get(type_name, "❓")
        matchups = TYPE_CHART.get(type_name, {})

        strong_vs  = [t for t, m in matchups.items() if m >= 2.0]
        weak_vs    = [t for t, m in matchups.items() if m <= 0.5]

        # Also find what beats this type (defending)
        weak_to    = [t for t, chart in TYPE_CHART.items() if chart.get(type_name, 1.0) >= 2.0]
        resists    = [t for t, chart in TYPE_CHART.items() if chart.get(type_name, 1.0) <= 0.5]

        def fmt_types(types):
            return " ".join(f"{TYPE_EMOJI.get(t,'❓')} {t.capitalize()}" for t in types) or "*None*"

        color = COLORS.get(type_name, COLORS["info"])
        embed = discord.Embed(
            title=f"{type_emoji} {type_name.capitalize()} Type",
            description=f"*{TYPE_LORE.get(type_name, '')}*",
            color=color
        )
        embed.add_field(name="⚡ Strong against (2×)", value=fmt_types(strong_vs), inline=False)
        embed.add_field(name="🛡️ Weak against (0.5×)", value=fmt_types(weak_vs),  inline=False)
        embed.add_field(name="💥 Weak to (takes 2×)",  value=fmt_types(weak_to),  inline=False)
        embed.add_field(name="🔰 Resists (takes 0.5×)", value=fmt_types(resists), inline=False)
        if type_name == "cosmic":
            embed.add_field(
                name="🌌 Special",
                value="*Cosmic types exist outside the elemental hierarchy — neutral to everything, super effective against nothing. Divine beings predate the type chart itself.*",
                inline=False
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  /codex <beast name> to look up a specific beast")
        await interaction.followup.send(embed=embed)

    # ── /lore ─────────────────────────────────────────────────────────────
    @app_commands.command(name="lore", description="Read the story of ChibiBeasts 📜")
    @app_commands.describe(chapter="Which part of the lore to read")
    @app_commands.choices(chapter=[
        app_commands.Choice(name="The Creation Myth", value="creation"),
        app_commands.Choice(name="The Five Collections", value="collections"),
        app_commands.Choice(name="The Sundering (Why Raids Exist)", value="sundering"),
        app_commands.Choice(name="Why You're Here", value="purpose"),
        app_commands.Choice(name="The Four Starters", value="starters"),
    ])
    async def lore(self, interaction: discord.Interaction, chapter: str = "creation"):
        await interaction.response.defer()

        LORE_CHAPTERS = {
            "creation": {
                "title": "📜 The Creation Myth",
                "text": (
                    "Before there were beasts, before there were trainers, before there was even a "
                    "*world* to stand on — there was only **the Loom**.\n\n"
                    "The Loom was not a place. It was the act of weaving itself: an endless, formless "
                    "process spinning raw possibility into shape.\n\n"
                    "Then, in a single instant called the **First Stitch**, the Loom wove four threads "
                    "tighter than any others — and they woke up. These four became the **Architects**: "
                    "vast, curious ideas that each wanted one small companion to carry their question "
                    "out into the world.\n\n"
                    "This is why every trainer's journey begins with a choice between four companions. "
                    "You are not picking a pet. **You are continuing a conversation that started before "
                    "the world had a floor to stand on.**"
                ),
                "color": COLORS["divine"]
            },
            "collections": {
                "title": "📜 The Five Divine Collections",
                "text": (
                    "As the Loom kept weaving, larger ideas crystallized into **Divine beings** — vast, "
                    "beautiful, and a little beyond understanding. They organized themselves into five "
                    "Collections, each a different answer to: *what does it mean for something to be eternal?*\n\n"
                    "🌌 **Cosmic Creators** — The raw stuff reality is made of.\n"
                    "*(Singularity, Astraea, Chronos, Aetherius)*\n\n"
                    "🏛️ **Architects of Reality** — Beginnings, endings, and everything between.\n"
                    "*(Genesis, Terminus, Paradox)*\n\n"
                    "🧵 **Celestial Loom** — Fate, consequence, and the threads connecting all things.\n"
                    "*(Horizon, Karma, Zodiac)*\n\n"
                    "🌑 **Primordial Aspects** — Forces too big for a single shape.\n"
                    "*(Abyss, Nebula, Supernova)*\n\n"
                    "🏮 **Mythological Pillars** — The things that hold the world up and refuse to fall.\n"
                    "*(Nirvana, Asgard, Atlas)*"
                ),
                "color": COLORS["legendary"]
            },
            "sundering": {
                "title": "📜 The Sundering — Why Raids Exist",
                "text": (
                    "A long time after the Architects sent their companions out, something the Loom "
                    "wove went *wrong*.\n\n"
                    "Not evil — the Loom doesn't do evil. But it tried to weave something too big, "
                    "too fast, and the thread **snapped**.\n\n"
                    "What came loose is called an **Altered Divine**: a being that was *supposed* to "
                    "become something like Genesis or Atlas, but fractured halfway through. The result "
                    "is a Divine-scale creature without a finished shape — unstable, in pain, and "
                    "instinctively consuming whatever's nearby trying to complete itself.\n\n"
                    "**A raid doesn't kill the Altered Divine. It finally finishes being born — correctly, "
                    "this time.** Which is why defeating one occasionally lets a trainer walk away with "
                    "a piece of what it was always meant to become."
                ),
                "color": COLORS["error"]
            },
            "purpose": {
                "title": "📜 Why You're Here",
                "text": (
                    "You are not an exterminator, a soldier, or a chosen one.\n\n"
                    "You're a trainer in a world that is, very literally, still being woven — and every "
                    "beast you catch, raise, battle with, and bond with is part of how that weaving gets "
                    "*gentler* instead of rougher.\n\n"
                    "**Catching and hatching** mirrors what the Architects did at the beginning: giving "
                    "a small unfinished idea a companion so it isn't alone.\n\n"
                    "**Battling** isn't conquest — beasts in ChibiBeasts fight to test and grow, the same "
                    "way the starters were sent out to see whether their Architect's idea could survive "
                    "contact with the world.\n\n"
                    "**Raiding** an Altered Divine is the highest-stakes form of the same instinct: "
                    "*helping something finish being born.*\n\n"
                    "The stakes are always really just: *help this unfinished thing settle into a shape "
                    "it can be happy with.* That's true of a wild Slime in the Whispering Woods and "
                    "it's true of a server-shaking raid boss. Same instinct, different scale."
                ),
                "color": COLORS["info"]
            },
            "starters": {
                "title": "📜 The Four Starters",
                "text": (
                    "Each Architect sent one small companion out into the world, carrying one question:\n\n"
                    "🔷 **Prismite** *(The Architect's First Idea)*\n"
                    "Prism shaped it to find out whether order could be gentle. So far, it has decided yes.\n\n"
                    "🧵 **Twine** *(The Loom's First Memory)*\n"
                    "Twine carries the very first thread the Loom ever spun — which is why, sometimes, "
                    "it seems to know what happens a few seconds before it does.\n\n"
                    "🫧 **Gloop** *(The Aspect's First Question)*\n"
                    "Aspect made Gloop to test whether change could be safe. Gloop has spent its whole "
                    "existence cheerfully proving yes, over and over, in a slightly different shape each time.\n\n"
                    "🌿 **Barkley** *(The Pillar's First Promise)*\n"
                    "Pillar shaped Barkley to prove that steadiness didn't have to be boring. Barkley "
                    "has never once let anyone down, and never plans to start."
                ),
                "color": COLORS["divine"]
            },
        }

        chapter_data = LORE_CHAPTERS.get(chapter, LORE_CHAPTERS["creation"])
        embed = discord.Embed(
            title=chapter_data["title"],
            description=chapter_data["text"],
            color=chapter_data["color"]
        )
        embed.set_footer(text="ChibiBeasts 🐾  •  /lore to read other chapters")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(World(bot))
