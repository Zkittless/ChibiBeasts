# Living adventures

`/explore` opens destination selection in the existing Field Journal. Keep the
saved exploration team and press **Start adventure**. The destination determines
the people, places, wildlife and recommended companion levels; the next scene is
an actual discovery, rather than the former four-option route menu.

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
the last three familiar roots are excluded on the next visit. If fewer eligible
scenes remain, the journey ends sooner. It never restarts a completed story or
pretends a changed name is new content. Every biome retains optional single wild
encounters and packs in its repeatable pool, so clearing its stories never removes
future capture opportunities.

## Combat and rewards

Journeys have five to eight meaningful scene decisions depending on the biome.
Exploring a building can use several of those steps, and continue on another
visit. Every scene offers a safe choice; entering a fight is explicit. Packs have
two or three sequential opponents using the existing entry, attack, swap, assist
and knockout animations. Combat and authored scenes edit the same journal message.
The full narrative and reward breakdown remain in **Battle log**.

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
fraction. A journey with no completed scene pays nothing. Battle-win progression
counts at most once per journey. No individual pack member pays extra rewards.

One existing habitat-weighted capture ticket is rolled at the first fight, with
the existing perks, consumables and chapter-six Loom guarantee. Winning that
encounter permits one companion to join on return. Later fights cannot reroll
that ticket. Peaceful legendary sightings do not promise capture eligibility.
There is at most one material bundle, or one saved abandoned-nest egg replacing
it, using the prior level gates, discovery odds and value budget. Charges and
regeneration rates are unchanged. Gathering discoveries offer a saved choice among
up to three regional materials of the already rolled rarity. If that rarity has
only one possible source, gathering instead adds up to 2% to the shared capped
bundle bonus. Neither path adds an extra item or journey step.

## Maintenance and verification

Existing saved version-one and version-two expeditions retain their original
router and settlement. Newly launched journal adventures use version three.
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
python scripts/preview_living_adventures.py
```

The combat audit replays production rules with stated synthetic team builds;
its measured clear rates do not predict every player's performance. The preview
serializes production Discord components and uses real battle artwork; it does
not claim a live Discord deployment test. Full project CI remains required before
merging.
