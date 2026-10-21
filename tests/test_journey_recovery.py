"""Production journey defeat, proportional resources and settlement recovery."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from tests.test_gameplay import DatabaseCase, interaction
from tests.test_journey_service import content
from utils import adventure_party, db as storage, expeditions, journey_service, trial_combat, trial_locks


class JourneyRecovery(DatabaseCase):
    async def ready(self, *, safe_effects=None):
        # Keep unrelated first-time achievement gold out of exact activity
        # budget assertions while still exercising the real reporting code.
        from utils.progress import ACHIEVEMENTS
        async with storage.connect() as db:
            await db.executemany('INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(?,?)',
                                 [(42, key) for key in ACHIEVEMENTS])
            await db.commit()
        ids = [await self.beast('twine', starter=True)]
        for slot, species in enumerate(('gloop', 'barkley'), 1):
            row = await self.beast(species)
            await adventure_party.set_slot(42, row, slot)
            ids.append(row)
        self.definition = content(battle=True)
        if safe_effects is not None:
            for root in self.definition['encounters']:
                for node in root['nodes'].values():
                    node['choices'][0]['effects'] = deepcopy(safe_effects)
        return ids

    async def begin(self):
        with patch('utils.adventure_content.load', return_value=deepcopy(self.definition)):
            return await expeditions.begin(42, 'woods', rng=random.Random(9), adventure_version=3)

    async def pick(self, trip, index=0):
        return await expeditions.choose(42, trip['id'], trip['version'], expeditions.options(trip)[index])

    async def save(self, trip):
        await self.execute('UPDATE expeditions SET payload=? WHERE id=?',
                           (json.dumps(trip['payload']), trip['id']))
        return await expeditions.read(42, trip['id'])

    async def fight(self, trip, *, strong=False):
        # A saved fixture can have unusually strong combat stats without
        # changing the collection or bypassing the actual battle service.
        for member in trip['payload'].get('combat', {}).get('party', trip['payload']['prepared_party']):
            member.update(speed=10000, base_speed=10000)
            if strong:
                member.update(attack=50000, base_attack=50000)
        trip = await self.save(trip)
        return await self.pick(trip, 1)

    async def win(self, trip):
        for _ in range(40):
            if trip['status'] != 'battle':
                return trip
            state = trip['payload']['combat']
            action = 'next' if state['status'] == 'between' else next(
                a['choice'] for a in trial_combat.legal_actions(state) if a['enabled'])
            trip = await journey_service.battle_action(42, trip['id'], trip['version'], action)
        self.fail('A three-member production pack did not reach its saved boundary.')

    async def wipe(self, trip):
        state = trip['payload']['combat']
        for member in state['party']:
            member.update(hp=0, mana=0, shield=0, guard=0, perks=[],
                          status=None, status_ward=False, evasion_percent=0,
                          damage_reduction_flat_percent=0)
        lead = state['party'][state['slot']]
        lead.update(hp=1, attack=1, base_attack=1, defense=0, base_defense=0)
        state['enemy'].update(hp=100000, max_hp=100000, attack=100000,
                              base_attack=100000, defense=100000, base_defense=100000,
                              shield=0, guard=0, mana=0, status=None)
        trip = await self.save(trip)
        # Resolve a legal player action followed by the real enemy AI. No
        # mocked lost result or direct invocation of settlement is involved.
        for _ in range(5):
            if trip['status'] != 'battle':
                return trip
            legal = trial_combat.legal_actions(trip['payload']['combat'])
            action = next(a['choice'] for a in legal if a['enabled'] and a['kind'] == 'move')
            trip = await journey_service.battle_action(42, trip['id'], trip['version'], action)
        self.fail('The one-HP active companion survived the forced enemy attack.')

    async def test_real_party_wipe_settles_zero_without_capture_or_progress(self):
        ids = await self.ready()
        before = await storage.get_player(42)
        trip = await self.fight(await self.begin())
        root = trip['payload']['current_root']
        trip = await self.wipe(trip)
        self.assertEqual(trip['status'], 'complete')
        self.assertEqual(trip['payload']['combat']['status'], 'lost')
        self.assertEqual((trip['payload']['completed'], trip['payload']['gold'], trip['payload']['exp']), (0, 0, 0))
        self.assertFalse(trip['payload']['caught'])
        self.assertFalse(trial_locks.locked(42))
        self.assertIsNone(await expeditions.current(42))
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), [])
        self.assertEqual(len(await storage.get_player_beasts(42)), 3)
        self.assertEqual((await storage.get_player(42))['gold'], before['gold'])
        self.assertEqual((await storage.get_player(42))['wins'], 0)
        progress = await self.rows('SELECT completed FROM adventure_progress WHERE encounter_id=?', (root,))
        self.assertEqual(progress[0]['completed'], 0)
        rows = await self.rows('SELECT id,hp,knocked_out_until FROM player_beasts WHERE user_id=42')
        self.assertEqual({row['id'] for row in rows}, set(ids))
        self.assertTrue(all(row['hp'] == 0 and row['knocked_out_until'] for row in rows))
        for row in rows:
            self.assertGreater(datetime.fromisoformat(row['knocked_out_until']), datetime.now(timezone.utc))

    async def test_wipe_after_one_stop_banks_only_earned_fraction(self):
        await self.ready()
        # Cross the XP threshold during defeat settlement: growth must not
        # turn a knocked-out companion back into a living one.
        await self.execute('UPDATE player_beasts SET exp=14 WHERE user_id=42')
        before = await storage.get_player(42)
        trip = await self.pick(await self.begin())
        trip = await self.fight(trip)
        base_gold, base_xp = trip['payload']['base_gold'], trip['payload']['base_exp']
        total = trip['payload']['total']
        trip = await self.wipe(trip)
        self.assertEqual((trip['status'], trip['payload']['completed']), ('complete', 1))
        self.assertEqual(trip['payload']['gold'], int(base_gold / total))
        self.assertEqual(trip['payload']['exp'], int(base_xp / total))
        self.assertEqual((await storage.get_player(42))['gold'] - before['gold'], trip['payload']['gold'])
        self.assertEqual(sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials')), 1)
        self.assertEqual((await storage.get_player(42))['wins'], 0)
        self.assertEqual(await self.rows("SELECT * FROM gameplay_events WHERE event_type='battle_win'"), [])
        rows = await self.rows('SELECT hp,level,knocked_out_until FROM player_beasts WHERE user_id=42')
        self.assertTrue(any(row['level'] > 1 for row in rows))
        self.assertTrue(all(row['hp'] == 0 and row['knocked_out_until'] for row in rows))

    async def test_team_tab_displays_saved_healing_and_knockouts_between_fights(self):
        from utils import journal_state, journal_team
        from utils.journal import JournalView
        ids = await self.ready(safe_effects={'heal': 5})
        await self.execute('UPDATE player_beasts SET hp=1 WHERE user_id=42')
        await self.execute('UPDATE player_beasts SET raid_slot=1 WHERE id=?', (ids[0],))
        trip = await self.pick(await self.begin())
        prepared = trip['payload']['prepared_party']
        self.assertGreater(prepared[0]['hp'], 1)
        view = JournalView(42, await journal_state.snapshot(42), page='team')
        self.addCleanup(view.stop)
        view.art_enabled = False
        view.team_art = SimpleNamespace(name='team-fixture.png')
        view.render()
        text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn(f"HP {prepared[0]['hp']}/{prepared[0]['max_hp']}", text)
        self.assertEqual(dict(journal_team.roster(view))['lead']['hp'], prepared[0]['hp'])
        # After a pack, the combat snapshot becomes authoritative instead of
        # the original prepared snapshot or the still-reserved collection.
        trip = await self.win(await self.fight(trip, strong=True))
        trip['payload']['combat']['party'][1]['hp'] = 0
        saved_lead = trip['payload']['combat']['party'][0]
        saved_lead['max_hp'] *= 2
        saved_lead['hp'] = saved_lead['max_hp'] // 3
        saved_lead['max_mana'] *= 2
        saved_lead['mana'] = saved_lead['_pve_mana_origin'][1] - 10
        trip = await self.save(trip)
        view.state = await journal_state.snapshot(42)
        view.trip = trip
        view.render()
        text = '\n'.join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        self.assertIn('Reserve', text)
        roster = dict(journal_team.roster(view))
        self.assertEqual(roster['1']['hp'], 0)
        self.assertIn('Recovering', journal_team.status(roster['1']))
        self.assertIn(f"HP 0/{roster['1']['max_hp']}", text)
        self.assertEqual(roster['lead']['hp'], max(1, roster['lead']['max_hp'] * saved_lead['hp'] // saved_lead['max_hp']))
        self.assertEqual(roster['lead']['mana'], saved_lead['_pve_mana_origin'][0] - 5)
        collection = await self.rows('SELECT hp FROM player_beasts WHERE id=?', (ids[1],))
        self.assertGreater(collection[0]['hp'], 0, 'The collection is deliberately unchanged until return.')
        view.team_mode = 'raid'
        raid_lead = dict(journal_team.roster(view))['raid1']
        self.assertEqual(raid_lead['hp'], 1, 'Raid display must keep using collection resources.')

    async def test_return_maps_prepared_hp_proportions_and_keeps_knockouts(self):
        ids = await self.ready()
        await self.perk('genesis_spark')
        await self.execute('UPDATE player_beasts SET mana=1 WHERE user_id=42')
        originals = {r['id']: r for r in await self.rows('SELECT * FROM player_beasts WHERE user_id=42')}
        trip = await self.begin()
        party = trip['payload']['prepared_party']
        self.assertEqual([r['mana'] for r in party], [21, 21, 21])
        for member in party:
            member['max_hp'] *= 2
        party[0]['hp'] = party[0]['max_hp'] // 3
        party[1]['hp'] = 0
        party[2]['hp'] = 1
        trip = await self.save(trip)
        await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        settled = {r['id']: r for r in await self.rows('SELECT * FROM player_beasts WHERE user_id=42')}
        for index, identity in enumerate(ids):
            expected = max(1, originals[identity]['max_hp'] * party[index]['hp'] // party[index]['max_hp']) if party[index]['hp'] else 0
            self.assertEqual(settled[identity]['hp'], expected)
            self.assertEqual(settled[identity]['max_hp'], originals[identity]['max_hp'])
            self.assertEqual(settled[identity]['mana'], 1, 'Unspent entry mana must not refill the collection.')
        self.assertIsNotNone(settled[ids[1]]['knocked_out_until'])
        self.assertIsNone(settled[ids[0]]['knocked_out_until'])
        self.assertEqual(settled[ids[2]]['hp'], 1, 'A living low proportion must remain alive.')

    async def test_temporary_scene_and_prepared_stat_boosts_do_not_persist(self):
        ids = await self.ready(safe_effects={'attack_boon': 10, 'defense_boon': 10})
        await self.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,'gnome_iron_plating')", (ids[0],))
        await storage.update_player(42, next_defense_boost=10, next_speed_boost=15)
        originals = {r['id']: r for r in await self.rows('SELECT * FROM player_beasts WHERE user_id=42')}
        trip = await self.begin()
        baseline = deepcopy(trip['payload']['prepared_party'])
        trip = await self.pick(trip)
        self.assertEqual(trip['payload']['boons'], {'attack': 10, 'defense': 10})
        for original, boosted in zip(baseline, trip['payload']['prepared_party']):
            self.assertGreater(boosted['attack'], original['attack'])
            self.assertGreater(boosted['defense'], original['defense'])
        await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        settled = {r['id']: r for r in await self.rows('SELECT * FROM player_beasts WHERE user_id=42')}
        for identity in ids:
            for key in ('attack', 'defense', 'speed', 'max_hp', 'max_mana', 'level'):
                self.assertEqual(settled[identity][key], originals[identity][key], (identity, key))
        player = await storage.get_player(42)
        self.assertEqual((player['next_defense_boost'], player['next_speed_boost']), (10, 15))

    async def test_zero_partial_and_full_returns_share_one_bounded_budget(self):
        await self.ready(safe_effects={'bonus': 5})
        for completed in (0, 1, 5):
            with self.subTest(completed=completed):
                await self.execute('UPDATE exploration_energy SET charges=3')
                before = await storage.get_player(42)
                materials_before = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
                trip = await self.begin()
                for _ in range(completed):
                    trip = await self.pick(trip)
                base_gold, base_xp = trip['payload']['base_gold'], trip['payload']['base_exp']
                fraction = completed / trip['payload']['total']
                multiplier = 1 + min(25, completed * 5) / 100
                choice = 'return' if 'return' in expeditions.options(trip) else 'return_early'
                trip = await expeditions.choose(42, trip['id'], trip['version'], choice)
                self.assertEqual(trip['payload']['gold'], int(base_gold * fraction * multiplier))
                self.assertEqual(trip['payload']['exp'], int(base_xp * fraction * multiplier))
                self.assertLessEqual(trip['payload']['gold'], int(base_gold * 1.25))
                self.assertLessEqual(trip['payload']['exp'], int(base_xp * 1.25))
                self.assertEqual((await storage.get_player(42))['gold'] - before['gold'], trip['payload']['gold'])
                materials_after = sum(r['quantity'] for r in await self.rows('SELECT quantity FROM player_materials'))
                self.assertEqual(materials_after - materials_before, int(completed > 0))

    async def test_pending_old_raid_cannot_heal_or_reward_reserved_journey_party(self):
        from cogs.guilds import RAID_BOSSES
        from utils.raids import RaidSession, recover_raids
        ids = await self.ready()
        await self.execute('UPDATE player_beasts SET hp=1 WHERE user_id=42')
        trip = await self.pick(await self.begin())
        boss = RAID_BOSSES['ancient'][0]
        payload = dict(boss=boss, users=[42], guild_id=None, server_id=None,
                       party={'42': [dict(id=ids[0], hp=1)]}, damage={'42': 1}, support={'42': 0}, raid_level=10)
        await self.execute('INSERT INTO raid_settlements(raid_id,payload) VALUES(9183,?)', (json.dumps(payload),))
        session = RaidSession(None, boss, [42], None)
        session.id, session.raid_level = 9183, 10
        session.party, session.damage, session.support = {42: payload['party']['42']}, {42: 1}, {42: 0}
        before = await self.rows('SELECT * FROM player_beasts WHERE user_id=42')
        wallet = (await storage.get_player(42))['gold']
        with self.assertRaises(ValueError):
            await session._settle_rewards(None)
        await recover_raids(user_id=42)
        self.assertEqual(await self.rows('SELECT * FROM player_beasts WHERE user_id=42'), before)
        self.assertEqual((await storage.get_player(42))['gold'], wallet)
        self.assertEqual((await self.rows('SELECT completed FROM raid_settlements WHERE raid_id=9183'))[0]['completed'], 0)
        self.assertEqual((await expeditions.current(42))['payload'], trip['payload'])
        await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        self.assertEqual((await self.rows('SELECT completed FROM raid_settlements WHERE raid_id=9183'))[0]['completed'], 1)
        settled = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (ids[0],)))[0]
        self.assertEqual(settled['hp'], settled['max_hp'])
        await self.execute('UPDATE player_beasts SET hp=1 WHERE id=?', (ids[0],))
        await recover_raids(user_id=42)
        self.assertEqual((await self.rows('SELECT hp FROM player_beasts WHERE id=?', (ids[0],)))[0]['hp'], 1)

    async def test_report_outage_keeps_committed_return_and_journal_retries_without_extra_wins(self):
        from utils.journal import JournalView, open_journal
        await self.ready()
        trip = await self.win(await self.fight(await self.begin(), strong=True))
        self.assertEqual(trip['payload']['fights_won'], 1)
        self.assertEqual(trip['payload']['completed'], 1)
        with patch('utils.progress.check_achievements', side_effect=RuntimeError('temporary report outage')):
            with self.assertLogs('chibibeasts.expeditions', level='ERROR'):
                trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
        saved = await expeditions.read(42, trip['id'])
        self.assertEqual((saved['status'], saved['reported']), ('complete', 0))
        self.assertFalse(trial_locks.locked(42))
        player = await storage.get_player(42)
        rows = await self.rows('SELECT * FROM player_beasts WHERE user_id=42')
        inventory = await self.rows('SELECT * FROM player_materials')
        self.assertEqual(player['wins'], 1)
        async def publish(view, *args, **kwargs):
            view.message = SimpleNamespace(id=87325, attachments=[], edit=AsyncMock())
        with patch.object(JournalView, 'publish', new=publish):
            view = await open_journal(interaction())
        self.addCleanup(view.stop)
        self.assertEqual((await expeditions.read(42, trip['id']))['reported'], 1)
        retry = deepcopy(saved)
        await expeditions.report(retry)
        await expeditions.report(retry)
        after = await storage.get_player(42)
        for key in ('wins', 'gold', 'exp', 'level'):
            self.assertEqual(after[key], player[key], key)
        self.assertEqual(await self.rows('SELECT * FROM player_beasts WHERE user_id=42'), rows)
        self.assertEqual(await self.rows('SELECT * FROM player_materials'), inventory)
        wins = await self.rows("SELECT value FROM player_progress WHERE user_id=42 AND stat_key='battle_win'")
        self.assertEqual(wins[0]['value'], 1)
        events = await self.rows("SELECT * FROM gameplay_events WHERE event_type='battle_win'")
        self.assertEqual(len(events), 1)

    async def test_daily_failure_preserves_atomic_story_and_retries_each_daily_once(self):
        from utils import progress, story
        from utils.journal import JournalView, open_journal
        await self.ready()
        await storage.update_player(42, level=3)
        state, chapter, _ = await story.journal(42)
        self.assertEqual(chapter['id'], 'chapter_1')
        self.assertEqual(state['step_progress']['chapter_1'].get('explore_3_biomes', 0), 0)
        trip = await self.pick(await self.begin())
        daily_set = [deepcopy(next(q for q in progress.QUEST_POOL if q['id'] == key))
                     for key in ('explore_times', 'collect_materials')]
        original_daily = progress._track_daily_event
        failures = []

        async def fail_once(uid, event, *args, **kwargs):
            if event == 'explore' and not failures:
                failures.append(event)
                raise RuntimeError('daily service temporarily unavailable')
            return await original_daily(uid, event, *args, **kwargs)

        async def publish(view, *args, **kwargs):
            view.message = SimpleNamespace(id=87326, attachments=[], edit=AsyncMock())

        with patch.object(progress, 'eligible_daily_set', new=AsyncMock(return_value=daily_set)), \
                patch.object(progress, '_track_daily_event', side_effect=fail_once):
            with self.assertLogs('chibibeasts.expeditions', level='ERROR'):
                trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early')
            self.assertEqual((trip['status'], trip['reported']), ('complete', 0))
            self.assertEqual(failures, ['explore'])
            self.assertFalse(trial_locks.locked(42))
            # These are the real questline and progress counters, read while
            # the daily callback is still pending. They share settlement's
            # writer commit rather than depending on a notification retry.
            story_before_retry = await story.get_quest_state(42)
            self.assertEqual(story_before_retry['step_progress']['chapter_1']['explore_3_biomes'], 1)
            counters = await self.rows("SELECT stat_key,value FROM player_progress WHERE user_id=42 AND stat_key IN ('explore','material_collect') ORDER BY stat_key")
            self.assertEqual(counters, [{'stat_key': 'explore', 'value': 1},
                                        {'stat_key': 'material_collect', 'value': 1}])
            materials = await self.rows('SELECT * FROM player_materials WHERE user_id=42')
            self.assertEqual(sum(row['quantity'] for row in materials), 1)
            wallet = (await storage.get_player(42))['gold']
            self.assertEqual(await self.rows('SELECT * FROM daily_quests WHERE user_id=42'), [])
            with patch.object(JournalView, 'publish', new=publish):
                view = await open_journal(interaction())
            self.addCleanup(view.stop)
            self.assertEqual((await expeditions.read(42, trip['id']))['reported'], 1)
            # Replaying the same stale, unreported receipt remains safe even
            # after both daily callbacks and the journal opener succeeded.
            await expeditions.report(trip)
            await expeditions.report(trip)
            dailies = await self.rows('SELECT quest_id,progress FROM daily_quests WHERE user_id=42 ORDER BY quest_id')
            self.assertEqual(dailies, [{'quest_id': 'collect_materials', 'progress': 1},
                                      {'quest_id': 'explore_times', 'progress': 1}])
        self.assertEqual(await story.get_quest_state(42), story_before_retry)
        self.assertEqual(await self.rows("SELECT stat_key,value FROM player_progress WHERE user_id=42 AND stat_key IN ('explore','material_collect') ORDER BY stat_key"), counters)
        self.assertEqual(await self.rows('SELECT * FROM player_materials WHERE user_id=42'), materials)
        self.assertEqual((await storage.get_player(42))['gold'], wallet)
        receipts = await self.rows("SELECT event_type,COUNT(*) AS count FROM gameplay_events WHERE event_key LIKE ? AND event_type IN ('explore','material_collect','daily:explore','daily:material_collect','material_story') GROUP BY event_type",
                                   ('expedition:' + trip['id'] + ':%',))
        self.assertEqual({r['event_type']: r['count'] for r in receipts},
                         {'explore': 1, 'material_collect': 1, 'daily:explore': 1,
                          'daily:material_collect': 1, 'material_story': 1})
