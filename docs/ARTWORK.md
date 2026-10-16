# Beast artwork

The roster uses one isolated pixel-style PNG per beast in `data/sprites`.
The filenames match the 86 IDs in `data/beasts.json`, including the evolved,
corrupted, ancient and altered divine forms. The former
`void_chronosv2.png` alias is now `void_chronos.png`.

`data/artwork.json` records filenames, content hashes and image dimensions.
`utils.artwork.beast_image_url()` resolves every image from this manifest.
Both raid systems and the shared beast loader use the same resolver.

The default public host is this repository's `main` branch on GitHub Raw.
Merge the code and PNGs together before deploying a refresh. Preview branches
or other public hosts can set `BEAST_IMAGE_BASE_URL` to their sprite directory.
A content hash in each URL causes Discord to request the updated image when
the art changes. No Cloudinary upload is needed for beast images.
Sanctuary-building illustrations retain their existing Cloudinary URLs.

The art was generated with built-in ImageGen from the character descriptions
and five approved style examples. `docs/art-prompts.json` contains the exact
prompt set and reference roles. These are transparent high-resolution
pixel-style renders, not strict native 32 by 32 exports. The generated alpha
channels and image pixels are preserved.

For a future art change, replace the appropriate PNG, then run:

```sh
python scripts/finalize_artwork.py
python -m unittest discover -s tests -v
python scripts/build_art_gallery.py --output chibibeasts-gallery.html --standalone
```

The finalizer checks completeness and PNG alpha channels and refreshes hashes;
it does not redraw or resize images. The gallery builder only packages image
files into HTML. Existing sprites remain recoverable through Git history.

The gameplay revamp adds four distinct starter awakenings: Prismara, Timestitch,
Nebuloom and Rootwarden. Their exact initial prompts and reference roles are
also saved in `docs/awakening-art-prompts.json`. The main prompt record includes
the Nebuloom background cleanup. No raster pixels were edited by scripts.

`python scripts/build_revamp_preview.py` builds the four before-and-after pairs.
Add `--standalone` to embed the original PNG bytes. Use
`python scripts/build_art_gallery.py --output chibibeasts-gallery.html --page-size 10`
to rebuild the paginated roster, with all 86 creatures across nine pages.
