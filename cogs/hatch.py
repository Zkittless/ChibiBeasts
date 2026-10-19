from utils import db as storage
import discord
from utils.habitats import BIOMES, destination_label
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
# The Nursery catalog owns grades, origin pools and exact effective odds.

from utils.nursery_catalog import instant_definitions, rarity_pool, choose_rarity

# Compatibility exports; the service and shop share this one catalog.
HATCH_EGGS = instant_definitions()
_BASE_EGG_POOLS = {key: dict(egg['pool']) for key, egg in HATCH_EGGS.items()}


def get_egg_pool(egg_type: str, perks=None, sanctuary=None) -> dict:
    return rarity_pool(egg_type, perks or (), sanctuary)


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
    rarity = choose_rarity(egg_type, rng=random)
    return rarity, rarity == 'altered_divine'

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

    @app_commands.command(name="nursery", description="Choose, hatch and care for your next companions")
    async def nursery(self, interaction: discord.Interaction):
        from utils.nursery_view import open_nursery
        return await open_nursery(interaction, bot=self.bot)

    @app_commands.command(name="hatch", description="Open an instant egg in the Nursery")
    @app_commands.describe(egg_type="Type of egg to hatch")
    @app_commands.choices(egg_type=[app_commands.Choice(name=f"{egg['name']} · Trainer Lv.{egg['min_level']}", value=key)
                                  for key, egg in HATCH_EGGS.items()])
    async def hatch(self, interaction: discord.Interaction, egg_type: str = "common_egg"):
        # Acknowledge before player lookup, inventory checks or image rendering.
        await interaction.response.defer()
        from utils import nursery
        from utils.nursery_view import open_nursery, request_key
        await get_or_create_player(interaction.user.id, str(interaction.user))
        try:
            state = await nursery.snapshot(interaction.user.id)
            egg = next((e for e in state['eggs'] if e['id'] == egg_type), None)
            if not egg or egg.get('incubation_hours'):
                raise ValueError('Choose an instant egg from the Nursery.')
            if egg.get('locked'):
                raise ValueError(f"Reach trainer level {egg['min_level']} to hatch {egg['name']}. Your eggs stay in your inventory.")
            if not egg.get('quantity'):
                raise ValueError(f"You do not have a {egg['name']}. Buy one in the Nursery when you are ready.")
            if egg['quantity'] > 1:
                return await open_nursery(interaction, bot=self.bot, already_deferred=True, egg_id=egg_type,
                    notice='Choose Hatch one or Hatch quantity below. Inventory and level are checked again when you confirm.')
            grants = await nursery.hatch(interaction.user.id, egg_type, request_key=request_key(interaction, 'hatch'))
        except ValueError as error:
            return await open_nursery(interaction, bot=self.bot, already_deferred=True, egg_id=egg_type, notice=str(error))
        return await open_nursery(interaction, bot=self.bot, already_deferred=True, egg_id=egg_type, page='reveal', grants=grants)


    @app_commands.command(name="explore", description="Begin or resume a choice-based expedition in your Field Journal")
    @app_commands.choices(destination=[app_commands.Choice(name=destination_label(key)[:100], value=key) for key in BIOMES])
    @app_commands.describe(expedition="Compatibility option; every exploration now uses your Field Journal")
    async def explore(self, interaction: discord.Interaction, destination: str = "woods", expedition: bool = True):
        await interaction.response.defer()
        from utils.journal import open_journal
        return await open_journal(interaction, bot=self.bot, page='adventure', realm=destination,
                                  begin_expedition=True, already_deferred=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hatch(bot))
