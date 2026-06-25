import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import aiosqlite
from utils.db import (
    get_or_create_player, get_player, update_player,
    add_beast_to_player, get_player_beasts, load_beasts,
    calc_player_exp_for_level, increment_catch_count
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement,
    record_bestiary_sighting, notify_unlocks, notify_quest_completions
)
from cogs.questline import advance_quest_step, get_quest_state
from utils.sanctuary import get_user_sanctuary, apply_explore_encounter_bonus

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "divine"]


def _catch_milestone_line(beast_name: str, rarity: str, count: int, is_milestone: bool) -> str | None:
    """Return a lore-flavored milestone message, or None if not a milestone."""
    if not is_milestone:
        return None

    if count == 1:
        # World first — always special, scales with rarity
        if rarity in ("ancient", "corrupted", "altered_divine"):
            return (
                f"*The Loom pauses. Every thread in the weave goes still.*\n"
                f"*This is the **first {beast_name}** ever caught.*\n"
                f"*Something older than record-keeping just noticed.*"
            )
        elif rarity == "divine":
            return (
                f"*The Loom acknowledges this. Quietly, but completely.*\n"
                f"*You are the first to hold a **{beast_name}**.*\n"
                f"*Orren would want to know. He already does.*"
            )
        elif rarity == "legendary":
            return (
                f"*The Bestiary has no entry for this yet.*\n"
                f"*You have the first **{beast_name}** ever hatched.*"
            )
        else:
            return f"*The first **{beast_name}** ever hatched. The Loom notes it.*"

    ordinal = f"{count:,}{'th' if 11 <= count % 100 <= 13 else {1:'st',2:'nd',3:'rd'}.get(count%10,'th')}"

    if rarity in ("ancient", "corrupted", "altered_divine"):
        messages = {
            2:  f"*Only the second **{beast_name}** in existence. The first trainer was not forgotten.*",
            3:  f"*Three **{beast_name}** now walk in the world. The Loom is watching all three of them.*",
            5:  f"*Five. The Architects are paying attention now.*",
            10: f"*Ten **{beast_name}** caught across all of history. The pattern is becoming clear to something.*",
        }
        return messages.get(count)

    elif rarity == "divine":
        messages = {
            3:  f"*Three **{beast_name}** in the world. They are aware of each other.*",
            5:  f"*The fifth **{beast_name}** hatched. Somewhere, the Loom exhales.*",
            10: f"*Ten **{beast_name}**. The Cosmic Creators notice when their kin multiply.*",
        }
        return messages.get(count)

    elif rarity == "legendary":
        messages = {
            5:  f"*Five **{beast_name}** now. The myths are becoming more crowded.*",
            10: f"*Ten **{beast_name}** walk the world. They do not travel together. They don't need to.*",
            25: f"*The {ordinal} **{beast_name}**. A legend repeated enough times becomes history.*",
        }
        return messages.get(count)

    elif rarity in ("rare", "epic"):
        messages = {
            10:  f"*The {ordinal} **{beast_name}**. The Loom has woven this shape before.*",
            50:  f"*{count} **{beast_name}** hatched. The pattern is well-established now.*",
            100: f"*One hundred **{beast_name}**. The species is no longer rare in the way it once was.*",
        }
        return messages.get(count)

    else:  # common / uncommon
        messages = {
            10:   f"*The {ordinal} **{beast_name}**. Still just as endearing as the first.*",
            100:  f"*{count} **{beast_name}** hatched. A familiar face by now.*",
            500:  f"*Five hundred **{beast_name}**. The Loom has woven this one many times. It doesn't mind.*",
            1000: f"*One thousand **{beast_name}**. Somewhere, the first one is still out there.*",
        }
        return messages.get(count)

# ── Base egg definitions ─────────────────────────────────────────────────────
# Pools are *base* values. Call get_egg_pool(egg_type, perks, sanctuary)
# at runtime to get the effective pool with all active modifiers applied.
#
# Design philosophy:
#   Instant eggs  = convenient, immediate, slightly worse odds
#   Incubation    = patient, time-gated, meaningfully better odds per tier
#   Top incubation = exclusive beasts unavailable anywhere else
#
# Divine rarity targets (avg eggs needed for a divine):
#   Celestial (8k, instant):  5% →  avg 20 eggs  (~88 days at Lv10)  — jackpot feel
#   Abyssal (25k, instant):  25% →  avg 4 eggs   (~55 days at Lv10)  — endgame grind
#   Epic incubation (12k):   25% →  beats Celestial if patient
#   Top incubation (50k, 48h): 40% + exclusive beasts — worth the wait AND gold

