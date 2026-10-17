# ChibiBeasts 🐾

A Discord creature RPG about collecting companions, caring for them, and helping an unfinished world grow. Python 3.12, discord.py, and SQLite via aiosqlite.

## Play

Begin with `/start`, then `/guide`. Choose a companion, explore a destination, and use `/questline` from trainer level 3. `/play`, battles and training build bonds. `/dailies` shows four daily quests; `/daily` claims a separate daily reward.

`/start` shows all four starter portraits and lets you browse each companion's play style, moves and base stats before pressing **Choose**. `/codex` opens an illustrated overview, with separate **Moves & stats** and **Lore** tabs. Artwork is packaged with the bot and attached directly to Discord messages; enable **Attach Files** and **Embed Links** in the bot's channel permissions. See the [interactive layout preview](docs/artwork-preview.html), [desktop screenshot](docs/artwork-preview/desktop.png), and [implementation notes](docs/ARTWORK-CARDS.txt).

| Destination | Trainer level | Main inhabitants |
|---|---:|---|
| Whispering Woods | 1 | Nature, earth, arcane, light |
| Ember Wastes | 3 | Fire, earth, shadow |
| Glacial Hollows | 5 | Ice, wind |
| Sunken Abyssal Trenches | 8 | Water, shadow |
| Celestial Loom | 10 | Cosmic, arcane, light |

`/explore` has a 30-minute base cooldown; `/challenge` has 20 minutes. `/play` has two hours. Equipped time perks reduce the relevant timers. Encounters use regional species pools and exclude starter and evolution-only forms.

## Starter awakenings

Each starter has its own Divine bond evolution, with a distinct silhouette and new passive:

| Companion | Awakening | Form and ability |
|---|---|---|
| Prismite | **Prismara** | Crystal serval with a shard mantle and three tails; protective prism shield |
| Twine | **Timestitch** | Clockwork jerboa with spring legs and a spool back; an extra action every third turn |
| Gloop | **Nebuloom** | Jelly salamander with star fins and a crescent tail; stronger restorative skill |
| Barkley | **Rootwarden** | Forest drake with leaf wings, antlers and an eggshell chestplate; damage reduction and healing while guarding |

Reach **beast level 15, bond 30, and happiness 70**. Check `/bond`, then use `/evolve <beast number>`. No catalyst is needed. Nickname, disposition, level, EXP, training, earned stat boosts, Ambrosia bonus, gear and party assignments survive evolution. Bond continues to strengthen the starter family after awakening.

Radiant and Ascended evolutions retain their own item requirements. Evolution-only forms cannot hatch randomly. The roster contains **86 creatures**, each with a local, versioned PNG. See the [evolution preview](chibibeasts-evolutions.html) and [artwork notes](docs/ARTWORK.md).

## Campaign

All ten chapters connect through a persistent journal and atomic reward claims. Existing collections, materials, trainer levels and completed story rewards are reconciled when the journal opens. Accounts that reached the old chapter-five ending continue at chapter six.

The story resolves the clasp, Maren and Orren's reunion, the Fraying, and the World Tree Seed. In the finale, **care, curiosity or courage** changes the concluding scene and earned title. The restored hollow and post-story NPC dialogue persist. The story-only Seed is earned in chapter five and incubates for 24 hours; it is unavailable for purchase.

Story spoilers and the shared terminology are documented in [the lore bible](data/LORE.md).

## Battles and raids

Wild battles, NPC spars, `/battle` and `/ranked` use the same interactive combat runner. Every beast has three different move roles: a reliable attack that restores mana, a typed skill or healing skill, and a guard that prepares a stronger strike. Attacks, skills and guards each earn one ultimate charge; an ultimate spends three charges and 50 mana. Automatic bonus strikes earn no extra charge. Wild opponents build charges under the same rules. Armor, evasion, shields, passives, bonds, gear and equipped perks affect the actual hit before HP is removed. Items affect the live battle state.

