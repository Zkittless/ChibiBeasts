# Living adventures

`/explore` opens destination selection in the existing Field Journal. Keep the
saved exploration team and press **Start adventure** to enter battle immediately.
The first five biomes require two encounters; the upper five require three.
A single short decision separates encounters. The prompt and each choice's
effect appear directly in the same journal, above the story-choice dropdown.

The launch catalog contains **200 one-time discoveries**, including **30 connected
three-scene locations**, and **60 explicitly repeatable wilderness encounters**:
20 one-time roots and six repeatable roots in each of ten biomes, 320 authored
scene pages in total. Nested pages are not counted as separate discoveries.

Every major completed discovery stays complete for that trainer. Unfinished
locations resume at their next unresolved page, including their frozen prose if a
later release changes the catalog. Choices can preserve a bridge, deliver a
message, change a site's use or unlock a later encounter through permanent world
flags. Failed fights and retreat leave their story scene unresolved. A deployment
or failed media upload does not erase the saved combat cursor.

This is finite authored content. When one-time discoveries run out, the journal
calls repeatable encounters **Familiar territory**. A run never repeats a root;
the last three familiar roots are excluded on the next visit. If no eligible scene
remains, a short regrouping choice preserves the fixed combat route. The journal
never restarts a completed story or pretends a changed name is new content.
Required encounters stay available after all stories are complete, including the
saved recruitable encounter.

## Combat and rewards

A full journey follows battle, short discovery/preparation, then battle, with a
second interlude and third encounter in the upper five biomes. Its fixed route
has three or five completed beats. Story choices cannot skip the required fights,
and running out of eligible discoveries offers a short regrouping choice without
shrinking the reward denominator. Retreat remains available and banks earned
partial progress; it is not a full clear.

Each interlude advances one authored scene. Connected locations resume at their
next unresolved scene in a later interlude or visit, so several story pages never
block the next required fight. The complete authored body and choice descriptions
wrap in the message without an expansion button or a character-based excerpt.
The dropdown repeats each choice; effects that exceed its description limit stay
complete in the message. A resolved story choice's outcome leads into the next
required encounter, and past outcomes remain in Battle log. Moves show their MP
cost; unavailable moves and ultimates remain visible as disabled buttons.
One **Swap / Assist** button opens a native modal containing the currently usable
reserve actions, with HP, MP and turn costs. Opening or cancelling it is free;
submitting a choice validates the saved battle revision and consumes its normal
turn. Attack, swap, assist and knockout animations keep using the same journal.
Packs have two or three sequential opponents using the existing entry, attack,
swap, assist and knockout animations. All combat and discoveries edit the same
journal message. Resolved outcomes remain in **Battle log**.

The private Care picker lists only injured, knocked-out or status-affected
companions, using current battle resources when a live team exists. It opens on a
knockout first, omits unnecessary paging/search controls, and removes recovered
patients after treatment. Recovery never redirects the selected item to another
companion. A companion opened explicitly from its own beast card remains an
explicit target for treats and other supported preparations.

Encounter strength is fixed to the departure team size. Solo enemies use 50%
of the full-team HP profile and 60% of its attack; pairs use 75% HP and 85% attack.
Three-member teams retain the existing profile. Losing a teammate never lowers
later enemy stats. The measured samples are in
[combat-adventure-balance.json](combat-adventure-balance.json).

The saved team keeps HP, mana, conditions, entry passives and assist charge across
fights. Overworld costs cannot knock a companion out, and recovery never revives.
Attack and defense boons cap at 20% each, recovery at 20% of surviving-team maximum
HP per run, reward bonuses at 25%, and research at one allowance. Temporary stats
expire on return and are never written into permanent collection stats. Queued
next-battle preparations are consumed once at the first fight, not on a peaceful
trip. Team preparations stay reserved until return.

There is one reward bundle per exploration charge, irrespective of pack size or
fight count. Base gold is 6–10 times the destination's existing gold reward level;
the trainer XP effort is 0.8 of a normal encounter. Team activity XP uses effort
0.85 after a won fight, or 0.2 for a peaceful trip, with the existing bounded lead
and reserve split and overlevel scaling. Returning early earns the completed
fraction. A journey with no completed route beat pays nothing. Battle-win progression
counts at most once per journey. No individual pack member pays extra rewards.

The route saves one designated recruitable encounter. Only that encounter rolls
the existing habitat-weighted capture ticket, perks, capture consumables and
chapter-six Loom guarantee. Its first opponent is explicitly marked as able to
join after victory. Hostile encounters, optional story fights and other pack
members cannot be recruited. Winning the designated encounter permits one
companion to join on return; other fights cannot reroll that ticket. Capture
consumables remain unused if the trainer retreats before that encounter. Peaceful
legendary sightings do not promise capture eligibility.
There is at most one material bundle, or one saved abandoned-nest egg replacing
it, using the prior level gates, discovery odds and value budget. Charges and
regeneration rates are unchanged. Gathering discoveries offer a saved choice among
up to three regional materials of the already rolled rarity. If that rarity has
only one possible source, gathering instead adds up to 2% to the shared capped
bundle bonus. Neither path adds an extra item or journey step.

## Maintenance and verification

Existing saved version-one and version-two expeditions retain their original
router and settlement. Newly launched journal adventures use version three with combat pacing enabled.
Unfinished story-only version-three saves upgrade on resume without spending a
charge or rerolling rewards. Existing combat cursors and their capture ticket stay
intact, and already earned supplies and reward fractions remain bankable.
Migration adds trainer-scoped progress, resolved-node, flag and recent-history
tables without rewriting existing companions or inventory. Saved version-three
battles resume exactly on startup; persistent team locks are restored.

The author contract is [ADVENTURE-CONTENT-SCHEMA.md](ADVENTURE-CONTENT-SCHEMA.md).
Permanent root, node, choice and flag IDs must not be repurposed. Author encounters
under `data/adventures/`, keep names and materials within existing canon, and run:

```text
python -m unittest tests.test_adventure_content tests.test_journeys tests.test_journey_combat tests.test_journey_service tests.test_living_catalog
python scripts/audit_living_adventures.py --output docs/living-adventure-balance.json
python scripts/audit_adventure_memory.py
python scripts/audit_combat_adventures.py
python scripts/preview_combat_adventures.py
```

The combat audit replays production rules with stated synthetic team builds;
its measured clear rates do not predict every player's performance. The
[combat preview](combat-adventure-preview/index.html)
serializes production Discord components and uses real battle artwork; it does
not claim a live Discord deployment test. Full project CI remains required before
merging.
