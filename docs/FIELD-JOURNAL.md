# Field Journal and expeditions

Open `/journal`. The home page shows your active companion, exploration charges, story chapter and one pinned goal. Native Discord controls connect Adventure, Companions, Workshop, Story and Activities. Artwork ships with the bot; rejected uploads fall back to working text controls.

See the [offline interactive preview](journal-preview/field-journal.html), [home screen](journal-preview/home.png), [wild encounter](journal-preview/encounter.png) and [phone layout](journal-preview/mobile.png). These use real production component payloads with a sample trainer.

- **Adventure:** `/explore` opens this page and begins a saved expedition in the requested habitat. An existing trip resumes without spending another charge. Remaining charges and the next recharge stay visible during its choices. You can also choose an unlocked habitat and begin from the journal. Follow tracks to challenge, approach or observe a creature; gather one of the offered resources; or repair, study or rest at a realm landmark. Gathering and landmark routes can investigate one optional creature encounter before returning.
- **Companions:** browse the whole collection with pages of 25, make a healthy companion active, play together, pin an awakening or review and confirm a ready bond evolution. Bond growth and the starter family's combat bonus are explained here. The confirmation identifies the individual companion and its current species.
- **Workshop:** open the existing crafting panel in the same message. Pin the selected recipe or return to the journal. Opening a pinned recipe selects it without crafting or spending anything.
- **Story:** read introductions, check objectives and report back with the existing atomic chapter rewards. The final chapter retains all three choices. The completion scene stays visible until you turn the page.
- **Activities:** daily reward readiness, daily objectives and buttons opening the existing shop, wild challenge, guild and raid flows. The detailed command guide remains available here.

Goals are saved across restarts. Recipe progress includes the current Gnome Forge discount, material stacks and owned or equipped pieces. Creature hunts list actual eligible habitats and respect trainer level gates; pinning a creature does not change its encounter odds. Awakening goals follow the companion's permanent row ID, recognize its evolved form and handle a companion leaving the collection.

The separate guide, story-progress, bond-status, active-switch and material-bag commands are retired. Their complete flows live in the journal's Home, Story, Companions and Workshop pages. Distinct actions and detailed views retain their commands; Activities' command guide lists the available entrypoints. Existing command-backed activity buttons keep working.

## Charges and rewards

Each new expedition spends one charge. `/explore` always uses the journal's saved choice flow, including older invocations with `expedition:False`. One charge regenerates every 1,800 seconds, up to three. Bramble Walker reduces the interval to 90%; Chronos Hourglass reduces it to 80%; both together give 1,296 seconds. A newly initialized account receives one available charge. An existing exploration timestamp seeds the remaining wait, so reopening a journal or upgrading does not clear a running timer. Missing charges, locked habitats and companions needing recovery leave the journal open with an explanation and working navigation.

Each completed expedition grants exactly one material, capped gold and trainer EXP, plus at most one caught creature. Material choices remain within the rolled rarity and region. Repair gives 2 bond and 5 happiness; observation gives 1 bond; rest restores 5% max HP and 3 happiness without reviving a knocked-out companion; study adds 2 × trainer level EXP. These benefits apply to the original travelling companion if it is still owned. Existing encounter boosts, Observatory odds, compass precedence, Library EXP, catches, bestiary, achievements, daily objectives and campaign progression are preserved.

Base gold is 3–6 × reward level, capped at trainer level 50. Base trainer EXP is 10–14 × trainer level. A battle win adds 25% gold and 30% EXP; Pixie Pocket and Arcane Library use their existing bonuses. Gathering and landmark choices add no second material payout. A failed approach ends that encounter, and a trip never rolls a second creature. Opening another panel cannot reroll a saved find, spend the same charge twice or grant a completed trip twice.

## Recovery and deployment

Every accepted choice is stored with a revision. An old selection gets an acknowledged refresh message. Item, gold, EXP and caught-companion rewards commit together when you return home. A disconnected or interrupted battle keeps gathered supplies and resumes on the return path; battle victory recorded before a later message failure keeps the catch. A deleted or expired journal can be reopened through `/explore` or `/journal`.

Startup adds `exploration_energy`, `expeditions` and `journal_goals` tables without resetting existing data. Use the existing Railway volume and environment variables. Startup's normal global sync removes the retired commands and refreshes descriptions and defaults. Run owner-only `!sync guild` for servers with guild command copies, or `!sync` to refresh global registration manually. Keep one bot process per database, as required by the existing in-memory battle reservations.

Run `python -m unittest discover -s tests -v`. `python scripts/preview_journal_hub.py --output <directory>` rebuilds five packaged realm banners and exports actual component payloads plus an interactive offline preview. The preview is a local sample of production pages; it does not call Discord, charge a player or buy anything. Live Discord delivery still depends on the bot's existing channel permissions.
