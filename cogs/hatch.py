from utils import db as storage
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import aiosqlite
from utils.db import (
    get_or_create_player,
    add_beast_to_player, get_player_beasts, load_beasts,
    get_catch_count
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement,
    record_bestiary_sighting, notify_unlocks, notify_quest_completions
)
from utils.sanctuary import get_user_sanctuary

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
                f"*You have the first **{beast_name}** ever caught.*"
            )
        else:
            return f"*The first **{beast_name}** ever caught. The Loom notes it.*"

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
#   Top incubation = curated collections and a higher guaranteed rarity floor
#
# Incubation prices/unlocks live in utils.economy_balance. Instant eggs favor
# convenience; Epic incubators favor broad collection, Legendary eggs offer
# curated Legendary/Divine pools, and Divine eggs guarantee a focused collection.

_BASE_EGG_POOLS = {
    # Common: fast and cheap — no rare access, just common/uncommon
    "common_egg":    {"common": 0.75, "uncommon": 0.25},
    # Rare: mid-tier — no legendary access, good rare/epic spread
    "rare_egg":      {"uncommon": 0.35, "rare": 0.50, "epic": 0.15},
    # Celestial: high-tier — legendary access but divine is rare (5%)
    "celestial_egg": {"epic": 0.50, "legendary": 0.45, "divine": 0.05},
    # Abyssal: 74% Legendary, 25% Divine, 1% Altered Divine before Fortune.
    "abyssal_egg":   {"legendary": 0.74, "divine": 0.25, "altered_chance": 0.01},
}

HATCH_EGGS = {
    "common_egg": {
        "name": "🥚 Common Egg", "price": 200, "min_level": 1,
        "flavor": "A small, warm egg. Something common and wonderful is inside.",
    },
    "rare_egg": {
        "name": "🥚✨ Rare Egg", "price": 1500, "min_level": 3,
        "flavor": "The shell vibrates faintly. Something with opinions is in there.",
    },
    "celestial_egg": {
        "name": "🌌🥚 Celestial Egg", "price": 8000, "min_level": 10,
        "flavor": "The Loom wove this one slowly. Whatever is inside has been waiting.",
    },
    "abyssal_egg": {
        "name": "🌊💎 Abyssal Egg", "price": 25000, "min_level": 15,
        "flavor": "It comes from somewhere deeper than deep. The weight of it is strange.",
    },
}


def get_egg_pool(egg_type: str, perks=None, sanctuary=None) -> dict:
    pool={k:v for k,v in _BASE_EGG_POOLS.get(egg_type,{"common":1.0}).items() if k!="altered_chance"}
    def promote(target,amount):
        # Bonuses improve tiers this egg already offers; a cheap egg stays cheap.
        if target not in pool:return
        rank=RARITY_ORDER.index(target)
        donors=[k for k in pool if RARITY_ORDER.index(k)<rank]
        available=sum(pool[k] for k in donors)
        moved=min(amount,available)
        if not moved:return
        for key in donors:pool[key]-=moved*pool[key]/available
        pool[target]=pool.get(target,0)+moved
    if any(p.get("perk_id")=="stardust_touch" and p.get("equipped") for p in perks or ()):
        promote("rare",.10);promote("epic",.10)
    if sanctuary and sanctuary.get("celestial_observatory"):
        promote("epic",.02);promote("legendary",.02)
    total=sum(pool.values())
    return {k:v/total for k,v in pool.items() if v>0}


# Keep for explore wild encounter rolls only
HATCH_RATES = {
    "wild": {"common": 0.45, "uncommon": 0.25, "rare": 0.15, "epic": 0.09, "legendary": 0.05, "divine": 0.01}
}

EGG_PRICES = {k: v["price"] for k, v in HATCH_EGGS.items()}
EGG_NAMES  = {k: v["name"]  for k, v in HATCH_EGGS.items()}

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
    total=sum(pool.values())
    pool={k:v/total for k,v in pool.items()}
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
    async with storage.connect() as db:
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
        await db.execute("BEGIN IMMEDIATE")
        reward = COLLECTION_REWARDS[collection]
        claimed=await db.execute(
            "INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?, ?)",
            (interaction.user.id, ach_id)
        )
        if not claimed.rowcount:return
        from utils.rewards import shards
        await shards(db,interaction.user.id,reward["celestial_shards"])
        await db.execute("UPDATE players SET gold=gold+? WHERE user_id=?",(reward["gold"],interaction.user.id))
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
        async with storage.connect() as db:
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
        async with storage.connect() as db:
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
            async with storage.connect() as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
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

            async with storage.connect() as db:
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
                    divine_pool = [b for b in beasts_data.values() if b["rarity"] == "divine" and b["id"] not in STARTER_IDS and not b.get("evolution_only")]
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
                    from utils.hatching import attune_hatch
                    beast=await attune_hatch(modal_interaction.user.id,beast,beasts_data)
                    rarity=beast["rarity"]
                    beast_row_id = await add_beast_to_player(modal_interaction.user.id, {**beast, "caught_from": "hatch"})
                    hatched.append((beast, rarity, beast_row_id))

            if not hatched:
                return await modal_interaction.followup.send(embed=discord.Embed(
                    description="✦ The eggs fizzled. Try again!", color=COLORS["error"]
                ))

            # Auto-set active if first ever beast
            all_owned = await get_player_beasts(modal_interaction.user.id)
            if len(all_owned) == len(hatched):
                async with storage.connect() as db:
                    await db.execute("UPDATE player_beasts SET is_active = 1 WHERE id = ?", (hatched[0][2],))
                    await db.commit()

            if quantity == 1:
                # Single hatch — full dramatic reveal
                beast, rarity, beast_row_id = hatched[0]
                count, is_milestone = await get_catch_count(beast["id"], rarity)
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
                    count, is_milestone = await get_catch_count(beast["id"], rarity)
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

            completed_quests = await track_quest_event(modal_interaction.user.id, "hatch",amount=len(hatched))
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



    @app_commands.command(name="explore", description="Begin or resume a choice-based expedition in your Field Journal")
    @app_commands.choices(destination=[app_commands.Choice(name="Whispering Woods · Lv1",value="woods"),app_commands.Choice(name="Ember Wastes · Lv3",value="ember"),app_commands.Choice(name="Glacial Hollows · Lv5",value="glacial"),app_commands.Choice(name="Sunken Trenches · Lv8",value="trenches"),app_commands.Choice(name="Celestial Loom · Lv10",value="loom")])
    @app_commands.describe(expedition="Compatibility option; every exploration now uses your Field Journal")
    async def explore(self, interaction: discord.Interaction, destination: str = "woods", expedition: bool = True):
        await interaction.response.defer()
        from utils.journal import open_journal
        return await open_journal(interaction, bot=self.bot, page='adventure', realm=destination,
                                  begin_expedition=True, already_deferred=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hatch(bot))
