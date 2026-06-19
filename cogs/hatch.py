import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import aiosqlite
from utils.db import (
    get_or_create_player, get_player, update_player,
    add_beast_to_player, get_player_beasts, load_beasts,
    calc_player_exp_for_level
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement,
    record_bestiary_sighting, notify_unlocks, notify_quest_completions
)
from cogs.questline import advance_quest_step
from utils.sanctuary import get_user_sanctuary, apply_explore_encounter_bonus

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "divine"]

# ── Base egg definitions ─────────────────────────────────────────────────────
# Pools are *base* values. Call get_egg_pool(egg_type, perks, sanctuary)
# at runtime to get the effective pool with all active modifiers applied.
# This keeps the static data honest and prevents exploration bonuses from
# silently conflicting with hardcoded fractions.

_BASE_EGG_POOLS = {
    "common_egg":    {"common": 0.70, "uncommon": 0.25, "rare": 0.05},
    "rare_egg":      {"uncommon": 0.30, "rare": 0.45, "epic": 0.20, "legendary": 0.05},
    "celestial_egg": {"epic": 0.30, "legendary": 0.35, "divine": 0.25, "altered_chance": 0.10},
    "abyssal_egg":   {"legendary": 0.40, "divine": 0.55, "altered_chance": 0.05},
}

HATCH_EGGS = {
    "common_egg": {
        "name": "🥚 Common Egg", "price": 200,
        "flavor": "A small, warm egg. Something common and wonderful is inside.",
    },
    "rare_egg": {
        "name": "🥚✨ Rare Egg", "price": 1500,
        "flavor": "The shell vibrates faintly. Something with opinions is in there.",
    },
    "celestial_egg": {
        "name": "🌌🥚 Celestial Egg", "price": 8000,
        "flavor": "The Loom wove this one slowly. Whatever is inside has been waiting.",
    },
    "abyssal_egg": {
        "name": "🌊💎 Abyssal Egg", "price": 25000,
        "flavor": "It comes from somewhere deeper than deep. The weight of it is strange.",
    },
}


