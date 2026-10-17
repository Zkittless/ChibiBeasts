# Cooperative raid arena

The [move animation overhaul](MOVE-ANIMATIONS.md) adds explicit move profiles,
recorded move/species/slot metadata and whole-body retreats to this arena.

The current [Field Journal UI](FIELD-JOURNAL-UI.md) applies the approved fantasy
frames, fitted health values and a lower journal to this arena. Its review
clips show the current presentation; the samples below record the first arena.

Guild `/raid` and `/ancient` now place the boss above a circle of active
companions in one shared pixel-art scene. All one to ten trainer layouts are
supported. Numbered positions match the native trainer panel, which retains
each player's HP, mana, ultimate charge, damage, support and action recovery.
The boss health bar, phase marks, remaining time and Weave warning sit above
the scene so they cannot cover characters on a narrow screen.

`/sparr` already uses the animated duel battlefield and the NPC's home realm;
the same combat and presentation features remain available there.

## Realms and actions

| Boss | Arena background |
| --- | --- |
| Corrupted Leviathan | Abyssal Trenches |
| Corrupted Fenrir | Glacial Hollows |
| Corrupted Dragon | Ember Wastes |
| Ancient Chronos, Ancient Genesis, Ancient Abyss | Celestial Loom |

The renderer reuses the packaged creature portraits and approved realm art.
Swapping changes the actual companion artwork. Attacks use their assigned move
effects, ultimates charge before impact, guards show barriers, healing raises
particles, Weave sends threads toward the boss, and boss sweeps cross the party.
Defeated creatures retreat offstage until revived or replaced by a healthy swap.

Multiple trainers share a short 18-frame, 1.2-second one-shot GIF instead of
waiting through individual clips. Health changes at impact reflect immutable
snapshots from the real combat engine. Combined damage has one legible number;
individual totals remain in the trainer panel. Artwork never rolls damage,
consumes combat randomness or advances the encounter.
New knockouts and a closing party withdrawal use a 36-frame, 3-second clip
with a separate departure segment; active callbacks still do not await it.

## Responsiveness and failure handling

The existing 1.5-second display batching combines nearby actions. Combat
resolves immediately, each trainer keeps their own cooldown, and all seven
buttons stay registered during rendering and upload. Active callbacks never
wait for animation playback. Events arriving during an upload appear in the
next batch; a bounded 32-event buffer prevents an animation backlog.

New boss warnings interrupt obsolete render preparation. The native warning
is published first while retaining the previous image, then its full six-second
response window starts after Discord accepts the edit. A slower arena upload
does not shorten that window. Combat locks are released before rendering and
HTTP operations.

Raids and duels share two bounded worker threads. A canceled render retains
its worker slot until the thread actually finishes. Portraits and backgrounds
are cached; there is no unbounded task queue. Render preparation has the same
six-second timeout as duels and HTTP operations have 15-second deadlines.
Three unavailable render attempts disable art for that raid. A rejected file
upload retries the text panel with the same controls. Attachment names are
unique and their streams close on success, rejection and cancellation.

Victory settlement is saved before final artwork. The closing clip removes
controls, then a PNG holds the departed scene after that clip's duration and
the 0.2-second playback allowance. A
deleted panel, failed final render or canceled caller cannot undo or duplicate
earned rewards. Existing restart recovery, entry refunds and reservations
remain in place.

Discord controls GIF autoplay and cannot report individual client playback.
These clips provide best-effort animation after upload; users with autoplay
disabled still have native HP/resource text and action results. The game does
not pause the entire party for a client animation.

## Evidence and deployment

- [Four-trainer action GIF](raid-preview/team-clash-4.gif)
- [Ten-trainer action GIF](raid-preview/team-clash-10.gif)
- [All six bosses, desktop and 320-pixel layouts](raid-preview/realms-mobile.png)
- [Actual clip sizes and local performance](raid-preview/validation.json)
- [Fresh-seed balance replay](raid-preview/holdout-summary.json)

The recorded six ten-trainer renders took about a quarter-second each locally.
All twelve four/ten-trainer sample clips are below 1 MB and play once for
1.2 seconds. Performance figures exclude Discord upload and client playback.
The balance replay matches the previous runtime holdout exactly apart from
its timestamp: 576 prepared trials, median 6:04, 10th–90th percentile
5:14–7:35, and 98.96% wins. Another 96 behavior trials also match. This is a
synthetic model, not live player telemetry. Damage, boss budgets, cooldowns,
rewards and the ten-minute limit are unchanged.

All 219 local tests pass. The focused regressions use Discord.py's real
component dispatcher to check click acknowledgements during slow rendering
and uploads, concurrent players,
warning priority, bounded workers, distinct swaps, KO persistence, file
fallback and settlement after cancellation or deletion. They do not log into
a live Discord server.

No new Railway variables, services, schema migrations or runtime dependencies
are required. Deploy the source and existing packaged art together. The bot
uses **Attach Files**, **Embed Links**, and its normal channel permissions;
text fallback remains available if attachments are rejected.

Reproduce without a bot token:

```sh
python -m unittest tests.test_raid_art -v
python -m unittest discover -s tests -v
python scripts/preview_raid_arena.py --output raid-preview-output
python scripts/audit_combat_balance.py --output raid-balance-output --holdout-only
```
