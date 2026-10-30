"""Ownership, unchanged combat stats and real Gear callbacks for costumes."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import armor_art, battle_art, cosmetics, crafting, db as storage, expeditions, gear_art
from utils import hollowlight, journal_state, journal_team_art, trial_locks, trials
from utils.duels import active_fighters, persist_pve_states, prepare_state
from utils.gear_view import GearView
from utils.workshop import WorkshopView


class GhostCostumes(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        armor_art.clear_cache()
        self.lead = await self.beast('twine', starter=True)
        await storage.update_player(42, level=30)

    async def unlock(self, uid=42):
        await self.execute('INSERT OR IGNORE INTO hollowlight_unlocks VALUES(?,?,?,1)',
                           (uid, hollowlight.EVENT_ID, cosmetics.GHOST))

    async def equipped(self, uid=42):
        async with storage.connect() as db:
            return await cosmetics.equipped(db, uid)

    async def gear(self):
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (self.lead,))
        await self.execute("UPDATE player_beasts SET rune_id='bark_sigil' WHERE id=?", (self.lead,))

    async def state(self):
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (self.lead,)))[0]
        return await prepare_state(42, row, storage.get_beast_data(row['beast_id']), [], consume=False, preserve_resources=True)

    async def view(self):
        view = GearView(42)
        await view.reload()
        view.selected_id = self.lead
        view.message = SimpleNamespace(id=123, attachments=[], edit=AsyncMock())
        view.render()
        self.addCleanup(view.stop)
        return view

    async def click(self, view, button, *, uid=42):
        click = component(uid=uid)
        click.edit_original_response.return_value = view.message
        with patch.object(gear_art, 'render_portrait', new=AsyncMock(return_value=None)):
            await button.callback(click)
        return click

    async def test_only_current_owner_with_trainer_unlock_can_equip(self):
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        self.assertEqual(await self.rows('SELECT * FROM player_cosmetics'), [])
        await self.unlock()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST, expected_species='twine', expected_current=None)
        self.assertEqual(await self.equipped(), {self.lead: cosmetics.GHOST})
        await storage.get_or_create_player(43, 'Other trainer')
        await self.unlock(43)
        with self.assertRaisesRegex(ValueError, 'companion changed'):
            await cosmetics.set_equipped(43, self.lead, cosmetics.GHOST)
        self.assertEqual(await self.equipped(43), {})

    async def test_costume_changes_only_appearance_and_preserves_equipped_effects_and_resources(self):
        await self.unlock()
        await self.gear()
        await self.execute('UPDATE player_beasts SET hp=17,mana=7 WHERE id=?', (self.lead,))
        before_rows = await self.rows('SELECT * FROM player_beasts WHERE id=?', (self.lead,))
        bound = await self.rows('SELECT * FROM player_equipment')
        before = await self.state()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        after = await self.state()
        self.assertEqual(after['armor_id'], cosmetics.appearance_id(cosmetics.GHOST))
        self.assertEqual(after['stat_armor_id'], 'gnome_iron_plating')
        after.pop('armor_id')
        after.pop('stat_armor_id')
        before.pop('armor_id')
        self.assertEqual(after, before)
        self.assertEqual(await self.rows('SELECT * FROM player_beasts WHERE id=?', (self.lead,)), before_rows)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), bound)
        await cosmetics.set_equipped(42, self.lead, None, expected_current=cosmetics.GHOST)
        restored = await self.state()
        self.assertEqual(restored['armor_id'], 'gnome_iron_plating')
        self.assertEqual(restored['mana'], 7)
        await persist_pve_states(42, [restored], restored)
        self.assertEqual((await self.rows('SELECT hp,mana FROM player_beasts WHERE id=?', (self.lead,)))[0], {'hp': 17, 'mana': 7})

    async def test_costume_never_activates_gear_dormant_at_current_trainer_level(self):
        await self.unlock()
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'chronos_paradox_plate')", (self.lead,))
        await storage.update_player(42, level=1)
        before = await self.state()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        after = await self.state()
        for key in ('max_hp', 'attack', 'defense', 'speed', 'mana', 'max_mana'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(after['max_hp'], (await self.rows('SELECT max_hp FROM player_beasts WHERE id=?', (self.lead,)))[0]['max_hp'])

    async def test_transfer_does_not_inherit_previous_owners_costume_even_with_unlock(self):
        await self.unlock()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        await storage.get_or_create_player(43, 'Recipient')
        await self.unlock(43)
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (self.lead,))
        self.assertEqual(await self.equipped(), {})
        self.assertEqual(await self.equipped(43), {})
        self.assertNotIn(self.lead, await gear_art.equipped_armor_ids(42))
        self.assertNotIn(self.lead, await gear_art.equipped_armor_ids(43))
        with self.assertRaisesRegex(ValueError, 'companion changed'):
            await cosmetics.set_equipped(42, self.lead, None)
        await cosmetics.set_equipped(43, self.lead, cosmetics.GHOST, expected_current=None)
        self.assertEqual(await self.equipped(43), {self.lead: cosmetics.GHOST})
        await self.execute('DELETE FROM hollowlight_unlocks WHERE user_id=43')
        self.assertEqual(await self.equipped(43), {})

    async def test_owned_costume_wears_after_event_closes_and_removal_preserves_gear(self):
        await self.unlock()
        await self.gear()
        await self.execute('UPDATE hollowlight_events SET starts_at=1,ends_at=2,redeem_until=3')
        self.assertEqual((await hollowlight.status(42))['phase'], 'ended')
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        self.assertEqual((await gear_art.equipped_armor_ids(42))[self.lead], cosmetics.appearance_id(cosmetics.GHOST))
        await cosmetics.set_equipped(42, self.lead, None)
        self.assertEqual((await gear_art.equipped_armor_ids(42))[self.lead], 'gnome_iron_plating')
        self.assertEqual((await self.rows('SELECT rune_id FROM player_beasts WHERE id=?', (self.lead,)))[0]['rune_id'], 'bark_sigil')

    async def test_listing_and_pvp_reservations_block_costume_changes(self):
        await self.unlock()
        await self.execute("INSERT INTO beast_market(seller_id,beast_row_id,ask_price,expires_at) VALUES(42,?,100,'2100-01-01')", (self.lead,))
        with self.assertRaisesRegex(ValueError, 'Delist'):
            await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        await self.execute('DELETE FROM beast_market')
        active_fighters.add(42)
        try:
            with self.assertRaisesRegex(ValueError, 'already in a battle'):
                await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        finally:
            active_fighters.discard(42)
        self.assertEqual(await self.rows('SELECT * FROM player_cosmetics'), [])

    async def test_saved_adventure_keeps_appearance_and_prevents_writes_after_lock_cache_loss(self):
        await self.unlock()
        await self.gear()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        trip = await expeditions.begin(42, 'woods', adventure_version=3)
        saved = deepcopy(trip['payload'])
        self.assertEqual(saved['prepared_party'][0]['armor_id'], cosmetics.appearance_id(cosmetics.GHOST))
        self.assertEqual(saved['prepared_party'][0]['stat_armor_id'], 'gnome_iron_plating')
        trial_locks.unlock(42)  # Simulate cold-process cache; persisted guard must still hold.
        with self.assertRaisesRegex(ValueError, 'still exploring'):
            await cosmetics.set_equipped(42, self.lead, None)
        self.assertEqual((await expeditions.read(42, trip['id']))['payload'], saved)
        self.assertEqual(await self.equipped(), {self.lead: cosmetics.GHOST})

    async def test_saved_trial_keeps_appearance_and_prevents_writes_after_lock_cache_loss(self):
        await self.unlock()
        await self.gear()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        run = await trials.begin(42, 'woods', seed=17)
        saved = deepcopy(run['payload'])
        self.assertEqual(saved['party'][0]['armor_id'], cosmetics.appearance_id(cosmetics.GHOST))
        trial_locks.unlock(42)
        with self.assertRaisesRegex(ValueError, 'Trial is still underway'):
            await cosmetics.set_equipped(42, self.lead, None)
        current = (await self.rows('SELECT payload FROM trial_runs WHERE id=?', (run['id'],)))[0]
        self.assertEqual(json.loads(current['payload']), saved)
        self.assertEqual(await self.equipped(), {self.lead: cosmetics.GHOST})

    async def test_gear_buttons_recheck_owner_selection_current_costume_and_transfer(self):
        await self.unlock()
        other = await self.beast('fairy')
        view = await self.view()
        first = view.cosmetic_button
        foreign = await self.click(view, first, uid=43)
        foreign.response.send_message.assert_awaited_once()
        self.assertEqual(await self.equipped(), {})
        view.selected_id = other
        view.render()
        await self.click(view, first)
        self.assertIn('older beast selection', view.notice)
        self.assertEqual(await self.equipped(), {})
        view.selected_id = self.lead
        view.render()
        first = view.cosmetic_button
        await self.click(view, first)
        self.assertEqual(view.selected_id, self.lead)
        await self.click(view, first)
        self.assertIn('costume changed', view.notice)
        self.assertEqual(await self.equipped(), {self.lead: cosmetics.GHOST})
        remove = view.cosmetic_button
        await self.execute('UPDATE player_beasts SET user_id=43 WHERE id=?', (self.lead,))
        await self.click(view, remove)
        self.assertIn('no longer in your collection', view.notice)
        self.assertEqual(await self.equipped(), {})
        self.assertEqual(await self.equipped(43), {})

    async def test_gear_can_remove_saved_costume_when_its_variant_is_missing(self):
        await self.unlock()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        view = await self.view()
        with patch.object(cosmetics, 'variant_path', return_value=None):
            view.render()
            self.assertEqual(view.cosmetic_button.label, 'Show stat gear')
            self.assertFalse(view.cosmetic_button.disabled)
            await self.click(view, view.cosmetic_button)
            self.assertEqual(await self.equipped(), {})
            self.assertTrue(view.cosmetic_button.disabled)

    async def test_journal_workshop_and_team_use_visual_namespace_without_changing_stat_slot(self):
        await self.unlock()
        await self.gear()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        snapshot = await journal_state.snapshot(42)
        row = next(member for member in snapshot.workshop.companions if member['id'] == self.lead)
        self.assertEqual(row['armor_id'], 'gnome_iron_plating')
        self.assertEqual(row['rune_id'], 'bark_sigil')
        visual = cosmetics.appearance_id(cosmetics.GHOST)
        self.assertEqual(row['appearance_id'], visual)
        self.assertEqual(journal_team_art.snapshot([row])[0].armor_id, visual)
        workshop = WorkshopView(42, await crafting.workshop_state(42), selected='bark_sigil')
        self.addCleanup(workshop.stop)
        workshop.using, workshop.target_id = True, self.lead
        self.assertEqual(workshop.appearance(), ('twine', visual))
        workshop.section, workshop.selected = 'armor', 'kelpie_scale_mail'
        self.assertEqual(workshop.appearance(), ('twine', 'kelpie_scale_mail'))
        workshop.using, workshop.result_row_id = False, self.lead
        self.assertEqual(workshop.appearance(), ('twine', visual))

    async def test_actual_renderers_return_separate_costume_cutout_and_freeze_it_for_combat(self):
        await self.unlock()
        await cosmetics.set_equipped(42, self.lead, cosmetics.GHOST)
        visual = cosmetics.appearance_id(cosmetics.GHOST)
        costume = cosmetics.portrait('twine', visual)
        self.assertIsNotNone(costume)
        self.assertEqual(armor_art.portrait('twine', visual).tobytes(), costume.tobytes())
        original = costume.tobytes()
        costume.putpixel((0, 0), (255, 0, 0, 255))
        self.assertEqual(cosmetics.portrait('twine', visual).tobytes(), original)
        self.assertNotEqual(gear_art.portrait_filename('twine', visual), gear_art.portrait_filename('twine', 'gnome_iron_plating'))
        state = await self.state()
        captured = battle_art.snapshot([state])
        await cosmetics.set_equipped(42, self.lead, None)
        self.assertEqual(captured[0].armor_id, visual)
        self.assertEqual(captured[0].hp, state['hp'])
