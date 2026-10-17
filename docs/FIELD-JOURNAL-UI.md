# Field Journal battle UI

The approved Field Journal layout now renders in duels and cooperative raids.
Framed companion portraits, a fantasy heading, in-bar HP/MP values, and glowing
ultimate charges surround the existing pixel-art realm. The battle journal
sits below the characters. Discord's native move buttons remain clickable
under the image.

## Number fitting and ULT spacing

All text is measured with the exact rendering font, including its vertical
bearings and numeric outline. HP/MP values are centered inside a padded content
rectangle within the bar. Size decreases when a longer total needs room;
exceptionally large totals use compact units, while active native text retains
the exact values. Empty bars, full bars and zero mana use the same alignment.

ULT owns a separate label rectangle. The first orb starts after a fixed gap,
each orb has its own slot, and the charge count has another rectangle beyond
the third orb. Neither the label nor the count can cover a charge. Regressions
check actual painted bounds across the entire roster at levels 6 and 50,
single-digit, seven-digit and unusually large totals, and all four charge states.

- [Normal duel at full size](field-journal/duel.png)
- [The same image at 320 pixels](field-journal/duel-320.png)
- [Normal, large and empty resource bars](field-journal/number-spacing.png)
- [Measured text bounds](field-journal/spacing-validation.json)

## Journal and text cleanup

The journal uses actual combat logs. It shows the move before impact and the
resolved result with the impact, then holds those entries at the next prompt.
Bonus strikes capture their own result after the primary move. Raid entries
come from the shared session's actual event, with trainer numbers matching the
arena markers. Long entries are fitted inside the journal.

Visual panels omit duplicate native action logs. Closing duel panels omit
their repeated HP/MP summary; PvE reward messages no longer repeat final HP or
dump a second battle log. Trainer battle result messages also omit the extra
log. Rewards, catch outcomes, lessons, time-limit rules and story text remain.
Closed visual raid panels drop the repeated party list. Active raids keep each
trainer's native resources and contribution totals, plus urgent native warnings.

If a renderer or upload fails, the complete native text panel remains usable.
Visual cleanup operates on an independent embed copy, so attachment rejection
cannot remove the fallback's data. No gameplay formulas, cooldowns, decision
windows, raid warning windows or settlement rules change.

## Review and deployment

- [Actual attack clip](field-journal/attack.gif)
- [Actual ultimate clip](field-journal/ultimate.gif)
- [Ten-trainer raid clip](field-journal/raid-ten.gif)
- [Duel clip measurements](field-journal/duel-validation.json)
- [All six raid boss measurements](field-journal/raid-validation.json)
- [Fresh-seed balance replay](field-journal/holdout-summary.json)

Duels render at 640 × 528; raids render at 640 × 692. The journal does not cover
the fighting area or trainer markers. Both renderers retain one-shot playback,
unique attachments, the shared two-worker limit, and the 2 MB per-clip limit.
Timings in the review files are synthetic local measurements and exclude
Discord upload and client playback. GIF autoplay is controlled by Discord.

The bundled [Cinzel display font](../data/fonts/README.md) needs no system
installation. There are no new Railway variables, runtime dependencies or
database migrations. Deploy source and packaged assets together.

```sh
python -m unittest tests.test_field_journal tests.test_battle_art tests.test_raid_art -v
python -m unittest discover -s tests -v
python scripts/preview_field_journal.py --output field-journal-output
python scripts/preview_battlefields.py --output duel-output
python scripts/preview_raid_arena.py --output raid-output
```
