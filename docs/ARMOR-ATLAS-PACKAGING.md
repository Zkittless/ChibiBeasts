# Species-specific armor wardrobes

`scripts/stage_armor_atlas.py` packages generated **complete creature sprites**.
It does not fit shared armor fragments or alter a creature's body pixels. Each
input is a transparent 4 by 4 atlas for one species, with armor variants arranged
in `data/equipment.json` insertion order. The exact list belongs in `armor_order`
in the job manifest; a mismatch is rejected rather than guessed.

The local job manifest has this shape (all sixteen IDs are required in the real
`armor_order` list):

```json
{
  "version": 1,
  "armor_order": ["sprite_silk_attire", "gnome_iron_plating", "..."],
  "jobs": {
    "prismite": {
      "source": "sources/prismite-atlas.png",
      "prompt": "Exact generation prompt",
      "status": "generated",
      "approved": false
    }
  }
}
```

Source paths can be absolute, or relative to the job manifest. A job is processed
only when its status is `generated` or `approved`. Other statuses stay pending.
One generated image cannot stand in for multiple species.

From the repository root:

```powershell
..\.tools\python312\python.exe scripts/stage_armor_atlas.py ..\output\armor-refit\jobs.json
```

The default output is `../output/armor-refit`. The staged game paths are
`data/armor/variants/<armor_id>/<species>.png`, accompanied by a candidate
`data/armor/manifest.json` and `atlas-validation.json`. Nothing in the production
asset directory changes during this step. Existing approved Gnome-Iron sprites
for Twine, Barkley, and Gloop are retained byte for byte.

The extractor requires real RGBA transparency. Proportional integer boundaries
partition a 4 by 4 atlas even if the canvas dimensions are not divisible by four;
this neither resizes the source nor drops pixels. It rejects empty cells,
artwork at cell boundaries, unusually small
cells, and a second substantial disconnected silhouette. Cropping uses alpha
greater than 20 while retaining original alpha values inside the crop; scaling
only reduces dimensions with nearest-neighbor sampling, to at most 360 by 352.
Use `--max-size WIDTH HEIGHT` to choose a smaller package size. There is no
background removal, pixel painting, or synthesized replacement art.

Visual review remains necessary: these checks cannot establish creature
identity, the correct armor theme, or the ordering of visually plausible cells.
`atlas-validation.json` includes crop coordinates, source SHA-256, packaged
SHA-256 through the staged manifest, prompts, and separate counts for dedicated
full sprites versus legacy component coverage. Staged candidates are not a claim
that all 1,376 species/armor combinations have approved art.

Export repeatable contact sheets for any validated species with:

```powershell
..\.tools\python312\python.exe tools/preview_armor_contacts.py --stage ..\output\armor-refit --species prismite
```

This writes all sixteen looks at 140 and 180 pixels on light and dark backgrounds
to the stage's `contacts` directory. Multiple species can follow `--species`.
Source hashes are checked before composition and source bytes are checked again
afterward. The helper will not substitute generic components for missing sprites.

After reviewing a source atlas, set that job's `approved` field to `true` and run
with `--install-approved` to register only its validated variants. The installer
checks every staged hash before copying any file. Unapproved and rejected jobs
are never installed. Replacing one of the three approved starter Gnome sprites
also requires the explicit `--replace-approved-gnome` option.

```powershell
..\.tools\python312\python.exe scripts/stage_armor_atlas.py ..\output\armor-refit\jobs.json --install-approved
```

Restart the game process after replacing assets so its appearance caches reload.

Export the complete offline beast picker, with all sixteen outfits per beast and
a light/dark background control, after installing the approved wardrobes:

```powershell
..\.tools\python312\python.exe tools/preview_armor_wardrobes.py --require-complete
```

The default output is `../output/armor-refit/wardrobes/index.html`. The export
verifies registered hashes and refuses to claim completion when any pair is missing.
