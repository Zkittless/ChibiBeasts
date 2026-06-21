# ── ChibiBeasts Progress Tracking ───────────────────────────────────────────
# Shared helpers for Achievements and Daily Quests. Other cogs call
# `track_event(...)` after a meaningful gameplay action (catch, hatch, win a
# battle, deal raid damage, etc.) and this module handles:
#   1. Updating relevant daily quest progress
#   2. Checking + unlocking any newly-earned achievements
#   3. Returning a list of "unlock" notifications the caller can DM/announce
#
# This keeps achievement/quest logic out of hatch.py / battle.py / guilds.py
# so those cogs only need one extra call each.

import aiosqlite
import discord
import random
from datetime import datetime, timezone

DB_PATH = "db/chibibeast.db"


# ── Achievement Definitions ─────────────────────────────────────────────────
# stat-based achievements are evaluated directly against the `players` row
# (or a quick COUNT query) rather than tracked incrementally, so they can
# never drift out of sync with real game state.

ACHIEVEMENTS = {
    # --- Collecting ---
    "first_steps":      {"name": "First Steps",        "emoji": "🐾", "desc": "Begin your ChibiBeasts journey",            "tier": "bronze", "reward": {"gold": 100}},
    "first_catch":       {"name": "New Friend",          "emoji": "🥚", "desc": "Catch or hatch your first beast",           "tier": "bronze", "reward": {"gold": 100}},
    "collector_10":       {"name": "Beast Collector",     "emoji": "📖", "desc": "Own 10 different beasts",                   "tier": "bronze", "reward": {"gold": 250}},
    "collector_25":       {"name": "Beast Hoarder",       "emoji": "📚", "desc": "Own 25 different beasts",                   "tier": "silver", "reward": {"gold": 600}},
    "collector_44":       {"name": "Living Bestiary",     "emoji": "🌟", "desc": "Own every beast species at least once",     "tier": "gold",   "reward": {"gold": 2000, "celestial_shards": 25}},
    "first_rare":         {"name": "Lucky Find",          "emoji": "🔵", "desc": "Catch or hatch a Rare beast",               "tier": "bronze", "reward": {"gold": 150}},
    "first_epic":         {"name": "Epic Encounter",      "emoji": "🟣", "desc": "Catch or hatch an Epic beast",              "tier": "silver", "reward": {"gold": 400}},
    "first_legendary":    {"name": "Legend Among Us",     "emoji": "🟡", "desc": "Catch or hatch a Legendary beast",          "tier": "silver", "reward": {"gold": 800}},
    "first_divine":       {"name": "Touched by Divinity", "emoji": "🌸", "desc": "Catch or hatch a Divine beast",             "tier": "gold",   "reward": {"gold": 1500, "celestial_shards": 10}},
    "divine_collector_5": {"name": "Pantheon Builder",    "emoji": "🏛️", "desc": "Own 5 different Divine beasts",            "tier": "gold",   "reward": {"gold": 2500, "celestial_shards": 20}},
    "divine_collector_16":{"name": "Ascended",            "emoji": "✨", "desc": "Own all 16 Divine beasts",                  "tier": "platinum","reward": {"gold": 10000, "celestial_shards": 100}},
    "first_altered_divine":{"name": "Beyond Reality",     "emoji": "⚠️", "desc": "Catch an Altered Divine from a raid",       "tier": "platinum","reward": {"gold": 5000}},

    # --- Questline ---
    "loom_witness":         {"name": "Witness to the Loom","emoji": "🧵", "desc": "Complete The Sundering of the Loom questline","tier": "platinum","reward": {"gold": 5000, "celestial_shards": 50}},

    # --- Battling ---
    "first_win":          {"name": "First Victory",       "emoji": "⚔️", "desc": "Win your first battle",                     "tier": "bronze", "reward": {"gold": 150}},
    "wins_10":             {"name": "Seasoned Battler",    "emoji": "🗡️", "desc": "Win 10 battles",                            "tier": "bronze", "reward": {"gold": 400}},
    "wins_50":             {"name": "Battle Veteran",      "emoji": "🛡️", "desc": "Win 50 battles",                            "tier": "silver", "reward": {"gold": 1200}},
    "wins_100":            {"name": "Arena Champion",      "emoji": "🏆", "desc": "Win 100 battles",                           "tier": "gold",   "reward": {"gold": 3000, "celestial_shards": 15}},

    # --- Exploration ---
    "first_explore":      {"name": "Wanderer",            "emoji": "🗺️", "desc": "Explore the world for the first time",      "tier": "bronze", "reward": {"gold": 75}},

    # --- Progression ---
    "level_10":            {"name": "Rising Trainer",      "emoji": "⬆️", "desc": "Reach trainer level 10",                    "tier": "bronze", "reward": {"gold": 300}},
    "level_25":            {"name": "Veteran Trainer",     "emoji": "⬆️", "desc": "Reach trainer level 25",                    "tier": "silver", "reward": {"gold": 800}},
    "level_50":            {"name": "Master Trainer",      "emoji": "⬆️", "desc": "Reach trainer level 50",                    "tier": "gold",   "reward": {"gold": 2000, "celestial_shards": 15}},

    # --- Economy ---
    "gold_5000":           {"name": "Pocket Change",       "emoji": "💰", "desc": "Accumulate 5,000 gold",                     "tier": "bronze", "reward": {"gold": 200}},
    "gold_50000":          {"name": "Small Fortune",       "emoji": "💎", "desc": "Accumulate 50,000 gold",                    "tier": "silver", "reward": {"gold": 1000}},

    # --- Social ---
    "first_trade":         {"name": "Dealmaker",           "emoji": "🤝", "desc": "Complete your first trade",                 "tier": "bronze", "reward": {"gold": 150}},
    "first_guild":         {"name": "Joiner",              "emoji": "🏰", "desc": "Join or create a guild",                    "tier": "bronze", "reward": {"gold": 150}},
    "first_raid_win":      {"name": "Raid Slayer",         "emoji": "💀", "desc": "Help defeat a raid boss",                   "tier": "silver", "reward": {"gold": 600}},
    "first_perk":          {"name": "Gifted",              "emoji": "🎯", "desc": "Obtain your first perk",                    "tier": "bronze", "reward": {"gold": 150}},
}

