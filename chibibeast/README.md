# 🐾 ChibiBeasts

A Discord creature-collector RPG bot — catch, hatch, battle, evolve, and trade chibi-style beasts across an original world with a creation myth, five named NPCs, a five-chapter story questline, guild raids, wild encounters, and NPC sparring.

**52 slash commands · 48 beasts · ~9,000 lines of Python**

---

## Features

### 🌍 World & Story
- **Full creation myth** — the Loom, four Architects, five Divine Collections, and a Sundering that explains why raid bosses exist
- **5-chapter questline** — *The Sundering of the Loom* — with real dialogue, objectives, and rewards culminating in the World-Tree Seed egg
- **5 named NPCs** (Maren, Cael, Sable, Orren, The Archivist) each with distinct voice, companion beast, four relationship levels, and contextual dialogue
- **96 encounter situation lines** across 32 beasts — every wild encounter describes what the beast is doing when you find it, not just that it exists
- **NPC ambient presence** — once you know an NPC, they occasionally leave traces in their home biome during explore (25% chance per encounter)
- **Questline-reactive flavor** — while on a chapter, the relevant biome acknowledges what you're there for
- **Discovery moments** — first biome visit, first Divine catch, collection completion — all surface lore-grounded narration
- In-game readable lore via `/lore`, beast encyclopedia via `/codex`, type chart via `/typeinfo`

### 🐾 Beasts & Collection
- **48 beasts** across 6 rarities: Common → Uncommon → Rare → Epic → Legendary → Divine
- **4 chooseable starters** (Prismite, Twine, Gloop, Barkley) each tied to an Architect from the creation myth
- **16 Divine beasts** across 5 named collections, each with a **unique passive battle ability** (double turns, lifesteal, shields, status reflection, stacking buffs, immunity, etc.)
- **11 evolution chains** — Common beasts can evolve up to Epic, Legendary, and even Divine tiers via Sunforge Core or Genesis Fruit
- **8 beast dispositions** (±10% stat modifier rolled on catch, Architects exempt)
- **Server bestiary** tracking who discovered each beast first

### 🥚 Getting Beasts
- `/explore` — 5 level-gated biomes with unique rarity pools, material drops, relic discovery, and 1-hour cooldown that persists across bot restarts
- `/hatch` — 4 instant-hatch egg tiers: Common (200g), Rare (1,500g), Celestial (8,000g / 25% Divine), Abyssal (25,000g / 55% Divine)
- 19 named incubation eggs with real timers (1hr → 96hr) via `/incubate` + `/hatchegg`
- Rarity pools computed at runtime — Stardust Touch perk and Celestial Observatory sanctuary bonus apply at call time

### ⚔️ Battle
- **`/battle @trainer`** — turn-based PvP with move selection, mana system, 7 status effects, type advantage, divine passives, and equipment bonuses
- **`/challenge <biome>`** — fight a wild beast; win to catch it. Wild beasts scale to your level ±2, drawn from that biome's rarity pool including Divines in the Celestial Loom
- **`/sparr <npc>`** — spar with any of the 5 NPCs once per day. Each has a distinct AI personality matching their character (Cael is random; Sable always ultimates; The Archivist is optimal). Win to advance your relationship and earn shards. The Archivist is locked until questline completion
- **Full 10-type advantage chart** (2× / 0.5×) with lore-grounded explanations
- **16 unique divine passives** fire during combat — all implemented
- Equipment bonuses (armor reduction, HP regen, crit immunity, evasion, death explosion, lifesteal) apply at battle start
- **Two-tier EXP curve**: starters level slowly (long-term investment); wild catches level in ~9 battles to Lv10 so players can experiment with type matchups freely
- **Losses yield consolation rewards** (~25% of win value) — tactical paralysis prevented
- **Turn 20 timeout**: both PvP and PvE resolve by HP percentage with a clear embed explaining the tiebreak — not a silent cliff

### 💰 Economy
- **Gold** from battles, explores, daily reward (scales with level), selling, raid loot
- **Celestial Shards** from achievements, raids, and daily quest completion bonus (+2 shards for completing all 3 daily quests) — spent at `/shard_shop`
- Shop prices calibrated so active players (completing daily quests) reach core weekly items in 3–4 days
- `/sell` items and materials (Whimsy Merchant perk adds +20% sell price)
- `/release` beasts for a rarity-scaled gold refund with lore-flavored confirmation
- `/buy` is fully atomic — single connection with `WHERE gold >= price` guard eliminates race conditions

### ⚒️ Crafting & Equipment
- 18 crafting materials (Common → Altered Divine)
- 12 craftable armor sets + 6 runes, each with unique battle effects and lore
- `/equip` and `/unequip` with full stat application at battle start
- Gnome Forge sanctuary upgrade gives 10% crafting discount (applied at runtime)
- All 18 items have implemented effects