def get_egg_pool(egg_type: str, perks: list = None, sanctuary: dict = None) -> dict:
    """
    Return the effective rarity pool for an egg type, with all active
    perk and sanctuary modifiers applied at call time.

    - Stardust Touch perk: +10% rare, +5% epic, proportionally reduce common
    - Celestial Observatory sanctuary: +2% epic, +2% legendary
    - Pools are re-normalised after each modifier so they never exceed 1.0
    """
    import copy
    pool = copy.copy(_BASE_EGG_POOLS.get(egg_type, {"common": 1.0}))
    # Strip non-rarity keys before normalising
    pool = {k: v for k, v in pool.items() if k != "altered_chance"}

    if perks:
        for perk in perks:
            if perk.get("perk_id") == "stardust_touch" and perk.get("equipped"):
                pool["rare"]   = min(pool.get("rare",   0) + 0.10, 0.50)
                pool["epic"]   = min(pool.get("epic",   0) + 0.05, 0.40)
                pool["common"] = max(pool.get("common", 0) - 0.10, 0.05)

    if sanctuary and sanctuary.get("celestial_observatory"):
        pool["epic"]      = min(pool.get("epic",      0) + 0.02, 0.50)
        pool["legendary"] = min(pool.get("legendary", 0) + 0.02, 0.45)

    # Re-normalise so all weights sum to exactly 1.0
    total = sum(pool.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        pool = {k: v / total for k, v in pool.items()}

    return pool


# Keep for explore wild encounter rolls only
HATCH_RATES = {
    "wild": {"common": 0.45, "uncommon": 0.25, "rare": 0.15, "epic": 0.09, "legendary": 0.05, "divine": 0.01}
}

EGG_PRICES = {k: v["price"] for k, v in HATCH_EGGS.items()}
EGG_NAMES  = {k: v["name"]  for k, v in HATCH_EGGS.items()}

EXPLORE_COOLDOWN = 3600  # 1 hour in seconds
# No longer using in-memory dict — cooldowns now persist via players.explore_last_at

def roll_rarity(rates: dict) -> str:
    roll = random.random()
    cumulative = 0
    for rarity, rate in rates.items():
        cumulative += rate
        if roll <= cumulative:
            return rarity
    return list(rates.keys())[-1]

STARTER_IDS = {"prismite", "twine", "gloop", "barkley"}

# ── Collection completion helper ─────────────────────────────────────────────
COLLECTION_REWARDS = {
    "Cosmic Creators":       {"gold": 3000, "celestial_shards": 30, "title": "Cosmic Witness",     "npc_line": "Cael goes very quiet when you tell him. Then: *'All four. I... yes. That makes sense actually.'*"},
    "Architects of Reality": {"gold": 4000, "celestial_shards": 40, "title": "Reality's Student",  "npc_line": "The Archivist says: *'You have met all three. They know who you are now. They've known for some time.'*"},
    "Celestial Loom":        {"gold": 3000, "celestial_shards": 30, "title": "Fate's Witness",     "npc_line": "Karma finds you. It doesn't usually do that. It doesn't say anything. It just looks at you for a long moment."},
    "Primordial Aspects":    {"gold": 4000, "celestial_shards": 40, "title": "Aspect Collector",   "npc_line": "Orren hears about this and is very still for a while. *'The three oldest forces. All three. Good. The world is more stable with someone holding them.'*"},
    "Mythological Pillars":  {"gold": 5000, "celestial_shards": 50, "title": "Pillar of the World","npc_line": "Maren writes something in her Bestiary for a long time. When she finishes: *'The Pillars notice who carries them. You should know that.'*"},
}

async def check_collection_completion(interaction, beast: dict, all_beasts: dict):
    """Check if catching this beast completed a divine collection. If so, reward and announce."""
    collection = beast.get("collection")
    if not collection or collection not in COLLECTION_REWARDS:
        return

    collection_beast_ids = {bid for bid, b in all_beasts.items() if b.get("collection") == collection}
    if not collection_beast_ids:
        return

    # Check if player now owns ALL beasts in this collection
    import aiosqlite
    async with aiosqlite.connect("db/chibibeast.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT DISTINCT beast_id FROM player_beasts WHERE user_id = ?",
            (interaction.user.id,)
        ) as c:
            owned_ids = {r["beast_id"] for r in await c.fetchall()}

        if not collection_beast_ids.issubset(owned_ids):
            return  # Not complete yet

        # Check if already rewarded
        ach_id = f"collection_{collection.lower().replace(' ', '_')}"
        async with db.execute(
            "SELECT 1 FROM achievements WHERE user_id = ? AND achievement_id = ?",
            (interaction.user.id, ach_id)
        ) as c:
            if await c.fetchone():
                return  # Already got this reward

        # Grant reward
        reward = COLLECTION_REWARDS[collection]
        await db.execute(
            "INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?, ?)",
            (interaction.user.id, ach_id)
        )
        await db.execute(
            "UPDATE players SET gold = gold + ?, celestial_shards = celestial_shards + ? WHERE user_id = ?",
            (reward["gold"], reward["celestial_shards"], interaction.user.id)
        )
        await db.commit()

    embed = discord.Embed(
        title=f"🌸 Collection Complete: {collection}!",
        description=(
            f"*You have gathered every being from the {collection}.*\n\n"
            f"{reward['npc_line']}\n\n"
            f"**Rewards:**\n"
            f"+{reward['gold']:,} 💰 gold | +{reward['celestial_shards']} 🔮 Celestial Shards\n"
            f"✦ Title unlocked: **{reward['title']}**"
        ),
        color=COLORS["divine"]
    )
    embed.set_footer(text="ChibiBeasts 🐾  •  The Loom remembers who paid attention.")
    await interaction.channel.send(embed=embed)

def get_beast_by_rarity(rarity: str, beasts: dict, exclude_ids: list = None) -> dict:
    excluded = set(exclude_ids or []) | STARTER_IDS
    pool = [b for b in beasts.values() if b["rarity"] == rarity and b["id"] not in excluded]
    if not pool:
        pool = [b for b in beasts.values() if b["rarity"] == rarity and b["id"] not in STARTER_IDS]
    return random.choice(pool) if pool else None

def check_glimmering_fortune(perks: list) -> bool:
    for perk in perks:
        if perk["perk_id"] == "glimmering_fortune" and perk["equipped"]:
            return random.random() < 0.0005
    return False

# Note: check_stardust_touch() was removed — its logic now lives in get_egg_pool()
# which applies the Stardust Touch bonus when building the effective rarity pool.

class HatchView(discord.ui.View):
    def __init__(self, beast_data: dict, player_id: int):
        super().__init__(timeout=60)
        self.beast_data = beast_data
        self.player_id = player_id

    @discord.ui.button(label="Set as Active Beast", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def set_active(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("This isn't your hatch!", ephemeral=True)
        import aiosqlite
        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute("UPDATE player_beasts SET is_active = 0 WHERE user_id = ?", (self.player_id,))
            await db.execute(
                "UPDATE player_beasts SET is_active = 1 WHERE user_id = ? AND beast_id = ? ORDER BY id DESC LIMIT 1",
                (self.player_id, self.beast_data["id"])
            )
            await db.commit()
        button.disabled = True
        button.label = "Set as Active ✓"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✦ **{self.beast_data['name']}** is now your active beast!",
                color=COLORS["success"]
            ),
            ephemeral=True
        )

class Hatch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_beast_embed(self, beast: dict, title: str, subtitle: str = "") -> discord.Embed:
        rarity = beast["rarity"]
        type_emoji = TYPE_EMOJI.get(beast["type"], "❓")
        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        rarity_label = RARITY_LABEL.get(rarity, rarity.capitalize())
        color = COLORS.get(rarity, COLORS["info"])

        embed = discord.Embed(
            title=title,
            description=f"### {rarity_emoji} **{beast['name']}** — *{beast['title']}*\n{subtitle}",
            color=color
        )
        embed.add_field(
            name=f"{type_emoji} Type",
            value=beast["type"].capitalize(),
            inline=True
        )
        embed.add_field(
            name="✨ Rarity",
            value=rarity_label,
            inline=True
        )
        if "collection" in beast:
            embed.add_field(name="📖 Collection", value=beast["collection"], inline=True)

        stats = beast["base_stats"]
        embed.add_field(
            name="📊 Base Stats",
            value=(
                f"❤️ HP: `{stats['hp']}`\n"
                f"⚔️ ATK: `{stats['attack']}`\n"
                f"🛡️ DEF: `{stats['defense']}`\n"
                f"💨 SPD: `{stats['speed']}`\n"
                f"💠 MANA: `{stats['mana']}`"
            ),
            inline=True
        )
        embed.add_field(
            name="⚡ Moves",
            value="\n".join(f"• {m}" for m in beast["moves"]) + f"\n🌟 **Ultimate:** {beast['ultimate']}",
            inline=True
        )
        embed.add_field(name="📜 Description", value=beast["description"], inline=False)

        if beast.get("image_url"):
            embed.set_image(url=beast["image_url"])

        embed.set_footer(text="ChibiBeasts 🐾  •  Use /beast to view your collection")
        return embed

    @app_commands.command(name="hatch", description="Hatch an egg to discover a new ChibiBeast! 🥚")
    @app_commands.describe(egg_type="Type of egg to hatch")
    @app_commands.choices(egg_type=[
        app_commands.Choice(name="🥚 Common Egg (200 gold)",                             value="common_egg"),
        app_commands.Choice(name="🥚✨ Rare Egg (1,500 gold)",                           value="rare_egg"),
        app_commands.Choice(name="🌌🥚 Celestial Egg (8,000 gold) — 25% Divine chance",  value="celestial_egg"),
        app_commands.Choice(name="🌊💎 Abyssal Egg (25,000 gold) — 55% Divine chance",   value="abyssal_egg"),
    ])
    async def hatch(self, interaction: discord.Interaction, egg_type: str = "common_egg"):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        egg_def = HATCH_EGGS[egg_type]
        price = egg_def["price"]

        if player["gold"] < price:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You need **{price:,} gold** for a {egg_def['name']}! You have `{player['gold']:,}`.",
                color=COLORS["error"]
            ))

        await update_player(interaction.user.id, gold=player["gold"] - price)

        msg = await interaction.followup.send(embed=discord.Embed(
            title="🥚 Hatching...",
            description=f"*{egg_def['flavor']}*",
            color=COLORS["info"]
        ))
        await asyncio.sleep(1.5)
        await msg.edit(embed=discord.Embed(
            title="🥚 Hatching...",
            description="*Cracks are forming...*\n✨✨✨",
            color=COLORS["info"]
        ))
        await asyncio.sleep(1.5)

        import aiosqlite
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1", (interaction.user.id,)
            ) as cursor:
                perks = [dict(r) for r in await cursor.fetchall()]

        beasts_data = load_beasts()

        if check_glimmering_fortune(perks):
            rarity = "divine"
            divine_pool = [b for b in beasts_data.values() if b["rarity"] == "divine" and b["id"] not in STARTER_IDS]
            beast = random.choice(divine_pool)
            subtitle = "🌟 **GLIMMERING FORTUNE ACTIVATED!** 🌟\n*The universe bent. You felt it.*"
        else:
            # Runtime pool accounts for Stardust Touch and Observatory bonuses
            sanctuary = await get_user_sanctuary(interaction.user.id)
            effective_pool = get_egg_pool(egg_type, perks, sanctuary)
            rarity = roll_rarity(effective_pool)
            beast  = get_beast_by_rarity(rarity, beasts_data)
            subtitle = f"*A new companion emerged from your {egg_def['name']}!*"

        if not beast:
            await update_player(interaction.user.id, gold=player["gold"])
            return await msg.edit(embed=discord.Embed(
                description="✦ The egg fizzled. Gold refunded. Try again!",
                color=COLORS["error"]
            ))

        beast_row_id = await add_beast_to_player(interaction.user.id, {**beast, "caught_from": "hatch"})

        all_owned = await get_player_beasts(interaction.user.id)
        if len(all_owned) == 1:
            async with aiosqlite.connect("db/chibibeast.db") as db:
                await db.execute("UPDATE player_beasts SET is_active = 1 WHERE id = ?", (beast_row_id,))
                await db.commit()
            subtitle += "\n✦ *Set as your active beast automatically!*"

        embed = self.build_beast_embed(beast, title=f"🥚 **{beast['name']}** Hatched!", subtitle=subtitle)
        if beast.get("divine_passive"):
            dp = beast["divine_passive"]
            embed.add_field(
                name=f"✨ Divine Passive: {dp['passive_name']}",
                value=dp["passive_desc"],
                inline=False
            )
        view = HatchView(beast, interaction.user.id)
        await msg.edit(embed=embed, view=view)

        completed_quests = await track_quest_event(interaction.user.id, "hatch")
        await unlock_simple_achievement(interaction.user.id, "first_steps")
        more_unlocked = await check_achievements(interaction.user.id)
        if interaction.guild:
            await record_bestiary_sighting(interaction.guild.id, beast["id"], interaction.user.id)
            await check_collection_completion(interaction, beast, beasts_data)
        if rarity == "divine" and "first_divine" in more_unlocked:
            collection = beast.get("collection", "the Divine Collections")
            await interaction.channel.send(embed=discord.Embed(
                title="🌸 Something Notices You",
                description=(
                    f"*When you hatch {beast['name']}, the Loom pauses.*\n\n"
                    f"*{beast['name']} belongs to {collection}.*\n\n"
                    f"*They don\'t usually let themselves be hatched. This one did.*\n\n"
                    f"*Maren would want to know. Orren already does.*"
                ),
                color=COLORS["divine"]
            ))
        await notify_quest_completions(interaction.channel, completed_quests)
        await notify_unlocks(interaction.channel, interaction.user, more_unlocked)


    @app_commands.command(name="explore", description="Explore the world and discover wild ChibiBeasts! 🗺️")
    async def explore(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Cooldown check — persisted in DB so it survives bot restarts
        import time
        now = time.time()
        cooldown = EXPLORE_COOLDOWN

        async with aiosqlite.connect("db/chibibeast.db") as _cddb:
            _cddb.row_factory = aiosqlite.Row
            async with _cddb.execute(
                "SELECT explore_last_at FROM players WHERE user_id = ?", (interaction.user.id,)
            ) as _cdc:
                _row = await _cdc.fetchone()
            last = _row["explore_last_at"] if _row and _row["explore_last_at"] else 0
            async with _cddb.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1", (interaction.user.id,)
            ) as cursor:
                perks = [dict(r) for r in await cursor.fetchall()]

        for perk in perks:
            if perk["perk_id"] == "bramble_walker":
                cooldown = int(cooldown * 0.90)
            if perk["perk_id"] == "chronos_hourglass":
                cooldown = int(cooldown * 0.80)

        remaining = cooldown - (now - last)
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You're still exploring! Rest for **{mins}m {secs}s** before venturing out again.",
                color=COLORS["error"]
            ))

        # Update last explore time in DB
        async with aiosqlite.connect("db/chibibeast.db") as _upddb:
            await _upddb.execute(
                "UPDATE players SET explore_last_at = ? WHERE user_id = ?",
                (now, interaction.user.id)
            )
            await _upddb.commit()

        # ── Quest tracking: every explore call counts toward the explore quest ──
        explore_quests_completed = await track_quest_event(interaction.user.id, "explore")
        explore_unlocked = await unlock_simple_achievement(interaction.user.id, "first_explore")
        if explore_unlocked:
            await notify_unlocks(interaction.channel, interaction.user, ["first_explore"])
        # Questline: track biome visits
        await advance_quest_step(interaction.user.id, "explore", biome=biome["name"])

        # ── Lore-canonical biomes ─────────────────────────────────────────────
        # Each biome has: display name, atmospheric flavor lines, and a rarity
        # weight override (None = use default HATCH_RATES["wild"]).
        # Biome access is gated by player level so early players don't
        # accidentally stumble into the Celestial Loom with a level-1 Slime.
        player_level = player.get("level", 1)

        BIOMES = [
            {
                "name": "🌲 The Whispering Woods",
                "lore": [
                    f"The trees here hum softly, like they're trying to remember a song.",
                    f"Somewhere in the undergrowth, something small shuffles through the glowing leaves.",
                    f"The woods smell like rain and old magic. Something is definitely watching you.",
                    f"Glowing mushrooms line the path. Your companion's ears perk up.",
                ],
                "rarity_weights": {"common": 0.60, "uncommon": 0.28, "rare": 0.10, "epic": 0.02},
                "nothing_found": "You explored the Whispering Woods but the forest kept its secrets today. Found a few coins in the roots, though.",
                "min_level": 1,
            },
            {
                "name": "🌊 The Sunken Abyssal Trenches",
                "lore": [
                    f"The pressure down here is immense. Bioluminescent creatures drift past like living lanterns.",
                    f"Something vast moves in the dark below. You decide not to look directly at it.",
                    f"The water glows faint violet this deep. Ancient runes mark the walls of the trench.",
                    f"Barnacles the size of fists line every surface. The silence down here has weight.",
                ],
                "rarity_weights": {"uncommon": 0.30, "rare": 0.35, "epic": 0.25, "legendary": 0.10},
                "nothing_found": "The Trenches yielded nothing today — whatever lives down here swam deeper when it heard you coming.",
                "min_level": 15,
            },
            {
                "name": "🔥 The Ember Wastes",
                "lore": [
                    f"Columns of flame burst from the cracked earth at random. Your companion steps carefully.",
                    f"The sky here is permanently orange. Everything smells like cinnamon and char.",
                    f"Scorched fossils poke through the ash fields. Whatever left them was very large.",
                    f"A Phoenix feather drifts past on a thermal. Someone was here before you.",
                ],
                "rarity_weights": {"uncommon": 0.25, "rare": 0.35, "epic": 0.30, "legendary": 0.10},
                "nothing_found": "The Ember Wastes burned quiet today. You pocketed a small piece of Phoenix Ash from the ground.",
                "min_level": 10,
            },
            {
                "name": "❄️ The Glacial Hollows",
                "lore": [
                    f"The ice here is so old it's turned blue. Strange shapes shift inside the deeper walls.",
                    f"Your breath mists in shapes that take a moment too long to disperse.",
                    f"Something enormous is frozen mid-stride in the ice behind you. You don't look back.",
                    f"Crystalline spires catch the pale light and split it into impossible colors.",
                ],
                "rarity_weights": {"uncommon": 0.25, "rare": 0.35, "epic": 0.28, "legendary": 0.12},
                "nothing_found": "The Hollows were silent and cold. You found a frozen gemstone worth a little gold.",
                "min_level": 12,
            },
            {
                "name": "🌌 The Celestial Loom",
                "lore": [
                    f"Reality is thin here. You can see other versions of this moment flickering at the edges.",
                    f"The Loom hums. It is not a sound so much as a feeling in your back teeth.",
                    f"Stars drift past at eye level, slowly, like they have nowhere to be.",
                    f"The ground is made of something that isn't quite light and isn't quite stone.",
                ],
                "rarity_weights": {"rare": 0.20, "epic": 0.40, "legendary": 0.30, "divine": 0.10},
                "nothing_found": "The Loom flickered and stilled. Even the divine have quiet days.",
                "min_level": 25,
            },
        ]

        # Filter by player level
        available_biomes = [b for b in BIOMES if player_level >= b["min_level"]]
        if not available_biomes:
            available_biomes = [BIOMES[0]]  # always have at least Whispering Woods

        biome = random.choice(available_biomes)
        location = biome["name"]
        lore_line = random.choice(biome["lore"])
        biome_weights = biome["rarity_weights"]

        loading = discord.Embed(
            title=f"🗺️ Exploring {location}...",
            description=f"*{lore_line}*",
            color=COLORS["info"]
        )

        # First-time biome discovery check
        BIOME_DISCOVERY_KEY = f"biome_discovered_{biome['name'].replace(' ','_').replace('🌌','').replace('❄️','').replace('🌊','').replace('🔥','').replace('🌲','').strip()}"
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT 1 FROM achievements WHERE user_id = ? AND achievement_id = ?",
                (interaction.user.id, BIOME_DISCOVERY_KEY)
            ) as c:
                already_discovered = await c.fetchone()
            if not already_discovered and biome.get("min_level", 1) > 1:
                await db.execute(
                    "INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?, ?)",
                    (interaction.user.id, BIOME_DISCOVERY_KEY)
                )
                await db.commit()
                BIOME_FIRST_LINES = {
                    "🌊 The Sunken Abyssal Trenches": (
                        "You've descended into the **Sunken Abyssal Trenches** for the first time.\n"
                        "*The pressure down here is immense. Somewhere below, something enormous moves.*\n"
                        "*Orren mentioned this place once. He said the Loom went especially deep here.*"
                    ),
                    "🔥 The Ember Wastes": (
                        "You've reached the **Ember Wastes** for the first time.\n"
                        "*Sable's forge is somewhere in here. The sky is permanently orange.*\n"
                        "*You understand now why she doesn't leave.*"
                    ),
                    "❄️ The Glacial Hollows": (
                        "You've entered the **Glacial Hollows** for the first time.\n"
                        "*The ice is so old it's turned blue. Something massive is frozen in the wall behind you.*\n"
                        "*You don't look directly at it. It doesn't seem to mind.*"
                    ),
                    "🌌 The Celestial Loom": (
                        "You've reached the **Celestial Loom** for the first time.\n"
                        "*Reality is thin here. Stars drift past at eye level.*\n"
                        "*Somewhere nearby, Cael is probably writing something down.*"
                    ),
                }
                first_line = BIOME_FIRST_LINES.get(biome["name"])
                if first_line:
                    await interaction.channel.send(embed=discord.Embed(
                        title="🗺️ New Location Discovered!",
                        description=first_line,
                        color=COLORS["info"]
                    ))
        msg = await interaction.followup.send(embed=loading)
        await asyncio.sleep(2)

        # Random outcome — use biome-specific rarity weights
        outcome = random.random()

        if outcome < 0.05:
            # Nothing found
            gold_found = random.randint(10, 50)
            await update_player(interaction.user.id, gold=player["gold"] + gold_found)
            await msg.edit(embed=discord.Embed(
                title=f"🗺️ {location}",
                description=(
                    f"*{biome['nothing_found']}*\n\n"
                    f"You did find **{gold_found} gold** on your way back though! 💰"
                ),
                color=COLORS["info"]
            ))
            await notify_quest_completions(interaction.channel, explore_quests_completed)
            return

        # Roll for encounter — biome weights + stardust_touch perk + sanctuary bonus
        beasts = load_beasts()
        rates = biome_weights.copy()

        for perk in perks:
            if perk["perk_id"] == "stardust_touch":
                rates["rare"]   = min(rates.get("rare",   0) + 0.05, 0.35)
                rates["epic"]   = min(rates.get("epic",   0) + 0.05, 0.25)
                rates["common"] = max(rates.get("common", 0) - 0.10, 0.10)

        # Celestial Observatory sanctuary bonus
        user_sanctuary = await get_user_sanctuary(interaction.user.id)
        rates = apply_explore_encounter_bonus(rates, user_sanctuary)

        # Spellbound Incense boost (persisted in DB)
        import time as _t
        async with aiosqlite.connect("db/chibibeast.db") as _idb:
            _idb.row_factory = aiosqlite.Row
            async with _idb.execute(
                "SELECT incense_active_until FROM players WHERE user_id = ?", (interaction.user.id,)
            ) as _ic:
                _irow = await _ic.fetchone()
        if _irow and _irow["incense_active_until"] and _t.time() < _irow["incense_active_until"]:
            rates["uncommon"] = min(rates.get("uncommon", 0) + 0.08, 0.45)
            rates["rare"]     = min(rates.get("rare",     0) + 0.05, 0.35)
            rates["common"]   = max(rates.get("common",   0) - 0.10, 0.05)

        rarity = roll_rarity(rates)
        beast = get_beast_by_rarity(rarity, beasts)

        if not beast:
            return await msg.edit(embed=discord.Embed(
                description="✦ Something went wrong. Try exploring again!",
                color=COLORS["error"]
            ))

        # Catch chance
        catch_chance = beast["catch_rate"]
        caught = random.random() < catch_chance

        if caught:
            beast_row_id = await add_beast_to_player(interaction.user.id, {**beast, "caught_from": "discover"})
            gold_bonus = random.randint(5, 30)
            await update_player(interaction.user.id, gold=player["gold"] + gold_bonus)

            # Material drop based on rarity — higher rarity = better materials
            RARITY_MATERIAL_POOLS = {
                "common":    [("pixie_silk", 0.40), ("gnome_iron_ore", 0.35), ("enchanted_bark", 0.25)],
                "uncommon":  [("harpy_down", 0.35), ("kelpie_scale", 0.35), ("hellhound_ember", 0.30)],
                "rare":      [("unicorn_hair", 0.35), ("manticore_stinger", 0.33), ("basilisk_eye", 0.32)],
                "epic":      [("phoenix_ash", 0.35), ("thunderbird_talon", 0.35), ("sphinx_papyrus", 0.30)],
                "legendary": [("dragon_scale", 0.35), ("kraken_ink", 0.33), ("fenrir_fur", 0.32)],
                "divine":    [],  # Divine beasts don't drop materials — they're above that
            }
            mat_pool = RARITY_MATERIAL_POOLS.get(rarity, [])
            mat_drop = None
            if mat_pool and random.random() < 0.60:  # 60% chance for a material drop
                roll = random.random()
                cumulative = 0.0
                for mat_id, chance in mat_pool:
                    cumulative += chance
                    if roll <= cumulative:
                        mat_drop = mat_id
                        break
                if mat_drop:
                    async with __import__("aiosqlite").connect("db/chibibeast.db") as db:
                        async with db.execute(
                            "SELECT id, quantity FROM player_materials WHERE user_id = ? AND material_id = ?",
                            (interaction.user.id, mat_drop)
                        ) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute(
                                "UPDATE player_materials SET quantity = quantity + 1 WHERE id = ?",
                                (existing[0],)
                            )
                        else:
                            await db.execute(
                                "INSERT INTO player_materials (user_id, material_id, quantity) VALUES (?,?,1)",
                                (interaction.user.id, mat_drop)
                            )
                        await db.commit()

            # ── Encounter situation flavor ──────────────────────────────
            # What is the beast doing when the player finds it?
            # A small story moment before the catch mechanic fires.
            situations = beast.get("encounter_situations", [])
            situation_line = random.choice(situations) if situations else ""

            # ── NPC ambient presence ────────────────────────────────────
            # NPCs in their home biome occasionally leave a trace.
            # Only fires if the player has at least 'known' relationship.
            from cogs.questline import get_quest_state as _get_qs_exp
            _qs_exp = await _get_qs_exp(interaction.user.id)
            _npc_rel_exp = _qs_exp.get("npc_relationships", {})

            NPC_AMBIENT = {
                "🌲 The Whispering Woods": {
                    "maren": [
                        "*Maren\'s voice, from somewhere deeper in the trees:* \'You\'re walking too fast. The Woods notice when you\'re not paying attention.\'",
                        "*A note pinned to a mushroom in Maren\'s handwriting:* \'If you see a Basilisk near the eastern rocks — don\'t stare. It\'s shy.\'",
                        "*Barkley is sitting on the path ahead, alone. He watches you pass, then turns back toward wherever Maren is.*",
                    ],
                    "orren": [
                        "*Orren is standing very still near an old tree.* \'The trees are remembering something today. It\'s not about you.\'",
                        "*The Dryad at Orren\'s shoulder brightens slightly as you pass — recognition from something that\'s been here longer than you have.*",
                        "*A small marker in the path — a stone placed just so. Orren\'s work. You step around it anyway.*",
                    ],
                },
                "🔥 The Ember Wastes": {
                    "sable": [
                        "*Sable\'s voice, from somewhere in the smoke:* \'Phoenix Ash falls here sometimes. Check the ground after a burn.\'",
                        "*A small Hellhound runs a patrol route you don\'t recognize. Sable\'s, probably. It glances at you and continues.*",
                    ],
                },
                "🌌 The Celestial Loom": {
                    "cael": [
                        "*Cael is crouched nearby, writing fast.* \'The fraying\'s worse today. Don\'t touch the threads.\'",
                        "*A page from Cael\'s notebook drifts past:* \'Loom density at 0.7 — check tomorrow. Check more carefully than last time.\'",
                    ],
                    "the_archivist": [
                        "*Something says, from no particular direction:* \'You\'re going to find something interesting today. I won\'t say what. That would be impolite.\'",
                    ],
                },
            }
            ambient_text = ""
            _biome_ambient = NPC_AMBIENT.get(biome["name"], {})
            _eligible = [lines for npc_id, lines in _biome_ambient.items()
                         if _npc_rel_exp.get(npc_id, "stranger") != "stranger"]
            if _eligible and random.random() < 0.25:
                ambient_text = random.choice(random.choice(_eligible))

            # ── Questline-reactive flavor ───────────────────────────────
            _curr_ch = _qs_exp.get("current_chapter")
            CHAPTER_BIOME_FLAVOR = {
                ("chapter_1", "🌲 The Whispering Woods"): [
                    "*Maren wanted you to explore this place. You wonder what she expects you to notice.*",
                ],
                ("chapter_2", "🌌 The Celestial Loom"): [
                    "*Cael is watching the Loom somewhere nearby. You can feel it being watched.*",
                    "*The fraying Cael mentioned — you think you can almost see it. A thread that looks slightly wrong.*",
                ],
                ("chapter_3", "🔥 The Ember Wastes"): [
                    "*Sable said the Wastes would have what she needs. She was right about the heat.*",
                ],
                ("chapter_4", "🌲 The Whispering Woods"): [
                    "*Orren said the relic is north. You check which direction north is.*",
                    "*The woods feel older today. Or maybe you\'re more aware of how old they are.*",
                ],
                ("chapter_5", "❄️ The Glacial Hollows"): [
                    "*The Archivist said the relic would find you here. You\'re not sure what that means.*",
                ],
                ("chapter_5", "🌊 The Sunken Abyssal Trenches"): [
                    "*The Archivist saw you finding this. They didn\'t say whether you\'d find it today.*",
                ],
            }
            reactive_text = ""
            _rl_key = (_curr_ch, biome["name"])
            if _rl_key in CHAPTER_BIOME_FLAVOR and random.random() < 0.35:
                reactive_text = random.choice(CHAPTER_BIOME_FLAVOR[_rl_key])

            # ── Build encounter embed with layered narrative ────────────
            mat_line = f"\n🪨 **{mat_drop.replace('_',' ').title()}** dropped!" if mat_drop else ""
            encounter_subtitle = ""
            if situation_line:
                encounter_subtitle += f"*{situation_line}*\n\n"
            if reactive_text:
                encounter_subtitle += f"{reactive_text}\n\n"
            if ambient_text:
                encounter_subtitle += f"{ambient_text}\n\n"
            encounter_subtitle += f"*You caught it!* +{gold_bonus} gold{mat_line}"

            embed = self.build_beast_embed(
                beast,
                title=f"🌟 Wild **{beast['name']}**!",
                subtitle=encounter_subtitle
            )
            view = HatchView(beast, interaction.user.id)
            await msg.edit(embed=embed, view=view)

            # ── Progress tracking: quests, achievements, bestiary ──────
            catch_quests_completed = await track_quest_event(interaction.user.id, "catch")
            unlocked = await unlock_simple_achievement(interaction.user.id, "first_steps")
            more_unlocked = await check_achievements(interaction.user.id)
            all_unlocked = (["first_steps"] if unlocked else []) + more_unlocked
            if interaction.guild:
                await record_bestiary_sighting(interaction.guild.id, beast["id"], interaction.user.id)
                await check_collection_completion(interaction, beast, load_beasts())
            # Questline: catch step with beast type tracking
            await advance_quest_step(interaction.user.id, "catch", beast_id=beast["id"])

            # First Divine catch — lore moment
            if rarity == "divine" and "first_divine" in more_unlocked:
                collection = beast.get("collection", "the Divine Collections")
                await interaction.channel.send(embed=discord.Embed(
                    title=f"🌸 Something Notices You",
                    description=(
                        f"*When you catch {beast['name']}, the Loom pauses.*\n\n"
                        f"*Not dramatically — it's a very small pause, the kind you feel in your back teeth "
                        f"before you can name it. Then it passes.*\n\n"
                        f"*{beast['name']} belongs to {collection}. "
                        f"These beings predate the type chart, the biomes, and most of the words "
                        f"currently available to describe them.*\n\n"
                        f"*They don't usually let themselves be caught. This one did.*\n\n"
                        f"*Maren would want to know about this. Orren already does.*"
                    ),
                    color=COLORS["divine"]
                ))

            # Questline chapter 5: Relic drops in specific biomes
            state = await get_quest_state(interaction.user.id) if True else None
            RELIC_BIOME_MAP = {
                "🌌 The Celestial Loom":          None,  # no relic here, handled by The Archivist
                "❄️ The Glacial Hollows":         ("glacial_relic", "chapter_5"),
                "🌊 The Sunken Abyssal Trenches": ("trench_relic", "chapter_5"),
            }
            relic_info = RELIC_BIOME_MAP.get(biome["name"])
            if relic_info:
                relic_id, needed_chapter = relic_info
                from cogs.questline import get_quest_state as _get_qs
                qs = await _get_qs(interaction.user.id)
                if (qs["current_chapter"] == needed_chapter
                        and relic_id not in qs["collected_relics"]
                        and random.random() < 0.30):  # 30% chance per catch in correct biome
                    relic_result = await advance_quest_step(
                        interaction.user.id, "relic_found", relic_id=relic_id
                    )
                    relic_name = relic_id.replace("_", " ").title()
                    await interaction.channel.send(embed=discord.Embed(
                        title=f"✨ Relic Discovered: {relic_name}!",
                        description=(
                            f"*While catching {beast['name']}, something catches your eye — "
                            f"a faint shimmer at the edge of the {biome['name'].split()[-1]}...*\n\n"
                            f"You've found the **{relic_name}**.\n"
                            f"*Use `/questline` to continue your story.*"
                        ),
                        color=COLORS["legendary"]
                    ))
            await notify_quest_completions(interaction.channel, explore_quests_completed + catch_quests_completed)
            await notify_unlocks(interaction.channel, interaction.user, all_unlocked)
        else:
            rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
            situations = beast.get("encounter_situations", [])
            escaped_situation = random.choice(situations) if situations else f"You found a wild **{beast['name']}** in {location}..."
            await msg.edit(embed=discord.Embed(
                title="🗺️ Encounter!",
                description=(
                    f"*{escaped_situation}*\n\n"
                    f"{rarity_emoji} **{beast['name']}** — {RARITY_LABEL.get(rarity)}\n\n"
                    f"*...but it slipped away before you could catch it.*\n"
                    f"Keep exploring — it might appear again."
                ),
                color=COLORS.get(rarity, COLORS["info"])
            ))
            await notify_quest_completions(interaction.channel, explore_quests_completed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hatch(bot))
