# Party PvE balance, version 2

New adventures should reward a prepared, varied team and attentive decisions.
Repeating attacks can still win a favorable matchup, but costs more health and
can leave companions knocked out. Towers are longer endurance challenges.
Powerful gear, training, bonds and advantageous types retain their value.

## Encounters

Adventure levels use the strongest **departing** companion, clamped to the
destination's fixed band. This applies to hostiles and the single recruitment
encounter. A weak lead cannot lower the level while a stronger reserve waits
behind it. Earlier destinations remain easier for an advanced team. Carnival
continues using its frozen strongest-member level and seasonal normalization.

Enemy HP and attack budgets use the departure roster size. A knockout does not
lower later enemies' stats. Budgets grow more slowly than the additional health
available to a larger team; adding a companion should remain useful. Gear and
training never raise enemy stats. Solo Woods encounters at levels 1–5 keep their
previous budgets and enemy decisions.

Each cell below is the per-opponent **HP / attack** multiplier against that
species' ordinary level-scaled combat state. Defense, speed, moves and passives
remain species-specific. HP-dependent shields follow the HP adjustment.

| Departing companions | Single | Each of two | Each of three |
| --- | --- | --- | --- |
| 1, beyond onboarding | 0.60 / 0.72 | 0.36 / 0.54 | 0.26 / 0.50 |
| 2 | 0.85 / 0.82 | 0.52 / 0.66 | 0.38 / 0.60 |
| 3 | 1.10 / 0.92 | 0.66 / 0.74 | 0.48 / 0.68 |

Full-team pairs now have a combined 1.32 ordinary-enemy HP budgets and triples
1.44, compared with 0.96 and 1.02 previously, before species differences. Their
attack pressure also increases. These are sequential opponents using the
existing battle controls, animations, resource carryover and one reward bundle.

Beyond the Woods, hostile species follow regional rarity weights, subject to
avoiding consecutive duplicates and hard-control species. Ordinary Divine foes
use the reviewed tower pool, excluding revival/absorption/bonus-chain outliers.
Rare recruitment tickets keep their original species and odds. Seeing a rare
hostile does not make it recruitable or award another drop.

## Decisions and towers

Version 2 enemies choose using visible active-fighter state. They finish a safe
knockout, defend against available ultimates or focus, preserve ultimates against
guard, and use applicable conditions. Decisions do not inspect the player's next
action, reserves, future enemies or the combat RNG. Foes retain the existing
one-heal and one-freeze/sleep-move limits per wave. Guard is limited to twice per
foe and cannot be repeatedly refreshed while its focus remains unused.

New towers retain their fixed level bands, species-composition constraints,
power ceilings, 5–15 waves, camp and three-item allowance. Per-role HP / attack /
defense multipliers are 0.50 / 0.68 / 0.85 for ordinary foes, 0.95 / 0.90 / 1.00
for minibosses and 1.20 / 1.05 / 1.00 for guardians. Towers are fixed challenges
designed around a team; bringing fewer companions does not shrink their lineup.

Adventure foes allow 20 rounds (16 for unchanged solo onboarding); tower limits
are 10 / 14 / 20 for normal / miniboss / guardian. These ceilings allow defensive
turns but still end a stalled fight. The existing decision deadline is unchanged.
Companion stats, move costs, item limits, recovery, drops and reward formulas
are unchanged. Trial XP still uses the existing difficulty calculation against
the final guardian, so its quote can reflect the guardian's increased strength.
PvP, ranked, spars, cooperative raids and their AI are outside this change.

## Saved runs and validation

New adventure departures save `combat_balance_version=2` and a frozen
`encounter_reference_level`. Saves without them continue using version 1 for
future encounters. Existing enemy templates remain authoritative. New trial
plans save version 2 and per-wave `tactics_version`; missing tactics metadata
selects the original AI. Nothing upgrades a running fight, refills a team or
reissues a reward. Later balance revisions must preserve these saved versions.

Reproduce the paired production-core audit with:

```sh
python scripts/audit_party_rebalance.py --seeds 20 --output docs/party-rebalance.json
python scripts/audit_hollowlight_combat.py --trials 20 --starter-trials 100 --output docs/party-rebalance-carnival.json
```

The [paired results](party-rebalance.json) cover all ten biomes, bare/prepared
builds, solo/duo/full teams, entry/upper-band levels, legendary/Divine teams,
Hexpurr and Raikyr. The two policies are basic attacks plus available ultimates,
and a visible-state heuristic that also uses type-aware swaps, defense, healing
and assists. The same route seeds and builds are tested against both versions.
Changed hostile selection intentionally changes species between versions.

Both policies are fallible. These fixed-build samples measure completion,
knockouts, remaining HP, actions and participation; they are not universal player
win rates. Prepared gear is checked for access, not acquisition time. The audit
includes intentionally poor matchups and underprepared teams. In particular,
late towers can defeat neutral-only or lower-rarity lineups even at the correct
level. Team composition and preparation matter alongside level.

In the 200 prepared entry-level adventure samples (20 seeds in each biome):

| Policy | Version 1 clears | Version 2 clears | V1 clears without KOs | V2 clears without KOs |
| --- | --- | --- | --- | --- |
| Attack + available ultimate | 200 | 184 | 131 | 54 |
| Tactical heuristic | 199 | 198 | 199 | 179 |

For the corresponding 200 tower samples, attack-only clears fell from 181 to
159. Tactical clears rose from 185 to 191 with more room for defensive turns,
while clears without knockouts fell from 165 to 140. Thus the increased threat
is visible in attrition even when a prepared team wins. These aggregates cover
the stated checkpoint rosters, not every possible team.

The [Carnival sample](party-rebalance-carnival.json) includes 400 fresh solo
starter routes and 260 team/checkpoint routes, with actual story interludes.
[Decision timing](party-rebalance-decision-timing.json) measures only local AI
CPU work; it excludes animation rendering and Discord upload/client delays.
Existing historical balance reports describe their earlier rule versions.
[Legacy parity checks](party-rebalance-legacy-parity.json) additionally compare
1,200 adventure encounter plans and 960 tower waves against merged main,
confirming unchanged version 1 enemy templates and round budgets.
