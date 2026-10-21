# Tower Trials

`/challenge` opens a free biome selector, also available from the Field Journal's Adventure actions. Begin commits one randomized climb using the active companion and up to two healthy assigned exploration reserves. One or two members may enter with a difficulty warning. The selector, equipped team, wave fights, result and Back navigation use the same Discord message.

## The climb

| Biome | Enemy levels | Waves | Midpoint miniboss | Camp after wave |
| --- | --- | ---: | ---: | ---: |
| Whispering Woods | 1–5 | 5 | — | 3 |
| Moonveil Marsh | 6–10 | 6 | — | 3 |
| Ember Wastes | 11–15 | 7 | — | 4 |
| Sunforge Dunes | 16–20 | 8 | 4 | 4 |
| Glacial Hollows | 21–25 | 9 | 5 | 5 |
| Stormglass Peaks | 26–30 | 10 | 5 | 5 |
| Sunken Abyssal Trenches | 31–35 | 11 | 6 | 6 |
| Cinderreef Caldera | 36–40 | 12 | 6 | 6 |
| Celestial Loom | 41–45 | 13 | 7 | 7 |
| Starfall Gardens | 46–50 | 15 | 8 | 8 |

Every final wave is a guardian drawn from a reviewed biome pool. Trainer access gates remain the exploration gates. Enemy levels rise through the biome's fixed band; they do not increase when a player equips stronger gear. Each wave has one opponent and uses the existing attack, skill, guard, ultimate, Swap and Assist rules.

An independent seed produces the full lineup at entry. The saved plan includes species, level, profile, hints and resolved enemy templates. It survives restart and catalog changes. Normal draws respect the biome's eligible species and rarity ceiling, exclude inappropriate passive combinations and limit repeats: no adjacent duplicate, at most two appearances per species, no adjacent healing/control specialists and at most `ceil(waves/4)` hard-control enemies. Miniboss and guardian pools are separate; a run's midpoint boss cannot also be its final guardian.

Enemies may heal once or apply freeze/sleep once per wave. Move effects and species passives remain visible. Ordinary waves use shorter combat budgets than bosses. Saved power ceilings reduce large species-stat swings within a role; gear and training still improve the team's actual chances. See `utils/trial_rules.py` and `utils/trial_combat.py` for the versioned configurations. An undefeated enemy at the round limit ends the attempt; an HP lead cannot clear it.

## Team resources and stopping

- HP, MP, conditions, ultimate charges, guard/focus, passive counters, entry flags and the Assist meter carry across waves. Opening effects apply once per participating member, and each new opponent gets its own opening. Queued trainer preparations enter with the lead once. Genesis Spark's entry mana is available once without granting a persistent refill merely for entering and leaving.
- One automatic midpoint camp restores 25% maximum HP and MP to survivors and clears their conditions. It cannot revive a knocked-out member. Knockouts remain unavailable throughout the attempt, even if their ordinary recovery timer elapses.
- Up to three owned care items may be used between waves. Current supported supplies are Pixie Dust Potion, Guild Elixir and Aether Tonic. Effects and consumption match their catalog definitions. Revives, permanent growth, queued battle boosts and cooldown resets are unavailable inside a run; other commands cannot bypass these limits.
- Between waves, Continue advances, Pause saves without paying, and Retreat & bank ends the run with its earned reward. A paused team remains reserved. Reopening resumes the exact state; it does not reroll or refresh resources. An unfinished run expires after 24 hours and banks its earned progress.
- A missing move after the full 45-second decision window ends the climb. The deadline starts after the prompt/clip upload and cannot be extended by refreshing. A technical interruption preserves the committed action cursor and pauses the fight for explicit resume.

The panel shows all three companions, the next enemy's level/type and tactic, wave progress, camp/item availability and earned/full-clear reward values. The guardian becomes visible by the midpoint. Older action text belongs to the Trial log. Enemy arrival, retreat, equipped swaps and assists use the normal battle renderer against the selected biome. A failed attachment falls back to text; a confirmed deleted message may be replaced without changing progression.

## Rewards and cooldown

Let B be one existing challenge-equivalent gold/XP allowance at the final level. Its XP difficulty uses the strongest prepared member, so a weak lead cannot inflate the reward. Gold, entry levels, reward parameters and normal bonus eligibility are frozen when the run begins.

| Waves | Full reward | Shared cooldown | With Chronos Hourglass |
| --- | ---: | ---: | ---: |
| 5–7 | 1.5 × B | 30 minutes | 24 minutes |
| 8–11 | 2 × B | 40 minutes | 32 minutes |
| 12–15 | 3 × B | 60 minutes | 48 minutes |

Cooldown starts at entry and belongs to the trainer. Switching biome or run length cannot shorten its saved ready time. A live run must settle before another begins. These ratios preserve the former challenge's long-run reward rate, rather than imposing a strict rolling-hour cap. A Chrono-Biscuit can reset the cooldown only outside an active trial. A remaining legacy challenge timer is honored on upgrade.

Earned rewards grow to 30% at the camp, 50% before the guardian, and 100% for a surviving-team knockout victory over the final guardian. Retreat, defeat, timeout or expiry settles the earned fraction once; trying the guardian does not confiscate progress. Zero cleared waves pays zero. Reserve XP is bounded using each member's own entry level before the run multiplier is applied, with normal contributed/idle shares and bonuses. There are no per-wave gold, XP, items or quest-win payouts.

One full clear grants one battle win and a personal biome stamp; later clears can improve the best action count. Stamps add no combat stats or extra currency. Tower opponents cannot be caught, and entry does not consume incense, capture boosts or compass charges. Wild captures remain in exploration. No new species or Divine collection is added by this update.

## Persistence and validation

`trial_runs` stores the full battle snapshot, plan, RNG cursor and revision. A writer transaction validates owner/current controls before every accepted decision, care use or transition. It commits resources and state before Discord playback. Settlement writes rewards, wins, story progress and stamps in that same transaction. Keyed auxiliary reporting can retry without paying again. Startup preserves interrupted fights, reloads persistent team reservations and retains the same enemy/turn state.

Relevant regressions are `test_trial_combat`, `test_trials`, `test_trial_panel`, `test_trial_guards` and `test_trial_entry_art`. The production-core audit in `scripts/audit_tower_trials.py` checks seeded lineups and strategy replays; its synthetic results are not player telemetry or measured Discord session duration. The [actual panel and animation gallery](tower-preview/index.html) is separate from the earlier design-only visualization.
