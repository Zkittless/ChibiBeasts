from utils import db as storage
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
from utils.progression import (AWAKENING_LEVEL, AWAKENING_TRAINER_LEVEL,
                               AWAKENING_BOND, AWAKENING_HAPPINESS)


# ── Data loaders ──────────────────────────────────────────────────────────────
def load_materials():
    from utils.catalog import load_catalog
    return load_catalog('materials.json')['materials']

def load_equipment():
    from utils.catalog import load_catalog
    data=load_catalog('equipment.json')
    return data['equipment'],data['runes']

# ── Egg definitions ───────────────────────────────────────────────────────────
from utils.nursery_catalog import incubation_definitions, choose_rarity, species_pool

# Older scripts can still import these names; actual hatches use utils.nursery.
EGGS = incubation_definitions()

from utils.economy_balance import INCUBATION_PRICES as EGG_PRICES

# ── Sanctuary upgrade definitions ─────────────────────────────────────────────
SANCTUARY_UPGRADES = {
    # ── Tier 1 ────────────────────────────────────────────────────────────
    "fairy_garden": {
        "name": "🌸 Fairy Garden",
        "tier": 1,
        "cost_tokens": 50,
        "description": "Claiming /daily gives benched companions +5 happiness, offsetting daily decay.",
        "lore": "The Fairies help because they want to. That's more unsettling than if they were paid.",
        "db_column": "fairy_garden",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/fairy_garden.png",
    },
    # ── Tier 2 ────────────────────────────────────────────────────────────
    "gnome_forge": {
        "name": "⚒️ Gnome Forge",
        "tier": 2,
        "cost_tokens": 150,
        "description": "Saves one material from each recipe stack of two or more; every ingredient still needs at least one.",
        "lore": "The Gnomes insisted on designing the logo themselves. Nobody is allowed to comment on the logo.",
        "db_column": "gnome_forge",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/gnome_forge.png",
        "requires": "fairy_garden",
    },
    "training_grounds": {
        "name": "🏋️ Training Grounds",
        "tier": 2,
        "cost_tokens": 150,
        "description": "Reduces gold cost of beast training by 10% for all guild members.",
        "lore": "The Grounds remember every beast that ever trained here. The memory makes the next one faster.",
        "db_column": "training_grounds",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/training_grounds.png",
        "requires": "fairy_garden",
    },
    # ── Tier 3 ────────────────────────────────────────────────────────────
    "celestial_observatory": {
        "name": "🔭 Celestial Observatory",
        "tier": 3,
        "cost_tokens": 300,
        "description": "Improves Epic and Legendary exploration odds and eligible instant-egg tiers. Egg bonuses stay within that egg's offered rarities.",
        "lore": "From here you can see the Celestial Loom directly, if you're patient and the night is clear.",
        "db_column": "celestial_observatory",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/celestial_observatory.png",
        "requires": "gnome_forge",
    },
    "arcane_library": {
        "name": "📚 Arcane Library",
        "tier": 3,
        "cost_tokens": 300,
        "description": "All guild members gain +15% EXP from battles and explores.",
        "lore": "Every book in the Library was written by something that no longer exists. They still have opinions.",
        "db_column": "arcane_library",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/arcane_library.png",
        "requires": "gnome_forge",
    },
    # ── Tier 4 ────────────────────────────────────────────────────────────
    "raid_altar": {
        "name": "⚔️ Raid Altar",
        "tier": 4,
        "cost_tokens": 500,
        "description": "Guild raids deal +10% damage. All members get +5% armor reduction against raid bosses.",
        "lore": "The Altar doesn't ask what you're fighting for. It only asks if you're ready.",
        "db_column": "raid_altar",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/raid_altar.png",
        "requires": "celestial_observatory",
    },
    "beast_market_stall": {
        "name": "🏪 Market Stall",
        "tier": 4,
        "cost_tokens": 500,
        "description": "Guild members can list 2 extra beasts on the market simultaneously.",
        "lore": "The Stall has been here longer than the guild. It was waiting for someone to use it properly.",
        "db_column": "beast_market_stall",
        "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/market_stall.png",
        "requires": "arcane_library",
    },
}


