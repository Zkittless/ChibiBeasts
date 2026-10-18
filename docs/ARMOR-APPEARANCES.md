# Equipped armor and the Field Journal

Each equippable armor set has a distinct design: leaf-and-silk tunics, riveted iron and a buckler, teal scales, obsidian and molten cores, white healer robes, petrified eyes, flame feathers, storm plates, crimson scales, shadow cloth, golden clockwork, violet branching regalia, bark strips, ember-resin tiles, thornweave and lined deepwater hide. Full dressed sprites are illustrated per creature so those designs follow its actual body and preserve its face, limbs and signature features.

`data/armor/manifest.json` registers approved full-sprite variants and their SHA-256 hashes. The approved Twine, Barkley and Gloop Gnome-Iron portraits are retained. The previous shared-component compositor produced poor fits and has been removed from rendering. Its legacy component files and `data/armor_fits.json` may remain as historical source material; they are never stretched onto a creature. An unregistered or unavailable species/armor pair shows the canonical portrait safely while preserving the equipment's gameplay effects.

The image loader runs locally with bounded caches. It selects the complete equipped sprite before motion, so armor follows attacks, ghosts, recoil, retreats and raid swaps. Each combat snapshot carries its own armor ID. No image generation or remote artwork requests happen during play, and no equipment effects or stats are changed by this feature.

The current package includes all 86 species and 16 armor sets: 1,376 reviewed, registered full-sprite combinations. Only registered files count as dedicated coverage, and a base-art fallback does not count as finished armor. `utils.armor_art.coverage()` reports actual installed dedicated and missing pairs. Local atlas-validation reports separately identify staged candidates; candidate artwork is not installed merely because it passes geometric checks.

Owned-creature portraits use the equipped appearance in the journal's home and Companions pages, awakening review, workshop fit/result, `/gear`, `/beastinfo`, play, evolution and battle introductions. Species references in the codex, starter selection, new catches and NPC/boss references retain their canonical artwork. Attachment filenames include species and armor, so paging between two members of the same species does not reuse another companion's gear. Image workers are bounded and acknowledged interactions retain usable text controls if artwork fails.

## Journal navigation

`/explore` now opens Adventure and begins a saved expedition. An existing trip resumes without another charge. Charges, recharge timing and the next decision remain visible. Return home to bank the trip's rewards. Locked destinations, empty charges and recovering companions leave a usable journal with an explanation. Both values of the legacy `expedition` parameter use this flow; the previous private quick-exploration runner was removed.

Five duplicate commands have been retired:

| Retired command | Journal route |
| --- | --- |
| `/guide` | `/journal` |
| `/materials` | Workshop, then Materials |
| `/setactive` | Companions, then Make active |
| `/questline` | Story |
| `/bond` | Companions |

Commands whose richer controls or action callbacks are still needed remain available. Source hints and pinned gathering destinations now use the actual saved-expedition material regions. The earlier quick-exploration acquisition model is labeled historical; this change does not adjust payouts or drop rates.

Deploy the code and `data/armor/` together, then restart the bot and run the existing owner command `!sync` (or `!sync guild` for guild-scoped development) to refresh slash-command registration. No new environment variables, database reset or schema changes are required.

## Artwork and verification

Full wardrobes are generated with the built-in image-generation tool as one transparent 4 by 4 atlas per species, in `equipment.json` insertion order. The local job manifest retains exact prompts and source identities. `scripts/stage_armor_atlas.py` extracts complete sprites, crops using alpha greater than 20, retains the source alpha within each crop, and only scales down with nearest-neighbor sampling. It rejects empty, clipped, overlapping or composite cells before producing review candidates. No armor pixels are painted by the packaging code. See [ARMOR-ATLAS-PACKAGING.md](ARMOR-ATLAS-PACKAGING.md) for the schema and explicit review/install workflow.

Run `python tools/preview_armor_contacts.py --stage ../output/armor-refit --species prismite` to export all sixteen validated candidates at 140 and 180 pixels on light and dark backgrounds. It verifies packaged hashes, composes existing sprites without altering source files, and refuses incomplete wardrobes. Omitting `--stage` previews complete installed wardrobes. The broader `tools/preview_armor.py` utility exports production duel, knockout and raid clips, and labels canonical fallbacks separately from dedicated variants. Unit coverage checks actual registered asset hashes, distinct dedicated appearances, exact base fallback behavior, cache isolation, equipped-state loading, attachment replacement, worker cancellation and journal exploration decisions.
