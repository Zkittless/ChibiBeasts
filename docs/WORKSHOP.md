# The Workshop

The original approved crafting design is now one Discord panel. A realm banner
and the three category tabs sit above the selected recipe. Native Discord text
keeps the effects and material counts readable on phones. Buttons handle the
rest of the flow in the same message.

- `/craft`: open the workshop without spending anything.
- `/craft <item>`: preserve the existing quick craft, then open that recipe with
  its updated materials and stock. A failed quick craft opens the recipe and
  explains what is missing.
- **Materials**: expand the material bag inside this panel, including when opened from the Field Journal.

Recipe browsing lives in the workshop; the separate recipes and material-bag commands are removed.

**Pin recipe** saves the selected recipe as your Field Journal goal. **Field Journal**
returns to the home panel in the same message. Opening that goal's recipe from
the journal selects it without crafting; progress includes current inventory,
equipped pieces, level requirements and the Gnome Forge discount.
The help menu, equipment prompts and evolution errors use the workshop route.

## The approved flow

1. Pick Armor, Runes or Evolution, then a recipe.
2. Read its effect, trainer level, owned copies and current material costs.
   Forge costs reflect actual guild membership. Materials and Sources expand
   in this panel; Refresh picks up inventory changes elsewhere.
3. Craft one copy. Armor becomes an unequipped equipment row; runes and
   catalysts go into inventory, preserving the existing storage rules.
4. Use Equip or Evolve. Choose a companion, review its current equipment or
   next form, then confirm. Replacements return old armor/runes. Evolution
   uses the selected catalyst and shows the new form's existing artwork.

Companion lists paginate in groups of 25. Single-candidate evolution skips the
unnecessary selector. Ineligible levels, market listings, KO recovery, battle
reservations and missing ownership cannot be bypassed by pressing an old button.
Starter bond awakenings remain available through the existing evolution flow;
the crafting tab covers the three craftable catalysts.

## Transactions and interaction handling

Every button and selector checks the owner and acknowledges before waiting for
SQLite, other inputs, artwork or HTTP. Stable control IDs continue routing after
tab changes. Changes serialize within a panel; a rapid repeat of a craft or
confirmation is refused while the first is processing. Out-of-order inputs do
not apply to a newly selected recipe.

Crafting checks fresh trainer level, Forge membership and ingredient quantities
inside a writer transaction. An insufficient ingredient rolls back all previous
deductions. Costs, rewards, achievements and quest events retain their existing
rules. Legacy split material stacks count together; consumption removes only
the required amount. Split catalyst stacks lose one copy for one evolution.

Equipment and evolution confirmations bind the companion's row, species and
displayed equipment. Renumbering, transfer, a changed loadout or another evolution
requires a new review. The workshop never substitutes another catalyst if the
selected one has disappeared.

The banner and companion portraits are packaged with the bot. Existing
attachments are retained across edits. Rejected artwork falls back to the full
native text and controls. Removed messages close the panel without retrying a
completed craft. Five minutes of inactivity disables its controls.

## Validation and deployment

- [Production component examples](workshop/components.json)
- [Component and text-limit checks](workshop/validation.json)
- [Packaged workshop banner](../data/workshop/header.png)

The preview script uses a temporary example database and actual `WorkshopView`
payloads. It does not sign into Discord or use live player data.

```sh
python -m unittest tests.test_workshop tests.test_economy_balance -v
python -m unittest discover -s tests -v
python scripts/preview_workshop.py
```

The existing pinned discord.py supports the native layout components. No runtime
dependencies, Railway variables or database migrations are added. Deploy source
and the packaged banner together. Startup's normal global command sync removes
the retired command and makes the recipe argument optional. Servers that keep
their own guild command copies can refresh them with the existing `!sync guild`.
