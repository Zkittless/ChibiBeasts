# Tower Trials and native companions — proposal

**Design proposal: tower gameplay and new species are not enabled yet.**

Rework `/challenge` into a randomized biome tower. Take the saved three-companion exploration team through consecutive enemy waves, manage its remaining resources, and face a guardian on the final wave. Exploration keeps discovery, routes and catches; towers test sustained team play; raids remain multiplayer encounters.

## One attempt, one team, one panel

`/challenge` opens a biome selector inside the journal flow. Show wave count, recommended companion band, equipped team, cooldown and reward policy before **Begin tower**. Three companions are the intended team, but allow one or two healthy owned companions with a clear difficulty warning. Do not lend temporary beasts or reduce rewards for a smaller party. One beast fights at a time with the existing **Swap / Assist** system; all three remain visible. Each wave contains **one enemy**, not a simultaneous enemy group.

Opening, choosing a biome and Back spend nothing. Starting freezes the selected companion IDs and rolls the entire attempt once. The battle, between-wave summary and final result update the same tower panel; **Back to trials** restores the biome selection and **Back to journal** returns to the existing journal. During a live run, Back may pause at a safe wave boundary; it never abandons or rerolls silently. Do not create a battle/result message per wave or require rerunning the command.

## Wave policy — starting numbers for simulation

Counts are fixed and advertised; enemy species, permitted behaviour profile, miniboss and final guardian change between attempts. Companion bands and trainer access gates remain the existing progression bands. Enemy level on wave `i` of `N` is `low + floor((high - low) * (i - 1) / (N - 1))`: a fixed low-to-high curve, never a level increase in response to a stronger player's team.

| Companion band | Biome | Waves | Midpoint miniboss / camp | Random final guardian examples |
| --- | --- | ---: | --- | --- |
| 1–5 | Whispering Woods | 5 | Camp after 3; no miniboss | Slime / Goblin |
| 6–10 | Moonveil Marsh | 6 | Camp after 3; no miniboss | Will-o-Wisp / Kelpie |
| 11–15 | Ember Wastes | 7 | Camp after 4; no miniboss | Hellhound / Wyvern |
| 16–20 | Sunforge Dunes | 8 | Wave 4; camp afterwards | Basilisk / Cerberus |
| 21–25 | Glacial Hollows | 9 | Wave 5; camp afterwards | Glacius / Yeti |
| 26–30 | Stormglass Peaks | 10 | Wave 5; camp afterwards | Thunderbird / Yeti |
| 31–35 | Abyssal Trenches | 11 | Wave 6; camp afterwards | Hydra / Kraken |
| 36–40 | Cinderreef Caldera | 12 | Wave 6; camp afterwards | Leviathan / Dragon |
| 41–45 | Celestial Loom | 13 | Wave 7; camp afterwards | Astraea / Terminus |
| 46–50 | Starfall Gardens | 15 | Wave 8; camp afterwards | Zodiac / Chronos |

Guardian species above exist in `data/beasts.json`; these are proposed selection pools, not new collectible forms or implemented Trials NPCs. The miniboss is independently drawn from a curated biome pool, excluding that attempt's final species. Give it a local nickname and show its real moves/tactic. Each guardian pool needs comparable effective threat before inclusion: rarity alone is not a balance budget.

**Provisional enemy profiles:** build legal species stats at the saved wave level, then ordinary waves use 45% HP, 65% attack and 85% defence; minibosses use 80% HP, 85% attack and 100% defence; final guardians use 100% of each. Round down, minimum one; speed, mana, moves and type rules stay unchanged. Rebase any percentage stat-growth reference to these adjusted stats so a passive cannot undo the profile. Apply no further rarity/boss multiplier. Normal waves should take roughly 2–4 player actions, not a full duel. Proposed round ceilings: 6 ordinary, 10 miniboss, 16 final. An undefeated enemy at the ceiling ends the climb; an HP lead never clears a wave.