TIER_COLOR = {
    "bronze":   0xCD7F32,
    "silver":   0xC0C0C0,
    "gold":     0xFFD700,
    "platinum": 0xE5E4E2,
}


async def init_progress_tables():
    """No-op: tables already exist via utils.db.init_db(). Kept for clarity/import symmetry."""
    pass


async def _has_achievement(db, user_id: int, achievement_id: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM achievements WHERE user_id = ? AND achievement_id = ?",
        (user_id, achievement_id)
    ) as c:
        return (await c.fetchone()) is not None


async def grant_achievement(db, user_id: int, achievement_id: str) -> bool:
    """Insert the achievement row and apply its gold/shard reward. Returns True if newly granted."""
    if await _has_achievement(db, user_id, achievement_id):
        return False
    ach = ACHIEVEMENTS.get(achievement_id)
    if not ach:
        return False
    await db.execute(
        "INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?, ?)",
        (user_id, achievement_id)
    )
    reward = ach.get("reward", {})
    if reward:
        sets = []
        values = []
        if "gold" in reward:
            sets.append("gold = gold + ?")
            values.append(reward["gold"])
        if "celestial_shards" in reward:
            sets.append("celestial_shards = celestial_shards + ?")
            values.append(reward["celestial_shards"])
        if sets:
            values.append(user_id)
            await db.execute(f"UPDATE players SET {', '.join(sets)} WHERE user_id = ?", values)
    return True


