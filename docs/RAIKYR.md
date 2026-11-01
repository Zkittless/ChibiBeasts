# Raikyr — The Unfallen Bolt

Raikyr is a personal Developer Exclusive storm dragon with legendary combat growth. The owner can grant him with `/dev give_raikyr discord_id:…`; the recipient need not share the developer server. They must already have a trainer and active lead. Raikyr starts at that lead's current level, with legendary growth applied immediately.

There is one copy per recipient, with no global recipient cap. Retrying the command returns the existing companion without changing its level, health, mana, EXP, nickname or equipment. Hexpurr and Raikyr have independent grant records. Personal companions cannot be traded, sold or released, and do not appear in wild encounters or ordinary egg pools.

## Combat

Raikyr uses Wind combat typing, with storm-themed artwork and moves. His level-one stat budget is comparable to the regular legendary Dragon: 180 HP, 120 attack, 90 defense, 95 speed and 110 mana. The Developer badge does not grant developer growth, free damage, survival passives or other stat advantages. Training, knockouts and subsequent levels use legendary rules.

| Move | Effect |
| --- | --- |
| Volt Rend | Standard basic attack; restores 10 mana. |
| Thunderbreak | 0.65-power attack that applies Weaken; costs 15 mana. |
| Eye of the Storm | Reduces the next hit by 55%, restores 20 mana and prepares a 40% stronger next attack. |
| Heavenfall | Standard 2.5-power ultimate; consumes three ultimate charges and 50 mana. |

Normal actions build ultimate charges through the shared combat rules. Heavenfall consumes the turn, resolves one damage event and respects guard, shields, type matchups and the existing damage rules. The persistent storm aura is visual only.

## Presentation

Heavenfall uses the approved charge, lightning ascent, storm eye, descending strike, shockwave and return. It plays at 30 FPS on the existing battle canvas. Damage and resource bars use resolved combat snapshots; the renderer does not mutate game state. The aura follows the companion in duels, exploration, trials and raids, and stops when the companion is defeated or the battle ends.

The two-second aura uses a shared, bounded cache of 120 transparent layers. Each outfit is drawn separately between the rear and front layers. Cached aura pixels are never edited by callers. Glow calculations crop to visible ink plus padding before blurring, preserving the effect while reducing work. The existing 4 MB attachment budget and six-second render timeout remain enforced.

Equipped armor and cosmetics use the ordinary appearance system, so the same outfit remains visible throughout attacks and idle animation. New fitted artwork is reviewed before release.