These are balance hypotheses. **Unmeasured pacing targets:** 8–12 minutes for the early towers and 20–30 minutes for the longest, including ordinary decisions. Actual production-engine replays and Discord upload timings must establish feasibility. If regular waves routinely reach the cap, lower their burden or revise counts; do not ship fifteen full-length duels and call it a quick activity.

## Randomized, with fair limits

Generate and save an independent attempt seed **and the resolved enemy plan**, config version, levels, profiles and reward envelope at launch. Reopening, pausing, a crash, inventory changes and changing the command's biome cannot reroll it. A fresh paid attempt generates a new plan. Combat outcomes remain authoritative saved actions, not reasons to regenerate the enemy lineup.

Ordinary species come from curated subsets of that destination's existing eligible wild pool, using its rarity weights after exclusions; never exceed the band's existing rarity ceiling or introduce a species merely because its type fits. No starters, evolution-only, egg-only, Ancient, Corrupted or developer beasts. Miniboss and guardian draws use separate reviewed pools. Native proposals below stay excluded until fully approved and implemented.

Starting composition limits: no adjacent duplicate species; at most two appearances per species when the eligible pool supports that count; at most `ceil(N / 4)` hard-control enemies across the **whole** plan; no adjacent healing or hard-control specialists. One enemy may use at most one heal per wave. Ordinary profiles cannot repeatedly apply freeze/sleep; a hard-control miniboss/guardian gets at most one such application that wave. All remaining choices use its existing moves. Do not suppress a declared species passive invisibly: exclude unsuitable passive combinations from that profile's pool. Validate effective-power ranges, not just names, before admitting a candidate; reject a generated plan that breaks its budget rather than accepting an impossible matchup. Backtracking through the finite candidate list must terminate with a reviewed fallback plan.

Preview the next enemy's level, type and notable condition before **Next wave**. The final guardian is selected at launch and revealed at the midpoint; reveal it at launch for the shortest towers if helpful. Telegraph its actual tactic before a decision. Examples already in the catalog include Hellfire Dash's burn, Thunder Clap's blind, Deep Current's slow and Regen Scale's healing. Glacius's Ice Shell freezes one action: guarding reduces hit damage, does not prevent freeze, and is consumed by that hit. Zodiac's Constellation Charge adds 5% attack/speed per action up to 40%; it must fit the final-wave budget rather than receiving additional boss scaling.

## Attrition, camps and safe stopping

- Enter with current HP/MP; no free launch refill. Carry **each member's HP, MP, conditions, ultimate charges, passive counters, one-use revival flags and remaining guard/focus**, plus the shared Assist meter and first-entry flags, across waves. Swaps/Assists keep their current costs. No automatic end-of-wave mana or healing, and no fresh opening shield or queued boost every floor. Benched conditions retain the current combat rules.
- Exactly one midpoint camp restores **25% of each surviving member's maximum HP and MP**, capped at its maximum, and clears its negative condition. A knockout stays out for the whole attempt; neither the camp nor elapsed recovery time brings it back. This is one saved claim, never a repeatable button.
- Allow at most **three paid HP/MP/cleanse item uses per attempt, between waves only**, with the existing item effects. No revive, growth treat, permanent-stat, cooldown reset or queued battle-boost item inside the run. Enforce this centrally so `/care` or `/use` cannot bypass the panel. Native healing/cleanse moves remain valid in combat; short wave limits bound stalling.
- After a cleared wave choose **Continue**, **Pause**, or **Retreat & bank**. Pausing saves state but pays nothing and keeps the chosen companions unavailable for gear changes, training, trades, evolution or other battles. Resume the same wave plan and exact resources. Expire an unfinished run after **24 hours** using the voluntary-retreat settlement. Make this lock visible and keep Retreat easy to reach.
- Save every resolved action and wave transition, not just the midpoint. Restart mid-wave restores its enemy HP, team state, action cursor and committed item use; it cannot reset a losing wave. A technical outage pauses. Missing the existing 45-second move deadline is a retreat/defeat, not a free safe pause. Do not run a hidden overall wall-clock timer while paused.

