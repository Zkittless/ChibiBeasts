"""Combat-first routes use real saved fights, one itinerary and one reward budget."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import random
from unittest.mock import Mock, patch

from tests.test_gameplay import DatabaseCase
from utils import adventure_party, db as storage, expeditions, journey_service, journeys, trial_combat, trial_locks
from utils.habitats import BIOMES


class CombatAdventures(DatabaseCase):
    async def ready(self):
        from utils.progress import ACHIEVEMENTS
        await storage.update_player(42, level=50)
        async with storage.connect() as db:
            await db.executemany('INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(?,?)',
                                 [(42, key) for key in ACHIEVEMENTS])
            await db.commit()
        self.ids = [await self.beast('twine', starter=True)]
        for slot, species in enumerate(('gloop', 'barkley'), 1):
            row = await self.beast(species)
            await adventure_party.set_slot(42, row, slot)
            self.ids.append(row)

    async def begin(self, realm='woods', seed=9, *, combat_route=True):
        from utils.duels import prepare_state
        async def prepared(*args, **kwargs):
            state = await prepare_state(*args, **kwargs)
            # Deterministic routing fixtures still use actual preparation,
            # move resolution, enemy AI, resource persistence and settlement.
            state.update(attack=1000000, base_attack=1000000,
                         speed=10000, base_speed=10000)
            return state
        rng = Mock(wraps=random.Random(9))
        rng.getrandbits.return_value = seed
        with patch('utils.duels.prepare_state', side_effect=prepared):
            return await expeditions.begin(42, realm, rng=rng, adventure_version=3,
                                           combat_route=combat_route)

    async def save(self, trip):
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?',
                           (json.dumps(trip['payload']), trip['id']))
        return await expeditions.read(42, trip['id'])

    async def unmark_old_save(self, trip):
        # Compatibility fixtures produce the deployed narrative v3 shape;
        # remove only their explicit opt-out marker to model a real old save.
        self.assertEqual(trip['payload'].pop('pacing_version'), 0)
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?',
                           (json.dumps(trip['payload']), trip['id']))

    async def legacy_step(self, trip):
        choice = next(c['value'] for c in trip['payload']['page']['choices']
                      if not c.get('effects', {}).get('battle')
                      and not c.get('effects', {}).get('material'))
        return await expeditions.choose(42, trip['id'], trip['version'], choice)

    async def act(self, trip):
        state = trip['payload']['combat']
        action = 'next' if state['status'] == 'between' else next(
            a['choice'] for a in trial_combat.legal_actions(state) if a['enabled'])
        return await journey_service.battle_action(42, trip['id'], trip['version'], action)

    async def finish_encounter(self, trip):
        for _ in range(40):
            if trip['status'] != 'battle':
                return trip
            trip = await self.act(trip)
        self.fail('A saved mandatory encounter did not reach its boundary.')

    async def choose_interlude(self, trip):
        choices = trip['payload']['page']['choices']
        choice = next(c['value'] for c in choices
                      if c['value'] not in ('return', 'return_early', 'return_egg')
                      and not c['value'].startswith('fight:')
                      and not c.get('effects', {}).get('battle'))
        return await expeditions.choose(42, trip['id'], trip['version'], choice)

    async def next_gate(self, trip):
        for _ in range(5):
            if trip['payload']['page']['kind'] == 'battle':
                return trip
            self.assertNotIn('return', expeditions.options(trip))
            trip = await self.choose_interlude(trip)
        self.fail('A brief interlude did not lead to the next required battle.')

    async def start_gate(self, trip):
        value = next(v for v in expeditions.options(trip) if v.startswith('fight:'))
        return await expeditions.choose(42, trip['id'], trip['version'], value)

    async def drive(self, trip):
        kinds = []
        started = set()
        for _ in range(120):
            if trip['status'] == 'complete':
                return trip, kinds
            if trip['status'] == 'battle':
                stamp = trip['payload']['fights_started']
                if stamp not in started:
                    started.add(stamp)
                    kinds.append(trip['payload']['combat']['plan']['total'])
                trip = await self.act(trip)
            elif 'return' in expeditions.options(trip):
                self.assertEqual(trip['payload']['battles_completed'], trip['payload']['battle_target'])
                trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
            elif trip['payload']['page']['kind'] == 'battle':
                trip = await self.start_gate(trip)
            else:
                trip = await self.choose_interlude(trip)
        self.fail('The combat-first itinerary did not finish within its bounded route.')

    def designated_pack_seed(self, realm='woods'):
        party = [dict(id=row, beast_id=species, name=species, type='nature')
                 for row, species in zip(self.ids, ('twine', 'gloop', 'barkley'))]
        for seed in range(100):
            payload = {}
            journeys.initialize(payload, realm, seed, party, combat_route=True)
            if payload['battle_schedule'][payload['recruitable_beat']].startswith('pack'):
                return seed
        self.fail('No seed assigned recruitment to a later mandatory pack.')

    async def test_every_destination_full_clear_requires_its_saved_battle_itinerary(self):
        await self.ready()
        for index, realm in enumerate(BIOMES):
            with self.subTest(realm=realm):
                await self.execute('UPDATE exploration_energy SET charges=3')
                material_before = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
                beasts_before = len(await storage.get_player_beasts(42))
                with patch('utils.expeditions._encounter', wraps=expeditions._encounter) as ticket:
                    trip = await self.begin(realm)
                    self.assertEqual(trip['status'], 'battle', 'A new adventure begins in real combat.')
                    target = 2 if index < 5 else 3
                    self.assertEqual(trip['payload']['pacing_version'], 1)
                    self.assertEqual(trip['payload']['battle_target'], target)
                    self.assertEqual(trip['payload']['total'], target * 2 - 1)
                    schedule = deepcopy(trip['payload']['battle_schedule'])
                    trip, sizes = await self.drive(trip)
                    self.assertEqual(ticket.await_count, 1)
                p = trip['payload']
                self.assertEqual((p['battles_completed'], p['fights_won'], p['fights_started']), (target, target, target))
                self.assertEqual(p['completed'], p['total'])
                self.assertEqual(p['battle_schedule'], schedule)
                self.assertEqual(len(sizes), target)
                self.assertEqual(sizes[0], 1)
                self.assertTrue(any(count in (2, 3) for count in sizes[1:]))
                self.assertEqual(p['gold'], int(p['base_gold'] * (1 + min(25, p['bonus']) / 100)))
                self.assertLessEqual(p['exp'], int(p['base_exp'] * 1.25))
                self.assertEqual(len(await storage.get_player_beasts(42)) - beasts_before, 1)
                material_after = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
                self.assertEqual(material_after - material_before, 1)
                self.assertFalse(trial_locks.locked(42))

    async def test_safe_interlude_cannot_skip_a_required_battle_or_forge_full_return(self):
        await self.ready()
        trip = await self.finish_encounter(await self.begin())
        old_choice = next(c['value'] for c in trip['payload']['page']['choices']
                          if not c.get('effects', {}).get('battle'))
        trip = await self.next_gate(trip)
        self.assertEqual(trip['payload']['battles_completed'], 1)
        self.assertEqual(trip['payload']['completed'], 2)
        self.assertTrue(any(v.startswith('fight:') for v in expeditions.options(trip)))
        self.assertFalse(any(v.startswith('scene:') for v in expeditions.options(trip)))
        before = deepcopy(trip)
        for forged in ('return', 'return_egg', old_choice, 'fight:99', 'material:phoenix_ash'):
            with self.subTest(forged=forged):
                with self.assertRaises(ValueError):
                    await expeditions.choose(42, trip['id'], trip['version'], forged)
                self.assertEqual(await expeditions.read(42, trip['id']), before)
        done = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(done['payload']['battles_completed'], 1)
        p = done['payload']
        self.assertEqual(p['gold'], int(p['base_gold'] * 2 / 3 * (1 + min(25, p['bonus']) / 100)))

    async def test_retreat_during_pack_does_not_credit_the_unfinished_battle(self):
        await self.ready()
        trip = await self.finish_encounter(await self.begin('glacial_deep'))
        trip = await self.start_gate(await self.next_gate(trip))
        self.assertGreater(trip['payload']['combat']['plan']['total'], 1)
        while trip['payload']['combat']['status'] == 'fighting':
            trip = await self.act(trip)
        self.assertEqual(trip['payload']['combat']['status'], 'between')
        self.assertEqual(trip['payload']['combat']['cleared'], 1)
        self.assertEqual((trip['payload']['battles_completed'], trip['payload']['completed']), (1, 2))
        done = await journey_service.battle_action(42, trip['id'], trip['version'], 'return')
        p = done['payload']
        self.assertEqual((done['status'], p['battles_completed'], p['completed']), ('complete', 1, 2))
        self.assertEqual(p['gold'], int(p['base_gold'] * 2 / 5 * (1 + min(25, p['bonus']) / 100)))
        self.assertLess(p['completed'], p['total'])

    async def test_only_the_saved_designated_pack_rolls_and_grants_one_recruit(self):
        await self.ready()
        seed = self.designated_pack_seed('glacial_deep')
        await storage.update_player(42, incense_charges=3, catch_boost_charges=3)
        with patch('utils.expeditions._encounter', wraps=expeditions._encounter) as ticket:
            trip = await self.begin('glacial_deep', seed)
            designated = trip['payload']['recruitable_beat']
            self.assertGreater(designated, 0)
            self.assertFalse(trip['payload']['capture_fight'])
            trip = await self.finish_encounter(trip)
            self.assertFalse(trip['payload']['caught'])
            self.assertEqual(ticket.await_count, 0)
            self.assertEqual(len(await storage.get_player_beasts(42)), 3)
            self.assertEqual((await storage.get_player(42))['incense_charges'], 3)
            while trip['payload']['battles_completed'] < designated:
                trip = await self.finish_encounter(await self.start_gate(await self.next_gate(trip)))
            trip = await self.start_gate(await self.next_gate(trip))
            self.assertTrue(trip['payload']['capture_fight'])
            self.assertGreater(trip['payload']['combat']['plan']['total'], 1)
            self.assertEqual(ticket.await_count, 1)
            while trip['payload']['combat']['status'] == 'fighting':
                trip = await self.act(trip)
            self.assertEqual(trip['payload']['combat']['status'], 'between')
            self.assertFalse(trip['payload']['caught'], 'A pack is not a recruiting win until its encounter is cleared.')
            self.assertEqual(len(await storage.get_player_beasts(42)), 3)
            trip, _ = await self.drive(trip)
            self.assertEqual(ticket.await_count, 1)
        self.assertTrue(trip['payload']['caught'])
        self.assertFalse(trip['payload']['capture_fight'], 'The later mandatory encounter is not another recruitment opportunity.')
        self.assertEqual(len(await storage.get_player_beasts(42)), 4)
        self.assertIsNotNone(trip['payload']['grant_id'])
        await expeditions.report({**trip, 'reported': 0})
        self.assertEqual(len(await storage.get_player_beasts(42)), 4)
        self.assertEqual((await storage.get_player(42))['wins'], 1)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_retreat_before_recruitable_beat_preserves_capture_preparations(self):
        await self.ready()
        seed = self.designated_pack_seed()
        await storage.update_player(42, incense_charges=2, ancient_compass_charges=2,
                                    catch_boost_charges=2)
        trip = await self.begin(seed=seed)
        self.assertFalse(trip['payload']['capture_fight'])
        done = await journey_service.battle_action(42, trip['id'], trip['version'], 'return')
        player = await storage.get_player(42)
        self.assertEqual((player['incense_charges'], player['ancient_compass_charges'], player['catch_boost_charges']), (2, 2, 2))
        self.assertEqual((done['payload']['completed'], done['payload']['gold'], done['payload']['exp']), (0, 0, 0))
        self.assertEqual(len(await storage.get_player_beasts(42)), 3)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])

    async def test_zero_energy_restart_resumes_exact_battle_without_another_charge(self):
        await self.ready()
        trip = await self.begin()
        self.assertEqual((await expeditions.energy(42)).charges, 0)
        saved = deepcopy(trip)
        with self.assertRaises(ValueError):
            await self.begin()
        async with storage.connect() as db:
            await expeditions.recover_interrupted(db)
            await trial_locks.reload(db)
            await db.commit()
        self.assertEqual(await expeditions.current(42), saved)
        self.assertEqual(await expeditions.read(42, trip['id']), saved)
        self.assertEqual((await expeditions.energy(42)).charges, 0)
        self.assertTrue(trial_locks.locked(42))
        self.assertEqual(len(await self.rows('SELECT * FROM expeditions')), 1)
        advanced = await self.act(trip)
        with self.assertRaises(ValueError):
            await journey_service.battle_action(42, trip['id'], trip['version'], ['Tick Bite', False])
        self.assertEqual(await expeditions.read(42, trip['id']), advanced)

    async def test_first_required_battle_wipe_has_no_payout_and_keeps_every_ko(self):
        await self.ready()
        trip = await self.begin()
        state = trip['payload']['combat']
        for member in state['party']:
            member.update(hp=0, mana=0, shield=0, guard=0, perks=[], status=None,
                          status_ward=False, evasion_percent=0, damage_reduction_flat_percent=0)
        state['party'][state['slot']].update(hp=1, attack=1, base_attack=1, defense=0, base_defense=0)
        state['enemy'].update(hp=100000, max_hp=100000, attack=100000,
                              base_attack=100000, defense=100000, base_defense=100000,
                              mana=0, shield=0, guard=0, status=None)
        trip = await self.save(trip)
        for _ in range(5):
            if trip['status'] == 'complete':
                break
            trip = await self.act(trip)
        self.assertEqual(trip['status'], 'complete')
        self.assertEqual(trip['payload']['combat']['status'], 'lost')
        self.assertEqual((trip['payload']['battles_completed'], trip['payload']['completed']), (0, 0))
        self.assertEqual((trip['payload']['gold'], trip['payload']['exp']), (0, 0))
        rows = await self.rows('SELECT hp,knocked_out_until FROM player_beasts WHERE user_id=42')
        self.assertTrue(all(row['hp'] == 0 and datetime.fromisoformat(row['knocked_out_until']) > datetime.now(timezone.utc) for row in rows))
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual((await storage.get_player(42))['wins'], 0)
        self.assertFalse(trial_locks.locked(42))

    async def test_empty_story_pool_cannot_shorten_the_required_battle_denominator(self):
        await self.ready()
        # Simulate finite content exhaustion independently of the production
        # catalog. Mandatory encounters must survive the missing interlude.
        empty = dict(version=1, biome='woods', encounters=[])
        with patch('utils.adventure_content.load', return_value=empty):
            trip = await self.begin()
        self.assertEqual((trip['status'], trip['payload']['total']), ('battle', 3))
        trip = await self.finish_encounter(trip)
        self.assertNotIn('return', expeditions.options(trip))
        trip = await self.next_gate(trip)
        self.assertEqual(trip['payload']['total'], 3)
        trip, sizes = await self.drive(trip)
        self.assertEqual(trip['payload']['battles_completed'], 2)
        self.assertEqual(trip['payload']['completed'], 3)
        self.assertEqual(len(sizes), 1, 'The second encounter still runs after the fallback interlude.')

    async def test_saved_story_upgrade_preserves_earned_credit_resources_and_one_bundle(self):
        await self.ready()
        old = await self.legacy_step(await self.begin(combat_route=False))
        self.assertEqual(old['payload']['completed'], 1)
        before = deepcopy(old)
        progress_before = await self.rows('SELECT * FROM adventure_progress WHERE user_id=42 ORDER BY encounter_id')
        await self.unmark_old_save(old)
        upgraded = await expeditions.read(42, old['id'])
        p = upgraded['payload']
        self.assertEqual((upgraded['id'], upgraded['version']), (before['id'], before['version'] + 1))
        self.assertEqual(p['pacing_version'], 1)
        self.assertEqual((p['battles_completed'], p['completed']), (0, 0))
        self.assertEqual(p['inherited_completed'], 1)
        self.assertEqual(p['inherited_fraction'], 1 / before['payload']['total'])
        for key in ('seed', 'base_gold', 'base_exp', 'material', 'offered', 'egg_offer',
                    'bonus', 'boons', 'prepared_party', 'party_ids', 'history'):
            self.assertEqual(p[key], before['payload'][key], key)
        self.assertEqual(p['route_continuation'], [before['payload']['current_root'], before['payload']['current_node']])
        self.assertEqual(p['page']['kind'], 'battle')
        self.assertEqual(await self.rows('SELECT * FROM adventure_progress WHERE user_id=42 ORDER BY encounter_id'), progress_before)
        self.assertEqual((await expeditions.energy(42)).charges, 0)
        self.assertEqual(await expeditions.current(42), upgraded)
        self.assertEqual(await expeditions.read(42, old['id']), upgraded, 'Migration is exactly once.')
        done = await expeditions.choose(42, upgraded['id'], upgraded['version'], 'return_early')
        self.assertEqual(done['payload']['outcome'], 'retreated')
        expected = int(p['base_gold'] * p['inherited_fraction'] * (1 + min(25, p['bonus']) / 100))
        self.assertEqual(done['payload']['gold'], expected)
        self.assertTrue(done['payload']['material_granted'])
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertTrue(done['payload']['party_xp'])
        stats = await self.rows("SELECT stat_key,value FROM player_progress WHERE user_id=42 AND stat_key IN ('explore','material_collect') ORDER BY stat_key")
        self.assertEqual(stats, [{'stat_key': 'explore', 'value': 1}, {'stat_key': 'material_collect', 'value': 1}])
        await expeditions.report({**done, 'reported': 0})
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_saved_active_pack_upgrade_keeps_cursor_and_existing_capture_ticket(self):
        from tests.test_journey_service import content
        await self.ready()
        with patch('utils.adventure_content.load', return_value=content(battle=True)), \
                patch('utils.expeditions._encounter', wraps=expeditions._encounter) as ticket:
            old = await self.begin(combat_route=False)
            old = await expeditions.choose(42, old['id'], old['version'], expeditions.options(old)[1])
            old = await self.act(old)
            self.assertEqual(old['payload']['combat']['status'], 'between')
            before = deepcopy(old)
            await self.unmark_old_save(old)
            upgraded = await expeditions.read(42, old['id'])
            p = upgraded['payload']
            self.assertEqual(upgraded['status'], 'battle')
            self.assertEqual(p['combat'], before['payload']['combat'])
            self.assertEqual(p['prepared_party'], before['payload']['prepared_party'])
            self.assertEqual(p['encounter'], before['payload']['encounter'])
            self.assertEqual(p['recruitable_beat'], -1)
            self.assertTrue(p['pending_battle']['counts_for_route'])
            self.assertEqual((await expeditions.energy(42)).charges, 0)
            self.assertEqual(await expeditions.current(42), upgraded)
            upgraded = await self.finish_encounter(upgraded)
            self.assertEqual(upgraded['payload']['battles_completed'], 1)
            self.assertTrue(upgraded['payload']['caught'])
            root, node = before['payload']['current_root'], before['payload']['current_node']
            self.assertNotEqual(upgraded['payload']['current_node'], node,
                                'A migrated terminal battle must not reopen its just-resolved scene.')
            self.assertTrue(upgraded['payload']['world_progress'][root]['completed'])
            self.assertEqual(await self.rows('SELECT completed FROM adventure_progress WHERE user_id=42 AND encounter_id=?', (root,)),
                             [{'completed': 1}])
            done, _ = await self.drive(upgraded)
            self.assertEqual(ticket.await_count, 1, 'An old saved encounter remains the only recruitment ticket.')
        self.assertEqual(done['payload']['outcome'], 'cleared')
        self.assertEqual(len(await storage.get_player_beasts(42)), 4)
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)

    async def test_saved_active_battle_upgrade_continues_to_its_saved_next_node(self):
        from tests.test_journey_service import content
        await self.ready()
        definition = content(battle=True, chains=True)
        for root in definition['encounters']:
            root['nodes'][root['entry']]['choices'][1]['next'] = root['id'] + '.node_1'
        with patch('utils.adventure_content.load', return_value=definition):
            old = await self.begin(combat_route=False)
        root = old['payload']['current_root']
        old = await expeditions.choose(42, old['id'], old['version'], expeditions.options(old)[1])
        expected_next = old['payload']['pending_battle']['next']
        await self.unmark_old_save(old)
        upgraded = await expeditions.read(42, old['id'])
        upgraded = await self.finish_encounter(upgraded)
        p = upgraded['payload']
        self.assertEqual(p['current_node'], expected_next)
        self.assertEqual(p['world_progress'][root]['node_id'], expected_next)
        self.assertFalse(p['world_progress'][root]['completed'])
        self.assertEqual(p['battles_completed'], 1)

    async def test_upgrade_with_stale_active_snapshot_does_not_relock_completed_user(self):
        await self.ready()
        old = await self.begin(combat_route=False)
        await self.unmark_old_save(old)
        # Model another callback committing a retreat while ensure_paced waits
        # for the user's combat lock with this already-read old snapshot.
        stale = deepcopy(old)
        done = await journey_service.choose(42, old['id'], old['version'], 'return_early')
        self.assertEqual(done['status'], 'complete')
        self.assertFalse(trial_locks.locked(42))
        refreshed = await journey_service.ensure_paced(stale)
        self.assertEqual(refreshed['status'], 'complete')
        self.assertFalse(trial_locks.locked(42),
                         'An old active read must not reserve a team after its concurrent return commits.')
        await self.execute('UPDATE exploration_energy SET charges=1 WHERE user_id=42')
        newer = await self.begin()
        self.assertTrue(trial_locks.locked(42))
        self.assertEqual((await journey_service.ensure_paced(stale))['status'], 'complete')
        self.assertTrue(trial_locks.locked(42), 'A stale completed trip must not unlock a newer adventure.')
        self.assertEqual((await expeditions.current(42))['id'], newer['id'])

    async def test_saved_material_picker_keeps_its_offers_before_next_required_battle(self):
        from tests.test_journey_service import content
        await self.ready()
        definition = content()
        for root in definition['encounters']:
            for node in root['nodes'].values():
                node['choices'][0]['effects'] = {'material': True}
        with patch('utils.adventure_content.load', return_value=definition):
            old = await self.begin(combat_route=False)
        self.assertGreater(len(old['payload']['offered']), 1)
        old = await expeditions.choose(42, old['id'], old['version'], expeditions.options(old)[0])
        self.assertEqual(old['payload']['page']['kind'], 'material')
        page = deepcopy(old['payload']['page'])
        offers = deepcopy(old['payload']['offered'])
        await self.unmark_old_save(old)
        upgraded = await expeditions.read(42, old['id'])
        self.assertEqual(upgraded['payload']['page'], page)
        self.assertEqual(upgraded['payload']['offered'], offers)
        self.assertEqual(upgraded['payload']['material_next_page']['kind'], 'battle')
        selected = page['choices'][-1]['value']
        upgraded = await expeditions.choose(42, upgraded['id'], upgraded['version'], selected)
        self.assertEqual(upgraded['payload']['page']['kind'], 'battle')
        self.assertEqual(upgraded['payload']['material'], selected.split(':', 1)[1])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        done = await expeditions.choose(42, upgraded['id'], upgraded['version'], 'return_early')
        rows = await self.rows('SELECT material_id,quantity FROM player_materials WHERE user_id=42')
        self.assertEqual(rows, [{'material_id': done['payload']['material'], 'quantity': 1}])

    async def test_saved_selected_nest_egg_survives_upgrade_and_replaces_material(self):
        from utils import expedition_events
        from utils.economy_balance import gold_reward_level
        await self.ready()
        source = sum(BIOMES['woods']['level_range']) // 2
        gold_level = gold_reward_level(source)
        base = random.Random(9).randint(gold_level * 6, gold_level * 10)
        chosen_seed = None
        for seed in range(4096):
            candidate = dict(level=50, base_gold=base, seed=str(seed))
            expedition_events.prepare_nest(candidate, 'woods')
            if candidate['egg_offer']:
                chosen_seed = seed
                break
        self.assertIsNotNone(chosen_seed, 'The real nest sampler must expose a deterministic saved offer.')
        old = await self.legacy_step(await self.begin(seed=chosen_seed, combat_route=False))
        offer = deepcopy(old['payload']['egg_offer'])
        self.assertIsNotNone(offer)
        # Model an old saved preference against its genuine preseeded offer.
        old['payload']['egg_reward'] = offer['id']
        await self.unmark_old_save(old)
        upgraded = await expeditions.read(42, old['id'])
        self.assertEqual(upgraded['payload']['egg_offer'], offer)
        self.assertEqual(upgraded['payload']['egg_reward'], offer['id'])
        done = await expeditions.choose(42, upgraded['id'], upgraded['version'], 'return_early')
        self.assertTrue(done['payload']['egg_granted'])
        self.assertFalse(done['payload']['material_granted'])
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        eggs = await self.rows('SELECT item_id,quantity FROM player_inventory WHERE user_id=42')
        self.assertEqual(eggs, [{'item_id': offer['id'], 'quantity': 1}])
        await expeditions.report({**done, 'reported': 0})
        self.assertEqual(await self.rows('SELECT item_id,quantity FROM player_inventory WHERE user_id=42'), eggs)

    async def test_completed_old_save_is_not_migrated_or_reopened(self):
        await self.ready()
        old = await self.begin(combat_route=False)
        old = await expeditions.choose(42, old['id'], old['version'], 'return_early')
        await self.unmark_old_save(old)
        row_before = await self.rows('SELECT * FROM expeditions WHERE id=?', (old['id'],))
        completed = await expeditions.read(42, old['id'])
        self.assertEqual(completed['status'], 'complete')
        self.assertNotIn('pacing_version', completed['payload'])
        self.assertIsNone(await expeditions.current(42))
        self.assertEqual(await self.rows('SELECT * FROM expeditions WHERE id=?', (old['id'],)), row_before)
        self.assertFalse(trial_locks.locked(42))
