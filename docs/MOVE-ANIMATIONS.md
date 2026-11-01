# Move animations and retreats

The approved battle overhaul applies to exploration, NPC spars, challenges,
trainer/ranked duels and cooperative raids. It reuses the existing portraits,
realm backgrounds and Field Journal HUD.

## Move coverage

`data/move-animations.json` explicitly assigns all 368 species/move entries
(358 distinct names across 92 creatures) a presentation profile. The current
renderer uses 66 authored sequences with staged preparation, contact, release
and recovery. Each creature's four moves select different sequences. Physical
paths include lunges, thrusts, leaps and grounded casts; divine moves assemble
clock fractures, accretion lenses, constellation seals, creation facets and
spectral courts. Literal motifs preserve arrows, feathers, spirit tails and
other creature details. The original family fields remain for mechanical
targeting and safe fallback effects. See [Battle choreography](BATTLE-CHOREOGRAPHY.md)
for the profile schema and [the replayable comparison](choreography-preview/index.html)
for production-renderer before-and-after clips.

The assignment follows the actual move effect as well as its name and creature
theme. Rewind Nibble restores guard/mana and readies the next attack; it does
not look like a damaging bite. Glitter Bounce heals and cleanses. Ice Shell
forms around the opponent it freezes. Twine's Borrowed Moment is a signature
attack; Timestitch's move of the same name is a defensive rewind. Nicknames
and bonus-action suffixes retain the canonical animation.

Combat remains authoritative. Animation only receives immutable before/after
snapshots and recorded action metadata. It never rolls damage, changes stats,
spends mana, advances status effects, or calculates rewards. HP/MP and status
updates align with the effect's impact; damage numbers have an outline for
contrast on bright snow and flame backgrounds. Unknown temporary actions keep
a safe visual fallback.

## Retreats

A finishing hit completes its normal impact and follow-through. The defeated
creature then turns away and moves offstage, with stepping/bobbing movement
for grounded creatures and drifting movement for wisps. Flying/ethereal raid
bosses rise away; grounded bosses leave laterally. Whole-body aspect ratio is
preserved. A final PNG and later status frames keep departed creatures offstage.
Revival and healthy swaps bring an upright companion back from the edge.
Failed/timed-out raids also withdraw the remaining party.

Basic duel actions last 1.65 seconds, skills 1.95, guard/healing 2.1,
ultimates 2.65 and status ticks 1.1. A new knockout adds a separate 1.4-second
retreat segment. The next 45-second choice window still starts after accepted
upload and playback, with the existing 0.2-second allowance.

Ordinary raids use concurrent 36-frame, 1.2-second clips and independent
trainer cooldowns. New knockouts or a closing party withdrawal add 1.8 seconds
to that clip. Raid events retain move name, species and slot so a batched action
does not use another companion's animation after a swap. Frozen/skipped turns
do not invent an attack, and harmless solved patterns do not show a boss strike.

## Limits, validation and deployment

The same two bounded, cancellation-safe rendering workers and six-second
preparation deadline serve both modes. Moves, retreats, swaps, assists and raid
clips now sample at 30 FPS, with their existing playback lengths. Their frame
counts follow the full clip duration, including departures. GIFs play once and
hold the resolved final state; identical held poses may share an encoded frame.
Hexpurr's ambient aura alone loops (60 frames over two seconds), without delaying
controls. All clips share the same encoder, palette handling and 4,000,000-byte
attachment ceiling. Quantizing each frame as it is drawn avoids retaining a full
RGB clip in memory. GIF delta compression preserves decoded pixels while reducing
upload size. Timing distributes 30/40 ms delays evenly in GIF's 10 ms units,
preserving each clip's total duration without a large final-frame remainder.
Failed/busy rendering and rejected attachments retain the existing text controls.
Native warning publication, early interaction acknowledgement, settlement and
reservation cleanup retain their existing paths.

No Railway variables, external services, art downloads, database migrations
or dependencies are added. Discord controls client GIF autoplay; tests use
offline rendering and the real component dispatcher with synthetic HTTP.

Run the regressions and export actual game clips without a bot token:

```sh
python -m unittest tests.test_animation_cadence tests.test_move_animations tests.test_battle_art tests.test_raid_art tests.test_field_journal -v
python scripts/preview_move_animations.py --output battle-overhaul-output --audit-roster
python -m unittest discover -s tests -v
```

The export includes the ten approved move examples, both retreat directions,
all six ten-trainer raid scenarios, a boss departure, GIF metadata and local
render/event-loop measurements. `--audit-roster` additionally renders every
species/move entry and records its frame count, duration and attachment size.
Generated clips are review outputs and do not need to be committed or deployed.

The [30 FPS validation summary](battle-fps-validation.json) covers the 364 move
entries available before Raikyr, plus 67 additional background and crowded-raid scenes. The largest
checked attachment was 2,556,525 bytes. Two simultaneous heavy renders completed
in 1.50 seconds locally, with event-loop p95 delay of 6.76 ms. These measurements
exclude live Discord transport/client playback and Railway performance.

The earlier [recorded export](move-animation-validation.json), before the 30 FPS
upgrade, rendered all 344 move
entries; the largest duel clip was 1,240,788 bytes. All six ten-trainer scenes
fit the file ceiling, including extra checks with ten simultaneous ultimates
and ten-companion wipes (largest 1,615,924 bytes). The 19 representative local
renders took a median 430 ms and maximum 655 ms; the event-loop heartbeat had
a p95 delay of 7.3 ms. These are local measurements, excluding Discord uploads
and client playback. The focused 57 animation/UI/dispatcher regressions passed.