Duels show both companions on a matching realm battlefield, with HP/MP bars above the scene. Attacks, skills, healing, guards and ultimates visibly animate before the next move prompt opens. All 86 portraits and five backgrounds ship with the bot. See the [actual attack clip](docs/battle-preview/attack.gif), [realm layouts](docs/battle-preview/realms-desktop-mobile.png), and [runtime notes](docs/ANIMATED-BATTLES.md). Discord's GIF autoplay setting controls client playback; text results and a final still remain available.

Guild `/raid` and `/ancient` share a cooperative system with a lobby, three companions per trainer, swapping, tending allies, guarding and boss-specific Weave responses. Guild raids require a level-five guild, 50 guild tokens, and a leader or officer to host. Trainers need level 10 and three healthy assigned companions. Ancient raids spend their summon item when the lobby starts.

Both raid types show a large boss and every trainer's active companion in a shared pixel-art arena. Up to ten companions animate together against the matching realm, with numbered positions matching the trainer panel. Attacks, ultimates, barriers, healing, swaps, Weave threads and boss sweeps use real combat results. Each trainer keeps independent live controls during clips. See the [four-trainer clip](docs/raid-preview/team-clash-4.gif), [ten-trainer clip](docs/raid-preview/team-clash-10.gif), and [raid arena notes](docs/COOPERATIVE-RAID-ARENA.md).

Prepared parties aim for **5–8 minute raids**, within the ten-minute limit. Boss budgets use party size and the median trainer's highest companion level, rounded down into five-level bands from 10 to 50. Gear and training improve your chances without increasing the boss's stats. Speed gives 3.5–6.5 second action recovery; Weave and Tend each have their own eight-second cooldown. Boss volleys pressure every standing trainer. Repeated freeze/sleep cannot lock a boss down, and percentage-HP effects have attack-based caps against bosses. Raids start at zero ultimate charges and up to 40% mana, plus equipped perk bonuses.

Every active contributor receives rewards, including support players. Forging materials have real drop routes. Successful rescues build a guaranteed companion catch after eight Corrupted clears or twelve Ancient clears of that species, with a chance to meet the companion sooner. Interrupted paid raids are refunded once on restart. Fainted companions recover after their timer or can be revived.

Gold, shards, tokens, guaranteed materials and EXP scale with raid level, reaching twice the level-10 amounts at level 50. Catch odds and guaranteed-catch clear counts stay the same across levels.

Raid patterns allow at least six seconds to respond. Each trainer can Weave once per pattern; the requirement adjusts when only one trainer has healthy companions left. Display updates combine rapid inputs, while combat resolves immediately. Victory payouts are saved before result messages and resume after an interrupted settlement. Items follow the companion currently fighting after a swap; party revives also restore the live bench.

The [battle runtime audit](docs/BATTLE-RUNTIME-AUDIT.txt) covers concurrent settlement, slow Discord updates, knockout batching and battle acceptance. Raid warnings grant their full response window after publication, Swap cycles through all three healthy companions, and the panel shows every trainer's mana and ultimate charges. [Before](docs/battle-runtime-before.json)/[after](docs/battle-runtime-after.json) benchmarks and the [replayed raid holdout](docs/battle-runtime-balance/holdout-summary.json) contain the measured evidence.

Ranked matches use gear and chosen moves. Five placements establish a rank; 28-day seasons archive qualified standings, award 5–50 shards, and reset ratings. Repeat-opponent limits reduce farming.

## Shops, crafting and care