### 🏰 Guilds & Raids
- Full guild system: create, invite, officer/leader ranks
- Raid bosses with HP tracking, damage leaderboard, MVP detection
- **Concurrent write protection** — per-raid `asyncio.Lock` prevents simultaneous attack coroutines from producing duplicate rewards or corrupt HP readings
- Altered Divine catch chance for top contributors — catch records the *purified divine form* per lore
- 3-tier Guild Sanctuary: Fairy Garden → Gnome Forge → Celestial Observatory (all effects applied at runtime)

### 📋 Progression
- 27 achievements Bronze → Platinum, auto-unlocking with channel announcements
- Daily quests: 3/day deterministic rotation, +2 shard bonus for completing all three
- Trainer levels with EXP scaling; beast levels with rarity-based stat growth
- Trainer titles earned from questline and collection completion, displayed in `/profile`
- 5 Divine collection completion rewards with unique NPC dialogue reactions

### ✅ Quality of Life
- `/help` — 9 categories covering all 52 commands
- `/stats` — server-wide statistics (trainers, beasts, divines, bestiary, raids, battles)
- `/daily` — claim daily reward, applies Fairy Garden happiness passively
- `/shard_shop` — 6 exclusive items with weekly purchase limits
- Cross-server profiles — beasts, gold, quests, and achievements follow players everywhere; only the bestiary is server-specific
- Global slash command error handler — no silent "application did not respond" failures

---

## Commands

| Cog | Commands |
|---|---|
| **starter.py** | `/start` |
| **hatch.py** | `/explore`, `/hatch` |
| **battle.py** | `/battle`, `/challenge`, `/sparr` |
| **profile.py** | `/profile`, `/collection`, `/beastinfo`, `/setactive`, `/nickname`, `/inventory`, `/use`, `/shop`, `/buy` |
| **guilds.py** | `/guild_create`, `/guild`, `/guild_invite`, `/raid`, `/raid_attack` |
| **social.py** | `/trade`, `/leaderboard`, `/perks`, `/perk_equip`, `/perk_unequip` |
| **progression.py** | `/dailies`, `/achievements`, `/bestiary` |
| **questline.py** | `/questline`, `/npc`, `/meet` |
| **world.py** | `/incubate`, `/eggs`, `/hatchegg`, `/sanctuary`, `/build`, `/craft`, `/recipes`, `/materials`, `/codex`, `/typeinfo`, `/lore` |
| **utilities.py** | `/equip`, `/unequip`, `/sell`, `/release`, `/evolve`, `/shard_shop`, `/daily`, `/stats`, `/help`, `/title` |

---

## Project Structure

```
chibibeast/
├── bot.py                    # Entry point, cog loader, global error handler
├── requirements.txt
├── railway.json              # Railway deployment config
├── Procfile
├── runtime.txt
├── .env.example
├── cogs/
│   ├── starter.py            # /start, StarterView (decoupled from egg system)
│   ├── hatch.py              # /explore, /hatch + biome/encounter/egg pool logic
│   ├── battle.py             # /battle, /challenge, /sparr + full combat engine
│   │                         #   AI personalities, divine passives, PvE/PvP shared engine
│   ├── guilds.py             # /guild*, /raid* + per-raid asyncio locks
│   ├── profile.py            # /profile, /shop, /buy (atomic), /use, /inventory
│   ├── progression.py        # /dailies, /achievements, /bestiary
│   ├── questline.py          # /questline, /npc, /meet + quest state machine
│   │                         #   RELATIONSHIP_LEVELS registry, named truncation constants
│   ├── social.py             # /trade, /leaderboard, /perk*
│   ├── utilities.py          # /equip, /unequip, /sell, /release, /evolve,
│   │                         #   /shard_shop, /daily, /stats, /help, /title
│   └── world.py              # /incubate, /eggs, /hatchegg, /sanctuary, /build,
│                             #   /craft, /recipes, /materials, /codex, /typeinfo, /lore
├── data/
│   ├── LORE.md               # Full lore bible (tone guide, world canon)
│   ├── beasts.json           # 48 beasts with stats, moves, passives, evolutions,
│   │                         #   and encounter_situations (96 lines across 32 beasts)
│   ├── equipment.json        # 12 armor sets + 6 runes with recipes and battle effects
│   ├── items.json            # 18 items (all effects implemented)
│   ├── materials.json        # 18 crafting materials
│   ├── npcs.json             # 5 NPCs with dialogue, personalities, relationship levels
│   ├── perks.json            # 12 perks across 5 rarities
│   └── questline.json        # 5-chapter questline with full dialogue and step definitions
└── utils/
    ├── db.py                 # Schema (19 tables), migrations, EXP curves, stat growth
    │                         #   Two-tier EXP: starters vs wild; HP clamp computed in
    │                         #   Python (not SQL) to avoid evaluation-order race condition
    ├── dispositions.py       # 8 beast dispositions (±10% stat modifiers)
    ├── progress.py           # Achievements (27), daily quests, bestiary tracking
    │                         #   All reads batched in single connection; circular dep
    │                         #   handled with delayed import + typed exception logging
    ├── sanctuary.py          # Runtime sanctuary bonus application
    ├── theme.py              # Colors, emoji, bar formatters
    └── type_chart.py         # 10-type advantage system (2×/0.5×) with lore
```

