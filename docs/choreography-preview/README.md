# Battle choreography preview

Open [index.html](index.html) locally to compare and replay 18 duels and a
ten-trainer raid. Both sides use the same combat seed, sprites, backgrounds,
outcomes and playback lengths. Effects and body movement change.

All 360 species/move entries now select from 64 authored sequences. Each
creature has four different sequences; related creatures share archetypes.
The coverage sheets show preparation and impact for a real example of every
sequence:

[1](sequences-01.png) · [2](sequences-02.png) · [3](sequences-03.png) ·
[4](sequences-04.png) · [5](sequences-05.png) · [6](sequences-06.png) ·
[7](sequences-07.png) · [8](sequences-08.png)

The [proof summary](proof-summary.json) verifies the captured source hashes.
Detailed reports cover [all roster moves and real boss turns](roster-validation.json),
[20 bounded worker jobs](worker-benchmark.json), and
[six ten-ultimate stress cases](raid-stress.json).
The largest checked GIF was 1,898,803 bytes, below the 2,000,000-byte cap.
Worker rendering peaked at 963 ms locally; event-loop delay p95 was 7.83 ms.
These measurements exclude Discord upload and client playback.

Full roster GIFs remain in the local audit output rather than being duplicated
in this preview directory. Reproduce them using the commands in
[Battle choreography](../BATTLE-CHOREOGRAPHY.md).
