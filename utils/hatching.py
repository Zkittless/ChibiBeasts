"""Hatch attunement and rarity promotion without lowering an egg's quality."""
import asyncio
from collections import defaultdict
import random
import aiosqlite
from utils import db as storage
incubation_locks = defaultdict(asyncio.Lock)
RARITIES = ['common', 'uncommon', 'rare', 'epic', 'legendary', 'divine', 'altered_divine']

def promote_rates(rates, changes):
    rates = rates.copy()
    for target, amount in changes.items():
        donors = [r for r in rates if RARITIES.index(r) < RARITIES.index(target)]
        available = sum((rates[r] for r in donors))
        moved = min(amount, available)
        if not moved:
            continue
        for rarity in donors:
            rates[rarity] -= moved * rates[rarity] / available
        rates[target] = rates.get(target, 0) + moved
    total = sum(rates.values())
    return {r: weight / total for r, weight in rates.items() if weight > 0}

async def attune_hatch(user_id, rolled, roster):
    """Use an attunement on a matching beast of the rolled tier; otherwise keep it."""
    async with aiosqlite.connect(storage.DB_PATH) as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT astral_type FROM players WHERE user_id=?', (user_id,)) as c:
            row = await c.fetchone()
        if not row or not row[0]:
            return rolled
        pool = [b for b in roster.values() if b['type'] == row[0] and b['rarity'] == rolled['rarity'] and (not b.get('evolution_only')) and (not b.get('starter')) and (b.get('id') not in {'prismite', 'twine', 'gloop', 'barkley'})]
        if not pool:
            return rolled
        result = random.choice(pool)
        await db.execute('UPDATE players SET astral_type=NULL WHERE user_id=?', (user_id,))
        await db.commit()
        return result
