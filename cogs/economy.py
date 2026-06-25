"""
economy.py — Beast Market, Appraisal, and Training
Commands: /market, /list, /delist, /appraise, /train
"""
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from datetime import datetime, timezone, timedelta

from utils.progress import unlock_simple_achievement
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_beast_data, get_player_beasts, DB_PATH
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI

# ── Gold cost per training session by rarity ───────────────────────────────
TRAIN_COST = {
    "common":        50,
    "uncommon":      150,
    "rare":          400,
    "epic":          1000,
    "legendary":     3000,
    "divine":        8000,
    "altered_divine":15000,
    "corrupted":     15000,
    "ancient":       20000,
    "dev":           0,
}

# Stat gain per session
TRAIN_GAIN = {
    "common":        1,
    "uncommon":      1,
    "rare":          2,
    "epic":          2,
    "legendary":     3,
    "divine":        3,
    "altered_divine":4,
    "corrupted":     4,
    "ancient":       5,
    "dev":           5,
}

TRAIN_CAP = 20   # sessions per stat
MARKET_DURATION_DAYS = 7

# Base appraisal values by rarity (at level 1)
APPRAISE_BASE = {
    "common":        100,
    "uncommon":      400,
    "rare":          1500,
    "epic":          6000,
    "legendary":     20000,
    "divine":        50000,
    "altered_divine":120000,
    "corrupted":     100000,
    "ancient":       150000,
    "dev":           0,
}


def appraise_beast(beast_row: dict) -> int:
    """Calculate estimated gold value of a beast.

    Formula design targets (lv50 active player earns ~4,800g/day):
      Common  lv50: ~350g   (same-day purchase)
      Rare    lv50: ~5k     (1 day)
      Epic    lv50: ~23k    (5 days)
      Legendary lv50: ~69k  (14 days)
      Divine  lv25: ~110k   (23 days)
      Divine  lv50: ~172k   (36 days)
      Divine  lv50 fully maxed: ~317k (66 days)
    """
    rarity = beast_row.get("rarity", "common")
    base   = APPRAISE_BASE.get(rarity, 100)
    level  = beast_row.get("level", 1)

    # Level curve: 5% per level — meaningful but doesn't explode at divine
    level_mult = 1.0 + (level - 1) * 0.05

    # Training bonus: 0.8% per session (reduced from 1% to prevent inflation)
    train_sessions = (
        (beast_row.get("train_atk", 0) or 0) +
        (beast_row.get("train_def", 0) or 0) +
        (beast_row.get("train_spd", 0) or 0) +
        (beast_row.get("train_hp",  0) or 0)
    )
    train_mult = 1.0 + train_sessions * 0.008

    # Gear bonus: 8% armor, 4% rune (reduced from 10%/5%)
    gear_mult = 1.0
    if beast_row.get("equipment_id"):
        gear_mult += 0.08
    if beast_row.get("rune_id"):
        gear_mult += 0.04

    value = int(base * level_mult * train_mult * gear_mult)
    return max(base, value)


