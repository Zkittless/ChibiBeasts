# Adventure teams and branching expeditions

Open `/journal` → **Companions** → **Adventure team**, or **Adventure** → **Choose adventure team**. Your active companion leads; assign up to two reserves. One companion remains a valid team. There is no new trainer-level gate, and raid-party assignments remain separate.

## Battles

Wild challenges and expedition encounters use your chosen team. One companion fights at a time. **Swap** spends the current action; a healthy reserve automatically takes over after a knockout. Every member keeps their own health, mana, status, gear and ultimate charge during the fight. Each member's opening effects happen once on first entry. Repeated swaps do not reset resources or repeat opening effects.

Three successful damaging ordinary moves fill the shared assist meter. Guards, healing, ultimates, bonus strikes, swaps and assists do not fill it. Calling a healthy reserve costs the action, the complete meter and 12 of that reserve's mana:

- A companion with a healing move restores up to 10% of the active companion's maximum HP, also capped by the helper's HP budget.
- Earth and ice companions without healing moves guard the next incoming hit by 25%.
- Other companions deliver a modest typed strike capped against the active companion's strength.

Assists grant no ultimate charge and trigger no bonus turn. Opponents retain their existing actions and destination levels. The 20-round limit and 45-second decision deadline still apply. Mentor spars, player duels, ranked matches and cooperative raids retain their existing formats.

The lead keeps the established companion XP reward. Encounter difficulty considers the prepared team's strongest matchup, so a weak lead cannot manufacture a difficulty bonus while a powerful reserve wins the fight. Reserves receive 35%, or 50% when participating, bounded by their own level's normal XP budget. A teammate at least three levels behind the lead receives a small catch-up bonus, capped at 60%. Overlevelled reserves receive less XP from weak encounters. Existing scholar/library bonuses apply once. Party size does not multiply trainer XP, gold or catches.

The battlefield shows armored reserves, their health and the shared meter. A tag has separate exit and entrance phases; HP bars switch identity rather than interpolating between unrelated companions. Assists visit from the bench and return. Final stills and native text controls remain available when animation playback is disabled or an upload fails.

## Expeditions

New trips contain three meaningful choices, with an optional fourth discovery. Each region has its own trail, gathering area, landmark, obstacle, rescue and deep-discovery descriptions. Earlier choices determine later events: restoring markers reveals a crossing, studying a landmark reveals a hidden trail, and helping a traveller can reveal a sheltered entrance.

Companion types can solve regional obstacles. A safe detour is always available, so a particular species or type is never mandatory. Short camps immediately restore 5% maximum HP to healthy travelling companions, once per trip, and cannot revive a knockout. Careful crossings and deeper exploration cost a small amount of HP; travel damage cannot knock a companion out. A team entirely at 1 HP cannot choose the risky deeper route.

Return at any stop. Gold and trainer XP are proportional to completed stops; returning at the trailhead gives no rewards or exploration progress. The completed trip grants at most one material and one caught companion. Route bonuses are capped at 25% of the single expedition reward budget. Additional stops do not create additional payouts or reroll a catch. Story commission materials remain offered in the appropriate gathering area.

Noncombat trips grant a small team XP budget, scaled to destination level and earned progress. A trip that launched a battle does not also grant a second noncombat XP payout. Helpful choices grant the original lead's bond benefits, with reserves bounded to one bond and three happiness per trip.

The original charge budget remains: one charge per 30 minutes, banking up to three, with existing time perks. Starting consumes one charge; resuming consumes none. Team IDs, offered choices and outcomes save after every input. Team edits and travelling-companion evolution are locked until return. Duplicate, stale and foreign inputs cannot award twice. Interrupted encounters resume on a safe follow-up page. Old saved expeditions retain their original flow.

## Validation and previews

From the project directory, using the installed Python runtime:

```sh
python -m unittest discover -s tests -v
python scripts/audit_adventure_teams.py --output ../output/adventure-team/balance-validation.json
python scripts/preview_adventure_team.py --output ../output/adventure-team
```

The audit runs synthetic battles with production combat rules across all ten destination bands. Its win rates describe the stated test teams and policy, not live players. The preview script exports actual armored battle clips, final stills, contact sheets, a replay gallery and attachment-size/timing evidence. A live Discord smoke test is still needed for permissions, gateway routing and client playback timing.

Committed examples: [armored tag switch](adventure-team/tag-switch.gif), [healing assist](adventure-team/heal-assist.gif), [final still](adventure-team/tag-switch-final.png), [animation timings](adventure-team/animation-validation.json), and [720-trial balance audit](adventure-balance.json).

The migration adds only the adventure-team assignment table. Existing collections, gear, economy, raid parties and saved expeditions are preserved. Back up the live SQLite database before any deployment, as with the existing upgrade process.
