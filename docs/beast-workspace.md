# Beast workspace navigation

Previously, the actions in `/beastinfo` opened unrelated messages. Care and Gear had no route back to the beast card, growth results removed their controls, and Team retired the collection browser entirely.

The card and its actions now share a single Discord message and navigation lifetime. Care, Gear, Train, Evolve and Team replace its contents in place. Each action has **Back to beast** plus a shared action selector. Success and blocked growth results retain those controls. Training's **Train again** opens a new preview and requires another confirmation. Rename refreshes the original card with its confirmation inline.

The workspace remembers the selected immutable beast ID, query, rarity, sorting, collection page and expanded-details setting. Returning loads fresh stats, appearance and inventory. Renaming or evolving outside the saved filter keeps that individual visible; Back to roster restores the filtered results. If the individual leaves the collection, the workspace reports it and returns to the roster without selecting a replacement.

Care and Gear stay focused on the selected beast inside this workspace. Their standalone commands retain their collection pickers. Team shows the equipped three-member stage and can assign the viewed beast to exploration or raid slots without leaving the message. The explicit Field Journal button remains an exit to the main hub.

## Interaction safety and balance

All views share one lock. Back waits for an item or gear operation already in progress, then loads the committed state. Successful navigation retires the previous view; its callbacks and timeout cannot overwrite the current panel. A failed navigation publish keeps the original view active. Embedded actions use the same five-minute inactivity timeout as the card.

Existing item, equipment, training, evolution and team services still enforce ownership, immutable identity, expected gear/price, inventory, progression requirements and combat/expedition reservations. No prices, rewards, stat gains, limits or database schema change.

## Verification

`tests/test_beast_workspace.py` drives the actual controls through full action/Back round trips, mutations and result screens. It checks one message, no panel followups or deletion, saved collection context, rename/evolution outside a filter, transfers, stale and foreign clicks, retired timeouts, failed publication, and Back during item use. Existing standalone Care, Gear and growth tests remain in place.

```powershell
python -m unittest tests.test_beast_workspace -v
python scripts/preview_beast_workspace.py --capture
```

The exporter records 15 actual callback transitions into the same example message and packages ten real embed/component screens with local artwork. The offline walkthrough is `docs/beast-workspace-preview/beast-workspace.html`. Browser validation checks all screens at 1100, 390 and 320 pixels, art loading, one visible message, and all five action/Back journeys. The preview demonstrates navigation only; mutation behavior is covered by the database regressions. No Discord bot or live player account is used for these checks.