def roll_egg_rarity(egg_id: str) -> str:
    return choose_rarity(egg_id, rng=random) if egg_id in EGGS else 'common'


def pick_beast_for_rarity(rarity: str, egg: dict) -> dict | None:
    egg_id = egg.get('id') or next((key for key, item in EGGS.items() if item == egg), None)
    pool = species_pool(egg_id, rarity) if egg_id else []
    return random.choice(pool) if pool else None


class World(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /incubate ──────────────────────────────────────────────────────────
    async def incubate_autocomplete(self, interaction: discord.Interaction, current: str):
        # Autocomplete has no defer response; keep it a small inventory read.
        inventory = await storage.get_inventory(interaction.user.id)
        owned = {row['item_id']: row['quantity'] for row in inventory}
        return [app_commands.Choice(name=f"{egg['name']} (x{owned[egg_id]})"[:100], value=egg_id)
                for egg_id, egg in EGGS.items() if owned.get(egg_id, 0) > 0
                and current.lower() in egg['name'].lower()][:25]

    @app_commands.command(name="incubate", description="Start passive incubation in the Nursery")
    @app_commands.describe(egg_name="Egg to incubate (from your inventory)")
    @app_commands.autocomplete(egg_name=incubate_autocomplete)
    async def incubate(self, interaction: discord.Interaction, egg_name: str):
        await interaction.response.defer()
        from utils import nursery
        from utils.nursery_view import open_nursery, request_key
        await get_or_create_player(interaction.user.id, str(interaction.user))
        egg_id = egg_name.lower().replace(' ', '_').replace('-', '_')
        if egg_id not in EGGS:
            egg_id = next((key for key, egg in EGGS.items() if egg_name.lower() in egg['name'].lower()), None)
        notice = 'Choose an incubation egg from the Nursery.'
        if egg_id:
            try:
                await nursery.incubate(interaction.user.id, egg_id, request_key=request_key(interaction, 'incubate'))
                notice = 'Your egg is settling in. Incubation finishes at its deadline; care is optional.'
            except ValueError as error:
                notice = str(error)
        return await open_nursery(interaction, bot=self.bot, already_deferred=True, egg_id=egg_id, page='incubations', notice=notice)

    @app_commands.command(name="eggs", description="Open your Nursery eggs and passive incubators")
    async def eggs(self, interaction: discord.Interaction):
        from utils.nursery_view import open_nursery
        return await open_nursery(interaction, bot=self.bot, page='incubations')

    @app_commands.command(name="tend", description="Offer optional care, or choose a ready hatchling")
    @app_commands.describe(egg_id="The incubation ID from /eggs (leave blank for oldest available egg)")
    async def tend(self, interaction: discord.Interaction, egg_id: int = None):
        return await self._tend(interaction, egg_id)

    async def _tend(self, interaction, egg_id=None):
        await interaction.response.defer()
        from utils import nursery
        from utils.nursery_view import open_nursery
        await get_or_create_player(interaction.user.id, str(interaction.user))
        state = await nursery.snapshot(interaction.user.id)
        rows = state['incubations']
        row = next((r for r in rows if r['id'] == egg_id), None) if egg_id is not None else next((r for r in rows if r.get('ready') or r.get('care_available')), None)
        notice, offer = '', None
        try:
            if not row:
                raise ValueError('No egg is ready for care yet. Eggs keep incubating while you are away.' if rows else 'No eggs incubating. Choose an origin egg to begin.')
            if row.get('ready'):
                offer = await nursery.prepare_offer(interaction.user.id, row['id'])
            else:
                await nursery.care(interaction.user.id, row['id'])
                notice = 'A little warmth and company. Optional care adds starting bond; the deadline stays the same.'
        except ValueError as error:
            notice = str(error)
        return await open_nursery(interaction, bot=self.bot, already_deferred=True, page='offer' if offer else 'incubations', notice=notice, offer=offer)

    @app_commands.command(name="sanctuary", description="View and upgrade your guild's Sanctuary 🏰")
    async def sanctuary(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with storage.connect() as db:
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
            sanctuary = dict(sanctuary) if sanctuary else {}

        upgrades = list(SANCTUARY_UPGRADES.items())
        total = len(upgrades)
        uid = interaction.user.id

        def build_page(page: int) -> discord.Embed:
            page = max(1, min(page, total))
            key, upgrade = upgrades[page - 1]
            col     = upgrade["db_column"]
            built   = bool(sanctuary.get(col, 0))
            req     = upgrade.get("requires")
            req_met = not req or bool(sanctuary.get(req, 0))

            if built:
                status     = "✅ Built"
                status_color = "success"
            elif not req_met:
                req_name   = SANCTUARY_UPGRADES[req]["name"]
                status     = f"🔒 Locked — requires {req_name}"
                status_color = "info"
            else:
                status     = f"🔨 Available — costs `{upgrade['cost_tokens']} 🎟️ tokens`"
                status_color = "legendary"

            built_count = sum(1 for k, u in SANCTUARY_UPGRADES.items() if sanctuary.get(u["db_column"], 0))

            embed = discord.Embed(
                title=f"{upgrade['name']}  ·  Tier {upgrade['tier']}",
                description=(
                    f"**{status}**\n\n"
                    f"{upgrade['description']}\n\n"
                    f"*{upgrade['lore']}*"
                ),
                color=COLORS.get(status_color, COLORS["legendary"])
            )
            if upgrade.get("image_url"):
                embed.set_image(url=upgrade["image_url"])
            embed.set_footer(
                text=f"🏰 {guild_row['name']} · {built_count}/{total} built · Page {page}/{total} · /build <upgrade> to construct"
            )
            return embed

        class SancView(discord.ui.View):
            def __init__(self_v, page=1):
                super().__init__(timeout=180)
                self_v.page = page
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self_v.page <= 1, row=0)
                pg   = discord.ui.Button(label=f"{self_v.page}/{total}", style=discord.ButtonStyle.secondary, disabled=True, row=0)
                nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self_v.page >= total, row=0)
                async def _prev(bi, _v=self_v):
                    if bi.user.id != uid:
                        return await bi.response.send_message("✦ This isn't your sanctuary view!", ephemeral=True)
                    _v.page -= 1; _v._rebuild()
                    await bi.response.edit_message(embed=build_page(_v.page), view=_v)
                async def _nxt(bi, _v=self_v):
                    if bi.user.id != uid:
                        return await bi.response.send_message("✦ This isn't your sanctuary view!", ephemeral=True)
                    _v.page += 1; _v._rebuild()
                    await bi.response.edit_message(embed=build_page(_v.page), view=_v)
                prev.callback = _prev; nxt.callback = _nxt
                self_v.add_item(prev); self_v.add_item(pg); self_v.add_item(nxt)

        await interaction.followup.send(embed=build_page(1), view=SancView(1))

    # ── /build ────────────────────────────────────────────────────────────
    @app_commands.command(name="build", description="Build a Sanctuary upgrade for your guild ⚒️")
    @app_commands.describe(upgrade="Which upgrade to build")
    @app_commands.choices(upgrade=[
        app_commands.Choice(name="🌸 Fairy Garden (T1 — 50 tokens)",           value="fairy_garden"),
        app_commands.Choice(name="⚒️ Gnome Forge (T2 — 150 tokens)",           value="gnome_forge"),
        app_commands.Choice(name="🏋️ Training Grounds (T2 — 150 tokens)",      value="training_grounds"),
        app_commands.Choice(name="🔭 Celestial Observatory (T3 — 300 tokens)",  value="celestial_observatory"),
        app_commands.Choice(name="📚 Arcane Library (T3 — 300 tokens)",         value="arcane_library"),
        app_commands.Choice(name="⚔️ Raid Altar (T4 — 500 tokens)",             value="raid_altar"),
        app_commands.Choice(name="🏪 Market Stall (T4 — 500 tokens)",           value="beast_market_stall"),
    ])
    async def build(self, interaction: discord.Interaction, upgrade: str):
        await interaction.response.defer()
        up = SANCTUARY_UPGRADES.get(upgrade)
        if not up:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Unknown upgrade.", color=COLORS["error"]
            ))

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
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

            async with db.execute("SELECT id FROM player_inventory WHERE user_id=? AND item_id='sanctuary_blueprint' AND quantity>0",(interaction.user.id,)) as c:blueprint=await c.fetchone()
            cost=0 if blueprint else up["cost_tokens"]
            if cost and guild_row["guild_tokens"] < cost:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Not enough guild tokens! Need **{up['cost_tokens']}**, have **{guild_row['guild_tokens']}**.",
                    color=COLORS["error"]
                ))

            if blueprint:
                await db.execute("UPDATE player_inventory SET quantity=quantity-1 WHERE id=?",(blueprint["id"],))
                await db.execute("DELETE FROM player_inventory WHERE id=? AND quantity<=0",(blueprint["id"],))
            elif cost:
                await db.execute("UPDATE guilds SET guild_tokens=guild_tokens-? WHERE id=?",(cost,gid))
            if sanctuary:
                await db.execute(f"UPDATE guild_sanctuary SET {col} = 1 WHERE guild_id = ?", (gid,))
            else:
                await db.execute(
                    f"INSERT INTO guild_sanctuary (guild_id, {col}) VALUES (?, 1)", (gid,)
                )
            await db.commit()

        await unlock_simple_achievement(interaction.user.id, "first_sanctuary")
        success_embed = discord.Embed(
            title=f"✅ {up['name']} Built!",
            description=(
                f"*{up['lore']}*\n\n"
                f"**Effect:** {up['description']}\n\n"
                f"All guild members now benefit from this upgrade."
            ),
            color=COLORS["success"]
        )
        if up.get("image_url"):
            success_embed.set_image(url=up["image_url"])
        await interaction.followup.send(embed=success_embed)

    # ── /craft ────────────────────────────────────────────────────────────
    async def craft_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show all craftable items — mark ready ones with ✅."""
        equipment, runes = load_equipment()
        # Also include items with recipes (evolution items etc)
        from utils.db import load_items as _li
        craftable_items = {k: v for k, v in _li().items() if v.get("recipe")}
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT material_id, quantity FROM player_materials WHERE user_id = ?",
                (interaction.user.id,)
            ) as c:
                mats = {r["material_id"]: r["quantity"] for r in await c.fetchall()}
        choices = []
        from utils.economy_balance import recipe_level
        trainer = await storage.get_player(interaction.user.id)
        sanctuary = await get_user_sanctuary(interaction.user.id)
        for item_id, item in {**equipment, **runes, **craftable_items}.items():
            if current.lower() not in item["name"].lower():
                continue
            recipe = apply_craft_discount(item.get("recipe", {}),sanctuary)
            level_ready = trainer and trainer['level'] >= recipe_level(item)
            can_craft = level_ready and all(mats.get(mid, 0) >= qty for mid, qty in recipe.items())
            prefix = "✅ " if can_craft else "🔒 " if not level_ready else ""
            choices.append(app_commands.Choice(name=f"{prefix}{item['name']} · Lv.{recipe_level(item)}"[:100], value=item_id))
        return choices[:25]

    @app_commands.command(name="craft", description="Open the workshop, or quickly craft a named item ⚒️")
    @app_commands.describe(item_name="Optional: craft this item and open its workshop panel")
    @app_commands.autocomplete(item_name=craft_autocomplete)
    async def craft(self, interaction: discord.Interaction, item_name: str | None = None):
        from utils.workshop import open_workshop
        await open_workshop(interaction, item_name=item_name)

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

        from utils.artwork import card_artwork_attachment
        from utils.beast_cards import CodexView, codex_embed
        view = CodexView(interaction.user.id, beast)
        with card_artwork_attachment(beast_id) as artwork:
            message = await interaction.followup.send(embed=codex_embed(beast), view=view, file=artwork, wait=True)
        view.message = interaction.channel.get_partial_message(message.id) if interaction.channel else message

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
                value="*Cosmic beings predate the elemental hierarchy. Shadow — the void before stars — cuts through cosmic presence. Pure Light still reaches them. Everything else simply watches.*",
                inline=False
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  /codex <beast name> to look up a specific beast")
        await interaction.followup.send(embed=embed)

    # ── /lore ─────────────────────────────────────────────────────────────
    @app_commands.command(name="lore", description="Read the story of ChibiBeasts 📜")
    async def lore(self, interaction: discord.Interaction):
        await interaction.response.defer()
        chapter = "creation"
        uid = interaction.user.id

        LORE_CHAPTERS = {
            "creation": {
                "title": "📜 The Creation Myth",
                "text": (
                    "Before there were beasts, before there were trainers, before there was even a "
                    "*world* to stand on — there was only **the Loom**.\n\n"
                    "The Loom was not a place. It was the act of weaving itself: an endless, formless "
                    "process spinning raw possibility into shape.\n\n"
                    "Then, in a single instant called the **First Stitch**, the Loom wove four threads "
                    "tighter than any others — and they woke up. These four became the **First Voices**: Prism, Loom, Aspect and Pillar — "
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
                    "The pressure can leave **Altered Divines**, changed but stable companions, or "
                    "overwhelm a creature into a **Corrupted** protection loop. These are different states.\n\n"
                    "In raids, trainers loosen the loop through battle, guarding and shared care. "
                    "**The creature survives.** Sometimes it chooses to accompany a trainer; every "
                    "successful rescue builds toward that friendship. Ancient raids are meetings with "
                    "the original powers, not executions."
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
                    "**Raiding** a Corrupted or Ancient beast is the highest-stakes form of the same instinct: "
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
                    "has never once let anyone down, and never plans to start.\n\n"
                    "Their bonds can grow into **Prismara, Timestitch, Nebuloom and Rootwarden**. "
                    f"Reach companion level {AWAKENING_LEVEL}, trainer level {AWAKENING_TRAINER_LEVEL}, "
                    f"bond {AWAKENING_BOND} and happiness {AWAKENING_HAPPINESS}; "
                    "open /journal → Companions to follow your progress."
                ),
                "color": COLORS["divine"]
            },
        }

        LORE_OPTIONS = [
            ("creation",    "📜", "The Creation Myth"),
            ("collections", "🌌", "The Five Collections"),
            ("sundering",   "⚔️", "The Sundering"),
            ("purpose",     "🧵", "Why You're Here"),
            ("starters",    "🔷", "The Four Starters"),
        ]

        def build_lore_embed(ch: str) -> discord.Embed:
            data = LORE_CHAPTERS.get(ch, LORE_CHAPTERS["creation"])
            emb  = discord.Embed(title=data["title"], description=data["text"], color=data["color"])
            emb.set_footer(text="ChibiBeasts 🐾")
            return emb

        class LoreView(discord.ui.View):
            def __init__(self_v, ch="creation"):
                super().__init__(timeout=180)
                self_v.chapter = ch
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                select = discord.ui.Select(
                    placeholder="📜 Read a chapter…",
                    options=[
                        discord.SelectOption(label=f"{emoji} {name}", value=key, default=key==self_v.chapter)
                        for key, emoji, name in LORE_OPTIONS
                    ],
                    row=0
                )
                async def _on_select(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your lore view!", ephemeral=True)
                    self_v.chapter = inter.data["values"][0]
                    self_v._rebuild()
                    await inter.response.edit_message(embed=build_lore_embed(self_v.chapter), view=self_v)
                select.callback = _on_select
                self_v.add_item(select)

        await interaction.followup.send(embed=build_lore_embed("creation"), view=LoreView("creation"))


async def setup(bot: commands.Bot):
    await bot.add_cog(World(bot))
