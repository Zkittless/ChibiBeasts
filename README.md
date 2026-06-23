# 🐾 ChibiBeasts

A Discord creature-collector RPG bot — catch, hatch, battle, evolve, and raid alongside chibi-style beasts across an original world with a creation myth, five named NPCs, a five-chapter story questline, guild raids, ancient boss encounters, and a full 3-beast party system.

**59 slash commands · 70 beasts · ~13,400 lines of Python**

---

## Features

### 🌍 World & Story
- **Full creation myth** — the Loom, four Architects, five Divine Collections, and a Sundering that explains why raid bosses exist
- **5-chapter questline** — *The Sundering of the Loom* — with real dialogue, objectives, and rewards culminating in the World-Tree Seed egg
- **5 named NPCs** (Maren, Cael, Sable, Orren, The Archivist) each with distinct voice, companion beast, four relationship levels, and contextual dialogue
- **96 encounter situation lines** across 32 beasts — every wild encounter describes what the beast is doing when you find it
- **NPC ambient presence** — once you know an NPC, they occasionally leave traces in their home biome during explore
- **Questline-reactive flavor** — while on a chapter, the relevant biome acknowledges what you're there for
- In-game readable lore via `/lore`, beast encyclopedia via `/codex`, type chart via `/typeinfo`

### 🐾 Beasts & Collection
- **70 beasts** across 10 rarities: Common → Uncommon → Rare → Epic → Legendary → Divine → Altered Divine → Corrupted → Ancient → Dev
- **4 chooseable starters** (Prismite, Twine, Gloop, Barkley) each tied to an Architect from the creation myth
- **16 Divine beasts** across 5 named collections, each with a **unique passive battle ability**
- **3 Altered Divine beasts** (Void Chronos, Fractured Genesis, Abyssal Nebula) — purified forms of Ancient bosses, obtainable from raid top-3 catches
- **3 Corrupted beasts** — guild raid bosses (Corrupted Leviathan, Fenrir, Dragon)
- **3 Ancient beasts** — party raid bosses (Ancient Chronos, Genesis, Abyss)
- **1 Dev beast** — Desync the Infinite (Ouroboros), dev-exclusive, 99999/9999 base stats
- **12 evolution chains** — Common beasts can evolve up to Epic, Legendary, and Divine tiers
- **8 beast dispositions** (±10% stat modifier rolled on catch)
- **Dynamic stat growth** per rarity per level — all 10 rarities have unique growth curves
- **Global catch counter** with lore-flavored milestone messages at rarity-specific thresholds
- **Per-player beast numbering** — every beast gets a `#number` for consistent reference across all commands
- **Server bestiary** tracking who discovered each beast first
- `/collection` — tabbed by rarity with inline card layout, 10 per page

### 🥚 Getting Beasts
- `/explore` — 5 level-gated biomes with unique rarity pools weighted toward your active beast's rarity, material drops, relic discovery, and 1-hour cooldown
- `/hatch` — 4 instant-hatch egg tiers: Common (200g), Rare (1,500g), Celestial (8,000g / 25% Divine), Abyssal (25,000g / 55% Divine)
- 19 named incubation eggs with real timers (1hr → 96hr) and a proportional tend schedule via `/tend`
- `/ancient <summon_item>` — summon Ancient raid bosses using items dropped from Corrupted raids or purchased from the shard shop

### ⚔️ Battle
- **`/battle @trainer`** — turn-based PvP with move selection, mana system, 7 status effects, type advantage, divine passives, and equipment bonuses
- **`/challenge <biome>`** — fight a wild beast; win to catch it. Wild beasts scale to your level ±2 with rarity bias toward your active beast — no dead zones
- **`/sparr <npc>`** — spar with any of the 5 NPCs once per day with distinct AI personalities
- **Full 10-type advantage chart** (2× / 0.5×) with lore-grounded explanations
- **Dynamic scaling throughout PvE** — gold, EXP, status chance, and mana gain all scale with stats and level
- **Speed-influenced mana gain** — faster beasts charge ultimates quicker (`8 + speed//40`, cap 15)
- **Speed-influenced status chance** — `(attacker_spd / (atk_spd + def_spd)) × 0.25`
- **Mana-scaled ultimate damage** — 1.8× at 50 mana, 2.7× at full 100 mana

