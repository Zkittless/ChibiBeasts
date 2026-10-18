"""Pacing contracts: continuous raid power, bounded entry economy and story access."""
import statistics
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from utils import combat, db as storage, story
from utils.raid_balance import (BOSS_PROFILES, REFERENCE_TEAMS, budget, reference_stats,
                                reference_species, GUILD_ENTRY_TOKENS, GUILD_VICTORY_TOKENS)
from utils.raids import RaidLobby, RaidSession, party_rows, refund_raid, active_raids
from utils.duels import active_fighters
from cogs.guilds import RAID_BOSSES
from tests.test_gameplay import DatabaseCase


class ContinuousRaidPower(unittest.TestCase):
    def test_every_level_counts_without_old_reference_tier_cliffs(self):
        for boss in BOSS_PROFILES:
            previous = budget(boss, {0: [{'level': 10}]})
            for level in range(11, 51):
                row = budget(boss, {0: [{'level': level}]})
                with self.subTest(boss=boss, level=level):
                    self.assertEqual(row['level'], level)
                    for stat in ('hp', 'attack', 'defense'):
                        # Before this change attack jumped 90% at 25 and 86% at 40.
                        self.assertLess(row[stat] / previous[stat], 1.12)
                        self.assertGreaterEqual(row[stat], previous[stat])
                previous = row
        for level in (15, 25, 40):
            before, after = reference_stats(level - .0001), reference_stats(level + .0001)
            for stat in before:
                self.assertAlmostEqual(before[stat], after[stat], delta=.01)

    def test_reference_anchors_use_canonical_growth_and_natural_late_companions(self):
        from utils.progression import companion_growth
        catalog = storage.load_beasts()
        for level, keys in REFERENCE_TEAMS:
            expected = []
            for key in keys:
                growth = companion_growth(key, level, catalog)
                expected.append({stat: value + growth[stat]
                                 for stat, value in catalog[key]['base_stats'].items()})
            for stat, value in reference_stats(level).items():
                self.assertEqual(value, statistics.mean(row[stat] for row in expected))
        self.assertTrue(all(catalog[key]['rarity'] == 'divine' for key in REFERENCE_TEAMS[-1][1]))
        self.assertEqual(budget('corrupted_dragon', {0: [{'level': 99}]})['level'], 50)

    def test_benchmark_equipment_obeys_trainer_craft_and_material_region_gates(self):
        from scripts.audit_combat_balance import BUILDS, GEAR, PROGRESSION_STAGES
        from utils.economy_balance import recipe_level
        from utils.habitats import BIOMES, REGIONAL_MATERIALS
        definitions = {**GEAR['equipment'], **GEAR['runes']}
        for level, stage in PROGRESSION_STAGES.items():
            available = {mid for region, materials in REGIONAL_MATERIALS.items()
                         if BIOMES[region]['min_level'] <= stage['trainer_level'] for mid in materials}
            for key in BUILDS[stage['build']]:
                with self.subTest(companion_level=level, equipment=key):
                    self.assertLessEqual(recipe_level(definitions[key]), stage['trainer_level'])
                    self.assertTrue(set(definitions[key]['recipe']).issubset(available))

    def test_benchmark_reference_species_and_evolution_supplies_are_obtainable(self):
        from scripts.audit_combat_balance import BEASTS, BOSSES, PROGRESSION_STAGES
        from utils.catalog import load_catalog
        from utils.economy_balance import shop_level
        from utils.habitats import BIOMES, encounter_pool
        items = load_catalog('items.json')['items']
        for level, stage in PROGRESSION_STAGES.items():
            trainer = stage['trainer_level']
            for key in reference_species(level):
                current = key
                while parent := next((entry for entry in BEASTS.values()
                                      if (entry.get('evolution') or {}).get('evolves_to') == current), None):
                    evolution = parent['evolution']
                    self.assertLessEqual(evolution['level_required'], level)
                    self.assertLessEqual(evolution.get('trainer_level_required', 1), trainer)
                    method = evolution['method']
                    if method in items:
                        item = items[method]
                        if item.get('price', 0) > 0:
                            self.assertLessEqual(shop_level(item), trainer)
                        else:
                            self.assertGreaterEqual(trainer, 15)
                            self.assertTrue(any(method in boss['loot_table'] for boss in BOSSES), method)
                    current = parent['id']
                reachable = [region for region, data in BIOMES.items() if data['min_level'] <= trainer
                             and any(beast['id'] == current for beast in encounter_pool(BEASTS[current]['rarity'], region))]
                self.assertTrue(reachable, (level, trainer, key))

    def test_prepared_seeded_parties_keep_five_to_eight_minute_medians(self):
        # Production combat/action/pattern resolvers and synthetic clocks only.
        from scripts.audit_combat_balance import BOSSES, progression_matrix
        with patch('utils.raids.RaidPresentation', return_value=None):
            rows = progression_matrix()
        self.assertGreaterEqual(sum(row['outcome'] == 'win' for row in rows) / len(rows), .97)
        for field, values in [('level', (15, 25, 40, 50)), ('trainers', (1, 3, 10)),
                              ('boss', tuple(b['id'] for b in BOSSES))]:
            for value in values:
                won = [r['seconds'] for r in rows if r[field] == value and r['outcome'] == 'win']
                with self.subTest(field=field, value=value):
                    self.assertGreaterEqual(statistics.median(won), 300)
                    self.assertLessEqual(statistics.median(won), 480)

    def test_slow_inputs_and_ignored_patterns_are_materially_less_reliable(self):
        from scripts.audit_combat_balance import (BOSSES, BEASTS, PROGRESSION_STAGES, state_for, simulate_raid,
                                                   get_type_multiplier)
        outcomes = {True: [], False: []}
        with patch('utils.raids.RaidPresentation', return_value=None):
            for boss in BOSSES:
                party = sorted(reference_species(25), key=lambda key:
                    state_for(key, 25)['attack'] * get_type_multiplier(
                        BEASTS[key]['type'], BEASTS[boss['id']]['type']), reverse=True)
                for coordinated, cadence in ((True, 4), (False, 8)):
                    outcomes[coordinated].append(simulate_raid(party[0], 25, boss, 3, cadence,
                        build=PROGRESSION_STAGES[25]['build'], seed=47,
                        party_species=party, coordinated=coordinated))
        self.assertTrue(all(row['outcome'] == 'win' for row in outcomes[True]))
        self.assertGreaterEqual(sum(row['outcome'] != 'win' for row in outcomes[False]), 3)


