# Streamlined panels

Players can now act on the companion or build they are viewing, with detail available on demand.

| Area | Result |
| --- | --- |
| Field Journal | One page selector replaces six persistent tab buttons. Pin a goal and Refresh stay available. |
| Companions | Opens the existing searchable Collection browser and retires the previous journal message. A Field Journal button returns to the hub. |
| Companion card | Compact status, stats and evolution readiness. Details expands moves and lore. The actions selector offers Care, Gear, active lead, training, rename, evolution and Team. |
| Gear | Change armor or rune, browse owned copies, preview effects, equip or remove that slot. Workshop and Care are directly accessible. Single-page and inactive search controls stay hidden. |
| Team | Exploration and Raids use the same visual editor. Raid assignments remain separate. `/party` and `/raidparty` open raid mode. |
| Perks | Equipped build and slot usage first, then one perk's details. Equip, Unequip and the weekly Astral Resonance ability are panel actions. Activities links to the private editor. |
| Story and activities | Story objectives lead; dialogue can be read or hidden. Daily objectives start as a completion count and expand to progress and rewards. |
| History | Expedition recaps, battles, raids, trades and altered divines share a category selector and pagination. `/history` starts with Battles; Adventure's Battle log starts with Expeditions. |
| Command guide | Starts with the main panels and shows one topic at a time. Existing action commands and aliases are grouped under Shortcuts. |

Existing command shortcuts remain available. Multiplayer battle, ranked, trade and guild commands retain their distinct roles.

## Balance and stale controls

Gear uses the existing atomic equipment service, with immutable beast identity and expected current gear checked again on confirmation. Slot-specific removal returns only that slot's equipment. Ownership, level, market and combat guards remain in force.

Training previews show the existing cost after Sanctuary discounts. Confirmation calls the existing training handler and rechecks beast identity and the quoted price. Existing gains, daily and lifetime caps, gold spending, XP, achievements and quest events are preserved. Evolution uses the existing transaction and requirements; confirmation cannot follow a transferred or evolved beast. Rename writes against the owned immutable ID.

Raid team edits use the existing assignment services and combat reservations. Switching modes changes no saved assignments. Perk equips retain slot costs; weekly activation shares the same atomic weekly usage record with `/perk_use`. No database migration, loot table, reward amount or progression curve changes are needed.

## Verification and preview

The new `test_panel_*` suites cover mutations, stale confirmations, ownership, pagination, mode separation, story dialogue reopening and journal retirement. Existing journal, roster, equipment and workshop regressions remain in place.

`scripts/preview_journal_flow.py` exports actual Discord components and embed payloads from a disposable database. It includes both team modes, roster cards, Gear, Perks, training, history, Story, activities and the guide. The packaged preview is `docs/panel-streamline-preview/journal-flow.html`.

```powershell
python scripts/preview_journal_flow.py --output docs/panel-streamline-preview --capture
```

Capture uses Playwright and Chrome to check desktop, mobile and narrow layouts, portrait loading, navigation, detail expansion, guide topics and the battle-return flow. Preview actions are illustrative and do not affect a live account.
