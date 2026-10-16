# ChibiBeasts 🐾

A creature-collector RPG Discord bot. Collect, raise, and battle ChibiBeasts across multiple servers.

## Stack
- **Language:** Python 3.12
- **Library:** discord.py
- **Database:** SQLite via aiosqlite
- **Hosting:** Railway
- **Beast images:** versioned PNGs in `data/sprites`, served from GitHub Raw or `BEAST_IMAGE_BASE_URL`
- **Sanctuary images:** existing Cloudinary illustrations

## Repository Structure
```
bot.py                  — Entry point, global sync, !sync command
cogs/
  battle.py             — PvE /challenge, /sparr, PvP /challenge, raid battles
  ancient.py            — Ancient raid system
  dev.py                — Owner-only dev tools (guild-scoped, hidden globally)
  economy.py            — /market, /list, /delist, /appraise, /train
  guilds.py             — Guild system, /raid, Corrupted bosses
  hatch.py              — /hatch, /explore, incubation eggs
  profile.py            — /profile, /collection, /shop, /inventory, /use
  progression.py        — /dailies, /achievements, /bestiary
  questline.py          — /questline, /npc, /meet — 10-chapter main story
  ranked.py             — ELO ranked PvP, /rank, /ranked_leaderboard
  social.py             — /perks, /trade, /leaderboard
  starter.py            — /start, cinematic onboarding
  tasks.py              — Background tasks (happiness decay)
  utilities.py          — /guide, /daily, /equip, /gear, /sell, /title, /play, /history, /party, /raidparty, /stats
  world.py              — /craft, /recipes, /sanctuary, /build, /codex, /typeinfo, /lore
utils/
  db.py                 — Database init, migrations, shared helpers
  dispositions.py       — Beast personality system
  modals.py             — Reusable QuantityModal
  progress.py           — 57 achievements, 25 daily quest pool, quest event tracking
  sanctuary.py          — Sanctuary upgrade effect functions
  theme.py              — Colors, emojis, embed helpers
  type_chart.py         — Full type advantage/resistance matrix
data/
  beasts.json           — 82 beasts with stats, moves, descriptions
  artwork.json          — canonical sprite filenames, hashes and dimensions
  sprites/              — complete 82-beast pixel art set
  equipment.json        — Armor and runes with recipes and effects
  items.json            — Shop items, evolution items, consumables
  materials.json        — Crafting materials and drop sources
  npcs.json             — 5 NPCs with relationship levels and dialogue
  perks.json            — Trainer perks
  questline.json        — 10-chapter questline with full narrative dialogue
```

## Deployment
Hosted on Railway. Environment variables required:
- `DISCORD_TOKEN` — Bot token
- `OWNER_ID` — Your Discord user ID (controls !sync and dev commands)
- `GUILD_ID` — Home server ID (dev commands scoped here only)

## Sync Commands (owner only)
- `!sync` — Global sync, pushes all commands to every server
- `!sync clear` — Wipes guild-scoped commands from current server (fixes duplicates)

## Current State

### Beasts
- **82 total** — each with a local PNG and a versioned public image URL
- Rarities: common → uncommon → rare → epic → legendary → divine → altered divine
- Types: fire, water, ice, earth, wind, nature, arcane, shadow, cosmic, light
- Evolution system: Radiant (items) and Ascended (Genesis Fruit from Ancient raids)
- 4 starters: Prismite, Twine, Gloop, Barkley

### Combat
- **Turn-based** with type effectiveness, status effects (burn, freeze, poison, sleep, paralyze, blind, blight)
- **Ultimates** — 3-battle charge system, 2.5× damage, button shows charge progress (0/3 → 1/3 → 2/3 → ready)
- **Divine passives** — unique per-beast mechanics for divine/altered divine tier
- **Gear system** — armor (defense/HP) and runes (on-hit effects) per beast
- **Happiness modifier** — neglected beasts fight at -10% stats

