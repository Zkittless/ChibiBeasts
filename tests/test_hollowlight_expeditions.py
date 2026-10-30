"""Carnival routing uses saved encounters, one charge and seasonal admission."""
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from utils import adventure_content, adventure_party, db as storage, expeditions, hatching, hollowlight
from utils import journey_combat, journey_service, journeys, nursery_catalog, trial_combat
from utils.habitats import BIOMES, carnival_underlay, encounter_pool, eligible


class CarnivalContent(unittest.TestCase):
    def test_carnival_has_a_separate_valid_catalog_without_new_permanent_bands(self):
        document = adventure_content.load('hollowlight')
        self.assertEqual(adventure_content.validate_all({'hollowlight': document}), [])
        self.assertEqual(len(document['encounters']), 26)
        self.assertEqual(sum(len(root['nodes']) for root in document['encounters']), 32)
        self.assertEqual(len(BIOMES), 10)
        self.assertTrue(adventure_content.belongs('hollowlight_2026.theatre.scene_0', 'hollowlight'))
        self.assertFalse(adventure_content.belongs('hollowlight_2026.theatre.scene_0', 'woods'))

    def test_event_species_cannot_enter_permanent_wild_nursery_or_boss_pools(self):
        for species in ('patchkin', 'wickwing', 'veilmoth', 'hollow_king'):
            beast = storage.get_beast_data(species)
            self.assertIsNotNone(beast)
            self.assertFalse(eligible(beast))
            self.assertFalse(nursery_catalog._eligible(beast))
        for realm in BIOMES:
            for rarity in ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine'):
                self.assertFalse(any(beast.get('event_id') for beast in encounter_pool(rarity, realm)))
        self.assertEqual(encounter_pool('rare', 'hollowlight'), [])
        self.assertEqual(expeditions.hollowlight_pool('legendary', 29), [])
        self.assertEqual([beast['id'] for beast in expeditions.hollowlight_pool('legendary', 30)], ['veilmoth'])
        self.assertEqual(expeditions.hollowlight_pool('epic', 50), [])

    def test_underlay_respects_trainer_access_and_strongest_companion(self):
        self.assertEqual(carnival_underlay(1, 50), 'woods')
        self.assertEqual(carnival_underlay(50, 1), 'woods')
        self.assertEqual(carnival_underlay(50, 50), 'loom_deep')
        self.assertEqual(carnival_underlay(6, 15), 'ember')

    def test_low_level_carnival_openers_vary_by_seed_with_a_saved_guardian_finish(self):
        schedules = set()
        member = dict(id=1, beast_id='twine', name='Twine', type='cosmic')
        for seed in range(20):
            payload = dict(event_id=hollowlight.EVENT_ID, event_level=1)
            journeys.initialize(payload, 'hollowlight', seed, [member], combat_route=True)
            self.assertEqual(payload['battle_target'], 2)
            self.assertEqual(payload['recruitable_beat'], 0)
            self.assertEqual(payload['battle_schedule'][-1], 'single')
            schedules.add(tuple(payload['battle_schedule']))
            self.assertEqual(payload['page']['choices'][0]['effects']['battle'], payload['battle_schedule'][0])
        self.assertEqual(schedules, {('single', 'single'), ('pack2', 'single')})

    def test_fight_plan_can_freeze_seasonal_levels_without_altering_normal_defaults(self):
        normal = journey_combat.plan('woods', 73, 'single', 50)
        self.assertEqual(normal['waves'][0]['level'], 5)
        event = journey_combat.carnival_plan('woods', 73, 'single', 50, ticket='wickwing')
        self.assertEqual(event['waves'][0]['level'], 50)
        self.assertEqual(event['waves'][0]['species'], 'wickwing')
        guardian = journey_combat.carnival_plan('woods', 73, 'pack3', 1, ticket='veilmoth', guardian=True)
        self.assertEqual(guardian['total'], 1)
        self.assertEqual(guardian['waves'][0]['species'], 'hollow_king')
        self.assertTrue(guardian['waves'][0]['event_guardian'])
        hostile = journey_combat.carnival_plan('woods', 73, 'single', 1)
        self.assertEqual(hostile['waves'][0]['enemy_template']['armor_id'], 'cosmetic:ghost_costume_2026')
        self.assertEqual(event, json.loads(json.dumps(event)))


