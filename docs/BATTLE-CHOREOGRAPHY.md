# Battle choreography

The renderer stages each resolved move through anticipation, contact at `t=.48`,
release, and recovery. The move catalog assigns explicit choreography to all 360
species/move pairs across 90 beasts. Every beast has four different sequences,
so selecting another move changes its staging as well as its colors.

These are presentation profiles. Damage, mana, charges, status, healing,
cooldowns, rewards, and target selection remain owned by combat. A visual effect
never rolls an additional attack. The approved manilla pixel HUD stays in place.

## Explicit identities

`data/move-animations.json` version 2 retains the original family, palette, motion,
variant, and motif fields and adds:

| Field | Meaning |
| --- | --- |
| `sequence` | Registered choreography from `kinetic_effects` or `divine_effects`; canonical moves never use the legacy fallback. |
| `accent` | Fixed 0–65535 identity seed, used for deterministic lanes, facets, and particle arrangements. It does not read the global combat RNG. |
| `ultimate` | True only for the creature's catalog ultimate; affects presentation, never move power. |

The catalog is authored per creature and move. Runtime selection does not guess
from move names. Related creatures can share an archetype, while preserving
their own motif, palette, accent, motion, and ultimate staging.

## Divine signatures

| Creature | Signature sequence |
| --- | --- |
| Singularity | Supernova detonation: concentrated origin followed by a stellar release. |
| Astraea | Astral loom: threads assemble an ordered celestial weave. |
| Chronos | Terminus severance: the final moment cuts across the field. |
| Genesis | Genesis bloom: creation unfolds outward from the point of origin. |
| Fractured Genesis | Genesis implosion: creation collapses inward. |
| Paradox | Paradox loop: incompatible paths converge. |
| Karma | Karmic mirror: reflected geometry turns the return into the strike. |
| Zodiac | Zodiac alignment: constellations connect before alignment. |
| Abyss | Abyss descent: the target drops into a deep void composition. |
| Nebula | Stellar nursery: newborn stars arrange into an expanding formation. |
| Supernova | Supernova detonation: stellar compression releases a broad shock. |
| Nirvana | Nirvana lotus: enlightenment unfolds into an offensive release. |
| Horizon | Horizon gate: a dimensional gateway opens around the target. |
| Asgard | World tree: branching roots give the strike a living structure. |
| Atlas | Titan crush: descending weight meets a fractured ground plane. |
| Phoenix | Phoenix sunrise: a sunbird silhouette rises through flame. |
| Leviathan | Leviathan deluge: a broad wave crest travels across the field. |
| Dragon | Dragon apocalypse: draconic flame has a large staged release. |
| Fenrir | Ragnarok fangs: the jaw silhouette closes on its target. |
| Hydra | Hydra constellation: multiple heads form a coordinated strike. |
| Ouroboros | Ouroboros reset: a closed cycle resolves into the hit. |
| Prismara | Prism cathedral: faceted architecture assembles around the strike. |
| Radiant Kitsune | Sovereign veil: the spirit sovereign releases its protective court toward the target. |
| Veilmoth | Lunar procession: a moonlit procession crosses the field. |
| Hollow King | Phantom court: the guardian's spectral court delivers its decree. |

Lower tier moves use distinct physical and elemental staging: jaw closure,
crossed claws, thrusts, charging trails, fault lines, loom snaps, breath cones,
fire wheels, meteors, water spears, geysers, tidal crests, crystal prisons,
hail lances, forked lightning, sonic fronts, poison clouds, roots, and swarms.

## Truthful support moves

Names do not determine targets. `Leaf Dart`, `Glitter Bounce`, `Luminal Strike`,
and `Moss Mend` heal the actor and therefore use actor-centered recovery effects.
`Ice Shell` freezes its opponent and therefore grows its crystal prison at the
target. Twine's `Borrowed Moment` attacks with clock fracture; Timestitch's move
of the same name is defensive and rewinds around its actor.

All catalog ultimates are attacks, including `State of Nirvana` and
`Sovereign Veil`. Contextual lotus and sovereign effects use the recorded family
to distinguish a protective aura from an offensive release.

## Extending the roster

Add an explicit registered sequence, accent, and ultimate flag with the new
beast's profiles. Choose four distinct sequences that reflect the actual move
effects. Reuse visual primitives when appropriate, while giving the move a
recognizable silhouette and action path. `test_choreography_catalog` checks
coverage, registration, support targeting, aliases, and distinct selections.

Recreate production-renderer previews and inspect both actor sides before
publishing. Effects remain inside the battle stage; resource panels, reserve
cards, raid trainer markers, and journal text stay readable. Concurrent raids
use the same choreography with their existing shared clip and bounded renderer.
Existing move durations, impact timing, retreat segments, attachment limits,
and next-action windows are unchanged by the catalog migration.

## Reproduce the visual checks

Run from the repository root, using the project's Python dependencies:

```powershell
python scripts/preview_choreography.py --source-root <baseline-checkout> --output docs/choreography-preview --phase before
python scripts/preview_choreography.py --output docs/choreography-preview --phase after
python scripts/audit_choreography.py --output ../output/battle-choreography
python scripts/benchmark_choreography.py --output ../output/battle-choreography
python scripts/stress_choreography.py --output ../output/battle-choreography
```

The gallery compares 18 seeded duels and one ten-trainer raid. Its clips use
the actual combat resolver and production encoders. The roster audit exports
all 360 moves, checks file budgets, one-shot playback, exact durations, unchanged
snapshots and combat RNG, and records source fingerprints. It also renders six
actual boss turns and verifies that frozen bosses and harmless solved patterns
do not invent attacks. Eight contact sheets cover all 64 sequence archetypes.

The worker benchmark measures 14 duels and six concurrent raids through the
bounded renderer with an event-loop heartbeat. The stress check makes ten
matching ultimate casts against a synthetic high-health enemy. All are offline
checks: they do not measure Discord upload or client playback latency. Captures
made before committing record the parent commit and the exact source hashes;
the hashes identify the captured implementation.
