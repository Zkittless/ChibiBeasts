# Roster and Care preview

Open [roster-qol.html](roster-qol.html) to browse the example collection. The page runs locally without a Discord login or server.

The preview exports the game's actual roster, equipment, Care and Field Journal payloads from a disposable database with 30 companions. Packaged portraits show each individual's equipped armor. Local controls navigate those captured payloads; they do not contact Discord or affect a real trainer.

Try a roster page, select a companion, and return to the same page. Search supports the sample's full nicknames, species and exact collection numbers, including `Ember`, `Twine` and `#27`. Discord also supports partial searches. The rarity and sort controls show actual filtered exports. Care opened from a roster card or gear card keeps that individual selected.

The Care examples include a real potion transaction in the disposable database: 64 → 85 HP and four → three potions, with the lead unchanged. Opening the permanent-item review consumes nothing. The knocked-out companion's ordinary healing control is disabled. The exploration examples show selection and saved destinations before any expedition begins.

`components.json` contains the exported payloads. `validation.json` records component limits, text limits and the sample Care transaction. `browser-validation.json` records browser checks at 1100, 390 and 320 pixels. Screenshots use the corresponding `desktop-`, `mobile-` and `narrow-` prefixes.

Regenerate from the repository root with the normal project Python environment:

```powershell
python scripts/preview_roster_qol.py --capture
```

The optional `--tool-path` argument can point to a local Playwright installation when it is outside the active Python environment. Browser capture uses an installed Chrome.