class CarnivalExpeditions(DatabaseCase):
    async def ready(self, *, trainer=1, reserve_level=None):
        await storage.update_player(42, level=trainer)
        self.ids = [await self.beast('twine', starter=True)]
        if reserve_level is not None:
            reserve = await self.beast('gloop')
            await self.execute('UPDATE player_beasts SET level=? WHERE id=?', (reserve_level, reserve))
            await adventure_party.set_slot(42, reserve, 1)
            self.ids.append(reserve)
        async with storage.connect() as db:
            self.window = await hollowlight.window(db)

    async def begin(self, *, now=None, seed=9):
        from utils.duels import prepare_state
        async def routing_fixture(*args, **kwargs):
            state = await prepare_state(*args, **kwargs)
            # Resolve real moves/AI and writes quickly; balance is measured by
            # the separate fixed-build audit, not this routing fixture.
            state.update(attack=1000000, base_attack=1000000, speed=10000, base_speed=10000,
                         hp=1000000, max_hp=1000000)
            return state
        with patch('utils.duels.prepare_state', side_effect=routing_fixture):
            return await expeditions.begin(42, 'hollowlight', adventure_version=3,
                now=self.window['starts_at'] + 1 if now is None else now, rng=random.Random(seed))

    async def finish_encounter(self, trip):
        for _ in range(100):
            if trip['status'] != 'battle':
                return trip
            state = trip['payload']['combat']
            action = 'next' if state['status'] == 'between' else next(
                action['choice'] for action in trial_combat.legal_actions(state) if action['enabled'])
            trip = await journey_service.battle_action(42, trip['id'], trip['version'], action)
        self.fail('A Carnival encounter failed to terminate.')

    async def progress(self, trip):
        if trip['status'] == 'battle':
            return await self.finish_encounter(trip)
        choices = trip['payload']['page']['choices']
        choice = next((choice['value'] for choice in choices if choice['value'].startswith('fight:')), None)
        if choice is None:
            choice = next(choice['value'] for choice in choices
                          if choice['value'] not in {'return', 'return_egg', 'return_early'}
                          and not choice.get('effects', {}).get('battle')
                          and not choice.get('effects', {}).get('hp_cost'))
        return await expeditions.choose(42, trip['id'], trip['version'], choice)

    async def homeward(self, trip):
        for _ in range(40):
            if trip['payload']['journey_status'] == 'homeward':
                return trip
            trip = await self.progress(trip)
        self.fail('The Carnival itinerary did not reach its return page.')

    async def balance(self):
        async with storage.connect() as db:
            async with db.execute('SELECT embers FROM hollowlight_wallets WHERE user_id=?', (42,)) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0

    async def test_saved_carnival_route_has_one_capture_and_a_nonrecruitable_final_guardian(self):
        await self.ready()
        before = (await expeditions.energy(42)).charges
        with patch('utils.expeditions._encounter', wraps=expeditions._encounter) as capture:
            trip = await self.begin()
            self.assertEqual(trip['realm'], 'woods')
            p = trip['payload']
            self.assertEqual(p['destination'], 'hollowlight')
            self.assertEqual(p['visual_realm'], 'hollowlight')
            self.assertEqual(p['event_id'], hollowlight.EVENT_ID)
            self.assertEqual(p['battle_target'], 2)
            self.assertEqual(p['battle_schedule'][-1], 'single')
            self.assertIn(p['battle_schedule'][0], {'single', 'pack2'})
            self.assertEqual(p['recruitable_beat'], 0)
            self.assertEqual(p['event_level'], 1)
            self.assertEqual((await expeditions.energy(42)).charges, before - 1)
            frozen = deepcopy(p['combat'])
            self.assertEqual((await expeditions.current(42))['payload']['combat'], frozen)
            trip = await self.homeward(trip)
            self.assertEqual(capture.await_count, 1)
            self.assertFalse(trip['payload']['capture_fight'])
            self.assertEqual(trip['payload']['combat']['plan']['waves'][0]['species'], 'hollow_king')
            self.assertNotEqual(trip['payload']['encounter'], 'hollow_king')
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
        self.assertEqual(trip['status'], 'complete')
        self.assertEqual(trip['payload']['hollowlight_embers'], 10)
        self.assertEqual(await self.balance(), 10)
        with self.assertRaises(ValueError):
            await expeditions.choose(42, trip['id'], trip['version'] - 1, 'return')
        self.assertEqual(await self.balance(), 10)
        self.assertEqual(len(await self.rows('SELECT * FROM player_materials')), 1)

    async def test_mixed_level_party_freezes_strongest_level_without_loot_band_exploit(self):
        await self.ready(trainer=50, reserve_level=50)
        trip = await self.begin()
        self.assertEqual(trip['realm'], 'loom_deep')
        self.assertEqual(trip['payload']['event_level'], 50)
        self.assertEqual(trip['payload']['battle_target'], 3)
        self.assertLess(trip['payload']['recruitable_beat'], 2)
        self.assertTrue(all(wave['level'] == 50 for wave in trip['payload']['combat']['plan']['waves']))
        trip = await self.homeward(trip)
        guardian = trip['payload']['combat']['plan']
        self.assertEqual(guardian['waves'][0]['level'], 50)
        self.assertTrue(all(wave['departure_party_size'] == 2 for wave in guardian['waves']))

    async def test_new_departure_after_close_spends_no_charge(self):
        await self.ready()
        before = (await expeditions.energy(42)).charges
        with self.assertRaisesRegex(ValueError, 'closed'):
            await self.begin(now=self.window['ends_at'] + 1)
        self.assertEqual(await self.rows('SELECT * FROM expeditions'), [])
        self.assertEqual((await expeditions.energy(42)).charges, before)

    async def test_saved_adventure_can_finish_and_bank_after_the_season_closes(self):
        await self.ready()
        trip = await self.begin()
        with patch('utils.journey_service.time.time', return_value=self.window['ends_at'] + 60):
            trip = await self.homeward(trip)
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
        self.assertEqual(trip['status'], 'complete')
        self.assertEqual(trip['payload']['hollowlight_embers'], 10)
        self.assertEqual(await self.balance(), 10)

    async def test_retreat_before_a_win_awards_no_embers_or_bundle(self):
        await self.ready()
        trip = await self.begin()
        trip = await journey_service.battle_action(42, trip['id'], trip['version'], 'return')
        self.assertEqual(trip['payload']['hollowlight_embers'], 0)
        self.assertEqual(await self.balance(), 0)
        self.assertFalse(trip['payload']['material_granted'])
        self.assertFalse(trip['payload']['caught'])

    async def test_partial_win_pays_only_the_saved_required_battle_fraction(self):
        await self.ready()
        trip = await self.finish_encounter(await self.begin())
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual(trip['payload']['hollowlight_embers'], 5)
        self.assertEqual(await self.balance(), 5)
        self.assertEqual(trip['payload']['outcome'], 'retreated')

    async def test_carnival_cannot_consume_a_loom_key_or_count_as_a_loom_visit(self):
        await self.ready(trainer=50, reserve_level=50)
        await storage.update_player(42, prism_keys=1, divine_compass_charges=1)
        trip = await self.homeward(await self.begin())
        trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
        player = await storage.get_player(42)
        self.assertEqual((player['prism_keys'], player['divine_compass_charges']), (1, 1))
        self.assertNotIn(trip['payload']['encounter'], {'loom_fragment', 'hollow_king'})
        self.assertEqual(await self.rows("SELECT * FROM achievements WHERE user_id=42 AND achievement_id='first_celestial_loom'"), [])

    async def test_normal_hatch_attunement_cannot_select_event_species(self):
        await storage.update_player(42, astral_type='fire')
        rolled = storage.get_beast_data('unicorn')
        season = storage.get_beast_data('wickwing')
        self.assertEqual(rolled['rarity'], season['rarity'])
        roster = {beast['id']: beast for beast in (rolled, season)}
        result = await hatching.attune_hatch(42, rolled, roster)
        self.assertEqual(result['id'], rolled['id'])
        self.assertEqual((await storage.get_player(42))['astral_type'], 'fire')
