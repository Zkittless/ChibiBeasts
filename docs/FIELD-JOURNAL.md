# Field Journal

Open `/journal` for **Adventure**, **Beasts** and **Camp**. Section switches, companion tools and Back buttons edit the same message. Opening another journal replaces your previous journal in that channel after the new one is delivered. The approved three-section design keeps frequent actions visible and places secondary tools inside their section.

See the [recorded production layouts](journal-sections-preview/README.md) at phone widths.

## Adventure

Choose a destination, review the fitted team illustration and press **Start adventure · 1 charge**. The seasonal Carnival appears first while active. **Edit team** assigns the lead and two reserves; its Raids mode stores separate raid assignments. Care appears when a companion needs recovery.

**More travel options** contains Tower Trials, Battle log, Saved destination and Carnival rewards. Saving a default destination never changes the team or starts a run. An explicit `/explore destination:…` overrides only that opening. `/explore` opens setup or resumes the saved route and never spends a charge automatically.

During an adventure, story decisions use a dropdown with complete descriptions above it. Battles retain move buttons and a single **Swap / Assist** popup. **Journal** opens the overview without discarding the route; **Resume adventure** returns to it. A saved trial shows **Resume trial**. The travelling roster stays reserved until the run ends. Trial panels keep their existing combat lifetime and return to the same journal message.

## Beasts

The roster starts at Common, with owned rarities in a separate selector. Four companions appear per overview page; Previous/Next and search reach the entire collection. Search accepts a nickname, species or collection number. Selecting a companion shows its equipped portrait and **Companion actions**: Care & items, Gear, Make active lead, Train, Rename, Evolve / awaken and Team.

Those actions reuse the journal. **Back to beast** preserves the individual, rarity, search and page; **Back to beasts** returns to that roster context. Large detail pages use Previous/Next text pages instead of dropping text. The roster's **Care** button only lists injured or knocked-out companions. A selected beast's **Care & items** page also accepts growth and bond items while healthy. Existing review/confirmation and stale-selection safeguards remain in force.

## Camp

Choose Nursery, Workshop, Shop & inventory, Story & goals, Trainer & achievements, or Guild & community. Ready daily rewards and ready eggs appear as short actionable rows.

- **Nursery:** purchases, real hatch pools, passive incubators, tending, hatching, saved hatchling choices and research.
- **Workshop:** recipes, material inventory and sources, crafting, equipping and evolution confirmations.
- **Story & goals:** chapters, objectives, pinned goals and daily objectives. Saved goals keep their existing progress rules.
- **Trainer:** personal profile, perks, achievements, bestiary and Journal guide.
- **Guild & community:** guild information, market and leaderboard. Shared raid lobbies remain separate messages so other trainers can join.

## Slash command cleanup

Thirty duplicate entrypoints are removed from slash registration. Their internal handlers remain available to panel actions:

| Journal location | Removed slash commands |
|---|---|
| Beasts | collection, beastinfo, nickname, gear, equip, unequip, care, train, evolve |
| Camp → Nursery | nursery, hatch, incubate, eggs, tend |
| Camp → Workshop | craft |
| Camp → Shop & inventory | inventory, shop |
| Camp / Story & goals | daily, dailies |
| Camp → Trainer | achievements, bestiary, perks, perk_equip, perk_unequip, perk_use |
| Adventure → More travel options | history, challenge |
| Adventure → Edit team | party, raidparty |
| Camp → Guild & community | leaderboard |

`/journal` and `/explore` remain the principal entrypoints. `/profile` still looks up other trainers. `/use` remains for trainer items such as elemental attunement tokens, and `/play` retains its distinct interaction. Multiplayer, market-management, reference and developer commands remain available. The in-game Journal guide lists retained commands.

## Persistence, balance and delivery

Only confirming Start spends an exploration charge. Charge regeneration, egg prices and guarantees, training limits, item consumption, XP, gear effects, battle rules, rewards and existing transaction guards are unchanged. Navigation never grants resources. Saved trips, trial lineups, default destinations, teams and goals survive reopening and restart. No database reset or new schema is required.

Each published screen has fresh component IDs. The adapter forwards original control values, owner checks and modals to the existing game handlers. Old screens cannot apply actions after navigation, concurrent input is acknowledged, and failed publication leaves the prior screen as the recovery point. The message is edited with the durable bot transport, avoiding expired interaction webhook tokens. Artwork rejection retains text controls.

Startup global sync removes the duplicate commands. A one-time startup pass also removes only those names from existing guild slash registrations, preserving guild-only developer tools and context menus. Owner-only `!sync` or `!sync guild` can refresh registration manually. Keep one bot process per database, the existing Railway volume and current bot permissions.

Run `python -m unittest discover -s tests -v` for the regression suite. `tests/test_journal_hub.py` exercises actual controls, care consumption, modals, same-message returns and Discord's real view dispatcher. `tests/test_journal_commands.py` checks the loaded command registry and guild cleanup without contacting Discord. Older galleries in this repository document earlier layouts; they are not the current navigation specification.
