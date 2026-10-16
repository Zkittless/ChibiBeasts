# ChibiBeasts 🐾

A Discord creature RPG about collecting companions, caring for them, and helping an unfinished world grow. Python 3.12, discord.py, and SQLite via aiosqlite.

## Play

Begin with `/start`, then `/guide`. Choose a companion, explore a destination, and use `/questline` from trainer level 3. `/play`, battles and training build bonds. `/dailies` shows four daily quests; `/daily` claims a separate daily reward.

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

Wild battles, NPC spars, `/battle` and `/ranked` use the same interactive combat runner. Every beast has three different move roles: a reliable attack that restores mana, a typed skill or healing skill, and a guard that prepares a stronger strike. Ultimates need three charges and 50 mana. Armor, evasion, shields, passives, bonds, gear and equipped perks affect the actual hit before HP is removed. Items affect the live battle state.

Guild `/raid` and `/ancient` share a cooperative system with a lobby, three companions per trainer, swapping, tending allies, guarding and boss-specific Weave responses. Guild raids require a level-five guild, 50 guild tokens, and a leader or officer to host. Trainers need level 10 and three healthy assigned companions. Ancient raids spend their summon item when the lobby starts.

Every active contributor receives rewards, including support players. Forging materials have real drop routes. Successful rescues build a guaranteed companion catch after eight Corrupted clears or twelve Ancient clears of that species, with a chance to meet the companion sooner. Interrupted paid raids are refunded once on restart. Fainted companions recover after their timer or can be revived.

Ranked matches use gear and chosen moves. Five placements establish a rank; 28-day seasons archive qualified standings, award 5–50 shards, and reset ratings. Repeat-opponent limits reduce farming.

## Shops, crafting and care

- `/shop` sells instant eggs, incubation eggs and usable supplies. Whimsy Merchant discounts gold purchases by 5%.
- Shard purchases grant real inventory items or saved exploration charges. A Prism Key takes priority over the Divine Compass. Incubation skips move both tending and hatching clocks.
- Astral Reroll attunes a matching instant hatch at its rolled rarity; it remains saved when that rarity has no matching element.
- `/craft` includes armor, runes and evolution catalysts. `/equip` uses one armor slot and one rune slot per companion. Replacing armor returns the old piece; other copies remain equipped.
- All seven guild shop items work. `/build` spends an owned Blueprint on an eligible upgrade before charging guild tokens.
- Twelve perks unlock through normal levels, catches, battles, exploration, raids and campaign progress. `/perks` lists owned perks; `/perk_equip` chooses a build. Astral Resonance's weekly `/perk_use` skips six hours from an egg.
- A Fairy Garden adds five happiness to benched companions when `/daily` is claimed, offsetting their daily decay. Other Sanctuary bonuses affect crafting, training, exploration, EXP, raids and market capacity.

## Run and deploy

```sh
python -m pip install -r requirements.txt
python bot.py
```

Set `DISCORD_TOKEN` in the environment or `.env`. `OWNER_ID` controls owner tools; `GUILD_ID` scopes developer commands to the home server. See `env.example`. `BEAST_IMAGE_BASE_URL` can point at a preview branch or public sprite directory; the default image host uses this repository's `main` branch. Deploy code, sprite files and `data/artwork.json` together.

Back up the existing SQLite database before upgrading. Schema migrations preserve accounts and collections; they run during startup. On Railway, keep `db/` on the persistent volume. This update does not require deleting the database. Use owner-only `!sync` to refresh slash commands if needed.

## Source layout

- `cogs/`: 16 command extensions, including the new `/bond` journal.
- `utils/combat.py`, `utils/duels.py`, `utils/raids.py`: shared battle and raid rules.
- `utils/events.py`, `utils/story.py`, `utils/progress.py`: canonical events, campaign and milestones.
- `utils/gameplay.py`, `utils/items.py`, `utils/shops.py`, `utils/equipment.py`, `utils/rewards.py`: validated grants, consumption, purchases and progression.
- `utils/habitats.py`, `utils/hatching.py`: destination pools and hatch modifiers.
- `data/`: roster, artwork manifest, recipes, items, perks, NPCs and story.
- `tests/`: temporary-database regressions and Discord extension loading without login.

## Local checks and previews

```sh
python -m compileall -q bot.py cogs utils scripts tests
python -m unittest discover -s tests -v
python scripts/finalize_artwork.py
python scripts/build_revamp_preview.py
python scripts/build_art_gallery.py --output chibibeasts-gallery-full.html --standalone
```

Checks cover all ten chapters and all three endings, preserved evolution investments, concurrent claims and consumption, real item effects, armor and evasion, all creature and gear rules, crafting, perk unlocks, dated ranked seasons, raid catch guarantees, support rewards and interrupted-raid refunds. They do not connect to Discord. A live server smoke test remains necessary for permissions, slash-command sync and multi-player interaction timing.

The [paginated gallery](chibibeasts-gallery.html) and [full gallery](chibibeasts-gallery-full.html) show the current roster. The [original design preview](chibibeasts-preview.html) and `docs/art-exploration/` preserve the earlier art discussion. `ChibiBeasts-revamp.zip` contains the current bot source, data, art and tests, excluding secrets, player databases, caches and nested archives.