class StoryGateRules(unittest.TestCase):
    def test_new_chapters_have_spaced_trainer_and_region_gates(self):
        chapters = story.load_questline()['chapters']
        expected = (3, 6, 10, 14, 24, 30, 32, 34, 36, 40)
        for (key, chapter), gate in zip(chapters.items(), expected):
            state = story.state_from_row(42, None)
            state['completed_chapters'] = list(chapters)[:list(chapters).index(key)]
            self.assertFalse(story.check_chapter_unlock(chapter, state, {'level': gate - 1})[0])
            self.assertTrue(story.check_chapter_unlock(chapter, state, {'level': gate})[0])
            if chapter['location'] == 'Celestial Loom':
                self.assertGreaterEqual(gate, 30)
        self.assertFalse(any(s['type'].startswith('raid') for s in chapters['chapter_2']['steps']))
        self.assertTrue(any(s['type'].startswith('raid') for s in chapters['chapter_9']['steps']))

    def test_introduced_chapters_and_earned_legacy_objectives_stay_available(self):
        chapters = story.load_questline()['chapters']
        state = story.state_from_row(42, None)
        state['completed_chapters'] = ['chapter_1']
        state['current_chapter'] = 'chapter_2'
        state['step_progress']['chapter_2'] = {'_introduced': 1, 'reach_celestial_loom': 10,
                                               'participate_raid': 1}
        self.assertTrue(story.check_chapter_unlock(chapters['chapter_2'], state, {'level': 3})[0])
        self.assertTrue(story.complete(chapters['chapter_2'], state))
        state['step_progress']['chapter_2']['participate_raid'] = 0
        self.assertFalse(story.complete(chapters['chapter_2'], state))
        story.apply_event(state, chapters['chapter_2'], 'battle_win')
        self.assertTrue(story.complete(chapters['chapter_2'], state))
        state['step_progress']['chapter_2']['_pacing_revision'] = story.PACING_REVISION
        self.assertFalse(story.complete(chapters['chapter_2'], state))
        state['step_progress']['chapter_10'] = {'_introduced': 1, 'reach_player_level': 15,
            'collect_beasts_20': 12, 'hatch_the_seed': 1, 'complete_any_raid': 1}
        self.assertTrue(story.complete(chapters['chapter_10'], state))


