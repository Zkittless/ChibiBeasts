# Move animations and retreats

The approved battle overhaul applies to exploration, NPC spars, challenges,
trainer/ranked duels and cooperative raids. It reuses the existing portraits,
realm backgrounds and Field Journal HUD.

## Move coverage

`data/move-animations.json` explicitly assigns all 360 species/move entries
(350 distinct names across 90 creatures) a presentation profile. Profiles use
29 effect families with different geometry and movement: bites, slashes,
flame cones, thread snaps, ground roots, frost shells, waves, lightning,
clockwork, healing and defensive forms, among others. Each entry selects its
palette, body motion and a small variation in effect spread. Named motifs add
actual falling arrows, winged swarms, fangs, crystal lances, lotus petals and
expanding nova rings where they fit the move.

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

Ordinary raids keep their concurrent 18-frame, 1.2-second clips and independent
trainer cooldowns. New knockouts or a closing party withdrawal add 1.8 seconds
to that clip. Raid events retain move name, species and slot so a batched action
does not use another companion's animation after a swap. Frozen/skipped turns
do not invent an attack, and harmless solved patterns do not show a boss strike.

## Limits, validation and deployment

The same two bounded, cancellation-safe rendering workers and six-second
preparation deadline serve both modes. Duels use at most 44 source frames;
raids at most 36. GIFs play once, use one shared palette, and remain subject to
the existing 2,000,000-byte attachment ceiling. GIF timing distributes 10 ms
ticks evenly instead of holding a large remainder on the final frame.
Failed/busy rendering and rejected attachments retain the existing text controls.
Native warning publication, early interaction acknowledgement, settlement and
reservation cleanup retain their existing paths.

No Railway variables, external services, art downloads, database migrations
or dependencies are added. Discord controls client GIF autoplay; tests use
offline rendering and the real component dispatcher with synthetic HTTP.

Run the regressions and export actual game clips without a bot token:

```sh
python -m unittest tests.test_move_animations tests.test_battle_art tests.test_raid_art tests.test_field_journal -v
python scripts/preview_move_animations.py --output battle-overhaul-output --audit-roster
python -m unittest discover -s tests -v
```

The export includes the ten approved move examples, both retreat directions,
all six ten-trainer raid scenarios, a boss departure, GIF metadata and local
render/event-loop measurements. `--audit-roster` additionally renders every
species/move entry and records its frame count, duration and attachment size.
Generated clips are review outputs and do not need to be committed or deployed.

The [recorded export](move-animation-validation.json) rendered all 344 move
entries; the largest duel clip was 1,240,788 bytes. All six ten-trainer scenes
fit the file ceiling, including extra checks with ten simultaneous ultimates
and ten-companion wipes (largest 1,615,924 bytes). The 19 representative local
renders took a median 430 ms and maximum 655 ms; the event-loop heartbeat had
a p95 delay of 7.3 ms. These are local measurements, excluding Discord uploads
and client playback. The focused 57 animation/UI/dispatcher regressions passed.
