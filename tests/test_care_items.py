"""Care previews stay bound to the reviewed beast, resources and live team."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import adventure_party, care, combat, db as storage
from utils.duels import active_fighters, persist_pve_states, prepare_state
from utils.items import use_item


class CareItems(DatabaseCase):
    async def row(self, row_id):
        return (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]

    async def quantity(self, item_id):
        rows = await self.rows('SELECT COALESCE(SUM(quantity),0) AS amount FROM player_inventory WHERE user_id=42 AND item_id=?', (item_id,))
        return rows[0]['amount']

    async def review(self, item_id, row_id):
        state = await care.snapshot(42, row_id)
        return state, care.preview(state, item_id)

    async def apply(self, state, preview, *, choice=None):
        return await use_item(42, preview.item_id, choice=choice,
            beast_number=state.beast['player_number'], expected_row_id=state.beast['id'],
            expected_species=state.beast['beast_id'], expected_care_state=preview.fingerprint)

    async def injured(self, species='twine', *, starter=True):
        row_id = await self.beast(species, starter=starter)
        await self.execute('UPDATE player_beasts SET hp=10 WHERE id=?', (row_id,))
        return row_id

    async def live_party(self, *, armored=False):
        lead = await self.beast('twine', starter=True)
        reserve = await self.beast('fairy')
        await adventure_party.set_slot(42, reserve, 1)
        if armored:
            await storage.update_player(42, level=50)
            await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'mossbark_wrapping')", (reserve,))
        party = [await prepare_state(42, row, storage.get_beast_data(row['beast_id']), [], consume=False)
                 for row in await adventure_party.rows(42)]
        combat.active_states[42], combat.active_parties[42] = party[0], party
        return lead, reserve, party

    async def test_renumbered_review_never_heals_the_companion_in_the_old_number(self):
        lead = await self.injured()
        selected = await self.injured('fairy', starter=False)
        await storage.add_item(42, 'pixie_dust_potion')
        state, preview = await self.review('pixie_dust_potion', selected)
        old_number = state.beast['player_number']
        await self.execute('UPDATE player_beasts SET player_number=9 WHERE id=?', (selected,))
        await self.execute('UPDATE player_beasts SET player_number=? WHERE id=?', (old_number, lead))
        with self.assertRaises(ValueError):
            await self.apply(state, preview)
        self.assertEqual(await self.quantity(preview.item_id), 1)
        self.assertEqual((await self.row(lead))['hp'], 10)
        fresh, fresh_preview = await self.review(preview.item_id, selected)
        self.assertEqual(fresh.beast['id'], selected)
        self.assertEqual(fresh.beast['player_number'], 9)
        self.assertTrue(fresh_preview.usable)
        await self.apply(fresh, fresh_preview)
        self.assertGreater((await self.row(selected))['hp'], 10)
        self.assertEqual((await self.row(lead))['hp'], 10)

    async def test_ownership_loss_and_species_change_reject_old_target_without_spending(self):
        await storage.get_or_create_player(99, 'Recipient')
        await storage.add_item(42, 'pixie_dust_potion')
        for change in ('transfer', 'evolve'):
            with self.subTest(change=change):
                selected = await self.injured('twine', starter=False)
                state, preview = await self.review('pixie_dust_potion', selected)
                if change == 'transfer':
                    await self.execute('UPDATE player_beasts SET user_id=99 WHERE id=?', (selected,))
                else:
                    await self.execute("UPDATE player_beasts SET beast_id='timestitch' WHERE id=?", (selected,))
                with self.assertRaises(ValueError):
                    await self.apply(state, preview)
                self.assertEqual(await self.quantity(preview.item_id), 1)
                self.assertEqual((await self.row(selected))['hp'], 10)

    async def test_listing_blocks_both_individual_care_and_an_affected_party_member(self):
        lead = await self.injured()
        reserve = await self.beast('fairy')
        await adventure_party.set_slot(42, reserve, 1)
        await storage.add_item(42, 'pixie_dust_potion')
        await storage.add_item(42, 'tear_of_leviathan')
        individual = await self.review('pixie_dust_potion', lead)
        party = await self.review('tear_of_leviathan', lead)
        await self.execute("INSERT INTO beast_market(seller_id,beast_row_id,ask_price,expires_at) VALUES(42,?,100,'2099-01-01')", (lead,))
        for state, preview in (individual, party):
            with self.assertRaises(ValueError):
                await self.apply(state, preview)
            self.assertEqual(await self.quantity(preview.item_id), 1)
        _, fresh = await self.review('tear_of_leviathan', lead)
        self.assertFalse(fresh.usable)
        self.assertIn('Delist', fresh.reason)
        self.assertEqual((await self.row(lead))['hp'], 10)

    async def test_changed_health_requires_a_fresh_effect_review_without_spending(self):
        selected = await self.injured()
        await storage.add_item(42, 'pixie_dust_potion')
        state, preview = await self.review('pixie_dust_potion', selected)
        self.assertTrue(preview.usable)
        await self.execute('UPDATE player_beasts SET hp=12 WHERE id=?', (selected,))
        with self.assertRaises(ValueError):
            await self.apply(state, preview)
        self.assertEqual(await self.quantity(preview.item_id), 1)
        self.assertEqual((await self.row(selected))['hp'], 12)

    async def test_changed_live_condition_or_party_assignment_invalidates_review(self):
        lead, reserve, party = await self.live_party()
        party[1]['hp'] = 10
        await storage.add_item(42, 'aether_tonic')
        state, preview = await self.review('aether_tonic', reserve)
        party[1]['status'] = 'blight'
        with self.assertRaises(ValueError):
            await self.apply(state, preview)
        self.assertEqual(await self.quantity(preview.item_id), 1)
        self.assertEqual(party[1]['status'], 'blight')
        combat.active_states.clear()
        combat.active_parties.clear()
        await self.execute('UPDATE player_beasts SET hp=10 WHERE id=?', (lead,))
        await storage.add_item(42, 'tear_of_leviathan')
        state, preview = await self.review('tear_of_leviathan', lead)
        replacement = await self.beast('imp')
        await adventure_party.set_slot(42, replacement, 1)
        with self.assertRaises(ValueError):
            await self.apply(state, preview)
        self.assertEqual(await self.quantity(preview.item_id), 1)
        self.assertEqual((await self.row(lead))['hp'], 10)

    async def test_full_health_healthy_party_and_zero_rounding_heal_keep_items(self):
        selected = await self.beast('twine', starter=True)
        for item_id in ('pixie_dust_potion', 'tear_of_leviathan'):
            await storage.add_item(42, item_id)
            state, preview = await self.review(item_id, selected)
            self.assertFalse(preview.usable)
            with self.assertRaises(ValueError):
                await self.apply(state, preview)
            self.assertEqual(await self.quantity(item_id), 1)
        await self.execute('UPDATE player_beasts SET max_hp=4,hp=1 WHERE id=?', (selected,))
        state, tiny = await self.review('pixie_dust_potion', selected)
        self.assertFalse(tiny.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, tiny)
        self.assertEqual(await self.quantity(tiny.item_id), 1)
        self.assertEqual((await self.row(selected))['hp'], 1)

    async def test_knockout_needs_revive_and_elixir_matches_preview_for_selected_reserve(self):
        lead = await self.beast('twine', starter=True)
        selected = await self.beast('fairy')
        await storage.knockout_beast(selected, 'common')
        for item_id in ('pixie_dust_potion', 'phoenix_elixir'):
            await storage.add_item(42, item_id)
        state, ordinary = await self.review('pixie_dust_potion', selected)
        self.assertFalse(ordinary.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, ordinary)
        state, revive = await self.review('phoenix_elixir', selected)
        expected_hp = max(1, state.beast['max_hp'] // 2)
        self.assertTrue(revive.usable)
        self.assertIn(f'{expected_hp}/{state.beast["max_hp"]} HP', revive.text)
        await self.apply(state, revive)
        saved = await self.row(selected)
        self.assertEqual(saved['hp'], expected_hp)
        self.assertIsNone(saved['knocked_out_until'])
        self.assertEqual(await self.quantity('phoenix_elixir'), 0)
        self.assertEqual(await self.quantity('pixie_dust_potion'), 1)
        self.assertEqual((await self.row(lead))['hp'], (await self.row(lead))['max_hp'])

    async def test_live_knockout_can_revive_after_its_collection_timer_expires(self):
        _, reserve, party = await self.live_party()
        # The battle keeps its own equipment-adjusted maximum and KO state.
        # Opening Care applies expired collection recovery, without reviving
        # this still-fallen member of the running battle.
        party[1].update(hp=0, max_hp=100, ko_recorded=True, status='poison')
        await self.execute("UPDATE player_beasts SET hp=0,knocked_out_until='2000-01-01' WHERE id=?", (reserve,))
        await storage.add_item(42, 'phoenix_elixir')
        state, preview = await self.review('phoenix_elixir', reserve)
        self.assertEqual(state.beast['hp'], state.beast['max_hp'])
        self.assertEqual(state.target['hp'], 0)
        self.assertTrue(preview.usable)
        self.assertIn('0/100 → 50/100 HP', preview.text)
        result = await self.apply(state, preview)
        self.assertIn('Revived with 50 HP', result)
        self.assertEqual(party[1]['hp'], 50)
        self.assertIsNone(party[1]['status'])
        self.assertFalse(party[1]['ko_recorded'])
        saved = await self.row(reserve)
        self.assertEqual(saved['hp'], saved['max_hp'] // 2)
        self.assertIsNone(saved['knocked_out_until'])
        self.assertEqual(await self.quantity(preview.item_id), 0)
        await persist_pve_states(42, party, party[0])
        persisted = await self.row(reserve)
        self.assertEqual(persisted['hp'], saved['hp'])
        self.assertIsNone(persisted['knocked_out_until'])

    async def test_live_reserve_blight_cure_and_heal_persist_without_healing_the_lead(self):
        lead, reserve, party = await self.live_party(armored=True)
        self.assertGreater(party[1]['max_hp'], (await self.row(reserve))['max_hp'])
        party[1].update(hp=10, status='blight', status_turns=2)
        lead_hp = party[0]['hp']
        for item_id in ('pixie_dust_potion', 'aether_tonic'):
            await storage.add_item(42, item_id)
        state, blocked = await self.review('pixie_dust_potion', reserve)
        self.assertFalse(blocked.usable)
        self.assertIn('Blight', blocked.reason)
        with self.assertRaises(ValueError):
            await self.apply(state, blocked)
        state, tonic = await self.review('aether_tonic', reserve)
        self.assertTrue(tonic.usable)
        self.assertIn('Clear the current condition', tonic.text)
        await self.apply(state, tonic)
        self.assertIsNone(party[1]['status'])
        self.assertTrue(party[1]['status_ward'])
        await storage.add_item(42, 'aether_tonic')
        state, redundant_ward = await self.review('aether_tonic', reserve)
        self.assertFalse(redundant_ward.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, redundant_ward)
        self.assertEqual(await self.quantity('aether_tonic'), 1)
        state, heal = await self.review('pixie_dust_potion', reserve)
        self.assertTrue(heal.usable)
        expected_hp = min(party[1]['max_hp'], 10 + party[1]['max_hp'] // 5)
        self.assertIn(f'→ {expected_hp}/{party[1]["max_hp"]} HP', heal.text)
        await self.apply(state, heal)
        self.assertEqual(party[1]['hp'], expected_hp)
        self.assertEqual(party[0]['hp'], lead_hp)
        await persist_pve_states(42, party, party[0])
        saved = await self.row(reserve)
        self.assertEqual(saved['hp'], max(1, saved['max_hp'] * expected_hp // party[1]['max_hp']))
        self.assertEqual((await self.row(lead))['hp'], lead_hp)
        self.assertEqual(await self.quantity('pixie_dust_potion'), 0)
        self.assertEqual(await self.quantity('aether_tonic'), 1)
        combat.active_states.clear()
        combat.active_parties.clear()
        await storage.update_player(42, status_ward_charges=1)
        state, queued_ward = await self.review('aether_tonic', reserve)
        self.assertTrue(queued_ward.usable)
        self.assertIn('1 → 2', queued_ward.text)
        await self.apply(state, queued_ward)
        self.assertEqual((await storage.get_player(42))['status_ward_charges'], 2)
        self.assertEqual(await self.quantity('aether_tonic'), 0)

    async def test_live_party_recovery_shows_numbers_and_persists_only_the_current_team(self):
        lead, reserve, party = await self.live_party()
        stranger = await self.beast('imp')
        for row_id in (reserve, stranger):
            await storage.knockout_beast(row_id, 'common')
        party[0]['hp'] = 10
        party[1].update(hp=0, status='poison', ko_recorded=True)
        await storage.add_item(42, 'tear_of_leviathan')
        state, preview = await self.review('tear_of_leviathan', reserve)
        self.assertTrue(preview.usable)
        self.assertTrue(preview.requires_confirmation)
        self.assertIn('current battle team', preview.text)
        self.assertEqual({row['id'] for row in state.party}, {lead, reserve})
        for member in state.party:
            self.assertIn(f"#{member['player_number']}", preview.text)
        await self.apply(state, preview)
        self.assertTrue(all(row['hp'] == row['max_hp'] and row['status'] is None for row in party))
        self.assertFalse(party[1]['ko_recorded'])
        await persist_pve_states(42, party, party[0])
        for row_id in (lead, reserve):
            saved = await self.row(row_id)
            self.assertEqual(saved['hp'], saved['max_hp'])
            self.assertIsNone(saved['knocked_out_until'])
        self.assertTrue(storage.is_knocked_out(await self.row(stranger)))
        self.assertEqual(await self.quantity(preview.item_id), 0)

    async def test_growth_preview_and_commit_share_the_remaining_sixty_exp_allowance(self):
        selected = await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET level=10,exp=0 WHERE id=?', (selected,))
        date = datetime.now(timezone.utc).date().isoformat()
        await self.execute('INSERT INTO player_progress(user_id,stat_key,value) VALUES(42,?,55)', (f'activity_xp:{selected}:feed:{date}',))
        await storage.add_item(42, 'gnome_grown_carrots')
        await storage.add_item(42, 'gnome_grown_carrots')
        state, preview = await self.review('gnome_grown_carrots', selected)
        self.assertTrue(preview.usable)
        self.assertIn('+5 EXP', preview.text)
        self.assertIn('55/60 → 60/60', preview.text)
        result = await self.apply(state, preview)
        self.assertIn('Gained 5 EXP', result)
        saved = await self.row(selected)
        self.assertEqual((saved['level'], saved['exp']), (10, 5))
        state, capped = await self.review('gnome_grown_carrots', selected)
        self.assertFalse(capped.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, capped)
        self.assertEqual(await self.quantity(preview.item_id), 1)
        await self.execute('UPDATE player_beasts SET level=50 WHERE id=?', (selected,))
        _, max_level = await self.review('gnome_grown_carrots', selected)
        self.assertFalse(max_level.usable)
        self.assertIn('level 50', max_level.reason)

    async def test_ambrosia_preview_and_commit_stop_at_twenty_five_percent(self):
        selected = await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET damage_bonus=24,happiness=40 WHERE id=?', (selected,))
        await storage.add_item(42, 'ambrosia_tart')
        await storage.add_item(42, 'ambrosia_tart')
        state, preview = await self.review('ambrosia_tart', selected)
        self.assertTrue(preview.usable)
        self.assertTrue(preview.requires_confirmation)
        self.assertIn('+24% → +25%', preview.text)
        await self.apply(state, preview)
        saved = await self.row(selected)
        self.assertEqual((saved['damage_bonus'], saved['happiness']), (25, 100))
        state, capped = await self.review('ambrosia_tart', selected)
        self.assertFalse(capped.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, capped)
        self.assertEqual(await self.quantity(preview.item_id), 1)

    async def test_stat_preview_discloses_random_roll_and_last_treat_commits_once(self):
        selected = await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET stat_treats_used=29 WHERE id=?', (selected,))
        await storage.add_item(42, 'star_candy_shards')
        await storage.add_item(42, 'star_candy_shards')
        state = await care.snapshot(42, selected)
        with patch('utils.items.random.choice', side_effect=AssertionError('Preview rolled a permanent stat')):
            preview = care.preview(state, 'star_candy_shards')
        self.assertTrue(preview.usable)
        self.assertTrue(preview.requires_confirmation)
        self.assertIn('random Attack, Defense or Speed', preview.text)
        self.assertIn('29/30 → 30/30', preview.text)
        before = await self.row(selected)
        with patch('utils.items.random.choice', return_value='attack'):
            await self.apply(state, preview)
        saved = await self.row(selected)
        self.assertEqual(saved['attack'], before['attack'] + 3)
        self.assertEqual((saved['defense'], saved['speed']), (before['defense'], before['speed']))
        self.assertEqual(saved['stat_treats_used'], 30)
        state, capped = await self.review('star_candy_shards', selected)
        self.assertFalse(capped.usable)
        with self.assertRaises(ValueError):
            await self.apply(state, capped)
        self.assertEqual(await self.quantity(preview.item_id), 1)

    async def test_preparing_or_settling_battle_does_not_consume_care_items(self):
        lead, _, party = await self.live_party()
        party[0].update(hp=10, closed=True)
        await storage.add_item(42, 'pixie_dust_potion')
        for mode in ('settling', 'preparing'):
            with self.subTest(mode=mode):
                if mode == 'preparing':
                    combat.active_states.clear()
                    combat.active_parties.clear()
                    active_fighters.add(42)
                state, preview = await self.review('pixie_dust_potion', lead)
                self.assertFalse(preview.usable)
                self.assertIn('preparing or settling', preview.reason)
                with self.assertRaises(ValueError):
                    await self.apply(state, preview)
                self.assertEqual(await self.quantity(preview.item_id), 1)

    async def test_duplicate_guarded_use_heals_and_spends_only_once(self):
        selected = await self.injured()
        await storage.add_item(42, 'pixie_dust_potion')
        state, preview = await self.review('pixie_dust_potion', selected)
        expected_hp = min(state.beast['max_hp'], 10 + state.beast['max_hp'] // 5)
        results = await asyncio.gather(self.apply(state, preview), self.apply(state, preview), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual((await self.row(selected))['hp'], expected_hp)
        self.assertEqual(await self.quantity(preview.item_id), 0)