## One scaled run reward, not fifteen payouts

Longer climbs should pay more than one current battle. Let **B** be one current challenge-equivalent baseline at the saved final level: its gold roll and current trainer/companion XP helpers, using the strongest prepared team member's difficulty against the selected final profile. Freeze these inputs at entry; an early lead knockout or changed active member cannot manufacture a difficulty bonus.

| Tower length | Full-run reward | New shared cooldown | With Hourglass |
| --- | ---: | ---: | ---: |
| 5–7 waves | 1.5 × B | 30 minutes | 24 minutes |
| 8–11 waves | 2 × B | 40 minutes | 32 minutes |
| 12–15 waves | 3 × B | 60 minutes | 48 minutes |

This deliberately replaces today's 20-minute challenge cooldown with a duration matching the chosen run's reward. It preserves the **same long-run maximum reward rate**: three baseline budgets per hour, or 3.75 with Hourglass, before existing paid reset items. It is not a strict rolling-hour cap, especially when switching tower lengths. Save one trainer-wide `ready_at` when an attempt begins; selecting another biome or shorter tower cannot recalculate it. Starting another tower also requires the active run to settle. Time Shard may retain its current paid reset only outside an active run; that remains an explicit exception to the ordinary cooldown rate. A preparation failure before any battle action refunds its unused reservation safely.

For party XP, compute each companion's **baseline split first** with the current 35% / participating 50% reserve share, own-level cap and bounded catch-up. Apply the run multiplier to that bounded baseline, then progress and normal Scholar/Library bonuses once. Do not feed a tripled lead award into an unmodified one-battle reserve cap and accidentally leave reserves at their old payout. Accumulate participation across the run. Freeze entry levels, gear and reward parameters; retain the normal level-50 limits.

**Earned progress:** let `c` be cleared waves and `M = ceil(N / 2)` the camp wave. From zero through `M`, the earned fraction is `0.30 * c / M`. From `M` through `N−1`, it is `0.30 + 0.20 * (c−M) / (N−1−M)`. A clean final-wave victory raises it to `1.00`. Thus the midpoint earns 30% of the whole run reward, reaching the guardian earns 50%, and defeating it earns the remaining 50%.

Retreat, expiry or defeat settles that earned fraction **once**, rounded down separately for each gold/XP value; zero cleared waves pays zero. Progress already earned is not confiscated by attempting the guardian. **Retreat & bank** ends the run; **Pause** pays nothing and permits resuming. This rewards time spent without making farming the early floors more profitable than completing a tower. There is no additional loss consolation payout.

No wave directly grants gold, XP, bond, materials, quest-win events, ultimate completion bonuses or loot. One clean full clear counts as one battle/story win and one first-clear cosmetic stamp at final settlement. No extra payout for a stamp, party size or replay record. Guardians and ordinary tower enemies are not capture rolls: say so before entry, consume no catch boosts/incense/compass, and keep wild acquisition in exploration. Actual reward rates, partial-run strategies and paid reset costs still need the economy simulation; these are proposed starting numbers, not proven pacing.

## Implementation boundary and acceptance criteria

The combat primitives, tag/assist animation, proportional HP/MP write-back and atomic party XP already exist in `utils/team_combat.py`, `utils/duels.py` and `utils/adventure_party.py`. Seeded saved choices, version checks and settlement receipts have a model in `utils/expeditions.py`. **The current runner is one enemy, closes all states and persists at its end; the PvE wrapper also pays/records a result each invocation. Looping that wrapper fifteen times would duplicate rewards and reset run state.** Current persistence does not retain a full mid-battle tower snapshot, and in-memory fighter reservations do not protect a paused run after restart. Towers need a dedicated persistent coordinator and mutation guards, reusing combat rather than inventing another battle system. Multi-enemy waves are outside the first implementation.