### ⚔️ Raid System
- **3-beast party system** — assign your raid party via `/raidparty` before joining raids. All 3 slots must be filled. Party loads at first attack and locks in for the fight — `/setactive` mid-raid has no effect
- **Corrupted Guild Raids** (`/raid`) — guild officers trigger raids against Corrupted Leviathan, Fenrir, or Dragon. Any guild member can join by pressing Attack
- **Ancient Party Raids** (`/ancient`) — anyone can summon an Ancient boss with a summon item. Lobby with Join Party + Solo Run buttons; party-size scaling applies at lobby close
- **Dynamic boss scaling** — boss HP/ATK/DEF computed from actual party beast stats at raid start: `boss_hp = avg_player_dps × cycles × n_players^0.75`. Weak players don't inflate the boss beyond what the team can handle
- **Boss fights back** — auto-attacks a random party member every 10s for 5–9% of their max HP (Ancient) or 8–12% (Corrupted). Immune to defense outliers
- **Phase transitions** — at 70%, 40%, 15% HP the boss fires a named signature AoE move. Each threshold reduces boss DEF by 20%/40%/60%. A **📋 Phase Log** on the embed tracks all fired transitions permanently
- **Mana system** — build mana with normal attacks (speed-scaled), unleash ultimates at 50+ mana. Charging to 100 deals 2.7× damage vs 1.8× at minimum. Ultimate button turns blue when ready
- **3-beast party fights** — when your active beast is knocked out, an ephemeral swap UI shows remaining bench slots with current HP. Dead slots are filtered. If all 3 are KO'd you're eliminated but can still watch
- **Party-wipe detection** — raid ends immediately when all participants are eliminated with no bench remaining
- **Cinematic defeat scenes** — unique per-boss defeat narratives when the party falls, distinct from kill scenes
- **Concurrent write protection** — per-raid `asyncio.Lock` prevents simultaneous attack coroutines from corrupt HP readings
- **Embed architecture** — single shared `_update_embed()` coroutine with per-raid `asyncio.Lock`, wait-and-retry on contention, always reads live state (no stale snapshots)

### 💰 Economy
- **Gold** from battles, explores, daily reward (scales with level), selling, raid loot
- **Celestial Shards** from achievements, raids, and daily quest completion — spent at `/shard_shop`
- **Shard shop** — 3 pages of 3 items each (Utility / Cosmetics & Access / Ancient Relics)
- Ancient Relics page: Epoch Shard, Firstborn Ember, Void Prism (also drop from Corrupted raids)

### ⚒️ Crafting & Equipment
- 18 crafting materials (Common → Altered Divine)
- 12 craftable armor sets + 6 runes, each with unique battle effects and lore
- `/equip` and `/unequip` with full stat application at battle start

### 🏰 Guilds
- Full guild system: create, invite, officer/leader ranks, guild sanctuary
- 3-tier Guild Sanctuary: Fairy Garden → Gnome Forge → Celestial Observatory (all effects applied at runtime)

### 📋 Progression
- 27 achievements Bronze → Platinum, auto-unlocking with channel announcements
- Daily quests: 3/day deterministic rotation, +2 shard bonus for completing all three
- Trainer levels with EXP scaling; beast levels with rarity-based stat growth per level
- Trainer titles earned from questline and collection completion

### 🛠️ Dev Tools
- `/dev set_beast_level @member #beast level` — set any beast to any level for testing, applies correct rarity growth in both directions
- `/dev give_ouroboros @member` — grant Desync the Infinite
- `/dev reset_shard_shop @member` — reset weekly shard shop cooldown

### ✅ Quality of Life
- `/raidparty` — interactive button UI to assign/clear all 3 raid party slots with beast stats shown
- `/collection` — rarity tabs (Common through Special), 10 beasts per page, page indicator button
- `/beastinfo` — full beast detail with ◀ ▶ navigation and "🔍 Go to #" modal
- `/help` — 9 categories covering all commands
- `/stats` — server-wide statistics
- Cross-server profiles — beasts, gold, quests, and achievements follow players everywhere

