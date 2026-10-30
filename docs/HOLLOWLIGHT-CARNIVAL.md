# Hollowlight Carnival

Open `/explore`, choose **The Hollowlight Carnival**, then confirm **Start adventure** with the saved exploration team. Choosing a destination spends nothing. A Carnival departure uses one ordinary exploration charge and preserves the trainer's permanent destination preset. Seasonal difficulty scales to the departing team within normal progression limits.

Each visit requires **two encounters below companion level 26, or three from level 26 onward**, with authored discoveries between them. The strongest departing team member fixes the enemy level for the whole visit; putting a low-level lead ahead of a stronger reserve cannot lower the difficulty. Ordinary rewards remain capped to a permanent habitat band the trainer can access. Lower-level opening encounters vary by saved departure seed between one beast and a two-beast pack sharing one encounter budget. One encounter per visit can reward its first beast as a companion; other pack members stay hostile. Every route finishes with the non-recruitable **Hollow King** guardian.

The **Carnival** button inside the Adventure page opens the gift, Ember shop, stored Halloween eggs and owned title controls. These update the same Field Journal message. Purchases require a review and an exact-price confirmation; selecting an offer or egg never spends or hatches it.

## Rewards and limits

The welcome-back gift can be claimed once per trainer after choosing a starter, while the Carnival is active:

- One Hollowlight Welcome Egg: **75% Patchkin / 25% Wickwing**, with a guaranteed companion.
- Three Pixie Dust Potions, one Phoenix Elixir and **10 Lantern Embers**.

Each completed Carnival adventure awards **10 Embers**. An early return awards `floor(10 × required victories / required battles)` once for that saved trip. Optional battles and individual pack members add no Embers. The event does not multiply the normal trip's gold, material or capture bundle. Embers are a separate seasonal wallet, capped at **10,000**, and cannot be traded or substituted for gold or shards.

| Shop offer | Embers | Trainer level | Limit per trainer |
| --- | ---: | ---: | ---: |
| Guaranteed Patchkin Egg | 30 | 1 | 1 |
| Guaranteed Wickwing Egg | 50 | 1 | 1 |
| Guaranteed Veilmoth Egg | 120 | 30 | 1 |
| Ghost Costume · 2026 | 30 | 1 | 1 |
| Lanternkeeper · 2026 title | 20 | 1 | 1 |

Egg level is fixed when the gift is claimed or the egg is bought: the strongest owned companion's level, capped at the highest normal habitat ceiling unlocked by the trainer and at level 50. Later leveling cannot upgrade the stored voucher. Halloween vouchers are separate from ordinary egg inventory and normal Nursery pools. The Hollow King is a guardian and cannot join a collection.

**Veilmoth** is a fast Shadow Legendary built around blindness and a 20% heal/cleanse. Its accepted base stats are **150 HP / 95 attack / 85 defence / 105 speed / 125 mana**. A measured increase from its initial 525-point total to 560 made it more useful without matching the strongest direct-combat Legendaries: the candidate won 30.3% of equal-build peer duels with production AI, or 37.2% when a legal care policy also used third-slot healing. The [6,400-duel peer comparison](hollowlight-veilmoth-balance.json) records both policies and assumptions. The [660-route combat audit](hollowlight-combat-balance.json) covers the final opening-pack variation, fresh starters, full teams, mixed levels and prepared checkpoints; sampled fresh starter completion was 87–100%.

From the repository root, reproduce the peer comparison with `python scripts/audit_hollowlight_peers.py --output docs/hollowlight-veilmoth-balance.json`. The utility explicitly freezes both the original 525-point baseline and the accepted 560-point build.

The ghost costume is a **trainer-wide unlock**, equipped independently for each owned companion in Gear. It replaces the displayed outfit; equipped armor and runes retain their normal effects. Removing the costume restores the gear appearance. The title is optional and preserves the previous title when removed. Neither cosmetic grants stats. Seasonal species are excluded from permanent habitat/egg pools and permanent collection-completion requirements.

## Timing and permanent ownership

Event ID: `hollowlight_2026`. The first database initialization containing this release inserts one persisted start time, **14 days** of adventures/gift claims, and a further **7 days** to spend earned Embers. This is anchored to deployment, not a hard-coded Halloween date. Restarting or redeploying cannot extend it. Exact deadlines appear in the journal using Discord's local timestamp display.

Already-started adventures can settle safely after the gates close. During the seven-day grace period, new departures and unclaimed gifts are closed but the shop remains open. After grace, no new shop purchases are allowed. Owned companions, unopened eggs, costumes and titles remain owned permanently. Purchased or gifted eggs can hatch after either deadline; owned cosmetics can still be worn. Unspent Embers stay recorded without further redemption access. Seasonal species may return in future events; the **2026 cosmetics are exclusive to this edition**.

## Deployment and rollback

Deploy the code, all four new species definitions, battle/journal artwork and reviewed costume assets together. Run the ordinary database initialization against the persistent production volume; it adds the event tables and preserves any existing window and receipts. Verify the stored `hollowlight_events` row and journal deadlines after deployment. Never use a disposable preview/test database as the production volume.

Back up the database before deployment. Keep `hollowlight_events`, wallets, gift/purchase/unlock ledgers, egg vouchers, trip awards, request receipts and `player_cosmetics` intact during rollback. Do not delete rows, reset timestamps, or restore a pre-event database to reopen claims; that would also invalidate saved departure metadata or duplicate rewards.

After rewards have been granted, a rollback must retain compatible seasonal species definitions, ownership readers and artwork so existing companions and eggs remain usable. Prefer a compatibility fix that closes new entry points while retaining saved-run settlement and owned-reward access. No new administrator command is required; ordinary restarts are safe and preserve all event state.
