import aiosqlite
import json
import os

DB_PATH = "db/chibibeast.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 500,
            celestial_shards INTEGER DEFAULT 10,
            guild_tokens INTEGER DEFAULT 0,
            guild_id INTEGER DEFAULT NULL,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            happiness_avg REAL DEFAULT 100.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS player_beasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            beast_id TEXT,
            nickname TEXT DEFAULT NULL,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            hp INTEGER,
            max_hp INTEGER,
            attack INTEGER,
            defense INTEGER,
            speed INTEGER,
            mana INTEGER,
            max_mana INTEGER,
            happiness INTEGER DEFAULT 100,
            is_active INTEGER DEFAULT 0,
            is_favorite INTEGER DEFAULT 0,
            rarity TEXT,
            is_altered_divine INTEGER DEFAULT 0,
            altered_name TEXT DEFAULT NULL,
            divine_trait TEXT DEFAULT NULL,
            stat_points INTEGER DEFAULT 0,
            disposition TEXT DEFAULT NULL,
            caught_from TEXT DEFAULT 'hatch',
            caught_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS player_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_id TEXT,
            quantity INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS player_perks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            perk_id TEXT,
            equipped INTEGER DEFAULT 0,
            obtained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS guilds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT DEFAULT '',
            leader_id INTEGER,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            guild_tokens INTEGER DEFAULT 0,
            member_count INTEGER DEFAULT 1,
            max_members INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS guild_members (
            guild_id INTEGER,
            user_id INTEGER,
            rank TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS raids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            boss_id TEXT,
            boss_name TEXT,
            boss_type TEXT,
            max_hp INTEGER,
            current_hp INTEGER,
            guild_id INTEGER,
            channel_id INTEGER,
            status TEXT DEFAULT 'active',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS raid_participants (
            raid_id INTEGER,
            user_id INTEGER,
            damage_dealt INTEGER DEFAULT 0,
            PRIMARY KEY (raid_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER,
            receiver_id INTEGER,
            sender_beast_id INTEGER,
            receiver_beast_id INTEGER DEFAULT NULL,
            gold_offered INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS achievements (
            user_id INTEGER,
            achievement_id TEXT,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, achievement_id)
        );

        CREATE TABLE IF NOT EXISTS daily_quests (
            user_id INTEGER,
            quest_id TEXT,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            date TEXT,
            PRIMARY KEY (user_id, quest_id, date)
        );

        CREATE TABLE IF NOT EXISTS battles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger_id INTEGER,
            opponent_id INTEGER,
            winner_id INTEGER DEFAULT NULL,
            challenger_beast INTEGER,
            opponent_beast INTEGER,
            status TEXT DEFAULT 'pending',
            turn INTEGER DEFAULT 1,
            battle_log TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bestiary (
            guild_id INTEGER DEFAULT 0,
            beast_id TEXT,
            first_caught_by INTEGER,
            first_caught_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, beast_id)
        );

        CREATE TABLE IF NOT EXISTS altered_divines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beast_id TEXT,
            altered_name TEXT,
            caught_by INTEGER,
            server_id INTEGER,
            raid_id INTEGER,
            caught_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS incubating_eggs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            egg_type TEXT NOT NULL,
            egg_name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ready_at TIMESTAMP NOT NULL,
            hatched INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS guild_sanctuary (
            guild_id INTEGER PRIMARY KEY,
            fairy_garden INTEGER DEFAULT 0,
            gnome_forge INTEGER DEFAULT 0,
            celestial_observatory INTEGER DEFAULT 0,
            upgraded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guilds(id)
        );

        CREATE TABLE IF NOT EXISTS player_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            material_id TEXT,
            quantity INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );

        CREATE TABLE IF NOT EXISTS player_equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            beast_row_id INTEGER,
            equipment_id TEXT,
            equipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES players(user_id),
            FOREIGN KEY (beast_row_id) REFERENCES player_beasts(id)
        );

        CREATE TABLE IF NOT EXISTS player_questline (
            user_id INTEGER PRIMARY KEY,
            current_chapter TEXT DEFAULT NULL,
            completed_chapters TEXT DEFAULT '[]',
            step_progress TEXT DEFAULT '{}',
            collected_relics TEXT DEFAULT '[]',
            npc_relationships TEXT DEFAULT '{}',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES players(user_id)
        );
        """)
        await db.commit()

    await _run_migrations()
    print("✅ Database initialized")

async def _run_migrations():
    """Safe schema migrations — ALTER TABLE IF NOT EXISTS equivalent via try/except.
    Add new columns here whenever the schema grows; existing DBs upgrade cleanly."""
    migrations = [
        "ALTER TABLE player_beasts ADD COLUMN disposition TEXT DEFAULT NULL",
        "ALTER TABLE guilds ADD COLUMN sanctuary_tier INTEGER DEFAULT 0",
        # New player tracking columns
        "ALTER TABLE players ADD COLUMN title TEXT DEFAULT NULL",
        "ALTER TABLE players ADD COLUMN explore_last_at REAL DEFAULT 0",
        "ALTER TABLE players ADD COLUMN total_catches INTEGER DEFAULT 0",
        "ALTER TABLE players ADD COLUMN total_gold_earned INTEGER DEFAULT 0",
        "ALTER TABLE players ADD COLUMN incense_active_until REAL DEFAULT 0",
        "ALTER TABLE players ADD COLUMN brew_active INTEGER DEFAULT 0",
        "ALTER TABLE players ADD COLUMN damage_multiplier REAL DEFAULT 1.0",
        "ALTER TABLE players ADD COLUMN shard_shop_week TEXT DEFAULT NULL",
        # Equipment rune slot on player_beasts
        "ALTER TABLE player_beasts ADD COLUMN rune_id TEXT DEFAULT NULL",
        # Battle type tag so /stats counts all battles correctly (pvp/pve/sparr)
        "ALTER TABLE battles ADD COLUMN battle_type TEXT DEFAULT 'pvp'",
    ]
    async with aiosqlite.connect(DB_PATH) as db:
        for sql in migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column already exists — safe to skip
        await db.commit()

async def get_or_create_player(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cursor:
            player = await cursor.fetchone()
        if not player:
            await db.execute(
                "INSERT INTO players (user_id, username) VALUES (?, ?)",
                (user_id, username)
            )
            await db.commit()
            async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cursor:
                player = await cursor.fetchone()
        return dict(player)

async def get_player(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_player(user_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE players SET {sets} WHERE user_id = ?", values)
        await db.commit()

async def get_player_beasts(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM player_beasts WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_active_beast(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def add_beast_to_player(user_id: int, beast_data: dict):
    from utils.dispositions import roll_disposition, apply_disposition

    disposition = beast_data.get("disposition")  # allow override (e.g. starters pass None)
    if disposition is None:
        disposition = roll_disposition(beast_data["id"])

    # Apply disposition modifier to stats before storing
    final_stats = apply_disposition(beast_data["base_stats"], disposition)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO player_beasts
            (user_id, beast_id, level, hp, max_hp, attack, defense, speed, mana, max_mana,
             rarity, disposition, caught_from)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, beast_data["id"], 1,
            final_stats["hp"], final_stats["hp"],
            final_stats["attack"], final_stats["defense"],
            final_stats["speed"],
            final_stats["mana"], final_stats["mana"],
            beast_data["rarity"],
            disposition,
            beast_data.get("caught_from", "hatch")
        ))
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            row = await cursor.fetchone()
            return row[0]

async def get_inventory(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_inventory WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def add_item(user_id: int, item_id: str, quantity: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE player_inventory SET quantity = quantity + ? WHERE id = ?",
                (quantity, existing[0])
            )
        else:
            await db.execute(
                "INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?, ?, ?)",
                (user_id, item_id, quantity)
            )
        await db.commit()

async def remove_item(user_id: int, item_id: str, quantity: int = 1) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cursor:
            existing = await cursor.fetchone()
        if not existing or existing[1] < quantity:
            return False
        if existing[1] == quantity:
            await db.execute("DELETE FROM player_inventory WHERE id = ?", (existing[0],))
        else:
            await db.execute(
                "UPDATE player_inventory SET quantity = quantity - ? WHERE id = ?",
                (quantity, existing[0])
            )
        await db.commit()
        return True

def load_beasts():
    with open("data/beasts.json") as f:
        return json.load(f)["beasts"]

def load_items():
    with open("data/items.json") as f:
        return json.load(f)["items"]

def load_perks():
    with open("data/perks.json") as f:
        return json.load(f)

def get_beast_data(beast_id: str):
    beasts = load_beasts()
    return beasts.get(beast_id)

def calc_exp_for_level(level: int) -> int:
    """
    EXP required to advance FROM `level` to `level+1`.

    Two separate curves prevent the single-carry trap:
    - Starters use this steeper curve (100 * level^1.5). They are long-term
      companions and should take real investment to grow.
    - Wild catches use calc_exp_for_level_wild() — a flatter curve that lets
      players bring a new capture up to speed in ~12 battles at low levels,
      so experimenting with type combinations never feels punishing.
    """
    return int(100 * (level ** 1.5))


def calc_exp_for_level_wild(level: int) -> int:
    """
    Flatter EXP curve for wild-caught beasts (caught_from != 'starter').
    Lv10 in ~12 battles, Lv15 in ~30 battles at average 32 EXP/win.
    Players can experiment freely with type matchups without a multi-week grind.
    """
    return int(15 * (level ** 1.1))

def calc_player_exp_for_level(level: int) -> int:
    # Base lowered from 200 → 100 so active players reach Lv10 in ~13 days
    # instead of ~27. The curve shape (^1.8) is unchanged — progression still
    # accelerates naturally so mid/late game feels meaningfully harder than
    # early game, just without the early wall that causes new player churn.
    return int(100 * (level ** 1.8))

def get_perk_slots(player_level: int) -> int:
    if player_level >= 50: return 5
    if player_level >= 25: return 4
    if player_level >= 10: return 3
    return 2

def calc_stat_growth(beast_row: dict, levels_gained: int) -> dict:
    """
    Calculate stat increases for a beast gaining `levels_gained` levels.
    Growth is based on the beast's rarity — rarer beasts grow faster.
    Returns a dict of {stat: amount_to_add} for each stat.
    Designed to be called once per level-up event, accumulated for multi-level jumps.
    """
    RARITY_GROWTH = {
        "common":        {"hp": 4, "attack": 1, "defense": 1, "speed": 1, "mana": 2},
        "uncommon":      {"hp": 5, "attack": 2, "defense": 1, "speed": 1, "mana": 2},
        "rare":          {"hp": 6, "attack": 2, "defense": 2, "speed": 2, "mana": 3},
        "epic":          {"hp": 8, "attack": 3, "defense": 2, "speed": 2, "mana": 4},
        "legendary":     {"hp": 10,"attack": 4, "defense": 3, "speed": 3, "mana": 5},
        "divine":        {"hp": 12,"attack": 5, "defense": 4, "speed": 4, "mana": 6},
        "altered_divine":{"hp": 15,"attack": 6, "defense": 5, "speed": 5, "mana": 7},
    }
    rarity = beast_row.get("rarity", "common")
    growth = RARITY_GROWTH.get(rarity, RARITY_GROWTH["common"])
    return {stat: val * levels_gained for stat, val in growth.items()}

async def apply_beast_levelup(db, beast_row: dict, new_level: int, new_exp: int):
    """
    Apply level-up stat growth and update the beast row in the DB.
    `db` must be an open aiosqlite connection.

    Automatically uses the wild EXP curve for non-starter beasts so callers
    don't need to know which curve to use — the beast row's caught_from field
    determines it. Starters use the steeper curve; everything else uses the
    flatter wild curve that lets players experiment with type matchups freely.

    HP clamping note: SQLite evaluates SET expressions against the original row
    values, not the intermediate state of other assignments in the same UPDATE.
    Writing MIN(hp + ?, max_hp + ?) would clamp against the OLD max_hp before
    the new max_hp assignment takes effect — leaving the beast wounded on level-up.
    We compute the correct new_hp in Python first so the query always writes
    the right value regardless of SQLite's evaluation order.
    """
    if new_level <= beast_row["level"]:
        return
    levels_gained = new_level - beast_row["level"]
    growth = calc_stat_growth(beast_row, levels_gained)

    # Compute new max_hp and clamp current hp against it in Python,
    # not in SQL, to avoid the evaluation-order race condition.
    new_max_hp = beast_row["max_hp"] + growth["hp"]
    new_hp     = min(beast_row["hp"] + growth["hp"], new_max_hp)

    await db.execute("""
        UPDATE player_beasts SET
            level    = ?,
            exp      = ?,
            max_hp   = ?,
            hp       = ?,
            attack   = attack   + ?,
            defense  = defense  + ?,
            speed    = speed    + ?,
            mana     = mana     + ?,
            max_mana = max_mana + ?
        WHERE id = ?
    """, (
        new_level, new_exp,
        new_max_hp, new_hp,
        growth["attack"], growth["defense"], growth["speed"],
        growth["mana"], growth["mana"],
        beast_row["id"]
    ))


def get_beast_exp_for_level(beast_row: dict, level: int) -> int:
    """
    Return the correct EXP threshold for a beast's next level based on
    whether it was a starter or wild catch. Use this everywhere instead
    of calling calc_exp_for_level directly.
    """
    is_starter = beast_row.get("caught_from") == "starter"
    return calc_exp_for_level(level) if is_starter else calc_exp_for_level_wild(level)