---

## Local Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a bot application** at the [Discord Developer Portal](https://discord.com/developers/applications). Enable **Server Members** and **Message Content** privileged intents. Invite with `bot` and `applications.commands` scopes.

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Fill in DISCORD_TOKEN and GUILD_ID
   ```

4. **Run:**
   ```bash
   python bot.py
   ```
   On first run, `init_db()` creates `data/chibibeast.db` and all 19 tables automatically. Schema migrations are idempotent — re-running against an existing database is always safe.

---

## Deploying to Railway

1. Push this repo to GitHub (`.gitignore` excludes `.env` and the local database — **never commit your token**).

2. In Railway, create a new project from your GitHub repo. It detects `requirements.txt` and `railway.json` automatically.

3. Under **Variables**, add:
   - `DISCORD_TOKEN`
   - `GUILD_ID`

4. **Add a persistent volume — this is critical.** Railway's filesystem is ephemeral; without a volume, `data/chibibeast.db` resets on every redeploy and all player progress is lost.
   - Service → **Volumes** tab → **Add Volume**
   - Mount path: `/app/data`
   - Size: 1 GB (default)

5. Deploy. Check logs for:
   - `✅ Database initialized` — DB connected, tables created
   - `✅ Synced N slash command(s)` — commands registered with Discord

### Single-server vs. multi-server sync

`bot.py` uses **guild-scoped sync** (instant, one server only). For multi-server deployment, switch to global sync in `on_ready()`:

```python
synced = await bot.tree.sync()  # global — up to 1 hour to propagate
```

---

## Architecture Notes

**EXP curves:** Two separate curves prevent the single-carry trap. Starters use `100 * level^1.5` (steep — long-term companions). Wild catches use `15 * level^1.1` (flat — Lv10 in ~9 battles). `get_beast_exp_for_level(beast_row, level)` selects the right curve automatically via `caught_from`.

**HP level-up clamping:** `apply_beast_levelup()` computes `new_max_hp` and `new_hp` in Python before the SQL query. SQLite evaluates SET expressions against original row values — writing `MIN(hp + ?, max_hp + ?)` would clamp against the old `max_hp`, leaving the beast wounded on level-up.

**Egg pools:** `get_egg_pool(egg_type, perks, sanctuary)` computes the effective rarity pool at call time, applying Stardust Touch and Observatory bonuses before re-normalising to 1.0.

**Shop purchases:** Single connection — perk check, balance verify, `UPDATE ... WHERE gold >= price`, and inventory grant all commit atomically. `rowcount == 0` means a concurrent request already deducted the gold.

**Raid concurrency:** Each active raid has an `asyncio.Lock` in `_raid_locks`. All HP mutation and defeat checks happen inside the lock; Discord I/O happens after release. The lock is cleaned up in `end_raid`.

**Divine passive system:** `calc_damage()` is pure — returns `(damage, is_crit, type_mult, crit_charge_delta)`. Supernova's charge accumulation is returned as a delta and applied by the caller post-resolution. Six passive-trigger functions handle all 16 divine effects at the correct points.

**Mana invariant:** Mana is always clamped `[0, max_mana]` — `max(0, ...)` on deductions, `min(max_mana, ...)` on additions. Mana updates fire before blind-miss short-circuits so a missed ultimate still costs mana.

**AI personalities:** `ai_pick_move()` uses beast-level type advantage (all moves inherit the beast's element — no per-move type field exists). `preferred_move` picks `moves[0]` when advantage exists, `moves[len//2]` otherwise, preventing the "always spams first move" pattern.

**Questline state machine:** `advance_quest_step(user_id, event_type, **kwargs)` is called from hatch.py, guilds.py, and progress.py. Circular dependency handled with delayed import — `ImportError` silently skipped, all other exceptions logged to `chibibeasts.progress`.

**Status effects:** Freeze/sleep log before decrementing. Blind and paralyze track in end-of-turn cleanup. Mana updates before blind-miss short-circuit. Turn 20 timeout tracked via `timed_out`/`timed_out_pvp` flags — surfaced in result embed with HP-percentage tiebreak explanation.

---

## Content Summary

| Category | Count |
|---|---|
| Slash commands | 52 |
| Beasts (total) | 48 |
| — Starters | 4 |
| — Divines with unique passives | 16 |
| — Evolution chains | 11 |
| — Beasts with encounter situations | 32 |
| Items (all with implemented effects) | 18 |
| Crafting materials | 18 |
| Armor sets | 12 |
| Runes | 6 |
| Perks | 12 |
| NPCs with relationship levels | 5 |
| Questline chapters | 5 |
| Achievements | 27 |
| Biomes | 5 |
| Named incubation egg types | 19 |
| Divine collections | 5 |
| Python lines | ~9,000 |