def build_achievement_embed(unlocked_ids: list[str]) -> discord.Embed | None:
    if not unlocked_ids:
        return None
    embed = discord.Embed(
        title="🏆 Achievement Unlocked!" if len(unlocked_ids) == 1 else f"🏆 {len(unlocked_ids)} Achievements Unlocked!",
        color=TIER_COLOR.get(ACHIEVEMENTS[unlocked_ids[0]]["tier"], 0xFFD700)
    )
    for aid in unlocked_ids:
        ach = ACHIEVEMENTS[aid]
        reward = ach.get("reward", {})
        reward_str = " | ".join(
            f"+{v:,} {'💰 gold' if k == 'gold' else '🔮 shards'}" for k, v in reward.items()
        )
        embed.add_field(
            name=f"{ach['emoji']} {ach['name']} ({ach['tier'].capitalize()})",
            value=f"*{ach['desc']}*\n{reward_str}",
            inline=False
        )
    return embed


async def check_achievements(user_id: int) -> list[str]:
    """
    Evaluate all stat-based achievement criteria for a player and grant any
    newly-earned ones. Returns the list of achievement_ids newly unlocked
    this call (empty list if none).

    All reads are batched inside a single open connection to avoid N+1
    round-trips during high-concurrency event routing.
    """
    from utils.db import load_beasts

    newly_unlocked = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # ── Single connection, all reads batched ──────────────────────────
        async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as c:
            player = await c.fetchone()
        if not player:
            return []
        player = dict(player)

        # Batch: owned beasts (id + rarity only — no full row fetch needed)
        async with db.execute(
            "SELECT beast_id, rarity FROM player_beasts WHERE user_id = ?", (user_id,)
        ) as c:
            owned = [dict(r) for r in await c.fetchall()]

        # Batch: altered divine flag
        async with db.execute(
            "SELECT 1 FROM player_beasts WHERE user_id = ? AND is_altered_divine = 1 LIMIT 1", (user_id,)
        ) as c:
            has_altered = (await c.fetchone()) is not None

        # Batch: perk ownership
        async with db.execute(
            "SELECT 1 FROM player_perks WHERE user_id = ? LIMIT 1", (user_id,)
        ) as c:
            has_perk = (await c.fetchone()) is not None

        # Batch: guild membership
        async with db.execute(
            "SELECT 1 FROM guild_members WHERE user_id = ? LIMIT 1", (user_id,)
        ) as c:
            in_guild = (await c.fetchone()) is not None

        # ── Derive all conditions from the fetched data (no further queries) ──
        owned_species = {b["beast_id"] for b in owned}
        owned_divines = {b["beast_id"] for b in owned if b["rarity"] in ("divine", "altered_divine")}
        rarity_counts = {}
        for b in owned:
            rarity_counts[b["rarity"]] = rarity_counts.get(b["rarity"], 0) + 1

        all_beasts    = load_beasts()
        all_divine_ids = {b["id"] for b in all_beasts.values() if b["rarity"] == "divine"}

        candidates = []
        if owned:                                             candidates.append("first_catch")
        if len(owned_species) >= 10:                         candidates.append("collector_10")
        if len(owned_species) >= 25:                         candidates.append("collector_25")
        if all_beasts and owned_species >= set(all_beasts.keys()): candidates.append("collector_44")
        if rarity_counts.get("rare",      0) >= 1:           candidates.append("first_rare")
        if rarity_counts.get("epic",      0) >= 1:           candidates.append("first_epic")
        if rarity_counts.get("legendary", 0) >= 1:           candidates.append("first_legendary")
        if rarity_counts.get("divine",    0) >= 1:           candidates.append("first_divine")
        if len(owned_divines) >= 5:                          candidates.append("divine_collector_5")
        if all_divine_ids and owned_divines >= all_divine_ids: candidates.append("divine_collector_16")
        if has_altered:                                       candidates.append("first_altered_divine")
        if player["wins"] >= 1:                              candidates.append("first_win")
        if player["wins"] >= 10:                             candidates.append("wins_10")
        if player["wins"] >= 50:                             candidates.append("wins_50")
        if player["wins"] >= 100:                            candidates.append("wins_100")
        if player["level"] >= 10:                            candidates.append("level_10")
        if player["level"] >= 25:                            candidates.append("level_25")
        if player["level"] >= 50:                            candidates.append("level_50")
        if player["gold"] >= 5000:                           candidates.append("gold_5000")
        if player["gold"] >= 50000:                          candidates.append("gold_50000")
        if has_perk:                                         candidates.append("first_perk")
        if in_guild:                                         candidates.append("first_guild")

        # Grant — still inside the same open connection
        for aid in candidates:
            granted = await grant_achievement(db, user_id, aid)
            if granted:
                newly_unlocked.append(aid)

        await db.commit()

    return newly_unlocked


