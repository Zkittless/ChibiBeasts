# Animated battlefields

The current [Field Journal UI](FIELD-JOURNAL-UI.md) adds framed portraits,
fitted in-bar numbers, separate ultimate charge slots and a lower battle log.
Its review clips supersede the earlier visual samples linked below.

`/explore`, `/challenge`, `/sparr`, `/battle`, and `/ranked` show both companions
on one pixel-art battlefield. The HP/MP/ultimate strip sits above the scene so
it cannot cover either creature on a narrow phone screen. Nicknames select the
same canonical species portrait; all 86 packaged creatures are supported.

Each real action plays a short one-shot GIF: wind-up, lunge or typed projectile,
impact, recoil, and resource changes. Guard grows a barrier, healing raises
sparkles, ultimates charge before striking, and a defeated creature stays
collapsed. Reflected damage, lifesteal, revival, status ticks, skipped turns,
and bonus strikes use snapshots of the shared combat engine's actual results.
The renderer does not roll attacks or calculate a second version of damage.

An attack/skill takes 1.4 seconds, guard/healing/status effects 1.2 seconds,
and an ultimate 1.9 seconds, plus a 0.2-second playback allowance. Playback starts
after Discord accepts the attachment. The next prompt opens afterward, with a
fresh 45-second decision window. The same registered move view acknowledges
stale/duplicate clicks throughout rendering, upload, playback, and cleanup.

## Realms

Exploration and challenges use the chosen destination. Spars use the NPC's
catalogued home, including Sable's Ember Wastes and Cael's Celestial Loom.
Trainer and ranked battles use the host companion's element: fire -> Ember,
ice/wind -> Glacial, water/shadow -> Trenches, cosmic/arcane/light -> Loom,
and nature/earth/other -> Woods. Combat formulas and rewards are unchanged.
Cooperative raids use a [shared arena](COOPERATIVE-RAID-ARENA.md) with concurrent
action clips, independent trainer controls, and the same 5–8 minute balance.

The five approved backgrounds are packaged in `data/battle_realms/` (about
426 KiB combined). [Artwork provenance](../data/battle-artwork.json) records
the full prompts, built-in imagegen mode, source hashes and packaged hashes.
Existing creature portraits are reused without replacing their source files.
No image-generation service, API key, image host or new environment variable
is needed while the bot runs. Pillow is included in the runtime requirements.
The bot needs **Attach Files**, **Embed Links**, and its usual channel message
permissions. A rejected attachment is retried as a text panel.

## Runtime limits and fallback

Two bounded rendering workers operate outside the asyncio event loop. Slots
are acquired before submission; cancellation or timeout does not release a
slot until its actual thread finishes. There is no unbounded render queue.
Backgrounds and cropped portraits are cached; FreeType fonts are confined to
their rendering thread. Clips have at most 30 source frames and a 2 MB limit.
Every upload replaces the previous attachment with a uniquely named file;
streams close after both successful and failed uploads.

A busy, failed or six-second timed-out renderer switches that match to text
controls. HTTP operations have 15-second limits. Failed action/final artwork
cannot undo an already winning move. A deleted action panel is recreated at
the next prompt. State locks are released before media rendering, network I/O
and playback, so item use and unrelated players stay responsive. Cancellation
still saves ultimate charges, closes controls and releases battle reservations.

Discord controls GIF autoplay and cannot confirm when each client actually
starts displaying a clip. Autoplay-disabled clients still receive readable
HP/MP/status text, the action log, and a final PNG. There is no idle GIF loop,
background strobe or camera shake. Playback timing is best effort after upload;
it cannot guarantee simultaneous display on slow clients. Validation here uses
Discord.py's real component dispatcher and synthetic HTTP responses, without
logging the bot into a live server.

## Review artifacts and validation

- [Actual attack GIF](battle-preview/attack.gif)
- [Actual ultimate GIF](battle-preview/ultimate.gif)
- [Five realms at desktop and 320-pixel widths](battle-preview/realms-desktop-mobile.png)
- [Clip sizes, frame counts, real combat logs and local timings](battle-preview/validation.json)

The recorded local run rendered six action types in a median 182 ms (maximum
236 ms). Its 10 ms event-loop heartbeat had a p95 delay below 8 ms; these timings
exclude Discord uploads and client playback. Sample GIFs range from 197 KiB
to 1.05 MiB and play once, with their full declared duration preserved.

`tests/test_battle_art.py` covers all portraits and realm hashes, immutable
snapshots, HP timing at impact, genuine multi-frame clips, one-shot durations,
healing/guard/ultimate/KO poses, Unicode names, worker saturation and timeout,
real dispatcher clicks during playback, PvP ownership, cancellation during an
AI opening clip, fresh turn deadlines, status/bonus results, rejected uploads,
and settlement after a failed final redraw. Existing battle, shop, raid and
economy regressions remain enabled with a text-only presentation fixture.

Reproduce without any bot token or player database:

```sh
python -m pip install -r requirements-lock.txt
python -m unittest tests.test_battle_art -v
python scripts/preview_battlefields.py --output battle-preview-output
```

The optional developer script `scripts/prepare_battle_realms.py` rebuilds the
packaged realm images from the approved source-manifest and original assets;
normal installation uses the committed files directly.