- `/shop` sells instant eggs, incubation eggs and usable supplies. Whimsy Merchant discounts gold purchases by 5%.
- Shard purchases grant real inventory items or saved exploration charges. A Prism Key takes priority over the Divine Compass. Incubation skips move both tending and hatching clocks.
- Astral Reroll attunes a matching instant hatch at its rolled rarity; it remains saved when that rarity has no matching element.
- `/craft` includes armor, runes and evolution catalysts. `/equip` uses one armor slot and one rune slot per companion. Replacing armor returns the old piece; other copies remain equipped.
- All seven guild shop items work. `/build` spends an owned Blueprint on an eligible upgrade before charging guild tokens.
- Twelve perks unlock through normal levels, catches, battles, exploration, raids and campaign progress. `/perks` lists owned perks; `/perk_equip` chooses a build. Astral Resonance's weekly `/perk_use` skips six hours from an egg.
- A Fairy Garden adds five happiness to benched companions when `/daily` is claimed, offsetting their daily decay. Other Sanctuary bonuses affect crafting, training, exploration, EXP, raids and market capacity.
- `/trade` uses the collection numbers shown to players. Omitting a requested companion makes the offer a gift. Market purchases and trades check current ownership, gold, party assignments and listing terms in one transaction. Equipped armor and runes travel with the companion. Releasing a companion returns its gear; transfers and releases preserve the numbers of other companions.

Every completed `/explore` now brings back one crafting material, including quiet trips and escaped creatures. Basic armor/runes need about **8–11 explorations** in the seeded acquisition model. `/recipes` shows actual effects, crafting levels, exact habitat/raid routes and your current Forge discount. Equipment remains craftable; old unused rune price tags have been removed. Fenrir Fur now helps forge the Abyssal Shroud.

Gold purchases and crafting follow a level ladder. Instant eggs unlock at trainer levels **1/3/10/15**; Common through Divine incubation eggs unlock at **1/3/5/10/15/25**. Legendary incubation eggs cost **24,000g**, and the three guaranteed-Divine eggs cost **80,000g**. The World-Tree Seed comes from the campaign. Egg bonuses stay within an instant egg's offered tiers, with Glimmering Fortune retaining its rare Divine upgrade.

Raids and `/contribute` award personal **Guild Tickets** for `/guild_shop`; the guild's shared tokens pay for upgrades and raid entry. Existing personal raid tokens convert to tickets once at startup. Guild EXP requirements use 250 × current guild level, making level-five raid access roughly seven days for three daily contributors, or two to three days for ten, before missed days or membership changes.

Training costs 50–1,000g by rarity; new HP sessions grant three times the normal point gain. Phoenix Elixirs cost 750g, Luna Nectar 150g, and Star-Candy 1,500g. Each companion has 30 tracked Star-Candy uses; evolution preserves the counter. Carrots cost 150g and grant 25 + 5 × trainer level EXP, scaling through trainer level 30. Repeated temporary potions do not stack. Repeatable exploration/challenge/daily gold scales through level 50, while EXP and levels continue to progress. Loom Catalysts move an egg's schedule two hours closer for 800g.

## Run and deploy

```sh
python -m pip install -r requirements-lock.txt
python bot.py
```

Set `DISCORD_TOKEN` in the environment or `.env`. `OWNER_ID` controls owner tools; `GUILD_ID` scopes developer commands to the home server. See `env.example`. `BEAST_IMAGE_BASE_URL` can point at a preview branch or public sprite directory; the default image host uses this repository's `main` branch. Deploy code, sprite files and `data/artwork.json` together.

Back up the existing SQLite database before upgrading. Schema migrations preserve accounts and collections; they run during startup. On Railway, keep `db/` on the persistent volume. This update does not require deleting the database. Use owner-only `!sync` to refresh slash commands if needed.

The combat balance migration applies base-stat differences once to nine affected species, including the four starters. Existing level growth, disposition, training, bonds, equipment and other earned boosts are preserved. Deploy code and catalogs together and restart the bot to load the new rules.

All commands use the database configured in `utils.db.DB_PATH`, resolved from the project directory. SQLite uses WAL and indexed lookups. Keep SQLite's `-wal` and `-shm` files on the same volume while the bot runs; use SQLite's backup API or stop the bot before copying a database backup. The bot expects one running instance per database, since battle and lobby reservations live in memory.

`requirements-lock.txt` records the tested Python 3.12 runtime packages; `requirements.txt` declares compatible direct-dependency ranges. CI installs the lock, checks dependencies, compiles the code and runs the regressions. Static game catalogs are cached until restart. Developers editing JSON during a session can call `utils.catalog.clear_catalog_cache()`.

