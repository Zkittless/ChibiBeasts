# ── ChibiBeasts Sanctuary Runtime Effects ───────────────────────────────────
# Any cog can call get_sanctuary(guild_id) to check which upgrades are built,
# then use the helper functions below to apply effects at runtime.
# This keeps sanctuary logic in one place rather than scattered across cogs.

import aiosqlite

DB_PATH = "db/chibibeast.db"


async def get_sanctuary(guild_id: int) -> dict:
    """Return the sanctuary row for a guild, or all-zero defaults."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_sanctuary WHERE guild_id = ?", (guild_id,)
        ) as c:
            row = await c.fetchone()
    if not row:
        return {"fairy_garden": 0, "gnome_forge": 0, "celestial_observatory": 0}
    return dict(row)


async def get_user_sanctuary(user_id: int) -> dict:
    """Return the sanctuary for the guild a user belongs to, or defaults."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT gs.* FROM guild_members gm "
            "JOIN guild_sanctuary gs ON gm.guild_id = gs.guild_id "
            "WHERE gm.user_id = ?", (user_id,)
        ) as c:
            row = await c.fetchone()
    if not row:
        return {"fairy_garden": 0, "gnome_forge": 0, "celestial_observatory": 0}
    return dict(row)


def apply_explore_encounter_bonus(rarity_weights: dict, sanctuary: dict) -> dict:
    """
    Celestial Observatory: +2% encounter rate for Epic and Legendary.
    Returns modified rarity_weights dict.
    """
    if not sanctuary.get("celestial_observatory"):
        return rarity_weights
    weights = dict(rarity_weights)
    bonus = 0.02
    if "epic" in weights:
        weights["epic"] = min(weights["epic"] + bonus, 0.60)
    if "legendary" in weights:
        weights["legendary"] = min(weights["legendary"] + bonus, 0.40)
    # Normalize so weights sum to ≤ 1 (reduce common/uncommon slightly)
    total = sum(weights.values())
    if total > 1.0:
        scale = 1.0 / total
        weights = {k: v * scale for k, v in weights.items()}
    return weights


def apply_craft_discount(recipe: dict, sanctuary: dict) -> dict:
    """
    Gnome Forge: 10% reduction in material quantities (min 1).
    Returns modified recipe dict.
    """
    if not sanctuary.get("gnome_forge"):
        return recipe
    return {mat: max(1, int(qty * 0.90)) for mat, qty in recipe.items()}


async def apply_happiness_passive(user_id: int):
    """
    Fairy Garden: +5% happiness gain (1 point) for benched beasts daily.
    Call this during daily quest reset or on-login if implementing passive ticks.
    For now, applies +1 happiness to all non-active beasts with happiness < 100.
    """
    sanctuary = await get_user_sanctuary(user_id)
    if not sanctuary.get("fairy_garden"):
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE player_beasts SET happiness = MIN(100, happiness + 1) "
            "WHERE user_id = ? AND is_active = 0 AND happiness < 100",
            (user_id,)
        )
        await db.commit()
    return 1  # amount added

def apply_training_discount(cost: int, sanctuary: dict) -> int:
    """Training Grounds: -10% training cost for guild members."""
    if not sanctuary.get("training_grounds"):
        return cost
    return max(1, int(cost * 0.90))


def apply_exp_bonus(exp: int, sanctuary: dict) -> int:
    """Arcane Library: +15% EXP from battles and explores."""
    if not sanctuary.get("arcane_library"):
        return exp
    return int(exp * 1.15)


def apply_raid_damage_bonus(damage: int, sanctuary: dict) -> int:
    """Raid Altar: +10% raid damage for all guild members."""
    if not sanctuary.get("raid_altar"):
        return damage
    return int(damage * 1.10)


def get_raid_armor_bonus(sanctuary: dict) -> int:
    """Raid Altar: +5% armor reduction vs raid bosses."""
    return 5 if sanctuary.get("raid_altar") else 0


def get_market_slot_bonus(sanctuary: dict) -> int:
    """Market Stall: +2 market listing slots."""
    return 2 if sanctuary.get("beast_market_stall") else 0
