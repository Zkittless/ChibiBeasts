"""Numerical balance invariants, including legacy progression preservation."""
import unittest
from unittest.mock import patch
from utils import combat, db as storage
from utils.balance_migrations import BASE_STAT_CHANGES, REVISION
from utils.raid_balance import budget, action_interval
from utils.raids import RaidSession
from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from cogs.ancient import ANCIENT_BOSSES
from tests.test_gameplay import DatabaseCase


def state(key='griffin', level=30):
    return build_pve_beast_state(storage.get_beast_data(key), level)


def raid(boss=None, keys=('dragon', 'kraken', 'fenrir'), count=3, level=30):
    boss = boss or RAID_BOSSES['corrupted'][0]
    session = RaidSession(None, boss, set(range(count)), 5)
    session.party = {uid: [state(key, level) for key in keys] for uid in session.users}
    session.totems = {uid: False for uid in session.users}
    session.build_enemy()
    return session


class NumericalRules(unittest.TestCase):
    def test_every_creature_has_positive_stats_and_can_afford_its_signature(self):
        for key, data in storage.load_beasts().items():
            with self.subTest(creature=key):
                self.assertTrue(all(value > 0 for value in data['base_stats'].values()))
                from utils.dispositions import DISPOSITIONS, apply_disposition
                for disposition in DISPOSITIONS:
                    stats = apply_disposition(data['base_stats'], disposition)
                    self.assertGreaterEqual(stats['mana'], 50, (key, disposition))
                for level in (1, 10, 15, 30, 50):
                    companion = state(key, level)
                    ultimate = combat.move_spec(companion, companion['ultimate'], True)
                    self.assertGreaterEqual(companion['max_mana'], ultimate['mana_cost'])
                    for name in companion['moves']:
                        self.assertLessEqual(combat.move_spec(companion, name)['mana_cost'], companion['max_mana'])

    def test_one_percent_and_thirty_percent_use_the_same_units(self):
        self.assertEqual(combat.fraction(1), .01)
        self.assertEqual(combat.fraction(30), .3)
        for key, data in storage.load_beasts().items():
            for field, value in (data.get('divine_passive') or {}).get('passive_effect', {}).items():
                if field.endswith('_percent'):
                    self.assertGreaterEqual(value, 1, (key, field))
                    self.assertLessEqual(value, 100, (key, field))

    def test_null_origin_really_pierces_thirty_percent_of_armor(self):
        attacker, target = state('fractured_genesis'), state('atlas')
        attacker['attack'], target['defense'] = 200, 100
        with patch('utils.combat.random.random', return_value=1), patch('utils.combat.random.uniform', return_value=1):
            damage, _, _, _ = combat.calc_damage(attacker, target, attacker['moves'][0])
        self.assertEqual(damage, 117)  # 200 * 100 / (100 + 100 * .7)

    def test_primordial_fire_does_not_delete_all_defense(self):
        attacker, target = state('ancient_genesis'), state('atlas')
        attacker['attack'], target['defense'] = 200, 200
        with patch('utils.combat.random.random', return_value=1), patch('utils.combat.random.uniform', return_value=1):
            damage, _, _, _ = combat.calc_damage(attacker, target, attacker['moves'][0])
        self.assertGreater(damage, 66)
        self.assertLess(damage, 130)

    def test_percentage_hp_hits_are_capped_by_the_attacker_against_bosses(self):
        attacker = state('corrupted_dragon')
        attacker['attack'] = 200
        values = []
        for hp in (10000, 1000000):
            target = state('atlas')
            target.update(raid_boss=True, hp=hp, max_hp=hp, defense=100)
            with patch('utils.combat.random.random', return_value=1), patch('utils.combat.random.uniform', return_value=1):
                values.append(combat.calc_damage(attacker, target, attacker['moves'][0])[0])
        self.assertEqual(values, [140, 140])

    def test_boss_burn_and_corruption_do_not_scale_with_the_whole_health_bar(self):
        source = state('corrupted_leviathan')
        source['attack'] = 200
        target = state('atlas')
        target.update(raid_boss=True, hp=1000000, max_hp=1000000)
        combat.apply_status(target, 'burn', source, [])
        combat.begin_turn(target, source, [])
        self.assertEqual(target['max_hp'] - target['hp'], 150)
        before = target['hp']
        combat.end_turn(source, target, [])
        self.assertEqual(before - target['hp'], 24)
        source['hp'] = 0
        combat.end_turn(source, target, [])
        self.assertEqual(before - target['hp'], 24)

    def test_repeated_freeze_cannot_skip_every_boss_turn(self):
        source, target = state('frost_sprite'), state('atlas')
        target.update(raid_boss=True, hp=10000, max_hp=10000)
        skips = 0
        for _ in range(12):
            for _ in range(10):
                combat.apply_status(target, 'freeze', source, [])
            skips += not combat.begin_turn(target, source, [])
            combat.end_turn(target, source, [])
        self.assertGreater(skips, 0)
        self.assertLessEqual(skips, 4)

    def test_long_fights_do_not_exponentially_multiply_stats(self):
        for key, cap in (('zodiac', 1.4), ('ascended_pegasus', 2.0)):
            actor, target = state(key), state('atlas')
            base_attack, base_speed = actor['attack'], actor['speed']
            for _ in range(100):
                combat.begin_turn(actor, target, [])
            self.assertLessEqual(actor['attack'], int(base_attack * cap))
            self.assertEqual(actor['speed'], int(base_speed * cap))

    def test_ancient_abyss_leaves_the_opponent_a_viable_stat_floor(self):
        actor, target = state('ancient_abyss'), state('atlas')
        original = {key: target[key] for key in ('attack', 'defense', 'speed')}
        for _ in range(100):
            combat.begin_turn(actor, target, [])
        for key, value in original.items():
            self.assertGreaterEqual(target[key], int(value * .85))

    def test_signature_charge_builds_once_per_chosen_action(self):
        actor, target = state('timestitch'), state('atlas')
        target.update(hp=100000, max_hp=100000)
        actor.update(mana=100, ultimate_charges=0)
        for _ in range(3):
            combat.resolve_move(actor, target, actor['moves'][0], False, [])
        self.assertEqual(actor['ultimate_charges'], 3)
        combat.resolve_move(actor, target, actor['ultimate'], True, [])
        self.assertEqual(actor['ultimate_charges'], 0)
        combat.resolve_move(actor, target, actor['moves'][0], False, [], grant_charge=False)
        self.assertEqual(actor['ultimate_charges'], 0)
        combat.resolve_move(actor, target, actor['moves'][2], False, [])
        self.assertEqual(actor['ultimate_charges'], 1)

    def test_wild_opponents_start_with_the_same_charge_building_rule(self):
        self.assertEqual(state('ancient_chronos')['ultimate_charges'], 0)

    def test_ai_does_not_spam_a_condition_against_known_immunity(self):
        from utils.duels import ai_move
        actor, target = state('chronos'), state('ancient_abyss')
        actor.update(mana=100, ultimate_charges=0)
        self.assertEqual(ai_move(actor, target, 'optimal'), (actor['moves'][0], False))


