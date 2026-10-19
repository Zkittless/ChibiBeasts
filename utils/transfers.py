"""Fresh ownership, payment and gear transfer in one SQLite transaction."""
from datetime import datetime, timedelta, timezone
import aiosqlite
from utils import db as storage
from utils.duels import reserve_fighters


async def _transferable(db, owner, row_id, *, listing_id=None, allow_soulbound=False):
    async with db.execute('SELECT * FROM player_beasts WHERE id=? AND user_id=?', (row_id, owner)) as cursor:
        row = await cursor.fetchone()
    if not row:
        raise ValueError('This companion is no longer in that collection.')
    row = dict(row)
    if not allow_soulbound and (row['rarity'] == 'altered_divine' or row['is_altered_divine']):
        raise ValueError('Altered Divine companions are soulbound and cannot be transferred.')
    if row['is_active'] or row['raid_slot'] is not None:
        raise ValueError('Choose another active companion and remove this one from the raid party first.')
    async with db.execute('SELECT 1 FROM adventure_team WHERE user_id=? AND beast_row_id=?', (owner, row_id)) as cursor:
        if await cursor.fetchone():
            raise ValueError('Remove this companion from your adventure team before transferring or releasing it.')
    async with db.execute('SELECT id FROM beast_market WHERE beast_row_id=?', (row_id,)) as cursor:
        listing = await cursor.fetchone()
    if listing and listing['id'] != listing_id:
        raise ValueError('Delist this companion from the market before trading it.')
    return row


async def _move(db, row_id, owner, recipient):
    async with db.execute('SELECT COALESCE(MAX(player_number),0)+1 FROM player_beasts WHERE user_id=?', (recipient,)) as cursor:
        number = (await cursor.fetchone())[0]
    await db.execute('UPDATE player_beasts SET user_id=?,player_number=?,is_active=0,raid_slot=NULL WHERE id=? AND user_id=?', (recipient, number, row_id, owner))
    await db.execute('DELETE FROM adventure_team WHERE user_id=? AND beast_row_id=?', (owner, row_id))
    # Equipped armor travels with the advertised companion; runes are on its row.
    await db.execute('UPDATE player_equipment SET user_id=? WHERE beast_row_id=? AND user_id=?', (recipient, row_id, owner))
    return number