---

## Commands

| Cog | Commands |
|---|---|
| **starter.py** | `/start` |
| **hatch.py** | `/explore`, `/hatch`, `/tend` |
| **battle.py** | `/battle`, `/challenge`, `/sparr` |
| **profile.py** | `/profile`, `/collection`, `/beastinfo`, `/setactive`, `/nickname`, `/inventory`, `/use`, `/shop`, `/buy` |
| **guilds.py** | `/guild_create`, `/guild`, `/guild_invite`, `/raid` |
| **ancient.py** | `/ancient` |
| **social.py** | `/trade`, `/leaderboard`, `/perks`, `/perk_equip`, `/perk_unequip` |
| **progression.py** | `/dailies`, `/achievements`, `/bestiary` |
| **questline.py** | `/questline`, `/npc`, `/meet` |
| **world.py** | `/incubate`, `/eggs`, `/sanctuary`, `/build`, `/craft`, `/recipes`, `/materials`, `/codex`, `/typeinfo`, `/lore` |
| **utilities.py** | `/equip`, `/unequip`, `/sell`, `/release`, `/evolve`, `/shard_shop`, `/raidparty`, `/daily`, `/stats`, `/help`, `/title` |
| **dev.py** | `/dev set_beast_level`, `/dev give_ouroboros`, `/dev reset_shard_shop` |

---

## Project Structure