async def unlock_simple_achievement(user_id: int, achievement_id: str) -> bool:
    """For event-based achievements that aren't derivable from a stat snapshot
    (first_steps, first_explore, first_trade, first_raid_win). Call directly
    from the triggering cog. Returns True if newly granted."""
    async with aiosqlite.connect(DB_PATH) as db:
        granted = await grant_achievement(db, user_id, achievement_id)
        await db.commit()
    return granted


async def notify_unlocks(channel, member: discord.Member, unlocked_ids: list[str]):
    """Send an embed to the channel announcing newly unlocked achievements, if any."""
    if not unlocked_ids:
        return
    embed = build_achievement_embed(unlocked_ids)
    if embed:
        embed.set_author(name=f"{member.display_name}", icon_url=member.display_avatar.url)
        embed.set_footer(text="ChibiBeasts 🐾  •  /achievements to see your full collection")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


# ── Daily Quests ─────────────────────────────────────────────────────────────
# Each day, a player gets a fixed daily quest set rolled deterministically
# from a pool, sized by category. Progress is tracked per (user, quest_id, date).

QUEST_POOL = [
    {"id": "win_battles",     "name": "Battle Hardened",   "emoji": "⚔️", "desc": "Win {target} battle(s)",                "target": 2,  "reward_gold": 200, "reward_exp": 50,  "event": "battle_win"},
    {"id": "explore_times",   "name": "Wanderlust",        "emoji": "🗺️", "desc": "Explore {target} time(s)",              "target": 3,  "reward_gold": 150, "reward_exp": 40,  "event": "explore"},
    {"id": "hatch_eggs",      "name": "Crack the Shell",   "emoji": "🥚", "desc": "Hatch {target} egg(s)",                 "target": 1,  "reward_gold": 150, "reward_exp": 40,  "event": "hatch"},
    {"id": "catch_beasts",    "name": "Bounty Hunter",     "emoji": "🐾", "desc": "Catch {target} wild beast(s)",          "target": 2,  "reward_gold": 200, "reward_exp": 50,  "event": "catch"},
    {"id": "deal_raid_dmg",   "name": "Boss Buster",       "emoji": "💀", "desc": "Deal {target:,} raid damage",          "target": 1000,"reward_gold": 250, "reward_exp": 60,  "event": "raid_damage"},
    {"id": "spend_gold",      "name": "Big Spender",       "emoji": "🛍️", "desc": "Spend {target:,} gold in the shop",    "target": 500, "reward_gold": 150, "reward_exp": 30,  "event": "spend_gold"},
    {"id": "trade_once",      "name": "Trader's Instinct", "emoji": "🤝", "desc": "Complete {target} trade(s)",            "target": 1,  "reward_gold": 200, "reward_exp": 40,  "event": "trade"},
]

QUESTS_PER_DAY = 3


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_quest_set(user_id: int, date_str: str) -> list[dict]:
    """Deterministic per-user-per-day quest selection so re-rolling /dailies
    doesn't change the set, but each day (and each user) differs."""
    rng = random.Random(f"{user_id}:{date_str}")
    return rng.sample(QUEST_POOL, k=min(QUESTS_PER_DAY, len(QUEST_POOL)))


async def get_daily_quests(user_id: int) -> list[dict]:
    """Return today's quest set for a user with current progress, creating
    rows on first access."""
    date_str = _today_str()
    quests = _daily_quest_set(user_id, date_str)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        result = []
        for q in quests:
            async with db.execute(
                "SELECT * FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ?",
                (user_id, q["id"], date_str)
            ) as c:
                row = await c.fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?, ?, 0, 0, ?)",
                    (user_id, q["id"], date_str)
                )
                progress, completed = 0, 0
            else:
                progress, completed = row["progress"], row["completed"]
            result.append({**q, "progress": progress, "completed": bool(completed)})
        await db.commit()
        return result


