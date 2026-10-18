# Equipped armor and the Field Journal

All sixteen equippable armor sets have their own generated pixel-art components: leaf-and-silk panels, riveted iron and a buckler, teal scales, obsidian and molten cores, white healer cloth, petrified eyes, flame feathers, storm plates, crimson scales, shadow cloth, golden clockwork, violet branching regalia, bark strips, ember-resin tiles, thornweave and lined deepwater hide. These are separate designs, with different shapes and materials.

`data/armor/manifest.json` registers the packaged cutouts and their SHA-256 hashes. `data/armor_fits.json` defines explicit placements and protected face/signature regions for all 86 species. Chest and shoulder layers are clipped to the creature's body; rear cloth sits behind it. Protected regions retain the original pixels. Tiny spirits and shelled creatures use small bands or plastrons to keep faces and shells clear. The approved Twine, Barkley and Gloop Gnome-Iron portraits are retained as fitted full-sprite variants. The other combinations use the shared component designs and individual fit profiles; there are not 1,376 separately painted full-body images.

The compositor runs locally with bounded caches. It assembles equipment before motion, so armor follows attacks, ghosts, recoil, retreats and raid swaps. Each combat snapshot carries its own armor ID. No image generation or remote artwork requests happen during play, and no equipment effects or stats are changed by this feature.

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

The sixteen fitting kits were created with the built-in image-generation tool. Exact prompts, source filenames, source hashes and packaged component paths are in `armor-generation.json`. Packaging uses quadrant extraction, alpha cropping and nearest-neighbor sizing; original generated images are retained outside the repository. `scripts/build_armor_assets.py` accepts the local generation manifest for reproduction when those source files are present. Selected production components and approved variants are fully packaged in the repository.

Run `python tools/preview_armor.py --output ../output/armor-implementation` to export all 1,376 combinations and production duel, knockout and ten-trainer raid clips. The validator checks nonempty transparent images, distinct appearances and the existing 2 MB animation budget. Unit coverage also checks protected original pixels, cache isolation, real equipped-state loading, attachment replacement, worker cancellation and journal exploration decisions.