async def create_listing(owner, beast_number, price, *, days=7):
    if not isinstance(price, int) or not 100 <= price <= 10_000_000:
        raise ValueError('Choose a listing price between 100 and 10,000,000 gold.')
    with reserve_fighters([owner]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT id FROM player_beasts WHERE user_id=? AND player_number=?', (owner, beast_number)) as cursor:
                found = await cursor.fetchone()
            if not found:
                raise ValueError(f'No companion found with number #{beast_number}.')
            row = await _transferable(db, owner, found['id'])
            expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            cursor = await db.execute('INSERT INTO beast_market(seller_id,beast_row_id,ask_price,expires_at) VALUES(?,?,?,?)', (owner, row['id'], price, expires))
            await db.commit()
            return {**row, 'listing_id': cursor.lastrowid}


async def purchase_market(buyer, listing_id, expected_price):
    # Read only to discover the seller; all decisions use the locked fresh row below.
    async with storage.connect() as db:
        async with db.execute('SELECT seller_id FROM beast_market WHERE id=?', (listing_id,)) as cursor:
            seller = await cursor.fetchone()
    if not seller:
        raise ValueError('This listing has already sold or expired. Refresh the market.')
    seller = seller[0]
    if buyer == seller:
        raise ValueError('You already own this companion.')
    with reserve_fighters([buyer, seller]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT * FROM beast_market WHERE id=? AND seller_id=? AND julianday(expires_at)>julianday(?)', (listing_id, seller, datetime.now(timezone.utc).isoformat())) as cursor:
                listing = await cursor.fetchone()
            if not listing:
                raise ValueError('This listing has already sold or expired. Refresh the market.')
            price = listing['ask_price']
            if price != expected_price or price <= 0:
                raise ValueError('The price has changed. Refresh the market before buying.')
            row = await _transferable(db, seller, listing['beast_row_id'], listing_id=listing_id)
            cursor = await db.execute('UPDATE players SET gold=gold-? WHERE user_id=? AND gold>=?', (price, buyer, price))
            if not cursor.rowcount:
                raise ValueError(f'Not enough gold. You need {price:,} gold.')
            await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (price, seller))
            number = await _move(db, row['id'], seller, buyer)
            await db.execute('DELETE FROM beast_market WHERE id=?', (listing_id,))
            from utils.progress import grant_achievement
            await grant_achievement(db, buyer, 'first_market_buy')
            await grant_achievement(db, seller, 'first_market_sale')
            async with db.execute('SELECT gold FROM players WHERE user_id=?', (buyer,)) as cursor:
                balance = (await cursor.fetchone())[0]
            await db.commit()
            return {**row, 'player_number': number, 'price': price, 'balance': balance}


async def complete_trade(sender, recipient, offered_row, requested_row=None, gold=0, *, offer_key):
    if sender == recipient:
        raise ValueError('Choose another trainer to trade with.')
    if not isinstance(gold, int) or gold < 0:
        raise ValueError('Gold offered must be zero or a positive whole number.')
    with reserve_fighters([sender, recipient]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT 1 FROM trade_claims WHERE offer_key=?', (str(offer_key),)) as cursor:
                if await cursor.fetchone():
                    raise ValueError('This offer has already been accepted.')
            async with db.execute('SELECT COUNT(*) FROM players WHERE user_id IN (?,?)', (sender, recipient)) as cursor:
                if (await cursor.fetchone())[0] != 2:
                    raise ValueError('Both trainers need to begin with /start before trading.')
            await _transferable(db, sender, offered_row)
            if requested_row is not None:
                await _transferable(db, recipient, requested_row)
            cursor = await db.execute('UPDATE players SET gold=gold-? WHERE user_id=? AND gold>=?', (gold, sender, gold))
            if not cursor.rowcount:
                raise ValueError('The offering trainer no longer has enough gold.')
            await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (gold, recipient))
            offered_number = await _move(db, offered_row, sender, recipient)
            requested_number = await _move(db, requested_row, recipient, sender) if requested_row is not None else None
            cursor = await db.execute("INSERT INTO trades(sender_id,receiver_id,sender_beast_id,receiver_beast_id,gold_offered,status) VALUES(?,?,?,?,?,'completed')", (sender, recipient, offered_row, requested_row, gold))
            trade_id = cursor.lastrowid
            await db.execute('INSERT INTO trade_claims VALUES(?,?)', (str(offer_key), trade_id))
            await db.commit()
            return {'id': trade_id, 'offered_number': offered_number, 'requested_number': requested_number}


async def release_companion(owner, row_id, *, expected_species):
    """Revalidate the confirmation and return attached gear before removing a row."""
    from utils.gameplay import add_stack
    refunds = {'common': 50, 'uncommon': 150, 'rare': 500, 'epic': 1200,
               'legendary': 3000, 'divine': 8000, 'altered_divine': 20000}
    with reserve_fighters([owner]):
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            row = await _transferable(db, owner, row_id, allow_soulbound=True)
            if row['beast_id'] != expected_species:
                raise ValueError('This companion evolved after the prompt opened. Inspect it before releasing.')
            if row['rune_id']:
                await add_stack(db, 'player_inventory', owner, row['rune_id'])
            await db.execute('UPDATE player_equipment SET beast_row_id=NULL WHERE user_id=? AND beast_row_id=?', (owner, row_id))
            await db.execute('DELETE FROM player_beasts WHERE id=? AND user_id=?', (row_id, owner))
            refund = refunds.get(row['rarity'], 50)
            await db.execute('UPDATE players SET gold=gold+? WHERE user_id=?', (refund, owner))
            await db.commit()
            return refund