# ── /appraise ─────────────────────────────────────────────────────────────
class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="appraise", description="Get an estimated gold value for a beast 💰")
    @app_commands.describe(beast_id="Your beast number from /collection")
    async def appraise(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT pb.*, pe.equipment_id FROM player_beasts pb "
                "LEFT JOIN player_equipment pe ON pe.beast_row_id = pb.id AND pe.user_id = pb.user_id "
                "WHERE pb.user_id = ? AND pb.player_number = ?",
                (interaction.user.id, beast_id)
            ) as c:
                row = await c.fetchone()

        if not row:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ No beast found with number `#{beast_id}`.",
                color=COLORS["error"]
            ))

        row = dict(row)
        bd      = get_beast_data(row["beast_id"]) or {}
        name    = row.get("nickname") or bd.get("name", "?")
        rarity  = row.get("rarity", "common")
        r_emoji = RARITY_EMOJI.get(rarity, "⚪")
        value   = appraise_beast(row)

        # Value context
        if value < 2000:
            context = "Good starter value — commonly traded."
        elif value < 10000:
            context = "Solid mid-game value — worth trading for."
        elif value < 50000:
            context = "High-value beast — most trainers would want this."
        elif value < 200000:
            context = "Exceptional value — rare to see on the market."
        else:
            context = "Extraordinary. This beast is a serious asset."

        train_total = sum(row.get(f"train_{s}", 0) or 0 for s in ["atk","def","spd","hp"])

        embed = discord.Embed(
            title=f"💰 Appraisal: {r_emoji} {name} `#{beast_id}`",
            description=f"*Estimated market value based on rarity, level, training, and gear.*",
            color=COLORS.get(rarity, COLORS["info"])
        )
        embed.add_field(name="Estimated Value", value=f"**`{value:,}g`**", inline=True)
        embed.add_field(name="Rarity", value=f"{r_emoji} {RARITY_LABEL.get(rarity, rarity.title())}", inline=True)
        embed.add_field(name="Level", value=f"Lv.{row['level']}", inline=True)
        embed.add_field(name="Training Sessions", value=f"`{train_total}` total", inline=True)
        embed.add_field(name="Gear", value=("✅ Equipped" if row.get("equipment_id") or row.get("rune_id") else "None"), inline=True)
        embed.add_field(name="\u200b", value=f"*{context}*", inline=False)
        embed.set_footer(text="Use /list #beast <price> to put it on the market · /market to browse listings")
        await interaction.followup.send(embed=embed)

    # ── /train ─────────────────────────────────────────────────────────────
    @app_commands.command(name="train", description="Spend gold to permanently boost a beast's stat 🏋️")
    @app_commands.describe(
        beast_id="Your beast number from /collection",
        stat="Which stat to train (attack, defense, speed, hp)"
    )
    @app_commands.choices(stat=[
        app_commands.Choice(name="⚔️ Attack",  value="attack"),
        app_commands.Choice(name="🛡️ Defense", value="defense"),
        app_commands.Choice(name="💨 Speed",   value="speed"),
        app_commands.Choice(name="❤️ HP",      value="hp"),
    ])
    async def train(self, interaction: discord.Interaction, beast_id: int, stat: str):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND player_number = ?",
                (interaction.user.id, beast_id)
            ) as c:
                row = await c.fetchone()

            if not row:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ No beast found with number `#{beast_id}`.",
                    color=COLORS["error"]
                ))
            row = dict(row)

            rarity      = row.get("rarity", "common")
            col         = f"train_{stat[:3]}"  # train_atk, train_def, train_spd, train_hp
            if stat == "hp":
                col = "train_hp"
            elif stat == "attack":
                col = "train_atk"
            elif stat == "defense":
                col = "train_def"
            elif stat == "speed":
                col = "train_spd"

            sessions_done = row.get(col, 0) or 0
            cost          = TRAIN_COST.get(rarity, 50)
            gain          = TRAIN_GAIN.get(rarity, 1)

            RARITY_CAPS = {
                "common": 20, "uncommon": 20, "rare": 20,
                "epic": 20, "legendary": 15,
                "divine": 20, "altered_divine": 20,
                "corrupted": 20, "ancient": 20, "dev": 0
            }
            rarity_cap = RARITY_CAPS.get(rarity, 20)
            if sessions_done >= rarity_cap:
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        f"✦ **{row.get('nickname') or (get_beast_data(row['beast_id']) or {}).get('name','?')}** "
                        f"has already been trained to the maximum in **{stat.title()}** ({sessions_done}/{rarity_cap} sessions)."
                    ),
                    color=COLORS["error"]
                ))

            # Training Grounds sanctuary: -10% cost
            from utils.sanctuary import get_user_sanctuary as _gsanc, apply_training_discount as _atd
            _sanc = await _gsanc(interaction.user.id)
            cost = _atd(cost, _sanc)

            if player["gold"] < cost:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Need `{cost:,}g` to train. You have `{player['gold']:,}g`.",
                    color=COLORS["error"]
                ))

            # Apply training
            stat_col = stat if stat != "hp" else "max_hp"
            await db.execute(
                f"UPDATE player_beasts SET {stat_col} = {stat_col} + ?, {col} = {col} + 1 WHERE id = ?",
                (gain, row["id"])
            )
            if stat == "hp":
                await db.execute(
                    "UPDATE player_beasts SET hp = hp + ? WHERE id = ?",
                    (gain, row["id"])
                )
            await db.execute(
                "UPDATE players SET gold = gold - ? WHERE user_id = ?",
                (cost, interaction.user.id)
            )
            await db.commit()

        bd     = get_beast_data(row["beast_id"]) or {}
        name   = row.get("nickname") or bd.get("name", "?")
        r_emoji = RARITY_EMOJI.get(rarity, "⚪")
        sessions_left = TRAIN_CAP - sessions_done - 1
        stat_emoji = {"attack":"⚔️","defense":"🛡️","speed":"💨","hp":"❤️"}.get(stat,"📈")

        embed = discord.Embed(
            title=f"🏋️ Training Complete!",
            description=(
                f"{r_emoji} **{name}** `#{beast_id}`\n\n"
                f"{stat_emoji} **{stat.title()}** permanently increased by `+{gain}`!\n"
                f"Cost: `{cost:,}g` · Balance: `{player['gold']-cost:,}g`\n\n"
                f"Sessions remaining: `{sessions_left}/{TRAIN_CAP}`"
            ),
            color=COLORS.get(rarity, COLORS["success"])
        )
        if sessions_left == 0:
            embed.add_field(name="✨ Maxed Out", value=f"*This beast has been trained to the limit in {stat.title()}.*", inline=False)
        embed.set_footer(text=f"Use /appraise #{beast_id} to see how training affects market value")
        await unlock_simple_achievement(interaction.user.id, "first_train")
        # Check if all 4 stats are now maxed on this beast
        _rarity_caps = {
            "common": 20, "uncommon": 20, "rare": 20, "epic": 20,
            "legendary": 15, "divine": 20, "altered_divine": 20,
            "corrupted": 20, "ancient": 20, "dev": 0,
        }
        _cap = _rarity_caps.get(rarity, 20)
        _new_sessions = sessions_done + 1
        async with aiosqlite.connect(DB_PATH) as _tdb:
            _tdb.row_factory = aiosqlite.Row
            async with _tdb.execute(
                "SELECT train_atk, train_def, train_spd, train_hp FROM player_beasts WHERE id = ?",
                (row["id"],)
            ) as _tc:
                _tr = await _tc.fetchone()
        if _tr and all((_tr[c] or 0) >= _cap for c in ["train_atk","train_def","train_spd","train_hp"]):
            await unlock_simple_achievement(interaction.user.id, "max_train_beast")
        await interaction.followup.send(embed=embed)

    # ── /list ──────────────────────────────────────────────────────────────
    @app_commands.command(name="list", description="List a beast on the market for sale 📋")
    @app_commands.describe(
        beast_id="Your beast number to sell",
        price="Asking price in gold"
    )
    async def list_beast(self, interaction: discord.Interaction, beast_id: int, price: int):
        await interaction.response.defer()

        if price < 100:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Minimum listing price is `100g`.",
                color=COLORS["error"]
            ))
        if price > 10_000_000:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Maximum listing price is `10,000,000g`.",
                color=COLORS["error"]
            ))

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_beasts WHERE user_id = ? AND player_number = ?",
                (interaction.user.id, beast_id)
            ) as c:
                row = await c.fetchone()

            if not row:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ No beast found with number `#{beast_id}`.",
                    color=COLORS["error"]
                ))
            row = dict(row)

            # Check not active beast
            if row.get("is_active"):
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Can't list your active beast. Use `/setactive` to switch first.",
                    color=COLORS["error"]
                ))

            # Check not in raid party
            if row.get("raid_slot"):
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Can't list a beast in your raid party. Use `/raidparty` to remove it first.",
                    color=COLORS["error"]
                ))

            # Check already listed
            async with db.execute(
                "SELECT id FROM beast_market WHERE beast_row_id = ?", (row["id"],)
            ) as c:
                if await c.fetchone():
                    return await interaction.followup.send(embed=discord.Embed(
                        description="✦ This beast is already on the market. Use `/delist #{beast_id}` first.",
                        color=COLORS["error"]
                    ))

            expires = (datetime.now(timezone.utc) + timedelta(days=MARKET_DURATION_DAYS)).isoformat()
            await db.execute(
                "INSERT INTO beast_market (seller_id, beast_row_id, ask_price, expires_at) VALUES (?, ?, ?, ?)",
                (interaction.user.id, row["id"], price, expires)
            )
            await db.commit()

        bd      = get_beast_data(row["beast_id"]) or {}
        name    = row.get("nickname") or bd.get("name", "?")
        r_emoji = RARITY_EMOJI.get(row["rarity"], "⚪")
        appraised = appraise_beast(row)

        await unlock_simple_achievement(interaction.user.id, "first_market_sale")
        embed = discord.Embed(
            title="📋 Listed on the Market",
            description=(
                f"{r_emoji} **{name}** `#{beast_id}` listed for **`{price:,}g`**\n\n"
                f"*Estimated value: `{appraised:,}g`*\n"
                f"Listing expires in **{MARKET_DURATION_DAYS} days**.\n\n"
                f"Use `/delist #{beast_id}` to remove it early."
            ),
            color=COLORS["success"]
        )
        await interaction.followup.send(embed=embed)

    # ── /delist ────────────────────────────────────────────────────────────
    @app_commands.command(name="delist", description="Remove your beast from the market 🗑️")
    @app_commands.describe(beast_id="Your beast number to delist")
    async def delist_beast(self, interaction: discord.Interaction, beast_id: int):
        await interaction.response.defer()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT bm.id, pb.beast_id, pb.nickname, pb.rarity, pb.player_number "
                "FROM beast_market bm "
                "JOIN player_beasts pb ON pb.id = bm.beast_row_id "
                "WHERE bm.seller_id = ? AND pb.player_number = ?",
                (interaction.user.id, beast_id)
            ) as c:
                _row = await c.fetchone()
                listing = dict(_row) if _row else None

            if not listing:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ No active listing found for beast `#{beast_id}`.",
                    color=COLORS["error"]
                ))

            await db.execute("DELETE FROM beast_market WHERE id = ?", (listing["id"],))
            await db.commit()

        bd   = get_beast_data(listing["beast_id"]) or {}
        name = listing.get("nickname") or bd.get("name", "?")
        await interaction.followup.send(embed=discord.Embed(
            description=f"✅ **{name}** `#{beast_id}` has been removed from the market.",
            color=COLORS["success"]
        ))

    # ── /market ────────────────────────────────────────────────────────────
    @app_commands.command(name="market", description="Browse beasts listed for sale by other trainers 🏪")
    @app_commands.describe(filter_rarity="Filter by rarity (optional)")
    @app_commands.choices(filter_rarity=[
        app_commands.Choice(name="All",            value="all"),
        app_commands.Choice(name="📋 My Listings", value="mine"),
        app_commands.Choice(name="⚪ Common",       value="common"),
        app_commands.Choice(name="🟢 Uncommon",     value="uncommon"),
        app_commands.Choice(name="🔵 Rare",         value="rare"),
        app_commands.Choice(name="🟣 Epic",         value="epic"),
        app_commands.Choice(name="🟡 Legendary",    value="legendary"),
        app_commands.Choice(name="🌸 Divine",       value="divine"),
    ])
    async def market(self, interaction: discord.Interaction, filter_rarity: str = "all"):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        uid    = interaction.user.id
        now    = datetime.now(timezone.utc).isoformat()

        async def fetch_listings(flt):
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("DELETE FROM beast_market WHERE expires_at < ?", (now,))
                await db.commit()
                if flt == "mine":
                    sql = (
                        "SELECT bm.*, pb.beast_id, pb.nickname, pb.rarity, pb.level, pb.player_number, "
                        "pb.hp, pb.max_hp, pb.attack, pb.defense, pb.speed, pb.rune_id, "
                        "p.username AS seller_name FROM beast_market bm "
                        "JOIN player_beasts pb ON pb.id = bm.beast_row_id "
                        "JOIN players p ON p.user_id = bm.seller_id "
                        "WHERE bm.seller_id = ? ORDER BY bm.ask_price ASC LIMIT 50"
                    )
                    params = (uid,)
                elif flt == "all":
                    sql = (
                        "SELECT bm.*, pb.beast_id, pb.nickname, pb.rarity, pb.level, pb.player_number, "
                        "pb.hp, pb.max_hp, pb.attack, pb.defense, pb.speed, pb.rune_id, "
                        "p.username AS seller_name FROM beast_market bm "
                        "JOIN player_beasts pb ON pb.id = bm.beast_row_id "
                        "JOIN players p ON p.user_id = bm.seller_id "
                        "WHERE bm.seller_id != ? ORDER BY bm.ask_price ASC LIMIT 50"
                    )
                    params = (uid,)
                else:
                    sql = (
                        "SELECT bm.*, pb.beast_id, pb.nickname, pb.rarity, pb.level, pb.player_number, "
                        "pb.hp, pb.max_hp, pb.attack, pb.defense, pb.speed, pb.rune_id, "
                        "p.username AS seller_name FROM beast_market bm "
                        "JOIN player_beasts pb ON pb.id = bm.beast_row_id "
                        "JOIN players p ON p.user_id = bm.seller_id "
                        "WHERE bm.seller_id != ? AND pb.rarity = ? ORDER BY bm.ask_price ASC LIMIT 50"
                    )
                    params = (uid, flt)
                async with db.execute(sql, params) as c:
                    return [dict(r) for r in await c.fetchall()]

        listings    = await fetch_listings(filter_rarity)
        per_page    = 5
        is_mine     = filter_rarity == "mine"
        gold_str    = f"💰 Your gold: `{player['gold']:,}g`"

        def build_embed(page, lst, flt):
            mine_tab = flt == "mine"
            total_pg = max(1, (len(lst) + per_page - 1) // per_page)
            desc = ("Your active listings" if mine_tab else gold_str) + f" · {len(lst)} listing(s) · Page {page}/{total_pg}"
            emb = discord.Embed(
                title="📋 My Listings" if mine_tab else "🏪 Beast Market",
                description=desc,
                color=COLORS["info"] if mine_tab else COLORS["legendary"]
            )
            if not lst:
                emb.description = "*You have no active listings. Use `/list #beast <price>` to sell.*" if mine_tab else "*No beasts on the market right now.*"
                return emb
            for listing in lst[(page-1)*per_page : page*per_page]:
                bd       = get_beast_data(listing["beast_id"]) or {}
                name     = listing.get("nickname") or bd.get("name", "?")
                r_emoji  = RARITY_EMOJI.get(listing.get("rarity","common"), "⚪")
                price    = listing["ask_price"]
                has_rune = "💎" if listing.get("rune_id") else ""
                stats    = f"`{listing['attack']}ATK` `{listing['defense']}DEF` `{listing['speed']}SPD` `{listing['max_hp']}HP`"
                if mine_tab:
                    val = f"**`{price:,}g`** · Use `/delist {listing['player_number']}` to remove\n" + stats
                else:
                    ca  = "✅" if player["gold"] >= price else "❌"
                    val = f"**`{price:,}g`** {ca} · Seller: {listing.get('seller_name','?')}\n" + stats
                emb.add_field(name=f"{r_emoji} {name} · Lv.{listing['level']} {has_rune}", value=val, inline=False)
            emb.set_footer(text="📋 My Listings — /delist to remove" if mine_tab else "Click Buy · /list #beast <price> to sell")
            return emb

        if not listings and is_mine:
            return await interaction.followup.send(embed=build_embed(1, [], filter_rarity))
        if not listings:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No beasts on the market right now. Be the first — use `/list #beast <price>`!",
                color=COLORS["info"]
            ))

        class MarketView(discord.ui.View):
            def __init__(self_v, page=1, lst=None, flt="all"):
                super().__init__(timeout=120)
                self_v.page = page
                self_v.lst  = lst or []
                self_v.flt  = flt
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                total_pg = max(1, (len(self_v.lst) + per_page - 1) // per_page)

                # Rarity/filter select
                options = [
                    discord.SelectOption(label="All Rarities",   value="all",       default=self_v.flt=="all"),
                    discord.SelectOption(label="📋 My Listings", value="mine",      default=self_v.flt=="mine"),
                    discord.SelectOption(label="⚪ Common",       value="common",    default=self_v.flt=="common"),
                    discord.SelectOption(label="🟢 Uncommon",     value="uncommon",  default=self_v.flt=="uncommon"),
                    discord.SelectOption(label="🔵 Rare",         value="rare",      default=self_v.flt=="rare"),
                    discord.SelectOption(label="🟣 Epic",         value="epic",      default=self_v.flt=="epic"),
                    discord.SelectOption(label="🟡 Legendary",    value="legendary", default=self_v.flt=="legendary"),
                    discord.SelectOption(label="🌸 Divine",       value="divine",    default=self_v.flt=="divine"),
                ]
                select = discord.ui.Select(placeholder="🔍 Filter…", options=options, row=0)
                async def _filter(bi, _v=self_v):
                    if bi.user.id != uid:
                        return await bi.response.send_message("✦ This isn't your market view!", ephemeral=True)
                    await bi.response.defer()
                    new_flt  = bi.data["values"][0]
                    new_lst  = await fetch_listings(new_flt)
                    _v.lst   = new_lst
                    _v.flt   = new_flt
                    _v.page  = 1
                    _v._rebuild()
                    await bi.edit_original_response(embed=build_embed(1, new_lst, new_flt), view=_v)
                select.callback = _filter
                self_v.add_item(select)

                # Buy buttons (only for non-mine tabs)
                if self_v.flt != "mine":
                    page_lst = self_v.lst[(self_v.page-1)*per_page : self_v.page*per_page]
                    for i, listing in enumerate(page_lst):
                        bd   = get_beast_data(listing["beast_id"]) or {}
                        name = listing.get("nickname") or bd.get("name","?")
                        can  = player["gold"] >= listing["ask_price"]
                        btn  = discord.ui.Button(
                            label=f"Buy {name[:18]} ({listing['ask_price']:,}g)",
                            style=discord.ButtonStyle.success if can else discord.ButtonStyle.secondary,
                            disabled=not can, row=i+1
                        )
                        async def _buy(bi, lst=listing):
                            if bi.user.id != uid:
                                return await bi.response.send_message("✦ This isn't your market!", ephemeral=True)
                            await bi.response.defer(ephemeral=True)
                            async with aiosqlite.connect(DB_PATH) as _db:
                                _db.row_factory = aiosqlite.Row
                                async with _db.execute(
                                    "SELECT id FROM beast_market WHERE beast_row_id = ? AND seller_id = ?",
                                    (lst["id"], lst["seller_id"])
                                ) as _c:
                                    still_listed = await _c.fetchone()
                                if not still_listed:
                                    return await bi.followup.send("✦ This beast has already been sold.", ephemeral=True)
                                async with _db.execute("SELECT gold FROM players WHERE user_id = ?", (uid,)) as _c:
                                    _pr = await _c.fetchone()
                                if not _pr or _pr["gold"] < lst["ask_price"]:
                                    return await bi.followup.send(f"✦ Not enough gold. Need `{lst['ask_price']:,}g`.", ephemeral=True)
                                await _db.execute("UPDATE players SET gold = gold - ? WHERE user_id = ?", (lst["ask_price"], uid))
                                await _db.execute("UPDATE players SET gold = gold + ? WHERE user_id = ?", (lst["ask_price"], lst["seller_id"]))
                                await _db.execute("UPDATE player_beasts SET user_id = ?, is_active = 0, raid_slot = NULL WHERE id = ?", (uid, lst["beast_row_id"]))
                                await _db.execute("DELETE FROM beast_market WHERE beast_row_id = ?", (lst["beast_row_id"],))
                                async with _db.execute("SELECT COALESCE(MAX(player_number),0)+1 FROM player_beasts WHERE user_id = ?", (uid,)) as _c:
                                    new_num = (await _c.fetchone())[0]
                                await _db.execute("UPDATE player_beasts SET player_number = ? WHERE id = ?", (new_num, lst["beast_row_id"]))
                                await _db.commit()
                            bdd = get_beast_data(lst["beast_id"]) or {}
                            nm  = lst.get("nickname") or bdd.get("name","?")
                            re  = RARITY_EMOJI.get(lst.get("rarity","common"),"⚪")
                            await unlock_simple_achievement(uid, "first_market_buy")
                            spent = lst['ask_price']
                            new_bal = _pr['gold'] - spent
                            await bi.followup.send(embed=discord.Embed(
                                title=f"✅ Purchased: {re} {nm}",
                                description=(
                                    f"**`{spent:,}g`** spent · New balance: `{new_bal:,}g`\n\n"
                                    f"{nm} is now `#{new_num}` in your collection!\n"
                                    f"Use `/beastinfo {new_num}` to inspect it."
                                ),
                                color=COLORS["success"]
                            ), ephemeral=True)
                        btn.callback = _buy
                        self_v.add_item(btn)

                # Pagination
                total_pg = max(1, (len(self_v.lst) + per_page - 1) // per_page)
                if total_pg > 1:
                    prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self_v.page<=1, row=4)
                    pg   = discord.ui.Button(label=f"{self_v.page}/{total_pg}", style=discord.ButtonStyle.secondary, disabled=True, row=4)
                    nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self_v.page>=total_pg, row=4)
                    async def _prev(bi, _v=self_v):
                        _v.page -= 1; _v._rebuild()
                        await bi.response.edit_message(embed=build_embed(_v.page, _v.lst, _v.flt), view=_v)
                    async def _nxt(bi, _v=self_v):
                        _v.page += 1; _v._rebuild()
                        await bi.response.edit_message(embed=build_embed(_v.page, _v.lst, _v.flt), view=_v)
                    prev.callback = _prev; nxt.callback = _nxt
                    self_v.add_item(prev); self_v.add_item(pg); self_v.add_item(nxt)

        await interaction.followup.send(embed=build_embed(1, listings, filter_rarity), view=MarketView(1, listings, filter_rarity))

async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