class RaidBudgets(unittest.TestCase):
    def test_upgrading_any_creatures_stats_does_not_make_the_boss_stronger(self):
        for key, data in storage.load_beasts().items():
            if data['rarity'] == 'dev':
                continue
            session = raid(keys=(key, key, key))
            before = budget(session.boss['id'], session.party)
            for party in session.party.values():
                for member in party:
                    for stat in ('attack', 'defense', 'speed', 'hp', 'max_hp'):
                        member[stat] *= 10
            self.assertEqual(budget(session.boss['id'], session.party), before, key)

    def test_high_level_bench_and_party_size_count_toward_difficulty(self):
        session = raid(level=10)
        before = budget(session.boss['id'], session.party)
        for party in session.party.values():
            party[1]['level'] = 50
        after = budget(session.boss['id'], session.party)
        self.assertGreater(after['hp'], before['hp'])
        self.assertEqual(after['level'], 50)
        values = [raid(count=count).enemy['max_hp'] for count in (1, 3, 6, 10)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0] * 7)

    def test_speed_and_slow_change_action_recovery_with_bounds(self):
        actor = state('griffin')
        ordinary = action_interval(actor, actor['speed'])
        actor['speed'] *= 2
        self.assertLess(action_interval(actor, actor['speed']/2), ordinary)
        actor['status'] = 'slow'
        self.assertEqual(action_interval(actor, actor['speed']/2), ordinary)
        for speed in (1, 100000):
            actor.update(speed=speed, status=None)
            self.assertTrue(3.5 <= action_interval(actor, 100) <= 6.5)

    def test_every_trainer_faces_boss_pressure_in_a_large_party(self):
        session = raid(count=10)
        before = [session.state(uid)['hp'] for uid in session.users]
        with patch('utils.combat.random.random', return_value=1):
            session.resolve_boss_turn()
        for uid, hp in zip(session.users, before):
            self.assertLess(session.state(uid)['hp'], hp)
            self.assertGreater(session.state(uid)['hp'], hp * .6)

    def test_all_six_bosses_use_valid_distinct_budgets(self):
        budgets = [raid(boss=boss).enemy for boss in [*RAID_BOSSES['corrupted'], *ANCIENT_BOSSES]]
        self.assertTrue(all(b['raid_boss'] and b['max_hp'] > 1000 for b in budgets))
        self.assertGreater(len({(b['max_hp'], b['attack'], b['defense']) for b in budgets}), 4)

    def test_raid_contribution_includes_end_of_action_corruption(self):
        session = raid(keys=('corrupted_leviathan',)*3, count=1)
        session.clock = lambda: 1000
        before = session.enemy['hp']
        damage, defeated = session.resolve_action(0, 'attack')
        self.assertFalse(defeated)
        self.assertEqual(damage, before-session.enemy['hp'])
        self.assertEqual(session.damage[0], damage)

    def test_answering_patterns_reduces_pressure_at_every_level(self):
        for level in (10, 30, 50):
            for boss in [*RAID_BOSSES['corrupted'], *ANCIENT_BOSSES]:
                losses = []
                for solved in (False, True):
                    session = raid(boss=boss, count=3, level=level)
                    session.pending = True
                    session.weaves = 2 if solved else 0
                    session.clock = lambda: 100
                    before = session.state(0)['hp']
                    session.resolve_boss_turn()
                    losses.append(before-session.state(0)['hp'])
                    self.assertGreater(session.state(0)['hp'], before*.4)
                self.assertLess(losses[1], losses[0], (level, boss['id']))