Use one message with a compact `Wave 6/12 · next opponent · team HP/MP · camp/items remaining` header. Collapse older action text into the existing battle log. Animate the current enemy withdrawing and the next entering on the biome's background, with actual armored party art and existing tag/assist clips. Keep controls live through rendering, retain text fallback, and never replay every completed wave when resuming.

Before enabling: run at least **1,000 saved seeds per biome** to check every composition/level rule, then production-engine strategy replays for legal entry-level and strong three-member teams, plus 1–2 member onboarding cases. Measure completion, action counts, resource loss and render/upload sizes; no claimed win rate before this audit. Exercise all ten towers with each guardian candidate. Verify no reset of entry effects or KOs, one camp/three item claims, one settlement, bounded baseline party shares, long-run reward/cooldown rates and tier-switching, exact seeded resume, stale/foreign/double clicks, concurrent item use, deletion/upload failure and restarts on both sides of every action/settlement commit. A guardian, seed or passive that breaks the budget must be removed or tuned before release.

## Native shortlist

Add a native only when its concept earns the full art and integration cost. There is no one-beast-per-biome quota. The four candidates below fit their bands' current rarity weights and accepted types; none adds a Divine, collection, rarity or combat system. Exact stats and move numbers remain undecided. Rarity odds stay unchanged: adding a species redistributes that tier's pool, which must be disclosed and audited for wild and Nursery outcomes.

| Biome / band | Proposed native | Silhouette and personality | Existing combat role | Optional exploration moment |
| --- | --- | --- | --- | --- |
| Moonveil Marsh / 6–10 | **Wicknewt**, Uncommon · Water | A slender, solid-bodied reed newt with four small toes, an upright paddle-tail and a warm amber belly lantern. It patiently waits until every traveller has crossed. | Low attack, dependable healing/cleanse and mana recovery; a modest support companion using current move effects. | Its belly light reveals safe stepping stones through the fog. Any water companion can help with the crossing; following the marked detour is always safe. |
| Sunforge Dunes / 16–20 | **Glazeback**, Rare · Fire | A low, six-legged dune beetle with a broad glass carapace and a visible amber heat core. It collects warm pebbles and pushes the best one toward a friend. | Slow guard/focus bruiser; it stores heat visually while using the existing guard-then-attack rhythm. No new heat meter or reflected-damage rule. | It warms a cold trail marker to reveal an old route. Other fire companions or the ordinary sheltered path solve the same stop. |
| Cinderreef Caldera / 36–40 | **Kilnclaw**, Epic · Water | A squat warm-vent hermit crab with a chimney-shaped coral shell and one broad mitten-like pincer. It carries a warm pebble for anyone who looks cold. | High-defence sustain companion with a normal healing/cleanse move and a guarded strike. No shell replacement or counterattack subsystem. | It finds a cool route between steaming tide pools. Water companions can reveal the route; waiting for the mist to clear is the safe alternative. |
| Starfall Gardens / 46–50 | **Petalume**, Epic · Light | An upright lantern moth with four rounded petal wings, brush antennae and a small luminous thorax. It folds its wings around lost fireflies until they are ready to fly. | Fragile healing/cleanse support with a reliable attack; standard bounded healing Assist, no resurrection or party-wide passive. | It settles luminous pollen over faded path marks. Light companions can help; tracing the garden's existing stone border remains available. |

These moments stay within the expedition's existing choice and reward budget. Owning the named native never unlocks an exclusive material, catch roll, extra stop or mandatory route. Safe choices always allow normal completion. They are discoveries, not required exploration keys or reasons to replace a starter.

### Stormglass: choose one strong concept, or leave the biome to existing beasts