### Economy (lv25 active player)
- ~3,200g/day + ~8.7🔮/day
- `/daily` — free gold + shards once per day
- `/explore` — 1hr cooldown, materials + wild encounters
- `/challenge` — 30min cooldown, wild PvE battle, random catch chance
- `/sparr` — once per NPC per day, scales to player level
- `/market` — player-to-player beast trading, My Listings tab included
- `/train` — permanent stat boosts, capped by rarity
- `/appraise` — gold value estimate for beasts

### Shard Shop
| Item | Cost |
|---|---|
| Title Reset | 5🔮 |
| Rename Token | 10🔮 |
| Loom Fragment | 15🔮 |
| Astral Reroll | 15🔮 |
| Divine Compass | 25🔮 |
| Prism Key | 30🔮 |
| Epoch Shard (Chronos) | 60🔮 |
| Firstborn Ember (Genesis) | 80🔮 |
| Void Prism (Abyss) | 100🔮 |

### Incubation Eggs
| Rarity | Price | Incubation |
|---|---|---|
| Common | 300g | 1hr |
| Uncommon | 1,200g | 4hr |
| Rare | 4,000g | 8hr |
| Epic | 12,000g | 24hr |
| Legendary | 50,000g | 48hr |
| Divine | 100,000g | 96hr |

### Progression Systems
- **57 achievements** — all triggers wired across collecting, battling, crafting, raids, economy, social
- **Daily quests** — 4 per day, raid quests gated to level 10+
- **Questline** — 10 chapters, 5 NPCs (Maren, Cael, Sable, Orren, The Archivist), full narrative dialogue
- **NPC relationships** — stranger → known → trusted → companion, chapter-aware dialogue
- **Ranked PvP** — ELO system (1000 base, K=32), 5 placements, Bronze → Silver → Gold → Platinum → Diamond
- **Guild Sanctuary** — 7 upgrades across 4 tiers, passive bonuses to training/crafting/raids/market

### Sanctuary Upgrades
| Upgrade | Tier | Cost | Effect |
|---|---|---|---|
| Fairy Garden | 1 | 50 tokens | +1 happiness/day benched |
| Gnome Forge | 2 | 150 tokens | -10% craft cost |
| Training Grounds | 2 | 150 tokens | -10% train cost |
| Celestial Observatory | 3 | 300 tokens | +2% epic/legendary encounter |
| Arcane Library | 3 | 300 tokens | +15% EXP |
| Raid Altar | 4 | 500 tokens | +10% raid damage, +5% armor |
| Market Stall | 4 | 500 tokens | +2 market listing slots |

### Multi-Server
- Global command sync on startup
- Dev commands (under `/dev` group) scoped to home guild only — invisible globally
- Global player commands are available in every server after sync
- Owner-only developer commands require both `OWNER_ID` and `GUILD_ID`

## Known Architecture Notes
- SQLite with aiosqlite — fine for current scale, migrate to PostgreSQL if `database is locked` errors appear under heavy load
- `battle.py` has `DB_PATH` defined at module level for `run_pve_battle` (module-level function, no cog scope)
- JSON loaders use UTF-8 and resolve shared data relative to the repository root
- Starter claims and collection-number allocation are atomic across concurrent interactions
- A saved starter-claim flag prevents reclaiming a starter after selling or trading away a collection

## Artwork and local checks

See [docs/ARTWORK.md](docs/ARTWORK.md) for the sprite manifest, exact generation
prompts, public image hosting, and the gallery builder. Deploy the refreshed
PNGs and code together. A preview deployment can point `BEAST_IMAGE_BASE_URL`
at its branch's `data/sprites` directory.

```sh
python -m pip install -r requirements.txt
python -m compileall -q bot.py cogs utils scripts tests
python -m unittest discover -s tests -v
python scripts/build_art_gallery.py --output chibibeasts-gallery.html --standalone
```

The checks load all 15 extensions without connecting to Discord, verify image
coverage and hashes, test command scope, and exercise concurrent starter claims
against temporary databases. GitHub Actions runs the same checks on pull requests.