async def track_quest_event(user_id: int, event: str, amount: int = 1) -> list[dict]:
    """
    Call this after a gameplay event relevant to quests (e.g. event="battle_win").
    Advances progress on any of today's quests matching that event type, marks
    them completed + pays out reward the moment they cross target.
    Returns a list of quest dicts that were JUST completed by this call.
    """
    date_str = _today_str()
    quests = _daily_quest_set(user_id, date_str)
    matching = [q for q in quests if q["event"] == event]
    if not matching:
        return []

    just_completed = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for q in matching:
            async with db.execute(
                "SELECT * FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ?",
                (user_id, q["id"], date_str)
            ) as c:
                row = await c.fetchone()

            if row and row["completed"]:
                continue

            current_progress = row["progress"] if row else 0
            new_progress = min(q["target"], current_progress + amount)
            is_complete = new_progress >= q["target"]

            if row:
                await db.execute(
                    "UPDATE daily_quests SET progress = ?, completed = ? WHERE user_id = ? AND quest_id = ? AND date = ?",
                    (new_progress, int(is_complete), user_id, q["id"], date_str)
                )
            else:
                await db.execute(
                    "INSERT INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?, ?, ?, ?, ?)",
                    (user_id, q["id"], new_progress, int(is_complete), date_str)
                )

            if is_complete and not (row and row["completed"]):
                await db.execute(
                    "UPDATE players SET gold = gold + ? WHERE user_id = ?",
                    (q["reward_gold"], user_id)
                )
                just_completed.append(q)

        await db.commit()

    # ── Apply EXP with level-up handling for all just-completed quests ────
    # Done outside the DB block so we can use award_player_exp cleanly.
    if just_completed:
        total_exp = sum(q["reward_exp"] for q in just_completed if q.get("reward_exp"))
        if total_exp > 0:
            try:
                from cogs.battle import award_player_exp
                await award_player_exp(user_id, total_exp)
            except Exception:
                # Fallback: raw write if import fails (e.g. circular import at startup)
                import aiosqlite as _aio
                async with _aio.connect(DB_PATH) as _db:
                    await _db.execute(
                        "UPDATE players SET exp = exp + ? WHERE user_id = ?",
                        (total_exp, user_id)
                    )
                    await _db.commit()

    # ── All-quests-complete shard bonus ────────────────────────────────────
    # If the player just completed the final quest of their daily set,
    # grant +1 Celestial Shard. Targets the 3-4 day casual sweet spot:
    # with 2 base shards/day + 1 bonus on active days, a player who does
    # their dailies consistently earns ~3 shards/day, reaching the core
    # weekly shop items (Reroll 15 + Loom Fragment 10×2 = 35) in 4-5 days.
    if just_completed:
        date_str = _today_str()
        quests = _daily_quest_set(user_id, date_str)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            completed_count = 0
            for q in quests:
                async with db.execute(
                    "SELECT completed FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ?",
                    (user_id, q["id"], date_str)
                ) as c:
                    row = await c.fetchone()
                if row and row["completed"]:
                    completed_count += 1
            if completed_count >= QUESTS_PER_DAY:
                # Only grant the bonus once — check if we've already given it today
                async with db.execute(
                    "SELECT 1 FROM daily_quests WHERE user_id = ? AND quest_id = 'all_quests_bonus' AND date = ?",
                    (user_id, date_str)
                ) as c:
                    already_granted = await c.fetchone()
                if not already_granted:
                    await db.execute(
                        "UPDATE players SET celestial_shards = celestial_shards + 2 WHERE user_id = ?",
                        (user_id,)
                    )
                    # Award guild tokens if the player is in a guild — dailies are
                    # the primary earn mechanic for guild tokens so raiding stays
                    # accessible to active members without grinding.
                    # 5 tokens/day keeps the raid cost (50/150) at a 10–30 day
                    # cadence for a solo member, faster for coordinated guilds.
                    async with db.execute(
                        "SELECT guild_id FROM guild_members WHERE user_id = ?", (user_id,)
                    ) as gc:
                        gm = await gc.fetchone()
                    if gm and gm["guild_id"]:
                        await db.execute(
                            "UPDATE guilds SET guild_tokens = guild_tokens + 5 WHERE id = ?",
                            (gm["guild_id"],)
                        )
                    await db.execute(
                        "INSERT INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?, 'all_quests_bonus', 1, 1, ?)",
                        (user_id, date_str)
                    )
                    await db.commit()
                    # Tag the bonus shard in the completion list so callers can surface it
                    just_completed.append({
                        "id": "all_quests_bonus",
                        "name": "Daily Champion",
                        "emoji": "🔮",
                        "desc": "Complete all 3 daily quests",
                        "target": 3,
                        "reward_gold": 0,
                        "reward_exp": 0,
                        "reward_shards": 2,
                        "event": None
                    })

    return just_completed