**Recommend Chimekid — Rare · Wind.** A compact, four-legged mountain goat with a stocky woolly chest, cloven glass-dark hooves and two short, hollow U-shaped glass horns. Wind makes the horns hum; it leans into a gale until it finds the right note, then waits very seriously for you to hum back. A grounded, medium-speed guard/focus and slow role uses existing moves, not a sound meter. Its horns help the team hear a lull between gusts; another wind companion or a sheltered marked climb solves the same route. Keep the horns blunt and unbranched, the body low, and the outline wingless. There is no goat or ram in the catalog; Minotaur is a humanoid bull, Zodiac a long-legged stag, and Rootwarden a horned drake. No new climbing or resonance system is needed.

**Alternative Echofold — Rare · Wind.** A tiny bat with enormous kite-shaped ears, a round face and wings that fold into a neat cape. It hangs from trail bells without ringing them, then looks offended when the wind does. Fast and fragile, with a normal attack, existing blind effect and guard; no sonar meter. It listens for a sheltered rock passage, with the same wind-companion help or safe climb available. The catalog has no standalone bat, although Manticore already has bat wings; keep the body compact and ears dominant. This is viable, but Chimekid adds more silhouette variety beside the existing flyers and proposed moth.

Drop **Gustling**: Harpy, Thunderbird, Rukh and Simurgh already crowd its storm-bird idea. Review Chimekid first, keep Echofold as an alternative, and add neither if the concept art fails to feel distinct. A new background and good encounters can carry Stormglass without another species.

### Catalog overlap and lore

- **Wicknewt:** Mudpup already occupies the round muddy amphibian silhouette; Nebuloom is a jelly salamander with flipper feet, stars and a crescent tail. Wicknewt must remain slender, opaque and reed-like, with toes and a vertical paddle-tail. Its honest guiding contrasts with Will-o-Wisp's cheerful misdirection. If that distinction is weak in the concept sheet, drop it.
- **Glazeback:** change the initial tortoise concept to a beetle. Permafrost is already an ice-shell tortoise and Atlas the cosmic turtle; a warm recolour would add little. Its six legs and glass wing cases must read clearly even in a small equipped portrait.
- **Kilnclaw and Petalume:** the current catalog has no named crab or moth. Preserve those new outlines instead of borrowing Kraken tentacles, Phoenix flames, Fairy anatomy or Zodiac's cosmic scale.
- Native descriptions should show an ordinary small idea finding a home in the Loom. They are companions, not trophies, missing gods or another Divine collection. Trial guardians step back after a respectful test; they are never slain. Follow [the lore naming and tone guide](../data/LORE.md).

### Complete support before adding a species

Approval would cover a full companion, not a catalog entry with borrowed art. Each native needs its own approved base portrait **and all sixteen fitted armor appearances**, with face, limbs and signature shapes preserved. Verify every armor at roster, Care/Gear, three-member team and battle sizes; equipment/runes and attachment/cache manifests must work normally. A shell or pair of wings is not a reason to omit equipment support.

Also define and validate its stats/moves, wild habitats and catch eligibility, appropriate Nursery/egg family membership and exact displayed odds, research pools, Bestiary/collection records, gear binding, trading/release value, animation anchors and existing combat/Assist rules. Explicitly declare an evolution policy: these start as single-stage species with no new evolution recipe or Divine promotion; future forms require separate complete support. Confirm no accidental starter, collection or story-unlock changes.

Do not ship partial companions or silently expand egg pools while only the exploration backgrounds are ready. The biome release can stand alone using the current roster. Review the strongest native concepts, then implement approved species with their complete assets and integration as a separate change.

## Next review

Review the randomized climb, camp/item limits and scaled reward policy alongside the preview; choose native concepts separately. Tower gameplay and new species remain proposed. Validate the run coordinator and economy before production implementation.

Current mechanics and constraints: [Adventure teams](ADVENTURE-TEAMS.md), [World progression](PROGRESSION-WORLD.md), [Nursery](NURSERY.md), `cogs/battle.py`, `utils/habitats.py` and `data/beasts.json`. The production catalog is authoritative where older lore notes disagree with implemented rarity or awakening requirements.
