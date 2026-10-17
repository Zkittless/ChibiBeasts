"""Seeded offline combat/raid playthroughs against a disposable database."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils import combat, db as storage
from utils.duels import ai_move, active_fighters
from cogs.battle import build_pve_beast_state


def assert_state(state):
    assert 0 <= state['hp'] <= state['max_hp'], state['name']
    assert 0 <= state['mana'] <= state['max_mana'], state['name']


def duel_sweep():
    roster = storage.load_beasts()
    opponents = ['slime', 'fairy', 'hellhound', 'kelpie', 'chronos']
    outcomes = Counter()
    rounds = []
    for beast_id, beast in roster.items():
        for level in (1, 15, 30):
            for seed, opponent_id in enumerate(opponents):
                random.seed(f'{beast_id}:{level}:{seed}')
                states = [build_pve_beast_state(beast, level), build_pve_beast_state(roster[opponent_id], level)]
                states[0]['ultimate_charges'] = seed % 4
                log = []
                combat.start_battle(*states, log)
                for turn in range(1, 21):
                    for index in sorted(range(2), key=lambda i: combat.effective_speed(states[i]), reverse=True):
                        state, enemy = states[index], states[1-index]
                        if min(s['hp'] for s in states) <= 0:
                            break
                        if combat.begin_turn(state, enemy, log):
                            move, ultimate = ai_move(state, enemy, 'optimal')
                            combat.resolve_move(state, enemy, move, ultimate, log)
                            if state['hp'] > 0 and enemy['hp'] > 0 and combat.bonus_action(state):
                                combat.resolve_move(state, enemy, state['moves'][0], False, log, grant_charge=False)
                        combat.end_turn(state, enemy, log)
                        for checked in states:
                            assert_state(checked)
                    if min(s['hp'] for s in states) <= 0:
                        break
                rounds.append(turn)
                outcomes['knockout' if min(s['hp'] for s in states) <= 0 else 'judged_at_20_rounds'] += 1
    return {'fights': len(rounds), 'creatures': len(roster), 'levels': [1, 15, 30], 'opponents': opponents, 'outcomes': dict(outcomes), 'mean_rounds': round(sum(rounds)/len(rounds), 2)}


async def raid_sweep():
    from cogs.ancient import ANCIENT_BOSSES, SUMMON_ITEMS
    from cogs.guilds import RAID_BOSSES
    from utils.raids import RaidSession, active_raids
    results = []
    with tempfile.TemporaryDirectory() as directory:
        target = str(Path(directory) / 'simulation.db')
        with patch.object(storage, 'DB_PATH', target):
            await storage.init_db()
            for uid in (42, 77, 88):
                await storage.get_or_create_player(uid, f'Synthetic trainer {uid}')
                await storage.update_player(uid, level=20)
                for slot, beast_id in enumerate(('prismara', 'timestitch', 'rootwarden'), 1):
                    row_id = await storage.add_beast_to_player(uid, {**storage.get_beast_data(beast_id), 'level': 15})
                    await storage.set_raid_slot(uid, row_id, slot)
            async with storage.connect() as db:
                await db.execute("INSERT INTO guilds(id,name,leader_id,level,guild_tokens) VALUES(5,'Synthetic guild',42,5,10000)")
                await db.executemany('INSERT INTO guild_members(guild_id,user_id) VALUES(5,?)', [(uid,) for uid in (42, 77, 88)])
                await db.commit()
            for boss in [*RAID_BOSSES['corrupted'], *ANCIENT_BOSSES]:
                for users in ({42}, {42, 77, 88}):
                    random.seed(f"{boss['id']}:{len(users)}")
                    guild_id = 5 if boss['id'].startswith('corrupted_') else None
                    item = next((key for key, value in SUMMON_ITEMS.items() if value == boss['id']), None)
                    if item:
                        await storage.add_item(42, item)
                    channel = SimpleNamespace(id=123, guild=SimpleNamespace(id=7), send=AsyncMock())
                    raid = RaidSession(channel, boss, users, guild_id)
                    try:
                        await raid.start(42, item)
                        elapsed = [100.0]
                        raid.clock = lambda: elapsed[0]
                        raid.view = SimpleNamespace(stop=lambda: None)
                        mechanic_responses = 0
                        swaps = 0
                        for tick in range(1, 76):
                            for uid in raid.users:
                                state = raid.state(uid)
                                if state['hp'] <= 0:
                                    if any(s['hp'] > 0 for s in raid.party[uid]):
                                        raid.last.clear()
                                        await raid.action(uid, 'swap')
                                        swaps += 1
                                    continue
                                if raid.pending and uid not in raid.weavers:
                                    raid.last.clear()
                                    await raid.action(uid, 'weave')
                                    mechanic_responses += 1
                                move, ultimate = ai_move(state, raid.enemy, 'optimal')
                                kind = 'ultimate' if ultimate else ('attack', 'skill', 'guard')[state['moves'].index(move)]
                                raid.last.clear()  # Advance synthetic time without sleeping.
                                await raid.action(uid, kind)
                                # A solo trainer also has time to respond after the hit announcing a pattern.
                                if not raid.closed and raid.pending and uid not in raid.weavers:
                                    raid.last.clear()
                                    await raid.action(uid, 'weave')
                                    mechanic_responses += 1
                                for party in raid.party.values():
                                    for checked in party:
                                        assert_state(checked)
                                if raid.closed:
                                    break
                            if raid.closed:
                                break
                            elapsed[0] += 8
                            async with raid.lock, combat.lock_states(raid.users):
                                await raid.boss_turn()
                            if raid.enemy['hp'] <= 0:
                                await raid.finish(True)
                            if not any(s['hp'] > 0 for party in raid.party.values() for s in party):
                                await raid.finish(False)
                            if raid.closed:
                                break
                        if not raid.closed:
                            await raid.finish(False)
                        async with storage.connect() as db:
                            async with db.execute('SELECT status FROM raids WHERE id=?', (raid.id,)) as c:
                                status = (await c.fetchone())[0]
                        results.append({'boss': boss['id'], 'trainers': len(users), 'ticks': tick, 'result': status, 'weave_responses': mechanic_responses, 'swaps': swaps})
                    finally:
                        await raid._stop_refresh()
                        active_raids.pop(raid.id, None)
                        active_fighters.difference_update(users)
                        for uid in users:
                            combat.active_states.pop(uid, None)
                            combat.active_parties.pop(uid, None)
                        # Fresh run for each scenario; real knockout timers are tested separately.
                        async with storage.connect() as db:
                            await db.execute('UPDATE player_beasts SET hp=max_hp,knocked_out_until=NULL')
                            await db.commit()
    return results


async def main(output):
    report = {'measured_at_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'Offline seeded simulations; no human timing or Discord API calls. Raid action cooldowns are advanced synthetically.', 'duels': duel_sweep(), 'raids': await raid_sweep()}
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args().output.resolve()))
