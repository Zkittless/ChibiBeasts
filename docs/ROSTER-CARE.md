# Roster, gear and companion care

## Browse the full roster

Open `/collection` for ten companions per page. Search by nickname, species or `#number`, filter by rarity, or sort by collection number, highest level or highest rarity. The beast menu opens a detailed card directly. **Back to roster** returns to the same search and filters; detail arrows follow the filtered collection.

`/beastinfo` opens that same browser on the active companion. Its optional beast number supports name autocomplete. Details retain stats, moves, disposition, recovery status, evolution progress and equipped armor artwork. All browsing is read-only: it never promotes a lead or changes team assignments.

## Choose a beast in Gear

`/gear` no longer requires a number. It starts with the active beast; its optional argument supports name autocomplete. Choose another owned beast from the menu, search by nickname/species/number, or turn collection pages in the same message. Current armor, rune, available copies and trainer-level restrictions refresh from the save. Equipping still uses the existing `/equip` or Workshop flow.

## Apply healing and items

Open `/care`, optionally choose a beast through autocomplete, or press **Care** on a detailed roster card, Gear or a journal companion/team slot. Care is private and keeps that individual selected.

1. Choose an owned care item.
2. Review the actual effect, including HP before/after, daily EXP allowance or upgrade cap.
3. Press **Use 1**. The same panel updates health and remaining quantity.

Permanent stat treats, Ambrosia and party recovery require **Review**, then a separate **Confirm · Use 1**. Party recovery names all affected companions. Rename tokens first ask for a name, then show it for review. An exhausted item stays selected and disabled until another item is chosen; the panel never chooses a rarer replacement automatically.

Ordinary healing explains when a revive or cure is needed. Full-health healing, an already active Aether ward and party recovery with no beneficiary keep the item. Queued trainer effects are labeled as trainer effects. Existing item strengths, growth EXP allowances, permanent upgrade caps and random stat rolls stay unchanged. `/use` remains available as a shortcut.

Care checks current ownership, market listings, live battle state, target identity and the reviewed effect inside the item transaction. A stale preview refreshes before any item is spent. Repeated clicks on one rendered use button cannot consume twice. Image failures retain usable text and controls.

## Choose and save exploration

`/explore` opens destination selection without spending a charge. Choose an unlocked destination and press **Save destination** to remember it; the current exploration team already persists. An explicit command destination overrides only that opening. **Clear default** restores Woods.

Only **Begin expedition · 1 charge** starts a new trip. Opening an existing trip resumes its saved destination and exact travelling team. The legacy `expedition` option follows this same selection-first flow for either value.

See the [interactive preview](roster-qol-preview/roster-qol.html). It uses disposable example saves and actual Discord payloads; its local controls do not contact Discord or affect player accounts.
