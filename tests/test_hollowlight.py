"""Season boundaries and actual economy transactions, never a live player DB."""
import asyncio
from copy import deepcopy
import math
import random
from unittest.mock import patch

import aiosqlite

from tests.test_gameplay import DatabaseCase
from utils import db as storage, hollowlight as carnival


class HollowlightEconomy(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.lead = await self.beast('twine', starter=True)
        self.window = (await self.rows('SELECT * FROM hollowlight_events'))[0]
        self.now = self.window['starts_at'] + 10
        original = storage.get_beast_data
        definitions = {}
        for key, reference, rarity, element in (
                ('patchkin', 'goblin', 'uncommon', 'earth'),
                ('wickwing', 'unicorn', 'rare', 'fire'),
                ('veilmoth', 'dragon', 'legendary', 'shadow')):
            definitions[key] = {**deepcopy(original(reference)), 'id': key, 'name': key.title(),
                                'rarity': rarity, 'type': element, 'event_id': carnival.EVENT_ID}
        self.species = definitions
        source = patch.object(storage, 'get_beast_data', side_effect=lambda key: deepcopy(definitions[key]) if key in definitions else original(key))
        source.start()
        self.patches.append(source)

    async def money(self, amount):
        await self.execute('INSERT INTO hollowlight_wallets VALUES(?,?,?,?) ON CONFLICT(user_id,event_id) '
                           'DO UPDATE SET embers=excluded.embers,total_earned=excluded.total_earned',
                           (42, carnival.EVENT_ID, amount, amount))

    async def trip(self, key='trip', *, target=2, won=2):
        async with storage.connect() as db:
            meta = await carnival.require_active(db, self.now)
        return dict(id=key, user_id=42, payload=dict(event_id=carnival.EVENT_ID, hollowlight=meta,
                    battle_target=target, battles_completed=won, fights_won=99))

    async def award(self, trip, *, now=None):
        async with storage.connect() as db:
            await db.execute('BEGIN IMMEDIATE')
            value = await carnival.award_trip(db, trip, self.now if now is None else now)
            await db.commit()
        return value

    async def test_deployment_window_is_two_weeks_and_restarts_never_extend_it(self):
        self.assertEqual(self.window['ends_at'] - self.window['starts_at'], 14 * 86400)
        self.assertEqual(self.window['redeem_until'] - self.window['ends_at'], 7 * 86400)
        async with storage.connect() as db:
            await carnival.initialize(db, now=self.window['ends_at'] + 100)
            await db.commit()
        await storage.init_db()
        self.assertEqual((await self.rows('SELECT * FROM hollowlight_events'))[0], self.window)

    async def test_simultaneous_welcome_claims_grant_one_gift_and_one_egg(self):
        results = await asyncio.gather(*[carnival.claim_gift(42, request_key=f'gift-{index}', now=self.now) for index in range(6)])
        self.assertEqual(sum(not result['already_claimed'] for result in results), 1)
        self.assertEqual(len({result['egg']['id'] for result in results}), 1)
        inventory = {row['item_id']: row['quantity'] for row in await storage.get_inventory(42)}
        self.assertEqual(inventory, {'pixie_dust_potion': 3, 'phoenix_elixir': 1})
        status = await carnival.status(42, now=self.now)
        self.assertEqual(status['embers'], 10)
        self.assertEqual(len(status['eggs']), 1)
        self.assertEqual(status['eggs'][0]['pool'], {'patchkin': .75, 'wickwing': .25})

    async def test_gift_receipt_replays_after_closure_and_cannot_change_operation(self):
        result = await carnival.claim_gift(42, request_key='same', now=self.now)
        replay = await carnival.claim_gift(42, request_key='same', now=self.window['redeem_until'] + 1)
        self.assertEqual(result, replay)
        with self.assertRaisesRegex(ValueError, 'different choices'):
            await carnival.redeem(42, 'patchkin_egg', request_key='same', now=self.now)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 10)

    async def test_unclaimed_gift_and_new_departures_close_at_the_exact_boundary(self):
        for now in (self.window['starts_at'] - 1, self.window['ends_at']):
            with self.assertRaises(ValueError):
                await carnival.claim_gift(42, request_key=str(now), now=now)
            async with storage.connect() as db:
                with self.assertRaises(ValueError):
                    await carnival.require_active(db, now)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])

    async def test_welcome_needs_a_first_companion_and_never_blocks_starter_selection(self):
        await storage.get_or_create_player(77, 'New arrival')
        for claimed in (0, 1):
            await storage.update_player(77, starter_claimed=claimed)
            with self.assertRaisesRegex(ValueError, 'starter'):
                await carnival.claim_gift(77, request_key=f'before-start-{claimed}', now=self.now)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_eggs WHERE user_id=77'), [])

    async def test_egg_level_uses_progress_and_is_frozen_when_earned(self):
        await self.execute('UPDATE player_beasts SET level=50 WHERE id=?', (self.lead,))
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        self.assertEqual(gift['egg']['level'], 5)  # Trainer1 has only Woods1–5.
        await storage.update_player(42, level=30)
        await self.money(100)
        bought = await carnival.redeem(42, 'wickwing_egg', request_key='buy', now=self.now)
        self.assertEqual(bought['egg']['level'], 45)
        await self.execute('UPDATE player_beasts SET level=1 WHERE id=?', (self.lead,))
        result = await carnival.hatch_egg(42, bought['egg']['id'], request_key='hatch', now=self.window['redeem_until'] + 100)
        self.assertEqual(result['level'], 45)

    async def test_shop_charges_once_per_seasonal_offer_even_with_new_request_ids(self):
        await self.money(100)
        results = await asyncio.gather(*[carnival.redeem(42, 'patchkin_egg', request_key=f'buy-{index}', now=self.now)
                                         for index in range(5)], return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 70)
        self.assertEqual(len(await self.rows('SELECT * FROM hollowlight_eggs')), 1)
        self.assertEqual((await self.rows('SELECT quantity FROM hollowlight_purchases'))[0]['quantity'], 1)

    async def test_legendary_gate_and_short_funds_never_spend_currency(self):
        await self.money(150)
        with self.assertRaisesRegex(ValueError, 'level 30'):
            await carnival.redeem(42, 'veilmoth_egg', request_key='low', now=self.now)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 150)
        await storage.update_player(42, level=30)
        result = await carnival.redeem(42, 'veilmoth_egg', request_key='ready', now=self.now)
        self.assertEqual(result['egg']['pool'], {'veilmoth': 1})
        with self.assertRaisesRegex(ValueError, 'Not enough'):
            await carnival.redeem(42, 'wickwing_egg', request_key='poor', now=self.now)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 30)

    async def test_redemption_grace_allows_earned_currency_but_no_fresh_gifts(self):
        await self.money(100)
        grace = self.window['ends_at'] + 1
        self.assertEqual((await carnival.status(42, now=grace))['phase'], 'redeeming')
        result = await carnival.redeem(42, 'patchkin_egg', request_key='grace', now=grace)
        with self.assertRaises(ValueError):
            await carnival.claim_gift(42, request_key='late-gift', now=grace)
        closed = self.window['redeem_until']
        with self.assertRaises(ValueError):
            await carnival.redeem(42, 'wickwing_egg', request_key='late-buy', now=closed)
        self.assertEqual(await carnival.redeem(42, 'patchkin_egg', request_key='grace', now=closed + 1), result)
        self.assertEqual((await carnival.status(42, now=closed))['phase'], 'ended')

    async def test_unlocks_persist_after_event_without_granting_stats_or_beasts(self):
        await self.money(100)
        before = await storage.get_player_beasts(42)
        await carnival.redeem(42, carnival.GHOST_UNLOCK, request_key='skin', now=self.now)
        self.assertIn(carnival.GHOST_UNLOCK, await carnival.unlocks(42))
        async with storage.connect() as db:
            self.assertTrue(await carnival.owns_unlock(db, 42, carnival.GHOST_UNLOCK))
        self.assertEqual(await storage.get_player_beasts(42), before)
        status = await carnival.status(42, now=self.window['redeem_until'] + 1)
        self.assertIn(carnival.GHOST_UNLOCK, status['unlocks'])

    async def test_title_is_optional_and_restores_the_previous_title_safely(self):
        await self.money(100)
        with self.assertRaises(ValueError):
            await carnival.set_title(42, True, request_key='unowned')
        await carnival.redeem(42, carnival.TITLE_UNLOCK, request_key='title', now=self.now)
        await storage.update_player(42, title='Old friend')
        self.assertEqual((await carnival.set_title(42, True, request_key='wear'))['title'], carnival.TITLE)
        self.assertEqual((await carnival.set_title(42, False, request_key='remove'))['title'], 'Old friend')
        await carnival.set_title(42, True, request_key='wear-again')
        await storage.update_player(42, title='A new story title')
        self.assertEqual((await carnival.set_title(42, False, request_key='new-story'))['title'], 'A new story title')

    async def test_hatch_replay_uses_one_voucher_and_grants_one_real_companion(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        egg_id = gift['egg']['id']
        first = await carnival.hatch_egg(42, egg_id, request_key='hatch', now=self.now, rng=random.Random(4))
        second = await carnival.hatch_egg(42, egg_id, request_key='hatch', now=self.now, rng=random.Random(99))
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            await carnival.hatch_egg(42, egg_id, request_key='another', now=self.now)
        self.assertEqual(len(await storage.get_player_beasts(42)), 2)
        self.assertEqual((await self.rows('SELECT value FROM player_progress WHERE user_id=42 AND stat_key=\'hatch\''))[0]['value'], 1)
        row = (await self.rows('SELECT * FROM hollowlight_eggs'))[0]
        self.assertEqual((row['hatched'], row['notified']), (1, 1))

    async def test_owned_gift_and_paid_vouchers_hatch_after_closure_with_an_empty_roster(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        await self.money(100)
        paid = await carnival.redeem(42, 'patchkin_egg', request_key='paid', now=self.now)
        balance = (await carnival.status(42, now=self.now))['embers']
        for name, voucher in (('gift', gift['egg']), ('paid', paid['egg'])):
            with self.subTest(voucher=name):
                # Model an empty restored/legacy collection without clearing
                # the durable starter claim, paid egg or operation receipts.
                await self.execute('DELETE FROM player_beasts WHERE user_id=42')
                self.assertEqual(await storage.get_player_beasts(42), [])
                self.assertEqual((await storage.get_player(42))['starter_claimed'], 1)
                result = await carnival.hatch_egg(42, voucher['id'], request_key='hatch-' + name,
                    now=self.window['redeem_until'] + 100, rng=random.Random(4))
                replay = await carnival.hatch_egg(42, voucher['id'], request_key='hatch-' + name,
                    now=self.window['redeem_until'] + 200, rng=random.Random(99))
                self.assertEqual(result, replay)
                owned = await storage.get_player_beasts(42)
                self.assertEqual(len(owned), 1)
                self.assertEqual(owned[0]['id'], result['beast_row_id'])
                self.assertEqual(owned[0]['level'], voucher['level'])
                self.assertEqual((await storage.get_player(42))['starter_claimed'], 1)
                self.assertEqual((await carnival.status(42, now=self.now))['embers'], balance)
        eggs = await self.rows('SELECT hatched,notified FROM hollowlight_eggs')
        self.assertTrue(all(egg['hatched'] and egg['notified'] for egg in eggs))

    async def test_voucher_hatch_still_requires_an_existing_trainer(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        await self.execute('DELETE FROM players WHERE user_id=42')
        with self.assertRaisesRegex(ValueError, 'trainer'):
            await carnival.hatch_egg(42, gift['egg']['id'], request_key='orphan-hatch', now=self.now)
        self.assertEqual((await self.rows('SELECT hatched FROM hollowlight_eggs'))[0]['hatched'], 0)
        self.assertEqual(await self.rows("SELECT * FROM hollowlight_requests WHERE operation='hatch'"), [])

    async def test_missing_hatchling_definition_rolls_back_and_preserves_paid_egg(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        with patch.object(storage, 'get_beast_data', return_value=None):
            with self.assertRaisesRegex(ValueError, 'safely stored'):
                await carnival.hatch_egg(42, gift['egg']['id'], request_key='hatch', now=self.now)
        self.assertEqual((await self.rows('SELECT hatched FROM hollowlight_eggs'))[0]['hatched'], 0)
        self.assertEqual(await self.rows("SELECT * FROM hollowlight_requests WHERE operation='hatch'"), [])

    async def test_foreign_voucher_cannot_be_hatched_or_redirected_to_another_account(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        await storage.get_or_create_player(77, 'Other trainer')
        await self.beast('gloop', uid=77, starter=True)
        with self.assertRaises(ValueError):
            await carnival.hatch_egg(77, gift['egg']['id'], request_key='foreign', now=self.now)
        self.assertEqual(len(await storage.get_player_beasts(77)), 1)
        self.assertEqual((await self.rows('SELECT hatched FROM hollowlight_eggs'))[0]['hatched'], 0)

    async def test_notification_failure_cannot_repeat_payment_grant_or_hatch_progress(self):
        gift = await carnival.claim_gift(42, request_key='gift', now=self.now)
        with patch('utils.progress._track_daily_event', side_effect=RuntimeError('temporary')), self.assertLogs('chibibeasts.hollowlight', level='ERROR'):
            first = await carnival.hatch_egg(42, gift['egg']['id'], request_key='hatch', now=self.now)
        self.assertEqual((await self.rows('SELECT notified FROM hollowlight_eggs'))[0]['notified'], 0)
        second = await carnival.hatch_egg(42, gift['egg']['id'], request_key='hatch', now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(len(await storage.get_player_beasts(42)), 2)
        self.assertEqual((await self.rows("SELECT value FROM player_progress WHERE stat_key='hatch' AND user_id=42"))[0]['value'], 1)
        self.assertEqual((await self.rows('SELECT notified FROM hollowlight_eggs'))[0]['notified'], 1)

    async def test_required_wins_set_partial_reward_and_pack_foes_add_nothing(self):
        for target, won, expected in ((2, 0, 0), (2, 1, 5), (2, 2, 10), (3, 1, 3), (3, 2, 6), (3, 3, 10)):
            trip = await self.trip(f'{target}-{won}', target=target, won=won)
            self.assertEqual(await self.award(trip), expected)
            self.assertEqual(await self.award(trip), expected)
            self.assertEqual(trip['payload']['hollowlight_embers'], expected)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 34)
        self.assertEqual(len(await self.rows('SELECT * FROM hollowlight_trip_awards')), 6)

    async def test_saved_active_run_can_finish_after_redemption_and_award_cannot_be_topped_up(self):
        trip = await self.trip(target=3, won=1)
        self.assertEqual(await self.award(trip, now=self.window['redeem_until'] + 500), 3)
        trip['payload']['battles_completed'] = 3
        self.assertEqual(await self.award(trip), 3)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 3)

    async def test_forged_admission_or_battle_counts_preserve_wallet(self):
        trip = await self.trip()
        for changed in ('window', 'start', 'counts'):
            bad = deepcopy(trip)
            if changed == 'window':
                bad['payload']['hollowlight']['ends_at'] += 1
            elif changed == 'start':
                bad['payload']['hollowlight']['started_at'] = self.window['ends_at']
            else:
                bad['payload']['battles_completed'] = 20
            with self.assertRaises(ValueError):
                await self.award(bad)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 0)

    async def test_wallet_cap_and_event_awards_never_create_regular_gold(self):
        await self.money(9998)
        gold = (await storage.get_player(42))['gold']
        self.assertEqual(await self.award(await self.trip('near-cap')), 2)
        self.assertEqual(await self.award(await self.trip('at-cap')), 0)
        self.assertEqual((await carnival.status(42, now=self.now))['embers'], 10000)
        self.assertEqual((await storage.get_player(42))['gold'], gold)

    async def test_invalid_request_tokens_cannot_claim_gifts(self):
        for token in (None, True, '', 'x' * 201):
            with self.assertRaises(ValueError):
                await carnival.claim_gift(42, request_key=token, now=self.now)
        self.assertEqual(await self.rows('SELECT * FROM hollowlight_gifts'), [])

    async def test_legendary_and_costume_guarantees_fit_the_casual_event_window(self):
        rules = carnival.config()
        offers = {row['id']: row for row in rules['offers']}
        clears = math.ceil((offers['veilmoth_egg']['cost'] - rules['gift']['embers']) / rules['clear_embers'])
        self.assertLessEqual(clears, rules['duration_days'])
        self.assertEqual(offers['veilmoth_egg']['min_level'], 30)
        self.assertEqual(offers[carnival.GHOST_UNLOCK]['cost'], 30)
        self.assertTrue(all(offer['limit'] == 1 for offer in offers.values()))
