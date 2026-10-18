"""Balance regressions use pure rules or disposable SQLite saves only."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage
from utils.balance_migrations import (LEGACY_CATALOG, LEGACY_GROWTH,
                                      PROGRESSION_REVISION, apply_progression_balance)
from utils.dispositions import apply_disposition
from utils.gameplay import STARTER_FORMS, evolved_stats, evolve_beast
from utils.progression import (MAX_LEVEL, activity_beast_xp, advance_beast_xp,
                               advance_trainer_xp, companion_growth,
                               encounter_beast_xp, encounter_trainer_xp, rarity_growth)


class ProgressionBudgets(unittest.TestCase):
    def test_clean_win_budget_requires_multiple_days_of_encounters(self):
        level = trainer = 1
        exp = trainer_exp = wins = 0
        while level < 15:
            wins += 1
            enemy_level = level
            level, exp = advance_beast_xp(level, exp, encounter_beast_xp(level, enemy_level))
            trainer, trainer_exp = advance_trainer_xp(
                trainer, trainer_exp, encounter_trainer_xp(trainer, enemy_level))
            if wins == 10:
                self.assertLess(level, 10)
        self.assertGreaterEqual(wins, 30)
        self.assertLessEqual(wins, 40)
        self.assertGreaterEqual(trainer, 8)
        self.assertGreaterEqual(wins / 10, 3)
        self.assertLessEqual(wins / 10, 4)

    def test_ten_explorations_cannot_create_400_hp_starter(self):
        catalog = storage.load_beasts()
        for species in STARTER_FORMS:
            level, exp = 1, 0
            for _ in range(10):
                level, exp = advance_beast_xp(level, exp, encounter_beast_xp(level, level))
            hp = catalog[species]['base_stats']['hp'] + companion_growth(species, level, catalog)['hp']
            self.assertLess(level, 15)
            self.assertLess(hp, 160)

    def test_optional_gold_xp_is_bounded_and_max_level_discards_xp(self):
        for kind, cap in [('training', 40), ('feed', 60)]:
            earned = 0
            for _ in range(1000):
                earned += activity_beast_xp(kind, 5, earned)
            self.assertEqual(earned, cap)
            self.assertEqual(activity_beast_xp(kind, MAX_LEVEL), 0)
        self.assertEqual(advance_beast_xp(49, 0, 10 ** 9), (50, 0))
        self.assertEqual(advance_trainer_xp(49, 0, 10 ** 9), (50, 0))
        self.assertEqual(advance_beast_xp(50, 10 ** 9, 10 ** 9), (50, 0))

    def test_difficulty_effort_and_trivial_farming_change_reward(self):
        normal = encounter_beast_xp(15, 15)
        # Strong lower-level foes still deserve their actual difficulty reward.
        self.assertEqual(encounter_beast_xp(15, 5), normal)
        self.assertLess(encounter_beast_xp(15, 15, difficulty='trivial'), normal / 2)
        self.assertGreater(encounter_beast_xp(15, 15, difficulty='hard'), normal)
        self.assertLess(encounter_beast_xp(15, 15, effort=.25), normal / 2)
        self.assertEqual(encounter_beast_xp(15, 15, effort=0), 0)

    def test_starter_forms_have_rare_budgets_and_no_retroactive_rare_growth(self):
        catalog = storage.load_beasts()
        for before_id, after_id in STARTER_FORMS.items():
            before, after = catalog[before_id], catalog[after_id]
            self.assertEqual(after['rarity'], 'rare')
            self.assertEqual(after['evolution_only'], True)
            previous = {k: v + companion_growth(before_id, 15, catalog)[k]
                        for k, v in before['base_stats'].items()}
            following = {k: v + companion_growth(after_id, 15, catalog)[k]
                         for k, v in after['base_stats'].items()}
            self.assertLessEqual(following['hp'], 220)
            self.assertGreaterEqual(following['hp'] / previous['hp'], 1.4)
            self.assertLessEqual(following['hp'] / previous['hp'], 1.65)
            self.assertGreaterEqual(following['attack'] / previous['attack'], 1.4)
            self.assertLessEqual(following['attack'] / previous['attack'], 1.65)
            self.assertEqual(companion_growth(after_id, 16, catalog)['hp']
                             - companion_growth(after_id, 15, catalog)['hp'], 6)

    def test_early_evolutions_keep_same_level_hp_and_attack_jump_under_twofold(self):
        catalog = storage.load_beasts()
        for beast in catalog.values():
            evo = beast.get('evolution')
            if not evo:
                continue
            after = catalog[evo['evolves_to']]
            level = evo['level_required']
            if level == MAX_LEVEL:
                continue  # Earned final ascensions award their full tier at the cap.
            for stat in ('hp', 'attack'):
                old = beast['base_stats'][stat] + companion_growth(beast['id'], level, catalog)[stat]
                new = after['base_stats'][stat] + companion_growth(after['id'], level, catalog)[stat]
                self.assertGreater(new, old, (beast['id'], stat))
                self.assertLess(new / old, 2, (beast['id'], stat))

    def test_evolved_growth_catches_up_monotonically_to_full_rarity_at_50(self):
        catalog = storage.load_beasts()
        for parent in catalog.values():
            evo = parent.get('evolution')
            if not evo:
                continue
            species = evo['evolves_to']
            level = evo['level_required']
            form = catalog[species]
            at_unlock = companion_growth(species, level, catalog)
            if level < MAX_LEVEL:
                self.assertEqual(at_unlock, companion_growth(parent['id'], level, catalog))
                following = companion_growth(species, level + 1, catalog)
                self.assertTrue(all(following[stat] >= at_unlock[stat] for stat in following))
            previous = at_unlock
            for checkpoint in range(level + 1, MAX_LEVEL + 1):
                current = companion_growth(species, checkpoint, catalog)
                self.assertTrue(all(current[stat] >= previous[stat] for stat in current))
                previous = current
            self.assertEqual(companion_growth(species, MAX_LEVEL, catalog),
                             rarity_growth(form['rarity'], MAX_LEVEL - 1))
        slime = catalog['ascended_slime']
        self.assertEqual(slime['base_stats']['hp'] + companion_growth('ascended_slime', 50, catalog)['hp'], 848)
        self.assertEqual(slime['base_stats']['attack'] + companion_growth('ascended_slime', 50, catalog)['attack'], 390)


class EvolutionAndLegacySaves(DatabaseCase):
    async def _level_to(self, row_id, level):
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        async with storage.connect() as db:
            await storage.apply_beast_levelup(db, row, level, 0)
            await db.commit()

    async def test_awakening_checks_all_four_requirements(self):
        row_id = await self.beast('twine', starter=True)
        await self._level_to(row_id, 15)
        await self.execute('UPDATE player_beasts SET bond=40,happiness=70 WHERE id=?', (row_id,))
        await storage.update_player(42, level=7)
        with self.assertRaisesRegex(ValueError, 'trainer level 8'):
            await evolve_beast(42, 1)
        await storage.update_player(42, level=8)
        await self.execute('UPDATE player_beasts SET bond=39 WHERE id=?', (row_id,))
        with self.assertRaisesRegex(ValueError, '40 bond'):
            await evolve_beast(42, 1)
        await self.execute('UPDATE player_beasts SET bond=40,happiness=69 WHERE id=?', (row_id,))
        with self.assertRaisesRegex(ValueError, '70 happiness'):
            await evolve_beast(42, 1)
        await self.execute('UPDATE player_beasts SET happiness=70 WHERE id=?', (row_id,))
        await evolve_beast(42, 1)
        row = (await storage.get_player_beasts(42))[0]
        self.assertEqual((row['beast_id'], row['rarity'], row['max_hp']), ('timestitch', 'rare', 204))

    async def test_profile_readiness_refreshes_trainer_and_catalyst_requirements(self):
        from cogs.profile import Profile
        from tests.test_gameplay import interaction
        row_id = await self.beast('twine', starter=True)
        await self._level_to(row_id, 15)
        await self.execute('UPDATE player_beasts SET bond=40,happiness=70 WHERE id=?', (row_id,))
        await storage.update_player(42, level=7)

        async def open_profile(number):
            command = interaction()
            message = SimpleNamespace(attachments=[])
            command.followup.send.return_value = message
            await Profile.beastinfo.callback(Profile(None), command, number)
            payload = command.followup.send.call_args.kwargs
            self.addCleanup(payload['view'].stop)
            return payload, message

        def evolution_field(payload):
            return next(field.value for field in payload['embed'].fields if '→' in field.name)

        with patch('utils.gear_art.render_portrait', new=AsyncMock(return_value=None)):
            payload, message = await open_profile(1)
            self.assertIn('trainer level 8', evolution_field(payload))
            self.assertNotIn('ready to evolve', evolution_field(payload))
            await storage.update_player(42, level=8)
            click = interaction()
            click.edit_original_response.return_value = message
            await payload['view'].show(click)
            self.assertIn('ready to evolve', evolution_field(click.edit_original_response.call_args.kwargs))
            goblin = await self.beast('goblin')
            await self._level_to(goblin, 25)
            payload, message = await open_profile(2)
            self.assertIn('Forge Ember', evolution_field(payload))
            self.assertIn('Craft at trainer level', evolution_field(payload))
            self.assertNotIn('ready to evolve', evolution_field(payload))
            await storage.add_item(42, 'forge_ember')
            click = interaction()
            click.edit_original_response.return_value = message
            await payload['view'].show(click)
            self.assertIn('ready to evolve', evolution_field(click.edit_original_response.call_args.kwargs))

    async def test_evolution_and_later_levels_keep_earned_investments(self):
        await storage.update_player(42, level=8)
        for uid, (before_id, after_id) in enumerate(STARTER_FORMS.items(), 42):
            await storage.get_or_create_player(uid, 'Evolution tester')
            await storage.update_player(uid, level=8)
            row_id = await self.beast(before_id, uid, True)
            await self._level_to(row_id, 15)
            await self.execute("UPDATE player_beasts SET bond=40,happiness=90,nickname='Old friend',"
                               'attack=attack+9,max_hp=max_hp+12,max_mana=max_mana+7,train_atk=3,'
                               'stat_treats_used=2,damage_bonus=5,raid_slot=2 WHERE id=?', (row_id,))
            original = (await storage.get_player_beasts(uid))[0]
            expected = evolved_stats(original, storage.get_beast_data(before_id), storage.get_beast_data(after_id))
            await evolve_beast(uid, 1)
            row = (await storage.get_player_beasts(uid))[0]
            for name in ('id', 'nickname', 'bond', 'train_atk', 'stat_treats_used', 'damage_bonus', 'raid_slot', 'player_number'):
                self.assertEqual(row[name], original[name])
            self.assertEqual(row['attack'], expected['attack'])
            self.assertEqual(row['max_hp'], expected['hp'])
            await self._level_to(row_id, 16)
            following = (await storage.get_player_beasts(uid))[0]
            self.assertEqual(following['max_hp'] - row['max_hp'], 6)
            self.assertEqual(following['attack'] - row['attack'], 2)

    async def test_earned_level_50_ascension_reaches_divine_budget_and_keeps_extras(self):
        neutral = {**storage.get_beast_data('slime'), 'disposition': ''}
        row_id = await storage.add_beast_to_player(42, neutral)
        await self._level_to(row_id, 50)
        await self.execute('UPDATE player_beasts SET attack=attack+9,max_hp=max_hp+12,train_atk=3 WHERE id=?', (row_id,))
        await storage.add_item(42, 'genesis_fruit')
        await evolve_beast(42, 1)
        row = (await storage.get_player_beasts(42))[0]
        self.assertEqual((row['beast_id'], row['rarity'], row['level']), ('ascended_slime', 'divine', 50))
        self.assertEqual((row['max_hp'], row['attack'], row['train_atk']), (860, 399, 3))
        self.assertFalse(any(item['item_id'] == 'genesis_fruit' for item in await storage.get_inventory(42)))
        natural = storage.get_beast_data('singularity')
        self.assertGreaterEqual(row['max_hp'] - 12, natural['base_stats']['hp'] + rarity_growth('divine', 49)['hp'])
        self.assertGreaterEqual(row['attack'] - 9, natural['base_stats']['attack'] + rarity_growth('divine', 49)['attack'])

    async def _legacy_beast(self, species, *, level=15, disposition=None, ko=False):
        row_id = await self.beast(species)
        rarity, base = LEGACY_CATALOG[species]
        base = apply_disposition(base, disposition)
        stats = {name: value + LEGACY_GROWTH[rarity][name] * (level - 1)
                 for name, value in base.items()}
        stats['hp'] += 30  # earned training and permanent treats
        stats['attack'] += 9
        stats['mana'] += 7
        await self.execute("UPDATE player_beasts SET level=?,exp=11,rarity=?,disposition=?,"
                           "max_hp=?,hp=?,max_mana=?,mana=?,attack=?,defense=?,speed=?,"
                           "bond=61,train_atk=3,train_hp=3,stat_treats_used=2,nickname='Old friend',"
                           "knocked_out_until=? WHERE id=?",
                           (level, rarity, disposition, stats['hp'], 0 if ko else stats['hp'] // 2,
                            stats['mana'], stats['mana'] // 3, stats['attack'], stats['defense'],
                            stats['speed'], '2099-01-01 00:00:00' if ko else None, row_id))
        return row_id, stats

    async def _run_migration_again(self):
        async with storage.connect() as db:
            await db.execute('DELETE FROM game_meta WHERE key=?', (PROGRESSION_REVISION,))
            await db.commit()
        await storage.init_db()

    async def test_400_hp_timestitch_rebases_once_preserving_real_investment(self):
        row_id, stats = await self._legacy_beast('timestitch')
        self.assertGreaterEqual(stats['hp'], 400)
        original = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        await self._run_migration_again()
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        self.assertEqual((row['rarity'], row['max_hp'], row['attack']), ('rare', 234, 99))
        self.assertEqual(row['max_mana'], 135)
        self.assertAlmostEqual(row['hp'] / row['max_hp'], original['hp'] / original['max_hp'], delta=1 / row['max_hp'])
        self.assertAlmostEqual(row['mana'] / row['max_mana'], original['mana'] / original['max_mana'], delta=1 / row['max_mana'])
        for key in ('id', 'beast_id', 'level', 'exp', 'bond', 'nickname', 'train_atk', 'train_hp', 'stat_treats_used'):
            self.assertEqual(row[key], original[key])
        async with storage.connect() as db:
            await apply_progression_balance(db)
            await db.commit()
        self.assertEqual(row, (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0])

    async def test_migration_preserves_disposition_ko_and_caps_legacy_levels(self):
        row_id, _ = await self._legacy_beast('rootwarden', level=70, disposition='ancient', ko=True)
        await storage.update_player(42, level=72, exp=99999)
        await self._run_migration_again()
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        canonical = apply_disposition(storage.get_beast_data('rootwarden')['base_stats'], 'ancient')
        self.assertEqual(row['max_hp'], canonical['hp'] + companion_growth('rootwarden', 50, storage.load_beasts())['hp'] + 30)
        self.assertEqual((row['level'], row['exp'], row['hp']), (50, 0, 0))
        self.assertEqual(row['disposition'], 'ancient')
        self.assertEqual(row['knocked_out_until'], '2099-01-01 00:00:00')
        trainer = await storage.get_player(42)
        self.assertEqual((trainer['level'], trainer['exp']), (50, 0))

    async def test_migration_failure_rolls_back_rows_and_revision_marker(self):
        row_id, _ = await self._legacy_beast('timestitch')
        await self._legacy_beast('nebuloom')
        before = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        from utils.balance_migrations import rebase_companion
        calls = 0

        def interrupt_second_row(row, catalog):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Simulated interrupted upgrade')
            return rebase_companion(row, catalog)

        async with storage.connect() as db:
            await db.execute('DELETE FROM game_meta WHERE key=?', (PROGRESSION_REVISION,))
            await db.commit()
            with patch('utils.balance_migrations.rebase_companion', side_effect=interrupt_second_row):
                with self.assertRaisesRegex(RuntimeError, 'interrupted upgrade'):
                    await apply_progression_balance(db)
            await db.commit()
        self.assertEqual(before, await self.rows('SELECT * FROM player_beasts ORDER BY id'))
        self.assertFalse(await self.rows('SELECT 1 FROM game_meta WHERE key=?', (PROGRESSION_REVISION,)))
        await self._run_migration_again()
        converted = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        self.assertEqual(converted['rarity'], 'rare')

    async def test_levelup_and_grant_enforce_cap_without_reviving_ko(self):
        row_id = await self.beast('twine', starter=True)
        await self.execute('UPDATE player_beasts SET hp=0 WHERE id=?', (row_id,))
        await self._level_to(row_id, 100)
        row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
        self.assertEqual((row['level'], row['exp'], row['hp']), (50, 0, 0))
        with self.assertRaisesRegex(ValueError, 'between 1 and 50'):
            await storage.add_beast_to_player(42, {**storage.get_beast_data('twine'), 'level': 51})


if __name__ == '__main__':
    unittest.main()
