"""Adventure team action economy, lifecycle and settlement regressions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.battle import Battle, award_player_exp, build_pve_beast_state, run_pve_battle
from tests.test_gameplay import DatabaseCase, interaction
from tests.test_interactions import component
from utils import adventure_party, combat, db as storage
from utils.duels import active_fighters, persist_pve_states, prepare_state
from utils.team_combat import ASSIST_MANA, PartyBattle, TeamMoveView, team_duel
from utils.progression import encounter_beast_xp, encounter_trainer_xp


class AutoTeam(TeamMoveView):
    async def wait(self):
        self.chosen_move = self.battle.active['moves'][0], False


class AdventureCombat(DatabaseCase):
    async def party(self, species=('twine', 'gloop', 'barkley')):
        members = []
        for index, beast_id in enumerate(species):
            row_id = await self.beast(beast_id, starter=index == 0)
            row = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row_id,)))[0]
            members.append(combat.build_state(storage.get_beast_data(beast_id), row))
        return members

    def enemy(self):
        enemy = build_pve_beast_state(storage.get_beast_data('slime'), 1)
        enemy.update(hp=100000, max_hp=100000, attack=1, speed=1)
        return enemy

    def fake(self):
        fake = interaction()
        fake.channel.send.return_value = SimpleNamespace(edit=AsyncMock())
        return fake

    async def test_swap_replaces_attack_and_preserves_each_members_resources(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        party[0].update(mana=7, hp=12, ultimate_charges=2, status='burn', status_turns=2, guard=30)
        party[1].update(mana=19, ultimate_charges=1)
        battle.meter = 2
        original = [{key: state.get(key) for key in ('hp', 'mana', 'ultimate_charges', 'status', 'status_turns', 'guard')}
                    for state in party]
        with patch('utils.combat.resolve_move') as move, patch('utils.combat.bonus_action') as bonus:
            action, extra = battle.resolve(('swap', party[1]['id']))
            self.assertEqual(action.kind, 'swap')
            self.assertIsNone(extra)
            self.assertIs(battle.active, party[1])
            self.assertEqual(battle.meter, 2)
            self.assertIs(combat.active_states[42], party[1])
            battle.resolve(('swap', party[0]['id']))
            move.assert_not_called()
            bonus.assert_not_called()
        for state, resources in zip(party, original):
            self.assertEqual({key: state.get(key) for key in resources}, resources)

    async def test_only_normal_damaging_moves_charge_assist_meter(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        with patch('utils.combat.random.random', return_value=.99):
            battle.resolve((party[0]['moves'][2], False))
            self.assertEqual(battle.meter, 0, 'Guard cannot farm assist charge')
            for _ in range(4):
                battle.resolve((party[0]['moves'][0], False))
            self.assertEqual(battle.meter, 3)
            party[0].update(mana=100, ultimate_charges=3)
            battle.meter = 1
            battle.resolve((party[0]['ultimate'], True))
            self.assertEqual(battle.meter, 1)
        party[1]['hp'] -= 10
        battle.swap(party[1]['id'])
        battle.resolve((party[1]['moves'][1], False))
        self.assertEqual(battle.meter, 1, 'Healing cannot farm assist charge')

    async def test_heal_assist_costs_meter_reserve_mana_and_entire_action(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        party[0].update(hp=10, mana=9, ultimate_charges=2)
        party[1]['mana'] = 30
        battle.meter = 3
        enemy_hp = battle.enemy['hp']
        with patch('utils.combat.resolve_move') as move, patch('utils.combat.bonus_action') as bonus:
            action, extra = battle.resolve(('assist', party[1]['id']))
        self.assertEqual(action.kind, 'assist')
        self.assertEqual(action.support_effect, 'heal')
        self.assertEqual(battle.meter, 0)
        self.assertEqual(party[1]['mana'], 30 - ASSIST_MANA)
        self.assertGreater(party[0]['hp'], 10)
        self.assertLessEqual(party[0]['hp'] - 10, int(party[0]['max_hp'] * .1))
        self.assertEqual((party[0]['mana'], party[0]['ultimate_charges']), (9, 2))
        self.assertEqual(enemy_hp, battle.enemy['hp'])
        self.assertIsNone(extra)
        move.assert_not_called()
        bonus.assert_not_called()

    async def test_assist_cannot_revive_or_use_sleeping_low_mana_reserve(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        battle.meter = 3
        party[0]['hp'] = 10
        party[1].update(hp=0, mana=50)
        self.assertFalse(battle.can_assist(party[1]))
        party[1].update(hp=20, mana=11)
        self.assertFalse(battle.can_assist(party[1]))
        party[1].update(mana=50, status='sleep')
        self.assertFalse(battle.can_assist(party[1]))
        party[1]['status'] = None
        self.assertTrue(battle.can_assist(party[1]))

    async def test_guard_assist_never_stacks_or_grants_ultimate_charge(self):
        party = await self.party(('gloop', 'twine'))
        party[1]['beast_type'] = 'ice'
        battle = PartyBattle(party, self.enemy(), 42)
        party[1]['mana'] = 40
        battle.meter = 3
        action, _ = battle.resolve(('assist', party[1]['id']))
        self.assertEqual(action.support_effect, 'guard')
        self.assertEqual(party[0]['guard'], 25)
        self.assertEqual(party[0]['ultimate_charges'], 0)
        battle.meter = 3
        self.assertFalse(battle.can_assist(party[1]))

    async def test_attack_assist_caps_overleveled_support_and_grants_no_bonus_resources(self):
        party = await self.party(('twine', 'slime'))
        party[1]['move_effects'] = {move: {'power': 1} for move in party[1]['moves']}
        party[1].update(attack=1000000, mana=50, ultimate_charges=2)
        battle = PartyBattle(party, self.enemy(), 42)
        battle.enemy.update(defense=0, shield=0)
        battle.meter = 3
        original = party[0]['mana'], party[0]['ultimate_charges']
        with patch('utils.combat.bonus_action') as bonus:
            action, extra = battle.resolve(('assist', party[1]['id']))
        self.assertEqual(action.support_effect, 'attack')
        self.assertGreater(action.damage, 0)
        self.assertLessEqual(action.damage, party[0]['attack'] * .9)
        self.assertEqual((party[0]['mana'], party[0]['ultimate_charges']), original)
        self.assertEqual((party[1]['mana'], party[1]['ultimate_charges']), (38, 2))
        self.assertEqual(battle.meter, 0)
        self.assertIsNone(extra)
        bonus.assert_not_called()

    async def test_first_entry_opening_effects_apply_once_without_stacking_all_bench(self):
        party = await self.party()
        enemy = self.enemy()
        party[1]['divine_passive'] = {'passive_effect': {'enemy_speed_reduce_percent': 30, 'turns': 3}}
        party[2]['divine_passive'] = {'passive_effect': {'enemy_speed_reduce_percent': 50, 'turns': 4}}
        enemy['divine_passive'] = {'passive_effect': {'enemy_speed_reduce_percent': 20, 'turns': 2}}
        battle = PartyBattle(party, enemy, 42)
        combat.start_battle(battle.active, enemy, battle.log)
        self.assertNotIn('gravity_turns', enemy)
        self.assertNotIn('gravity_turns', party[1])
        with patch('utils.combat.start_battle', wraps=combat.start_battle) as opening:
            battle.swap(party[1]['id'])
            self.assertEqual((enemy['gravity_turns'], enemy['gravity_multiplier']), (3, .7))
            self.assertEqual((party[1]['gravity_turns'], party[1]['gravity_multiplier']), (2, .8))
            enemy['gravity_turns'] = 1
            party[1]['gravity_turns'] = 1
            battle.swap(party[0]['id'])
            battle.swap(party[1]['id'])
            self.assertEqual(enemy['gravity_turns'], 1)
            self.assertEqual(party[1]['gravity_turns'], 1)
            self.assertEqual(opening.call_count, 1)
            battle.swap(party[2]['id'])
            self.assertEqual((enemy['gravity_turns'], enemy['gravity_multiplier']), (4, .5))
            self.assertEqual(opening.call_count, 2)

    async def test_forced_replacement_skips_knocked_out_reserves(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        party[0]['hp'] = party[1]['hp'] = 0
        action = battle.replace_knockout()
        self.assertEqual(action.incoming_id, party[2]['id'])
        self.assertIs(battle.active, party[2])
        self.assertIs(combat.active_parties[42], battle.party)
        party[2]['hp'] = 0
        self.assertFalse(battle.alive)
        self.assertIsNone(battle.replace_knockout())

    async def test_preparing_reserves_does_not_clone_carried_charge_or_one_use_buffs(self):
        party = await self.party()
        await storage.update_player(42, ultimate_charges=3, next_defense_boost=20,
                                    next_speed_boost=20, status_ward_charges=3)
        rows = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        lead = await prepare_state(42, rows[0], storage.get_beast_data(rows[0]['beast_id']), [])
        reserve = await prepare_state(42, rows[1], storage.get_beast_data(rows[1]['beast_id']), [], consume=False, party_reserve=True)
        self.assertEqual((lead['ultimate_charges'], reserve['ultimate_charges']), (3, 0))
        self.assertTrue(lead['status_ward'])
        self.assertFalse(reserve['status_ward'])
        self.assertEqual(reserve['item_defense_boost_percent'], 0)
        self.assertEqual(reserve['item_speed_boost_percent'], 0)
        self.assertEqual((await storage.get_player(42))['status_ward_charges'], 2)

    async def test_runner_swap_costs_the_action_and_enemy_hits_incoming_member(self):
        party = await self.party()
        enemy = self.enemy()
        choices = []

        class SwapThenTimeout(TeamMoveView):
            async def wait(view):
                choices.append(view.battle.active['id'])
                view.chosen_move = ('swap', party[1]['id']) if len(choices) == 1 else None

        initial = party[1]['hp']
        with patch('utils.combat.random.random', return_value=.99):
            result = await team_duel(self.fake(), party, enemy, 'Tag cost', view_class=SwapThenTimeout)
        self.assertEqual(choices, [party[0]['id'], party[1]['id']])
        self.assertLess(party[1]['hp'], initial)
        self.assertEqual(enemy['hp'], enemy['max_hp'])
        self.assertEqual(party[1]['ultimate_charges'], 0)
        self.assertEqual(result[0], 1)
        self.assertTrue(result[3])

    async def test_knockout_promotes_reserve_and_returns_surviving_final_active(self):
        party = await self.party()
        party[0]['hp'] = 1
        enemy = self.enemy()
        enemy.update(hp=1, max_hp=1, attack=10000, speed=10000)
        result = await team_duel(self.fake(), party, enemy, 'Replacement', view_class=AutoTeam)
        self.assertEqual(result[0], 0, result[2])
        self.assertIs(result[1][0], party[1])
        self.assertGreater(result[1][0]['hp'], 0)
        self.assertEqual(result[1][1]['hp'], 0)
        self.assertFalse(result[3])
        self.assertEqual(set(result[1][0]['_participated']), {party[0]['id'], party[1]['id']})
        saved = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        self.assertEqual(saved[0]['hp'], 0)
        self.assertIsNotNone(saved[0]['knocked_out_until'])
        self.assertTrue(all(member['closed'] for member in party))
        self.assertNotIn(42, combat.active_states)
        self.assertNotIn(42, combat.active_parties)
        self.assertNotIn(42, active_fighters)

    async def test_cancelling_closes_all_members_and_saves_hp_mana_and_ko(self):
        party = await self.party()
        ready = asyncio.Event()
        fake = self.fake()

        class Waiting(TeamMoveView):
            async def wait(self):
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(team_duel(fake, party, self.enemy(), 'Interrupted', view_class=Waiting))
        await asyncio.wait_for(ready.wait(), 2)
        party[0].update(hp=13, mana=8, ultimate_charges=2)
        party[1].update(hp=0, mana=7)
        party[2].update(hp=11, mana=6)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        self.assertEqual([(row['hp'], row['mana']) for row in saved], [(13, 8), (0, 7), (11, 6)])
        self.assertIsNotNone(saved[1]['knocked_out_until'])
        self.assertEqual((await storage.get_player(42))['ultimate_charges'], 2)
        self.assertTrue(all(member['closed'] for member in party))
        self.assertNotIn(42, combat.active_states)
        self.assertNotIn(42, combat.active_parties)
        self.assertNotIn(42, active_fighters)
        self.assertIsNone(fake.channel.send.return_value.edit.call_args.kwargs['view'])

    async def test_persistence_scales_gear_resources_without_permanent_stat_growth(self):
        party = await self.party(('barkley',))
        row = (await self.rows('SELECT * FROM player_beasts'))[0]
        party[0].update(hp=row['max_hp'], max_hp=row['max_hp'] * 2, mana=10, max_mana=20)
        await persist_pve_states(42, party, party[0])
        saved = (await self.rows('SELECT * FROM player_beasts'))[0]
        self.assertEqual(saved['hp'], int(row['max_hp'] / 2))
        self.assertEqual(saved['mana'], int(row['max_mana'] / 2))
        self.assertEqual(saved['max_hp'], row['max_hp'])

    async def test_armored_injured_team_cannot_refill_resources_by_repeated_cancellation(self):
        await self.party()
        rows = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        await storage.update_player(42, level=50)
        initial = []
        for index, row in enumerate(rows):
            hp = (1, 17, 29)[index]
            mana = (0, row['max_mana'], 7)[index]
            await self.execute('UPDATE player_beasts SET hp=?,mana=? WHERE id=?', (hp, mana, row['id']))
            for equipment in ('mossbark_wrapping', 'bark_sigil'):
                await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)',
                                   (row['id'], equipment))
            initial.append((hp, mana))

        for _ in range(3):
            ready = asyncio.Event()

            class Waiting(TeamMoveView):
                async def wait(view):
                    ready.set()
                    await asyncio.Event().wait()

            async def waiting(*args, **kwargs):
                return await team_duel(*args, **kwargs, view_class=Waiting)

            with patch('utils.team_combat.team_duel', side_effect=waiting):
                task = asyncio.create_task(run_pve_battle(self.fake(), rows[0], storage.get_beast_data('twine'), [],
                    self.enemy(), 'wild', 'No entry healing', AsyncMock(), AsyncMock(), party_rows=rows))
                await asyncio.wait_for(ready.wait(), 2)
                live = combat.active_parties[42]
                self.assertTrue(all(member['max_hp'] > row['max_hp'] for member, row in zip(live, rows)))
                self.assertLessEqual(live[0]['hp'], 2, 'Armor must preserve a 1-HP injury')
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            saved = await self.rows('SELECT * FROM player_beasts ORDER BY id')
            self.assertEqual([(row['hp'], row['mana']) for row in saved], initial)

    async def test_genesis_entry_mana_is_not_persisted_but_actual_move_restoration_is(self):
        await self.party(('twine',))
        await storage.update_player(42, level=25)
        await self.perk('genesis_spark')
        perks = await self.rows('SELECT * FROM player_perks WHERE user_id=42')
        await self.execute('UPDATE player_beasts SET mana=5')
        for _ in range(3):
            row = (await self.rows('SELECT * FROM player_beasts'))[0]
            state = await prepare_state(42, row, storage.get_beast_data('twine'), perks,
                                        consume=False, preserve_resources=True)
            self.assertEqual(state['mana'], 25, 'Genesis retains its in-battle entry bonus')
            await persist_pve_states(42, [state], state)
            self.assertEqual((await self.rows('SELECT mana FROM player_beasts'))[0]['mana'], 5)
        before = state['mana']
        combat.resolve_move(state, self.enemy(), state['moves'][0], False, [])
        delta = state['mana'] - before
        self.assertGreater(delta, 0)
        await persist_pve_states(42, [state], state)
        self.assertEqual((await self.rows('SELECT mana FROM player_beasts'))[0]['mana'], 5 + delta)

    async def test_swap_controls_reject_old_prompt_clicks_and_wrong_owner(self):
        party = await self.party()
        battle = PartyBattle(party, self.enemy(), 42)
        view = TeamMoveView(battle)
        view.open_turn()
        owner, other = component(uid=42), component(uid=99)
        button = view.team_buttons[0]
        await button.callback(other)
        other.response.send_message.assert_awaited_once()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_defer():
            entered.set()
            await release.wait()

        owner.response.defer.side_effect = slow_defer
        clicking = asyncio.create_task(button.callback(owner))
        await entered.wait()
        view.prepare_team_turn()
        view.open_turn()
        release.set()
        await clicking
        self.assertIsNone(view.chosen_move)
        self.assertIn('next', owner.followup.send.call_args.args[0])
        view.stop()

    async def test_wrapper_settles_shared_xp_once_and_callback_gets_final_active(self):
        members = await self.party()
        rows = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        await self.execute('UPDATE player_beasts SET hp=1 WHERE id=?', (rows[0]['id'],))
        enemy = self.enemy()
        enemy.update(hp=1, max_hp=1, attack=10000, speed=10000)
        won, lost = AsyncMock(), AsyncMock()

        async def automatic(*args, **kwargs):
            return await team_duel(*args, **kwargs, view_class=AutoTeam)

        with patch('utils.team_combat.team_duel', side_effect=automatic), patch('utils.adventure_party.award_xp', wraps=adventure_party.award_xp) as award, patch('utils.duels.beast_exp', new=AsyncMock()) as old_award:
            result = await run_pve_battle(self.fake(), rows[0], storage.get_beast_data('twine'), [], enemy,
                                          'wild', 'Team rewards', won, lost, party_rows=rows)
        self.assertEqual(result[0], 0, result[2])
        won.assert_awaited_once()
        lost.assert_not_awaited()
        self.assertEqual(won.call_args.args[1]['id'], members[1]['id'])
        self.assertFalse(won.call_args.args[3])
        award.assert_awaited_once()
        old_award.assert_not_awaited()
        self.assertEqual(award.call_args.args[1], [row['id'] for row in rows])
        self.assertEqual(award.call_args.args[2], rows[0]['id'])
        self.assertEqual(len(result), 4)
        self.assertEqual(len(await self.rows('SELECT * FROM battles')), 1)

    async def test_wrapper_supplied_roster_never_imports_newly_selected_reserves(self):
        await self.party()
        rows = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        new_id = await self.beast('slime')
        await adventure_party.set_slot(42, new_id, 1)
        expected = [rows[0]['id'], rows[1]['id']]

        async def finish(_interaction, party, enemy, *_args, **_kwargs):
            self.assertEqual([s['id'] for s in party], expected)
            enemy['hp'] = 0
            return 0, [party[0], enemy], [], False

        with patch('utils.team_combat.team_duel', side_effect=finish):
            await run_pve_battle(self.fake(), rows[0], storage.get_beast_data('twine'), [], self.enemy(),
                                 'wild', 'Locked roster', AsyncMock(), AsyncMock(), party_rows=rows[:2])

    async def test_solo_wrapper_uses_existing_duel_callbacks_without_team_runner(self):
        await self.party(('twine',))
        row = (await self.rows('SELECT * FROM player_beasts'))[0]
        enemy = self.enemy()

        async def finish(_interaction, states, *_args, **_kwargs):
            states[1]['hp'] = 0
            return 0, states, [], False

        won, lost = AsyncMock(), AsyncMock()
        with patch('utils.duels.duel', side_effect=finish) as duel, patch('utils.team_combat.team_duel', new=AsyncMock()) as team:
            await run_pve_battle(self.fake(), row, storage.get_beast_data('twine'), [], enemy,
                                 'wild', 'Solo', won, lost, party_rows=[row])
        duel.assert_awaited_once()
        team.assert_not_awaited()
        won.assert_awaited_once()
        self.assertEqual(len(won.call_args.args), 4)
        lost.assert_not_awaited()

    async def test_team_difficulty_uses_easiest_member_independent_of_order(self):
        party = await self.party()
        enemy = self.enemy()
        party[0].update(max_hp=1, attack=1, defense=1, speed=1)
        party[1].update(attack=10000000)
        from utils.habitats import encounter_difficulty
        self.assertEqual(encounter_difficulty(party[0], enemy), 'boss')
        self.assertEqual(encounter_difficulty(party[1], enemy), 'trivial')
        self.assertEqual(adventure_party.encounter_difficulty(party, enemy), 'trivial')
        self.assertEqual(adventure_party.encounter_difficulty(list(reversed(party)), enemy), 'trivial')

    async def test_real_challenge_weak_lead_cannot_inflate_rewards_above_powerful_reserve(self):
        await self.party(('twine', 'imp'))
        rows = await self.rows('SELECT * FROM player_beasts ORDER BY id')
        weak_id, strong_id = [row['id'] for row in rows]

        class TagThenAttack(TeamMoveView):
            async def wait(view):
                if view.battle.active['id'] == weak_id:
                    view.chosen_move = 'swap', strong_id
                else:
                    view.chosen_move = view.battle.active['moves'][0], False

        async def automatic(*args, **kwargs):
            return await team_duel(*args, **kwargs, view_class=TagThenAttack)

        trainer_awards, companion_awards = [], []
        for lead, reserve in ((weak_id, strong_id), (strong_id, weak_id)):
            await self.execute('UPDATE player_beasts SET is_active=(id=?),level=1,exp=0,mana=max_mana WHERE user_id=42', (lead,))
            await self.execute('UPDATE player_beasts SET hp=20,max_hp=20,attack=1,defense=1,speed=50 WHERE id=?', (weak_id,))
            await self.execute('UPDATE player_beasts SET hp=100,max_hp=100,attack=10000,defense=20,speed=50 WHERE id=?', (strong_id,))
            await self.execute('DELETE FROM adventure_team WHERE user_id=42')
            await adventure_party.set_slot(42, reserve, 1)
            await storage.update_player(42, level=1, exp=0, challenge_last_at=0)
            with patch('cogs.battle.pick_encounter', return_value=storage.get_beast_data('slime')), \
                 patch('cogs.battle.encounter_level', return_value=5), \
                 patch('utils.gear_art.send_owned_portrait', new=AsyncMock()), \
                 patch('cogs.battle.check_achievements', new=AsyncMock(return_value=[])), \
                 patch('utils.team_combat.team_duel', side_effect=automatic), \
                 patch('cogs.battle.encounter_trainer_xp', wraps=encounter_trainer_xp) as trainer_xp, \
                 patch('cogs.battle.encounter_beast_xp', wraps=encounter_beast_xp) as beast_xp, \
                 patch('cogs.battle.award_player_exp', wraps=award_player_exp) as award, \
                 patch('utils.combat.random.random', return_value=.99):
                await Battle.challenge.callback(Battle(None), self.fake(), 'woods')
            self.assertEqual(trainer_xp.call_count, 1)
            self.assertEqual(trainer_xp.call_args.kwargs['difficulty'], 'trivial')
            self.assertEqual(beast_xp.call_args.kwargs['difficulty'], 'trivial')
            trainer_awards.append(award.call_args.args[1])
            companion_awards.append(encounter_beast_xp(*beast_xp.call_args.args, **beast_xp.call_args.kwargs))
        self.assertEqual(trainer_awards, [encounter_trainer_xp(1, 5, difficulty='trivial')] * 2)
        self.assertEqual(companion_awards[0], companion_awards[1])
        self.assertEqual(len(await self.rows('SELECT * FROM battles WHERE winner_id=42')), 2)
