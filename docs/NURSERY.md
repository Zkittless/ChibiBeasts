# Nursery and egg guarantees

Open `/nursery`, or **Field Journal → Activities → Nursery**. Browse guarantees, effective odds, eligible species, inventory and family research together. Buy, hatch or incubate from this panel. `/hatch`, `/incubate`, `/eggs` and `/tend` remain shortcuts to the same saved system.

An egg's **grade** sets its rarity floor and starting level. Its **family** determines which species can hatch. A higher rarity roll does not raise the advertised level band. Starter, evolution-only, Ancient, Corrupted and developer creatures cannot hatch from these eggs.

| Instant egg | Trainer level | Price | Guaranteed grade | Starting level | Base odds |
|---|---:|---:|---|---|---|
| Common | 1 | 200g | Common | 1–3 | 75% Common, 25% Uncommon |
| Wildheart | 6 | 700g | Uncommon | 6–9 | 75% Uncommon, 25% Rare |
| Rare | 12 | 1,500g | Rare | 14–18 | 80% Rare, 20% Epic |
| Resonant | 20 | 4,000g | Epic | 24–28 | 85% Epic, 15% Legendary |
| Celestial | 30 | 8,000g | Legendary | 34–38 | 90% Legendary, 10% Divine |
| Abyssal | 40 | 25,000g | Divine | 40–44 | 99% Divine, 1% Altered Divine |

Whimsy Merchant retains the 5% purchase discount. Stardust Touch and the Observatory promote only eligible offered tiers. Glimmering Fortune retains its 0.05% Divine jackpot where the actual family has eligible Divine creatures, preserving the floor and Altered chance. The panel displays the exact distribution used by the roll, including equipped bonuses. Astral attunement applies to the next matching instant hatch at its rolled rarity; it remains saved when no eligible same-tier species matches. Research selections do not consume attunement.

## Patient hatching

The nineteen existing timed egg IDs retain their inventory, prices, unlocks and incubation durations. Early eggs now have distinct elemental or curated families; high-grade collections keep explicit species pools. The World Tree Seed remains an earned, unsold story reward with its own level band.

Three incubator slots remain available. Chronos Hourglass retains its 20% duration reduction. Incubation finishes at its saved deadline without mandatory check-ins. Up to three optional care visits, spaced through incubation, give one starting bond each. They grant no extra level, rarity, stats or earlier deadline. Incubation skips move the care and hatch clocks together.

Optional care still advances the existing Patient Keeper daily quest. Care counters and daily rewards use saved receipts so a retry cannot count the same visit twice.

When ready, the egg rolls rarity **once**, then saves up to three distinct species of that rarity. Choose one companion; a family with fewer eligible species offers fewer choices. Reopening the panel or restarting the bot preserves the exact species, rarity and level. Selection is not three independent rarity rolls.

## Duplicate research

A duplicate gives one research point in its **grade and family**, banked up to sixteen. Eight points let the player select a species from that family's **grade-floor pool** on a paid hatch. Instant selection still consumes an owned egg; incubation selection consumes the already-paid ready incubation. It grants no additional companion or jackpot rarity. Research selections earn no research credit themselves.

The system remembers species after their first Nursery acquisition, including the collection held at that time. Releasing one cannot make it new again. Common research cannot purchase Divine or Altered companions, and one family's research cannot buy another family's species.

## Regional nests

New expeditions may save one scarce, regional nest clue at departure. Recovering an offered egg explicitly replaces the trip's ordinary material bundle; another choice keeps the materials, including quest supplies. No extra stop, catch or gold payout is added. Egg grades respect the habitat and trainer gate. Story Seeds and high-grade eggs are excluded.

Discovery chance is at most 10%, further limited so the expected maximum of egg resale or fully boosted hatch release value is at most 25% of base expedition gold. Choices, discovery roll and eligible egg are saved once. A veteran cannot turn an early habitat into a high-tier egg farm. Trips already in progress retain their original reward rules.

## Existing saves and failure recovery

Startup adds six Nursery tables without resetting inventory or changing old incubation deadlines. Existing ready schedules remain claimable below newer trainer gates; their hatch level is capped to that trainer's level when below the current gate. Old care progress becomes up to three optional bond points. No migration hatches or consumes an egg automatically.

Egg consumption, beast insertion, research spending, incubation claims and action receipts commit in the same SQLite writer transaction. Bulk hatches roll back together on failure and consume legacy split inventory stacks correctly. Retried action receipts cannot charge or hatch twice. Saved hatch records recover the reveal after a Discord or artwork failure. Hatch counters, starting bond and daily hatch rewards have separate transactional receipts; acquisition and story reconciliation use the game's existing keyed progression system. Collection rewards keep their existing achievement keys.

Use one bot process per database, as required by the game's existing in-memory battle reservations. New commands register through normal startup sync; owner-only `!sync guild` refreshes servers with guild-specific command copies.

## Validation

Run `python -m unittest discover -s tests -v`. `python scripts/audit_nursery.py` checks all 384 paid-egg, bonus and Merchant combinations, plus a conservative research replacement bound. `python scripts/audit_expedition_nests.py` checks all ten destinations and 100,000 saved discovery seeds. These are economy bounds, not claims about player retention or collection speed. `python scripts/preview_nursery.py --output docs/nursery-preview` exports real Discord component pages using a disposable database.