def build_quest_completion_embed(completed_quests: list[dict]) -> discord.Embed | None:
    if not completed_quests:
        return None
    # Filter out the internal bonus marker for the title count
    real_quests = [q for q in completed_quests if q["id"] != "all_quests_bonus"]
    bonus = next((q for q in completed_quests if q["id"] == "all_quests_bonus"), None)

    title = (
        "✅ Daily Quest Complete!" if len(real_quests) == 1
        else f"✅ {len(real_quests)} Daily Quests Complete!"
    )
    embed = discord.Embed(title=title, color=0x57F287)
    for q in real_quests:
        embed.add_field(
            name=f"{q['emoji']} {q['name']}",
            value=f"*{q['desc'].format(target=q['target'])}*\n+{q['reward_gold']:,} 💰 | +{q['reward_exp']} EXP",
            inline=False
        )
    if bonus:
        embed.add_field(
            name="🔮 Daily Champion Bonus!",
            value="*All 3 daily quests completed!*\n+2 💎 Celestial Shards | +5 🎟️ Guild Tokens",
            inline=False
        )
    return embed


async def notify_quest_completions(channel, completed_quests: list[dict]):
    if not completed_quests:
        return
    embed = build_quest_completion_embed(completed_quests)
    if embed:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


# ── Bestiary (server-wide first-discovery tracking) ────────────────────────

async def record_bestiary_sighting(guild_id: int, beast_id: str, user_id: int) -> bool:
    """
    Record that `beast_id` has been caught/hatched in this server. Returns
    True if this is the FIRST time anyone in the server has caught it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM bestiary WHERE guild_id = ? AND beast_id = ?", (guild_id, beast_id)
        ) as c:
            exists = await c.fetchone()
        if exists:
            return False
        await db.execute(
            "INSERT OR IGNORE INTO bestiary (guild_id, beast_id, first_caught_by) VALUES (?, ?, ?)",
            (guild_id, beast_id, user_id)
        )
        await db.commit()

    # Notify questline tracker with new bestiary count.
    # Delayed import avoids circular dependency (progress ↔ questline).
    # Errors are logged rather than silently swallowed so genuine bugs surface.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM bestiary WHERE guild_id = ?", (guild_id,)
        ) as c:
            count = (await c.fetchone())[0]

    try:
        from cogs.questline import advance_quest_step
        await advance_quest_step(user_id, "bestiary_update", count=count)
    except ImportError:
        pass  # questline cog not loaded — safe to skip silently
    except Exception as exc:
        import logging
        logging.getLogger("chibibeasts.progress").warning(
            "advance_quest_step failed after bestiary update for user %s: %s",
            user_id, exc
        )

    return True


async def get_bestiary_progress(guild_id: int) -> dict:
    """Returns {beast_id: {"first_caught_by": user_id, "first_caught_at": ts}} for this server."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT beast_id, first_caught_by, first_caught_at FROM bestiary WHERE guild_id = ?",
            (guild_id,)
        ) as c:
            rows = await c.fetchall()
        return {r["beast_id"]: dict(r) for r in rows}