class RaidAndStoryPersistence(DatabaseCase):
    async def test_launch_reserves_the_party_after_all_awaited_trainer_checks(self):
        async def player(uid):
            if uid == 77:
                # A different battle reserves the first trainer during the
                # second lookup. The raid must recheck the entire group.
                active_fighters.add(42)
            return {'level': 15}
        raid = RaidSession(None, RAID_BOSSES['corrupted'][0], {42, 77}, None)
        with patch('utils.raids.storage.get_player', new=AsyncMock(side_effect=player)), \
                patch('utils.raids.party_rows', new=AsyncMock()) as parties:
            with self.assertRaisesRegex(ValueError, 'entered another battle'):
                await raid.start(42, 'epoch_shard')
        parties.assert_not_awaited()
        self.assertIn(42, active_fighters)
        self.assertNotIn(77, active_fighters)

    async def test_locked_story_page_explains_the_next_gate_and_keeps_navigation(self):
        import discord
        from utils import journal_state
        from utils.journal import JournalView
        await storage.update_player(42, level=3)
        state = story.state_from_row(42, None)
        state['completed_chapters'] = ['chapter_1']
        state['current_chapter'] = 'chapter_2'
        await story.save_quest_state(state)
        view = JournalView(42, await journal_state.snapshot(42), page='story')
        self.addCleanup(view.stop)
        view.art_enabled = False
        await view.load_page()
        view.render()
        text = '\n'.join(item.content for item in view.walk_children()
                         if isinstance(item, discord.ui.TextDisplay))
        self.assertIn('trainer level 6', text)
        self.assertIn('next chapter is waiting', text)
        self.assertNotIn('hollow is restored', text)
        self.assertTrue(any(isinstance(item, discord.ui.Button) and item.label == 'Explore'
                            for item in view.walk_children()))

    async def prepared_party(self):
        await storage.update_player(42, level=15)
        for slot, key in enumerate(('griffin', 'basilisk', 'wyvern'), 1):
            rid = await storage.add_beast_to_player(42, {**storage.get_beast_data(key), 'level': 15})
            await storage.set_raid_slot(42, rid, slot)
        await self.execute("INSERT INTO guilds(id,name,leader_id,level,guild_tokens) VALUES(5,'Test guild',42,5,100)")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(5,42)')

    def cleanup_raid(self, raid):
        active_raids.pop(raid.id, None)
        active_fighters.discard(42)
        combat.active_states.pop(42, None)
        combat.active_parties.pop(42, None)

    async def test_first_raid_needs_trainer_and_three_prepared_companions(self):
        lobby = RaidLobby(42, None)
        with self.assertRaisesRegex(ValueError, 'trainer level 15'):
            await lobby.eligible(42)
        await self.prepared_party()
        await lobby.eligible(42)
        rows = await party_rows(42)
        await self.execute('UPDATE player_beasts SET level=14 WHERE id=?', (rows[2]['id'],))
        with self.assertRaisesRegex(ValueError, 'three level 15'):
            await party_rows(42)
        lobby.stop()

    async def test_victory_is_a_net_token_sink_and_recovery_refunds_exactly_once(self):
        await self.prepared_party()
        channel = SimpleNamespace(id=7, guild=None, send=AsyncMock())
        raid = RaidSession(channel, RAID_BOSSES['corrupted'][0], {42}, 5)
        try:
            await raid.start(42, None)
            raid.damage[42] = 1
            await raid.finish(True)
            tokens = (await self.rows('SELECT guild_tokens FROM guilds WHERE id=5'))[0]['guild_tokens']
            self.assertEqual(tokens, 100 - GUILD_ENTRY_TOKENS + GUILD_VICTORY_TOKENS)
            await raid._settle_rewards(None)
            self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds WHERE id=5'))[0]['guild_tokens'], tokens)
            self.assertLess(GUILD_VICTORY_TOKENS, GUILD_ENTRY_TOKENS)
        finally:
            self.cleanup_raid(raid)
        interrupted = RaidSession(channel, RAID_BOSSES['corrupted'][0], {42}, 5)
        try:
            await interrupted.start(42, None)
            await refund_raid(interrupted.id)
            await refund_raid(interrupted.id)
            self.assertEqual((await self.rows('SELECT guild_tokens FROM guilds WHERE id=5'))[0]['guild_tokens'], tokens)
        finally:
            self.cleanup_raid(interrupted)

    async def test_owned_quest_supplies_and_catches_are_retained(self):
        await storage.update_player(42, level=10)
        state = story.state_from_row(42, None)
        state['current_chapter'] = 'chapter_3'
        state['completed_chapters'] = ['chapter_1', 'chapter_2']
        state['step_progress']['chapter_3'] = {'_introduced': 1}
        await story.save_quest_state(state)
        await self.beast('hellhound')
        await self.execute("INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,'phoenix_ash',2)")
        async with storage.connect() as db:
            self.assertEqual(await story.quest_material_options(db, 42, 'ember'), [])
            await story.reconcile(db, state, story.load_questline()['chapters']['chapter_3'])
        self.assertTrue(story.complete(story.load_questline()['chapters']['chapter_3'], state))
        self.assertEqual((await self.rows("SELECT quantity FROM player_materials WHERE material_id='phoenix_ash'"))[0]['quantity'], 2)
