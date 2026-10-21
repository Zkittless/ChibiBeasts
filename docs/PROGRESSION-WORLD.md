# World progression integration

The world has ten distinct biomes across ten fixed difficulty bands. The original destination IDs continue to work, including the five former `*_deep` routes. Each biome has its own background and exploration scenery. A destination's trainer requirement controls access; its companion range describes the enemies. Within that fixed band, encounters roll within one level of the visiting companion where possible. An advanced region never falls below its floor, and an old region never rises above its ceiling.

## Overall balance

- First starter awakenings are Rare and require companion 15, trainer 8, bond 40 and happiness 70. The new form inherits its ancestor's earned growth at awakening; it does not instantly recalculate every earlier level at the new rarity. Its remaining growth gradually catches up to the new rarity's full budget by level 50, including earned level-50 ascensions. Both trainer and companion progression stop at 50.
- The startup stat migration uses a frozen legacy baseline and one atomic revision marker. It preserves identity, disposition, earned training/stat extras, resources in proportion, knockout state, and levels through the new cap. Legacy levels above 50 are capped. Retrying cannot apply the conversion twice, and a failed conversion rolls back its rows and marker together.
- Gnome-Grown Carrots cost 120 gold and grant 20 companion EXP. Singularity Soda grants up to 60 EXP. Both share a companion's 60 EXP growth-treat allowance per UTC day; Soda never skips whole levels. Four training sessions per companion per UTC day provide bounded training gains and at most 40 training EXP. These individual budgets remain attached to the companion across trades.
- Crafting, equipping and incubation gates follow Common 1, Uncommon 6, Rare 12, Epic 20, Legendary 30 and Divine 40. Already-owned equipment above the trainer's gate stays owned and bound, with its effects dormant until that gate is met. A legacy best-in-slot piece cannot bypass the new progression ladder.
- Instant Common/Rare/Celestial/Abyssal eggs require trainer 1/12/30/40 for both purchase and hatching. Hatch checkout rechecks the current trainer level inside the consumption transaction, so an old owned egg or stale quantity modal cannot bypass its gate. Starting an owned incubation egg uses the same current incubation tier gate; already-started schedules and earned World-Tree Seeds remain usable.
- PvP's shared daily growth budget, destination difficulty, raid/story gates, daily rewards, food and training form one progression system; expensive items and repeat battles cannot bypass the level and daily limits.

| Destination ID | Biome | Trainer requirement | Wild companion levels | Rarity weights |
| --- | --- | ---: | --- | --- |
| `woods` | Whispering Woods | 1 | 1–5 | Common 85%, Uncommon 15% |
| `woods_deep` | Moonveil Marsh | 3 | 6–10 | Common 65%, Uncommon 30%, Rare 5% |
| `ember` | Ember Wastes | 6 | 11–15 | Common 20%, Uncommon 65%, Rare 15% |
| `ember_deep` | Sunforge Dunes | 9 | 16–20 | Uncommon 25%, Rare 60%, Epic 15% |
| `glacial` | Glacial Hollows | 12 | 21–25 | Rare 70%, Epic 30% |
| `glacial_deep` | Stormglass Peaks | 16 | 26–30 | Rare 35%, Epic 60%, Legendary 5% |
| `trenches` | Sunken Abyssal Trenches | 20 | 31–35 | Epic 75%, Legendary 25% |
| `trenches_deep` | Cinderreef Caldera | 25 | 36–40 | Epic 35%, Legendary 65% |
| `loom` | Celestial Loom | 30 | 41–45 | Rare 5%, Epic 70%, Divine 25% |
| `loom_deep` | Starfall Gardens | 38 | 46–50 | Epic 40%, Divine 60% |

`utils.habitats.BIOMES` is the authoritative destination catalog. `BASE_BIOMES` contains the original five regional family IDs. Use `destination_key()` for encounters, event scenery and artwork, and `biome_key()` for regional materials, nest families and story counts. Moonveil retains the Woods' resource family; Sunforge retains Ember's; Stormglass retains Glacial's; Cinderreef retains the Trenches'; Starfall retains the Loom's. Existing encounter type pools, rarity odds, numerical gates and reward budgets are unchanged. Old display labels such as `The Whispering Woods · Depths` resolve to their original destination IDs before partial-name matching. The journal and `/explore` and `/challenge` choices read the shared definitions.

New expeditions save `destination_version=1` and their destination ID alongside the existing adventure payload. The five new biomes use distinct landmarks, tracks, gathering places, rescues, camps and discoveries. Their crossing stop adds one themed decision: read Moonveil's waterline, align Sunforge's sun mirror, read Stormglass's windsock, redirect Cinderreef's vent regulator, or remember the team's earlier route in Starfall. A saved clue describes the sheltered route and the shorter risky alternative. Both are available to every team; suitable companion abilities remain optional.

The crossing outcomes exactly match the existing obstacle budget: a sheltered route has no damage or added reward; a shortcut costs up to 3% max HP and adds 5% to the single reward bundle; an eligible healthy helper adds the existing 5% and route clue without HP cost. There are no timed inputs, extra catches, added material bundles or new payouts. Crossing variants and displayed choices are persisted, and reopening the journal reads the saved page without rolling again. In-flight expeditions without the destination version retain their old regional page text and original buttons as they continue; their destination artwork uses the new background. The three-stop route, optional fourth stop, once-per-trip camp and 25% bonus cap remain unchanged.

The journal recommends Common starter training in the Woods through companion level 7, then Moonveil Marsh from level 8 until the first awakening. `starter_training_destination()` supplies this visible recommendation; it does not lock other destinations. Ember's Fire/Shadow matchups can resist a starter's type, so a newly unlocked numeric range does not imply that every build is ready for it.