class SavedBalance(DatabaseCase):
    async def test_harder_raid_rewards_scale_and_still_pay_once(self):
        session = raid(count=1, level=50)
        session.id = 999
        session.party = {42: []}
        session.users = [42]
        await storage.update_player(42, level=30)
        before = await storage.get_player(42)
        with patch('utils.raids.random.random', return_value=1):
            await session.reward(42)
        after = await storage.get_player(42)
        self.assertEqual(after['gold'] - before['gold'], 600)
        self.assertEqual(after['celestial_shards'] - before['celestial_shards'], 6)
        self.assertEqual((await self.rows("SELECT quantity FROM player_materials WHERE material_id='kraken_ink'"))[0]['quantity'], 4)
        await session.reward(42)
        self.assertEqual(await storage.get_player(42), after)

    async def test_real_raid_start_uses_the_audited_budget_and_live_party(self):
        from types import SimpleNamespace
        from utils.raids import active_raids, refund_raid
        from utils.duels import active_fighters
        await storage.update_player(42, level=30)
        for slot, key in enumerate(('dragon', 'kraken', 'fenrir'), 1):
            row_id = await storage.add_beast_to_player(42, {**storage.get_beast_data(key), 'level': 30})
            await storage.set_raid_slot(42, row_id, slot)
        await self.execute("INSERT INTO guilds(id,name,leader_id,level,guild_tokens) VALUES(5,'Test guild',42,5,1000)")
        await self.execute('INSERT INTO guild_members(guild_id,user_id) VALUES(5,42)')
        for boss in [*RAID_BOSSES['corrupted'], *ANCIENT_BOSSES]:
            session = RaidSession(SimpleNamespace(id=7), boss, {42}, 5)
            try:
                await session.start(42, None)
                row = (await self.rows('SELECT max_hp,current_hp FROM raids WHERE id=?', (session.id,)))[0]
                self.assertEqual(row['max_hp'], session.enemy['max_hp'])
                self.assertEqual(row['current_hp'], session.enemy['hp'])
                self.assertTrue(all(s['ultimate_charges'] == 0 for s in session.party[42]))
                self.assertTrue(all(s['mana'] > 25 for s in session.party[42]))
                self.assertIs(combat.active_parties[42], session.party[42])
            finally:
                active_raids.pop(session.id, None)
                active_fighters.discard(42)
                combat.active_states.pop(42, None)
                combat.active_parties.pop(42, None)
                if session.id:
                    await refund_raid(session.id)

    async def test_legacy_stat_migration_preserves_training_disposition_and_knockouts(self):
        from utils.dispositions import apply_disposition
        await self.execute('DELETE FROM game_meta WHERE key=?', (REVISION,))
        for key, (old, new) in BASE_STAT_CHANGES.items():
            # Simulate an existing level-7 trained companion, using the old catalog.
            base = apply_disposition(old, 'feral')
            growth = storage.calc_stat_growth(storage.get_beast_data(key), 6)
            await self.execute('INSERT INTO player_beasts(user_id,beast_id,level,hp,max_hp,attack,defense,speed,mana,max_mana,rarity,disposition,bond,train_atk) VALUES(42,?,7,0,?,?,?,?,?,?,?,\'feral\',40,5)',
                               (key, base['hp']+growth['hp'], base['attack']+growth['attack']+20, base['defense']+growth['defense'], base['speed']+growth['speed'], base['mana']+growth['mana'], base['mana']+growth['mana'], storage.get_beast_data(key)['rarity']))
        await storage.init_db()
        first = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        await storage.init_db()
        self.assertEqual(await self.rows('SELECT * FROM player_beasts ORDER BY id'), first)
        for row in first:
            expected = apply_disposition(BASE_STAT_CHANGES[row['beast_id']][1], 'feral')
            growth = storage.calc_stat_growth(row, 6)
            self.assertEqual(row['attack'], expected['attack']+growth['attack']+20)
            self.assertEqual(row['max_hp'], expected['hp']+growth['hp'])
            self.assertEqual(row['max_mana'], expected['mana']+growth['mana'])
            self.assertEqual(row['hp'], 0)
            self.assertEqual((row['bond'], row['train_atk']), (40, 5))