```
chibibeasts/
├── bot.py                    # Entry point, cog loader, global error handler
├── requirements.txt
├── railway.json
├── cogs/
│   ├── starter.py            # /start
│   ├── hatch.py              # /explore, /hatch, /tend + biome/encounter/egg logic
│   ├── battle.py             # /battle, /challenge, /sparr + full combat engine
│   │                         #   Dynamic scaling: speed-based mana/status, rarity-biased
│   │                         #   encounters, EXP/gold scaling with level and enemy rarity
│   ├── guilds.py             # /guild*, /raid + Corrupted raid system
│   │                         #   Dynamic boss scaling, party system, phase transitions,
│   │                         #   concurrent-safe embed updates, defeat cinematics
│   ├── ancient.py            # /ancient + Ancient raid system
│   │                         #   Lobby with party/solo, same combat engine as guilds.py
│   ├── profile.py            # /profile, /collection (tabbed), /beastinfo, /shop, /buy
│   ├── progression.py        # /dailies, /achievements, /bestiary
│   ├── questline.py          # /questline, /npc, /meet + quest state machine
│   ├── social.py             # /trade, /leaderboard, /perk*
│   ├── utilities.py          # /equip, /unequip, /sell, /release, /evolve,
│   │                         #   /shard_shop, /raidparty, /daily, /stats, /help, /title
│   ├── world.py              # /incubate, /eggs, /sanctuary, /build, /craft, etc.
│   ├── tasks.py              # Background tasks
│   └── dev.py                # Dev-only commands (set_beast_level, give_ouroboros, etc.)
├── data/
│   ├── beasts.json           # 70 beasts with stats, moves, passives, evolutions,
│   │                         #   rarities: common/uncommon/rare/epic/legendary/divine/
│   │                         #   altered_divine/corrupted/ancient/dev
│   ├── equipment.json        # 12 armor sets + 6 runes
│   ├── items.json            # Items including 3 Ancient summon relics
│   ├── materials.json        # 18 crafting materials
│   ├── npcs.json             # 5 NPCs with dialogue and relationship levels
│   ├── perks.json            # 12 perks across 5 rarities
│   └── questline.json        # 5-chapter questline
└── utils/
    ├── db.py                 # Schema, migrations, EXP curves, stat growth
    │                         #   RARITY_GROWTH for all 10 rarities including corrupted/
    │                         #   ancient/dev; get_raid_party, set_raid_slot helpers;
    │                         #   raid_slot column on player_beasts
    ├── dispositions.py       # 8 beast dispositions (±10% stat modifiers)
    ├── modals.py             # Shared Discord modal components
    ├── progress.py           # Achievements (27), daily quests, bestiary tracking
    ├── sanctuary.py          # Runtime sanctuary bonus application
    ├── theme.py              # Colors, emoji, bar formatters
    └── type_chart.py         # 10-type advantage system (2×/0.5×)
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
   On first run, `init_db()` creates `db/chibibeast.db` and all tables automatically. Schema migrations are idempotent — re-running against an existing database is always safe.

---

## Deploying to Railway

1. Push this repo to GitHub (`.gitignore` excludes `.env` and the local database).

2. In Railway, create a new project from your GitHub repo.

3. Under **Variables**, add `DISCORD_TOKEN` and `GUILD_ID`.

4. **Add a persistent volume — critical.** Railway's filesystem is ephemeral; without a volume the database resets on every redeploy.
   - Service → **Volumes** tab → **Add Volume**
   - Mount path: `/app/db`
   - Size: 1 GB (default)

5. Deploy. Check logs for `✅ Database initialized` and `✅ Synced N slash command(s)`.

---

## Architecture Notes

**Raid boss scaling:** `boss_hp = avg_player_dps × cycles × n_players^0.75`. Boss HP is based on average DPS per player (not total), so weak players don't inflate the boss beyond what strong players can clear. The `n^0.75` multiplier ensures larger parties face a proportionally harder boss without it becoming impossible. Cycles are calibrated to realistic ~1.5s/attack cadence rather than theoretical 0.5s maximum.

**Embed update architecture:** A single shared `_update_embed()` coroutine per raid always reads live state from `active_raids[raid_id]` at execution time — no captured snapshots that go stale. A per-raid `asyncio.Lock` prevents concurrent edits; if the lock is held, the task waits 0.4s and retries once with fresh state before dropping.

**3-beast party system:** `raid_slot` column on `player_beasts` (values 1/2/3). Party loads from DB on first attack and locks into the raid dict for the fight duration. Per-slot HP tracked in `player_party_hp[(uid, slot)]`, updated on every boss hit and phase transition. Swap UI filters dead slots.

**Dynamic rarity-biased encounters:** Wild encounter rarity is weighted 3× toward the player's active beast rarity, 1.5× for adjacent tiers, within biome pool limits. Prevents high-rarity players one-shotting everything regardless of biome.

**Speed-based systems:** Mana gain = `min(15, 8 + speed//40)`. Status chance = `(atk_spd / (atk_spd + def_spd)) × 0.25`. Ultimate multiplier = `1.8 + max(0, mana-50)/50 × 0.9` (1.8× at 50 mana, 2.7× at 100).

**EXP curves:** Two separate curves prevent the single-carry trap. Starters use `100 × level^1.5` (steep). Wild catches use `15 × level^1.1` (flat — Lv10 in ~9 battles). Beast EXP from wild fights scales with enemy rarity and level: `randint(20,40) × rarity_mult + wild_level × 1.5`.

**HP level-up clamping:** `apply_beast_levelup()` computes new stats in Python before the SQL query. SQLite evaluates SET expressions against original row values — writing `MIN(hp + ?, max_hp + ?)` would clamp against the old `max_hp`.

**Shop purchases:** Single connection — perk check, balance verify, `UPDATE ... WHERE gold >= price`, and inventory grant all commit atomically. `rowcount == 0` means concurrent request already deducted gold.

---

## Content Summary

| Category | Count |
|---|---|
| Slash commands | 59 |
| Beasts (total) | 70 |
| — Common/Uncommon/Rare | 12/8/8 |
| — Epic/Legendary/Divine | 8/8/16 |
| — Altered Divine / Corrupted / Ancient | 3/3/3 |
| — Dev (Desync the Infinite) | 1 |
| Evolution chains | 12 |
| Corrupted raid bosses | 3 |
| Ancient raid bosses | 3 |
| Ancient summon items | 3 |
| Items (all with implemented effects) | 18+ |
| Crafting materials | 18 |
| Armor sets | 12 |
| Runes | 6 |
| Perks | 12 |
| NPCs with relationship levels | 5 |
| Questline chapters | 5 |
| Achievements | 27 |
| Biomes | 5 |
| Named incubation egg types | 19 |
| Python lines | ~13,400 |