Every advertised rarity has an eligible species. The forest edge and Moonveil also shelter low-tier shadow, ice, wind and water creatures; the volcanic Cinderreef shelters fire creatures. This retains a normal wild habitat for every eligible catalog species, including Dragon and Loom Fragment. A saved hunt goal remains readable if future catalog changes remove its habitat.

## Battle and reward interfaces

`build_pve_beast_state()` uses catalog base stats plus `utils.progression.companion_growth()`, including inherited growth for evolved forms. It clamps opponent levels to 1–50. It does not copy player gear into wild opponents.

`encounter_difficulty(actor, enemy)` compares the prepared battle stats with a power estimate:

`max_hp + 3 × attack + 2 × defense + speed + 0.25 × max_mana`

The enemy-to-companion ratio selects trivial below 0.40, easy below 0.70, normal below 1.30, hard below 1.80, and boss otherwise. This is a reward estimate, not a victory predictor; type effects, moves and tactical choices still affect combat.

Expedition battles and NPC spars award companion EXP once through `_run_pve_battle()`, calling `encounter_beast_xp(companion_level, enemy_level, difficulty=..., effort=...)`. The shared baseline is `22 + 4 × companion_level`, with actual-stat difficulty multipliers. There is no second numerical level-gap penalty: a weaker opponent's lower stats already reduce its difficulty reward. A clean win requires an actual enemy knockout and a surviving player companion. Losses, draws and time-limit outcomes receive 25% effort; a time-limit lead never grants a wild capture or clean-win quest event. The former personality-dependent `16 × enemy_level` award and challenge rarity multiplier are removed.

Trainer awards call `encounter_trainer_xp(trainer_level, enemy_level, difficulty=..., effort=...)`, with baseline `52 + 16 × trainer_level`. Challenges and spars use prepared stat difficulty. A completed expedition starts with 55% of a normal award; a clean optional battle upgrades that to one full encounter award at the saved enemy's difficulty. Studying adds 15% of the saved base exploration award. Earlier regions remain useful for supplies and collecting, while stronger companions receive less combat EXP there.

An expedition rolls and saves its encounter level once. Both a friendly discovery and a clean battle capture retain this level on settlement. Tower Trials do not grant captures. Saved legacy encounters without a level use the destination's lower bound. Normal exploration still grants at most one material and one companion per trip, with existing transaction/revision protections.

After rolling normal gathering choices, `expeditions.begin()` appends valid missing story-commission materials returned by `story.quest_material_options(db, user_id, base_region)`. This keeps active Chapter 3/7 Ember gathering predictable without granting additional materials or changing ordinary material rolls.

Rarity promotions are limited to eligible tiers at the chosen depth. Ancient Compass does not consume itself when it cannot leave a valid higher tier, and incense remains charged when no eligible promotion can change the odds. In the Loom, Prism Key adds 15 percentage points to Divine odds and takes precedence over Divine Compass, which adds 10; the result is capped at 90%.

Exploration and spars retain their existing between-fight HP/MP rules. [Tower Trials](TOWER-TRIALS.md) carry one saved team through a fixed level ramp, with one camp and one scaled reward settlement; their shared cooldown preserves the former challenge reward rate over time.

Gear uses the same trainer requirement to craft and equip, via `recipe_level()`. Already-bound legacy gear stays owned and visually equipped; `prepare_state()` suppresses its effects until the trainer qualifies. Dormant labels in the workshop, gear panel and companion journal explain that state. This single preparation path covers wild fights, spars, PvP and raids.

Normal and ranked PvP share `utils.pvp_rewards.award_match()`. One transaction grants both participants' rewards and records permanent match keys, UTC opponent claims, and per-player daily EXP totals. Each trainer can receive at most 60 companion EXP and 80 trainer EXP per UTC day across all companions and both modes. Only the first clean-knockout match against an opponent that day grants progress or gold; time-limit leads and draws grant neither. PvP applies its final budget amounts directly, so Scholar/Library bonuses cannot exceed those caps. Replaying a completed match on a later day cannot grant it again.

## NPC practice

Spars retain the existing atomic, once-per-NPC UTC daily claim. They use fixed partners and levels rather than multiplying the active companion's level.

| NPC | Trainer requirement | Partner and level |
| --- | ---: | --- |
| Maren | 3 | Barkley 5 |
| Cael | 8 | Timestitch 15 |
| Sable | 15 | Cerberus 25 |
| Orren | 23 | Behemoth 35 |
| Archivist | 35 | Paradox 48; Chapter 5 also required |

The first awakening UI reads the catalog requirements: companion 15, trainer 8, bond 40 and happiness 70. The mutation path in `utils.gameplay` enforces the same requirements.

## Validation and pacing assumptions

`tests.test_world_progression` checks ten complete bands, nonempty weighted tiers, every eligible wild species retaining a habitat, base resource compatibility, relative power/overlevel rewards, actual-knockout rewards/captures, aligned wild/expedition companion EXP, preserved catch levels, NPC access and promotion/compass edge cases. `tests.test_distinct_biomes` adds legacy label resolution, unchanged budgets and resource families, ten distinct saved scenes, crossing outcome parity, route-memory clues, old in-flight buttons, restart persistence, stale-click damage protection, optional helper safety and regional story notifications. Database cases use disposable databases.

The earlier reward-only route check assumed clean victories and is not a calendar-pacing benchmark. Actual battle journeys in `scripts/audit_progression.py` use production combat, prepared stats, legal earned gear, encounter sampling and reward helpers. Use their measured win rates and elapsed-day distributions to evaluate pacing; a count of hypothetical clean wins does not establish a casual awakening schedule.