## Source layout

- `cogs/`: 16 command extensions, including the new `/bond` journal.
- `utils/combat.py`, `utils/duels.py`, `utils/raids.py`: shared battle and raid rules.
- `utils/events.py`, `utils/story.py`, `utils/progress.py`: canonical events, campaign and milestones.
- `utils/gameplay.py`, `utils/items.py`, `utils/shops.py`, `utils/equipment.py`, `utils/rewards.py`: validated grants, consumption, purchases and progression.
- `utils/habitats.py`, `utils/hatching.py`: destination pools and hatch modifiers.
- `utils/catalog.py`, `utils/transfers.py`: cached definitions and transactional ownership changes.
- `data/`: roster, artwork manifest, recipes, items, perks, NPCs and story.
- `tests/`: temporary-database regressions and Discord extension loading without login.

## Local checks and previews

```sh
python -m compileall -q bot.py cogs utils scripts tests
python -m unittest discover -s tests -v
python -m pip check
python scripts/benchmark_gameplay.py --output performance.json
python scripts/simulate_gameplay.py --output simulation.json
python scripts/audit_combat_balance.py --output balance-results
python scripts/audit_combat_balance.py --output balance-results --holdout-only
python scripts/preview_raid_arena.py --output raid-preview-output
python scripts/audit_economy.py --output economy-results
python scripts/finalize_artwork.py
python scripts/build_revamp_preview.py
python scripts/build_art_gallery.py --output chibibeasts-gallery-full.html --standalone
```

Checks cover all ten chapters and all three endings, preserved evolution investments, concurrent claims and consumption, real item effects, armor and evasion, all creature and gear rules, crafting, perk unlocks, dated ranked seasons, raid catch guarantees, support rewards and interrupted-raid refunds. They do not connect to Discord. A live server smoke test remains necessary for permissions, slash-command sync and multi-player interaction timing.

The gameplay audit adds tests for overlapping battles, stale market offers, transfers with gear, interrupted payouts, raid display batching, response windows, live bench revives and daily reward races. The [audit report](docs/GAMEPLAY-AUDIT.txt), [validation results](docs/gameplay-audit-validation.json), [before](docs/performance-before.json)/[after](docs/performance-after.json) benchmarks and [seeded simulations](docs/gameplay-simulation.json) contain the measured evidence. Benchmarks use synthetic data and exclude Discord latency.

The later [combat balance audit](docs/COMBAT-BALANCE-AUDIT.txt) covers all 86 creatures, 430 stat profiles, 9,736 same-rarity fights and 11,340 raid trials. See the [roster overview](docs/balance-after/roster-overview.csv), [full stat sheet](docs/balance-after/creature-stats.csv), [fresh-seed results](docs/balance-after/holdout-summary.json) and [114-test validation](docs/combat-balance-validation.json). The prepared reference cohort's median was 6:04, with its 10th–90th percentile at 5:14–7:35; these are synthetic scenarios, not live player timings. Earlier gameplay reports describe their original revision's results.

The [economy audit](docs/ECONOMY-AUDIT.txt) reviews **52 shop offers, 29 recipes and all 21 materials**. Its [shop budgets](docs/economy-after/shop-affordability.csv), [crafting acquisition trials](docs/economy-after/recipes.csv), [reward values](docs/economy-after/raid-rewards.csv), [summary](docs/economy-after/summary.json) and [validation](docs/economy-validation.json) record the evidence. Income and acquisition times are explicit models, not live player telemetry. Existing inventory, companions and earned stats are preserved; the new treat counter starts at zero for legacy companions because historical treat counts were not recorded.

The [paginated gallery](chibibeasts-gallery.html) and [full gallery](chibibeasts-gallery-full.html) show the current roster. The [original design preview](chibibeasts-preview.html) and `docs/art-exploration/` preserve the earlier art discussion. `ChibiBeasts-revamp.zip` is the earlier art-and-story revamp snapshot. Use the repository for current gameplay code and tests.