_BASE_EGG_POOLS = {
    # Common: fast and cheap — no rare access, just common/uncommon
    "common_egg":    {"common": 0.75, "uncommon": 0.25},
    # Rare: mid-tier — no legendary access, good rare/epic spread
    "rare_egg":      {"uncommon": 0.35, "rare": 0.50, "epic": 0.15},
    # Celestial: high-tier — legendary access but divine is rare (5%)
    "celestial_egg": {"epic": 0.50, "legendary": 0.45, "divine": 0.05},
    # Abyssal: endgame — decent divine (25%) but incubation still beats it if patient
    "abyssal_egg":   {"legendary": 0.70, "divine": 0.25, "altered_chance": 0.01},
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

EXPLORE_COOLDOWN = 1800  # 30 minutes in seconds
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
ALTERED_DIVINE_IDS = ["void_chronos", "fractured_genesis", "abyssal_nebula"]


def roll_with_altered(egg_type: str) -> tuple[str, bool]:
    """
    Roll rarity for an egg, checking altered_chance first.
    Returns (rarity, is_altered) where is_altered=True means pick from ALTERED_DIVINE_IDS.
    """
    altered_chance = _BASE_EGG_POOLS.get(egg_type, {}).get("altered_chance", 0)
    if altered_chance and random.random() < altered_chance:
        return "altered_divine", True
    # Strip non-rarity keys and roll normally
    pool = {k: v for k, v in _BASE_EGG_POOLS.get(egg_type, {"common": 1.0}).items()
            if k != "altered_chance"}
    return roll_rarity(pool), False

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
    pool = [
        b for b in beasts.values()
        if b["rarity"] == rarity
        and b["id"] not in excluded
        and b.get("wild_encounter", True)  # exclude evolution-only beasts
        and b.get("catch_rate", 0) > 0     # exclude uncatchable beasts
    ]
    if not pool:
        pool = [
            b for b in beasts.values()
            if b["rarity"] == rarity
            and b["id"] not in STARTER_IDS
            and b.get("wild_encounter", True)
            and b.get("catch_rate", 0) > 0
        ]
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
                "UPDATE player_beasts SET is_active = 1 WHERE id = (SELECT id FROM player_beasts WHERE user_id = ? AND beast_id = ? ORDER BY id DESC LIMIT 1)",
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

        embed.set_footer(text="ChibiBeasts 🐾  •  Use /collection to view your beasts")
        return embed

    @app_commands.command(name="hatch", description="Hatch an instant egg from your inventory 🥚")
    @app_commands.describe(egg_type="Type of egg to hatch")
    @app_commands.choices(egg_type=[
        app_commands.Choice(name="🥚 Common Egg — common/uncommon",                                          value="common_egg"),
        app_commands.Choice(name="🥚✨ Rare Egg — uncommon/rare/epic",                                       value="rare_egg"),
        app_commands.Choice(name=f"🌌🥚 Celestial Egg — {int(_BASE_EGG_POOLS['celestial_egg'].get('divine',0)*100)}% Divine",  value="celestial_egg"),
        app_commands.Choice(name=f"🌊💎 Abyssal Egg — {int(_BASE_EGG_POOLS['abyssal_egg'].get('divine',0)*100)}% Divine",     value="abyssal_egg"),
    ])
    async def hatch(self, interaction: discord.Interaction, egg_type: str = "common_egg"):
        await get_or_create_player(interaction.user.id, str(interaction.user))
        egg_def = HATCH_EGGS[egg_type]

        # Check inventory
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, egg_type)
            ) as c:
                inv_row = await c.fetchone()

        if not inv_row or inv_row["quantity"] < 1:
            return await interaction.response.send_message(embed=discord.Embed(
                description=(
                    f"✦ You don't have a **{egg_def['name']}** in your inventory!\n"
                    f"Buy one from `/shop` → ⚡ Instant Eggs first."
                ),
                color=COLORS["error"]
            ), ephemeral=True)

        from utils.modals import QuantityModal

        async def do_hatch(modal_interaction: discord.Interaction, quantity: int):
            if not modal_interaction.response.is_done():
                await modal_interaction.response.defer()

            # Deduct all at once atomically
            async with aiosqlite.connect("db/chibibeast.db") as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                    (modal_interaction.user.id, egg_type)
                ) as c:
                    fresh = await c.fetchone()
                if not fresh or fresh["quantity"] < quantity:
                    return await modal_interaction.followup.send(embed=discord.Embed(
                        description=f"✦ You only have `{fresh['quantity'] if fresh else 0}` eggs now.",
                        color=COLORS["error"]
                    ))
                if fresh["quantity"] == quantity:
                    await db.execute("DELETE FROM player_inventory WHERE id = ?", (fresh["id"],))
                else:
                    await db.execute(
                        "UPDATE player_inventory SET quantity = quantity - ? WHERE id = ?",
                        (quantity, fresh["id"])
                    )
                await db.commit()

            async with aiosqlite.connect("db/chibibeast.db") as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1",
                    (modal_interaction.user.id,)
                ) as cursor:
                    perks = [dict(r) for r in await cursor.fetchall()]

            beasts_data = load_beasts()
            sanctuary = await get_user_sanctuary(modal_interaction.user.id)
            hatched = []

            for i in range(quantity):
                if check_glimmering_fortune(perks):
                    rarity = "divine"
                    divine_pool = [b for b in beasts_data.values() if b["rarity"] == "divine" and b["id"] not in STARTER_IDS]
                    beast = random.choice(divine_pool)
                else:
                    rarity, is_altered = roll_with_altered(egg_type)
                    if is_altered:
                        altered_pool = [b for b in beasts_data.values() if b["rarity"] == "altered_divine"]
                        beast = random.choice(altered_pool) if altered_pool else get_beast_by_rarity("divine", beasts_data)
                    else:
                        effective_pool = get_egg_pool(egg_type, perks, sanctuary)
                        rarity = roll_rarity(effective_pool)
                        beast = get_beast_by_rarity(rarity, beasts_data)
                if beast:
                    beast_row_id = await add_beast_to_player(modal_interaction.user.id, {**beast, "caught_from": "hatch"})
                    hatched.append((beast, rarity, beast_row_id))

            if not hatched:
                return await modal_interaction.followup.send(embed=discord.Embed(
                    description="✦ The eggs fizzled. Try again!", color=COLORS["error"]
                ))

            # Auto-set active if first ever beast
            all_owned = await get_player_beasts(modal_interaction.user.id)
            if len(all_owned) == len(hatched):
                async with aiosqlite.connect("db/chibibeast.db") as db:
                    await db.execute("UPDATE player_beasts SET is_active = 1 WHERE id = ?", (hatched[0][2],))
                    await db.commit()

            if quantity == 1:
                # Single hatch — full dramatic reveal
                beast, rarity, beast_row_id = hatched[0]
                count, is_milestone = await increment_catch_count(beast["id"], rarity)
                subtitle = f"*A new companion emerged from your {egg_def['name']}!*"
                msg = await modal_interaction.followup.send(embed=discord.Embed(
                    title="🥚 Hatching...", description=f"*{egg_def['flavor']}*", color=COLORS["info"]
                ))
                await asyncio.sleep(1.5)
                await msg.edit(embed=discord.Embed(
                    title="🥚 Hatching...", description="*Cracks are forming...*\n✨✨✨", color=COLORS["info"]
                ))
                await asyncio.sleep(1.5)
                embed = self.build_beast_embed(beast, title=f"🥚 **{beast['name']}** Hatched!", subtitle=subtitle)
                if beast.get("divine_passive"):
                    dp = beast["divine_passive"]
                    passive_labels = {"divine": "✨ Divine Passive", "altered_divine": "⚠️ Altered Passive",
                                      "corrupted": "🖤 Corrupted Passive", "ancient": "🏛️ Ancient Passive"}
                    plabel = passive_labels.get(beast.get("rarity", ""), "✨ Special Passive")
                    embed.add_field(name=f"{plabel}: {dp['passive_name']}", value=dp["passive_desc"], inline=False)
                milestone_line = _catch_milestone_line(beast["name"], rarity, count, is_milestone)
                if milestone_line:
                    embed.add_field(name="📜 The Loom Stirs", value=milestone_line, inline=False)
                view = HatchView(beast, modal_interaction.user.id)
                await msg.edit(embed=embed, view=view)
            else:
                # Multi-hatch — summary embed, increment counts silently
                lines = []
                milestone_lines = []
                for beast, rarity, beast_row_id in hatched:
                    r_emoji = RARITY_EMOJI.get(rarity, "⚪")
                    lines.append(f"{r_emoji} **{beast['name']}** — {RARITY_LABEL.get(rarity, rarity)}")
                    count, is_milestone = await increment_catch_count(beast["id"], rarity)
                    ml = _catch_milestone_line(beast["name"], rarity, count, is_milestone)
                    if ml:
                        milestone_lines.append(ml)
                embed = discord.Embed(
                    title=f"🥚 Hatched {quantity}x {egg_def['name']}!",
                    description="\n".join(lines),
                    color=COLORS["legendary"]
                )
                if milestone_lines:
                    embed.add_field(name="📜 The Loom Stirs", value="\n".join(milestone_lines), inline=False)
                embed.set_footer(text="ChibiBeasts 🐾  •  Check /collection to see your new beasts!")
                await modal_interaction.followup.send(embed=embed)

            completed_quests = await track_quest_event(modal_interaction.user.id, "hatch")
            await unlock_simple_achievement(modal_interaction.user.id, "first_steps")
            more_unlocked = await check_achievements(modal_interaction.user.id)
            if modal_interaction.guild:
                for beast, rarity, _ in hatched:
                    await record_bestiary_sighting(modal_interaction.guild.id, beast["id"], modal_interaction.user.id)
                    await check_collection_completion(modal_interaction, beast, beasts_data)
            await notify_quest_completions(modal_interaction.channel, completed_quests)
            await notify_unlocks(modal_interaction.channel, modal_interaction.user, more_unlocked)

        # Skip modal if only 1 egg — hatch immediately
        if inv_row["quantity"] == 1:
            await do_hatch(interaction, 1)
        else:
            from utils.modals import QuantityModal
            await interaction.response.send_modal(QuantityModal(
                title=f"Hatch {egg_def['name']}",
                item_name=egg_def["name"],
                max_quantity=inv_row["quantity"],
                callback=do_hatch
            ))



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

        # Questline: track biome visits (biome now assigned)
        await advance_quest_step(interaction.user.id, "explore", biome=biome["name"])

        # Override lore_line with questline-aware flavor if on a relevant chapter
        try:
            from cogs.questline import get_quest_state as _gqs
            _qstate = await _gqs(interaction.user.id)
            _curr_ch = _qstate.get("current_chapter", "")
            QUESTLINE_LORE_OVERRIDES = {
                ("chapter_1",  "🌲 The Whispering Woods"):  "The woods feel different when you're paying attention to them. Maren would say that's not a coincidence.",
                ("chapter_2",  "🌌 The Celestial Loom"):    "The Loom hums at a frequency you're starting to recognize. Cael said the fraying was subtle. He wasn't lying.",
                ("chapter_3",  "🔥 The Ember Wastes"):      "The Wastes are loud and orange and honest. Something here lives the life Sable is trying to make with her hands.",
                ("chapter_4",  "🌲 The Whispering Woods"):  "Orren was right — the woods remember things. You can feel it in how the roots have arranged themselves.",
                ("chapter_5",  "❄️ The Glacial Hollows"):   "The ice is old enough to remember what came before the first Sundering. So is whatever is frozen inside it.",
                ("chapter_5",  "🌊 The Sunken Abyssal Trenches"): "The Trenches go deeper than the world officially admits. The relic is down here, if you let something lead you to it.",
                ("chapter_6",  "🌌 The Celestial Loom"):    "The fraying Cael mentioned — you think you can see it now. A thread that doesn't quite know what it's attached to.",
                ("chapter_8",  "🌲 The Whispering Woods"):  "You're looking for something old. Not ancient — just old. Something that was here before the third revision.",
                ("chapter_9",  "🔥 The Ember Wastes"):      "Sable's chalk map is still on the forge table. The corrupted bosses came from this direction. You keep thinking about that.",
                ("chapter_10", "🌌 The Celestial Loom"):    "The Archivist's question echoes. *What kind of tamer are you becoming?* The Loom is waiting to see what you do next.",
            }
            _override = QUESTLINE_LORE_OVERRIDES.get((_curr_ch, location))
            if _override:
                lore_line = _override
        except Exception:
            pass

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

        # ── Encounter type ─────────────────────────────────────────────────────
        # Pixie Pocket perk: +5% affinity chance
        pixie_pocket = any(p.get("perk_id") == "pixie_pocket" and p.get("equipped") for p in perks)

        # 15% chance beast takes a liking to you (scales slightly with catch_rate)
        # Higher catch_rate beasts are friendlier — divine beasts never just walk up
        base_affinity = 0.15 * beast["catch_rate"]
        affinity_chance = min(0.20, base_affinity + (0.05 if pixie_pocket else 0))
        takes_liking = random.random() < affinity_chance

        TAKES_LIKING_LINES = {
            "common": [
                f"*{beast['name']} sniffs the air, looks at you, and decides you're probably fine.*",
                f"*{beast['name']} walks up and sits down next to you. You didn't ask it to. It didn't ask you either.*",
                f"*{beast['name']} examines you with the careful attention of a creature that has decided, without evidence, to trust you.*",
            ],
            "uncommon": [
                f"*{beast['name']} circles you twice, then sits. Something was decided in that second circle.*",
                f"*{beast['name']} looks at you for a long moment. Whatever it was checking for, you apparently have it.*",
            ],
            "rare": [
                f"*{beast['name']} approaches slowly. Rare creatures don't do this. This one is.*",
                f"*{beast['name']} stops a few feet away and doesn't leave. The Loom is watching this.*",
            ],
            "epic": [
                f"*{beast['name']} regards you with something that might be recognition. You haven't met before. That's what makes it strange.*",
            ],
            "legendary": [
                f"*{beast['name']} turns toward you. Legendary beasts don't turn toward things. They make things turn toward them. Something about you changed that.*",
            ],
            "divine": [
                f"*{beast['name']} appears beside you without crossing the distance between. It looks at you as if you've already decided something important.*",
            ],
        }
        tier = rarity if rarity in TAKES_LIKING_LINES else "common"

        # ── Common reward helper (runs after any successful catch) ─────────────
        async def apply_catch_rewards(caught_via_battle: bool = False) -> tuple:
            """Apply all post-catch rewards. Returns (gold_bonus, explore_exp, mat_drop, leveled_up, new_level)."""
            beast_row_id = await add_beast_to_player(interaction.user.id, {**beast, "caught_from": "discover"})
            gold_bonus = random.randint(player_level * 3, player_level * 8)
            if pixie_pocket:
                gold_bonus = int(gold_bonus * 1.10)
            if caught_via_battle:
                gold_bonus = int(gold_bonus * 1.25)  # battle bonus

            async with aiosqlite.connect("db/chibibeast.db") as _bcdb:
                async with _bcdb.execute("SELECT COUNT(*) FROM player_beasts WHERE user_id = ?", (interaction.user.id,)) as _bcc:
                    _bc_count = (await _bcc.fetchone())[0]
            from cogs.questline import advance_quest_step as _aqbc
            await _aqbc(interaction.user.id, "beast_count_check", count=_bc_count)

            explore_exp = random.randint(player_level * 12, player_level * 20)
            if caught_via_battle:
                explore_exp = int(explore_exp * 1.3)
            from utils.sanctuary import apply_exp_bonus as _aeb
            explore_exp = _aeb(explore_exp, user_sanctuary)
            await update_player(interaction.user.id, gold=player["gold"] + gold_bonus)
            from cogs.battle import award_player_exp as _award_exp
            _p_lvl, _, _p_leveled = await _award_exp(interaction.user.id, explore_exp)

            # Material drop
            RARITY_MATERIAL_POOLS = {
                "common":    [("pixie_silk", 0.40), ("gnome_iron_ore", 0.35), ("enchanted_bark", 0.25)],
                "uncommon":  [("harpy_down", 0.35), ("kelpie_scale", 0.35), ("hellhound_ember", 0.30)],
                "rare":      [("unicorn_hair", 0.35), ("manticore_stinger", 0.33), ("basilisk_eye", 0.32)],
                "epic":      [("phoenix_ash", 0.35), ("thunderbird_talon", 0.35), ("sphinx_papyrus", 0.30)],
                "legendary": [("dragon_scale", 0.35), ("kraken_ink", 0.33), ("fenrir_fur", 0.32)],
                "divine":    [],
            }
            mat_pool = RARITY_MATERIAL_POOLS.get(rarity, [])
            mat_drop = None
            if mat_pool and random.random() < 0.60:
                roll = random.random()
                cumulative = 0.0
                for mat_id, chance in mat_pool:
                    cumulative += chance
                    if roll <= cumulative:
                        mat_drop = mat_id
                        break
                if mat_drop:
                    async with aiosqlite.connect("db/chibibeast.db") as db:
                        async with db.execute("SELECT id, quantity FROM player_materials WHERE user_id = ? AND material_id = ?", (interaction.user.id, mat_drop)) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute("UPDATE player_materials SET quantity = quantity + 1 WHERE id = ?", (existing[0],))
                        else:
                            await db.execute("INSERT INTO player_materials (user_id, material_id, quantity) VALUES (?,?,1)", (interaction.user.id, mat_drop))
                        await db.commit()

            return gold_bonus, explore_exp, mat_drop, _p_leveled, _p_lvl, beast_row_id

        async def show_catch_embed(flavor_line: str, gold_bonus: int, explore_exp: int, mat_drop, leveled_up: bool, new_level: int, caught_via_battle: bool = False):
            """Build and display the catch embed."""
            mat_line = f"\n🪨 **{mat_drop.replace('_',' ').title()}** dropped!" if mat_drop else ""
            battle_bonus_line = "\n⚔️ *Battle bonus: +25% gold & EXP!*" if caught_via_battle else ""
            encounter_subtitle = (
                f"*{flavor_line}*\n\n"
                f"*You caught it!* +{gold_bonus} gold | +{explore_exp} EXP{mat_line}{battle_bonus_line}"
                + (f"\n⬆️ **Trainer leveled up to {new_level}!**" if leveled_up else "")
            )
            embed = self.build_beast_embed(beast, title=f"🌟 Wild **{beast['name']}**!", subtitle=encounter_subtitle)
            count, is_milestone = await increment_catch_count(beast["id"], rarity)
            milestone_line = _catch_milestone_line(beast["name"], rarity, count, is_milestone)
            if milestone_line:
                embed.add_field(name="📜 The Loom Stirs", value=milestone_line, inline=False)
            view = HatchView(beast, interaction.user.id)
            await msg.edit(embed=embed, view=view)

        async def apply_catch_tracking():
            """Quest/achievement/bestiary tracking after a successful catch."""
            catch_quests_completed = await track_quest_event(interaction.user.id, "catch")
            if rarity in {"rare", "epic", "legendary", "divine", "altered_divine", "corrupted", "ancient"}:
                rare_q = await track_quest_event(interaction.user.id, "catch_rare")
                if rare_q:
                    catch_quests_completed = catch_quests_completed + rare_q
            await advance_quest_step(interaction.user.id, "catch", beast_id=beast.get("id",""), beast_type=beast.get("type",""))
            unlocked = await unlock_simple_achievement(interaction.user.id, "first_steps")
            more_unlocked = await check_achievements(interaction.user.id)
            all_unlocked = (["first_steps"] if unlocked else []) + more_unlocked
            if interaction.guild:
                await record_bestiary_sighting(interaction.guild.id, beast["id"], interaction.user.id)
                await check_collection_completion(interaction, beast, load_beasts())
            await advance_quest_step(interaction.user.id, "catch", beast_id=beast["id"])

            if rarity == "divine" and "first_divine" in more_unlocked:
                collection = beast.get("collection", "the Divine Collections")
                await interaction.channel.send(embed=discord.Embed(
                    title="🌸 Something Notices You",
                    description=(
                        f"*When you catch {beast['name']}, the Loom pauses.*\n\n"
                        f"*Not dramatically — it's a very small pause, the kind you feel in your back teeth before you can name it. Then it passes.*\n\n"
                        f"*{beast['name']} belongs to {collection}. These beings predate the type chart, the biomes, and most of the words currently available to describe them.*\n\n"
                        f"*They don't usually let themselves be caught. This one did.*\n\n"
                        f"*Maren would want to know about this. Orren already does.*"
                    ),
                    color=COLORS["divine"]
                ))

            # Chapter 5 relic drops
            RELIC_BIOME_MAP = {
                "❄️ The Glacial Hollows":          ("glacial_relic", "chapter_5"),
                "🌊 The Sunken Abyssal Trenches":  ("trench_relic",  "chapter_5"),
            }
            relic_info = RELIC_BIOME_MAP.get(biome["name"])
            if relic_info:
                relic_id, needed_chapter = relic_info
                from cogs.questline import get_quest_state as _get_qs
                qs = await _get_qs(interaction.user.id)
                if (qs["current_chapter"] == needed_chapter
                        and relic_id not in qs["collected_relics"]
                        and random.random() < 0.30):
                    await advance_quest_step(interaction.user.id, "relic_found", relic_id=relic_id)
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

            await notify_quest_completions(interaction.channel, catch_quests_completed)
            await notify_unlocks(interaction.channel, interaction.user, all_unlocked)

        # ── PATH 1: Beast takes a liking to you ───────────────────────────────
        if takes_liking:
            liking_line = random.choice(TAKES_LIKING_LINES[tier])
            gold, exp, mat, leveled, new_lvl, _ = await apply_catch_rewards(caught_via_battle=False)
            await show_catch_embed(liking_line, gold, exp, mat, leveled, new_lvl, caught_via_battle=False)
            await apply_catch_tracking()
            await notify_quest_completions(interaction.channel, explore_quests_completed)

        # ── PATH 2: Must battle ───────────────────────────────────────────────
        else:
            r_emoji  = RARITY_EMOJI.get(rarity, "⚪")
            situations = beast.get("encounter_situations", [])
            situation_line = random.choice(situations) if situations else f"A wild {beast['name']} appears in {location}!"

            # Show battle intro
            wild_level = max(1, min(50, player_level + random.randint(-2, 2)))
            # Fetch active beast data for thumbnail
            from cogs.battle import build_pve_beast_state, run_pve_battle, get_active_beast
            active_row = await get_active_beast(interaction.user.id)
            active_beast_data = get_beast_data(active_row["beast_id"]) or {} if active_row else {}

            battle_intro = discord.Embed(
                title=f"⚔️ Wild {r_emoji} {beast['name']} Lv.{wild_level}!",
                description=(
                    f"*{situation_line}*\n\n"
                    f"**It won't go without a fight.**\n"
                    f"*Defeat it to add it to your collection!*"
                ),
                color=COLORS.get(rarity, COLORS["info"])
            )
            if beast.get("image_url"):
                battle_intro.set_image(url=beast["image_url"])
            if active_beast_data.get("image_url"):
                battle_intro.set_thumbnail(url=active_beast_data["image_url"])
            await msg.edit(embed=battle_intro)

            # Run battle
            enemy_state = build_pve_beast_state(beast, wild_level)

            battle_won = False

            async def on_explore_win(embed, p_state, e_state, timed_out=False):
                nonlocal battle_won
                if not timed_out:
                    battle_won = True

            async def on_explore_loss(embed, p_state, e_state):
                nonlocal battle_won
                battle_won = False
                # Give consolation gold even on loss
                consolation = random.randint(player_level * 2, player_level * 5)
                await update_player(interaction.user.id, gold=player["gold"] + consolation)
                escaped_embed = discord.Embed(
                    title=f"💨 {beast['name']} escaped!",
                    description=(
                        f"*{beast['name']} slipped away — but the fight wasn't for nothing.*\n\n"
                        f"You found **{consolation} gold** nearby. Keep exploring."
                    ),
                    color=COLORS["info"]
                )
                if beast.get("image_url"):
                    escaped_embed.set_thumbnail(url=beast["image_url"])
                await interaction.channel.send(embed=escaped_embed)

            active_beast_data_row = dict(active_row) if active_row else {}
            player_perks_list = perks

            await run_pve_battle(
                interaction=interaction,
                player_beast_row=active_beast_data_row,
                player_beast_data=active_beast_data,
                player_perks=player_perks_list,
                enemy_state=enemy_state,
                enemy_personality="aggressive",
                battle_title=f"Wild {beast['name']}",
                on_win=on_explore_win,
                on_loss=on_explore_loss,
            )

            if battle_won:
                gold, exp, mat, leveled, new_lvl, _ = await apply_catch_rewards(caught_via_battle=True)
                catch_flavor = f"You defeated {beast['name']} — and it recognized the strength. It follows."
                await show_catch_embed(catch_flavor, gold, exp, mat, leveled, new_lvl, caught_via_battle=True)
                await apply_catch_tracking()

            await notify_quest_completions(interaction.channel, explore_quests_completed)



async def setup(bot: commands.Bot):
    await bot.add_cog(Hatch(bot))
