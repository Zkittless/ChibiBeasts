# Rendered image typography

`PixelifySans.ttf` is the unmodified Pixelify Sans variable TrueType font used
throughout rendered game imagery. The shared helper uses weight 700 for display
text and 600 for body text; floating combat callouts also use bold 700.
The approved palette is warm
manilla headings and white body text, with a dark outline and crisp binary
rasterization. Layouts still fit lettering to its measured ink bounds.

- Project: https://github.com/eifetx/Pixelify-Sans
- Distribution: https://github.com/google/fonts/tree/main/ofl/pixelifysans
- License: [SIL Open Font License 1.1](OFL-PixelifySans.txt)
- Copyright: 2021 The Pixelify Sans Project Authors
- SHA-256: `9ba86cd010a4de309d263ceff8e8044092c9db7efda869620cb9ff1c4389e8a5`

The font is bundled so Railway needs neither a system font nor a network font
service. Each rendering thread owns its font faces. Native Discord text and
controls retain Discord's own typography.

`Cinzel.ttf` and its [license](OFL-Cinzel.txt) are retained for historical
artwork provenance; the current game renderer does not use that face.

Refresh the 13 static text-bearing images after changing shared typography:

```text
python scripts/rebuild_ui_typography.py
```

This updates Journal and Workshop banners and starter selection labels. It
does not regenerate creature portraits, sprites, armor, costumes or biome
paintings. Review contacts and source hashes are saved under
`output/typography/static-assets` outside the repository.
