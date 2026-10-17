"""Measure real raid hot paths on a disposable database; no Discord login needed."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import aiosqlite
from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from utils import combat, db as storage
from utils.raids import RaidSession


def summarize(samples):
    values = sorted(samples)
    return {'median_ms': round(statistics.median(values) * 1000, 3),
            'p95_ms': round(values[int((len(values) - 1) * .95)] * 1000, 3),
            'samples': len(values)}


def combat_bench():
    samples = []
    for data in storage.load_beasts().values():
        attacker = build_pve_beast_state(data, 30)
        defender = build_pve_beast_state(storage.get_beast_data('griffin'), 30)
        defender['hp'] = defender['max_hp'] = 10**9
        for _ in range(30):
            before = time.perf_counter()
            combat.resolve_move(attacker, defender, attacker['moves'][0], False, [])
            samples.append(time.perf_counter() - before)
    return summarize(samples)


async def database_bench():
    users = tuple(range(100, 110))
    with tempfile.TemporaryDirectory() as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'synthetic.db')):
        await storage.init_db()
        for uid in users:
            await storage.get_or_create_player(uid, 'Synthetic trainer')
        raid = RaidSession(SimpleNamespace(id=1, send=AsyncMock()), RAID_BOSSES['corrupted'][0], users, None)
        raid.id = 1
        elapsed = [1000.0]
        raid.clock = lambda: elapsed[0]
        inserts = []
        for uid in users:
            raid.party[uid] = []
            raid.totems[uid] = False
            for slot, species in enumerate(('griffin', 'basilisk', 'wyvern'), 1):
                data = storage.get_beast_data(species)
                state = build_pve_beast_state(data, 30)
                rid = (uid - users[0]) * 3 + slot
                state['id'] = rid
                raid.party[uid].append(state)
                inserts.append((rid, uid, species, slot, slot == 1, slot, 30,
                                state['hp'], state['max_hp'], state['attack'], state['defense'],
                                state['speed'], state['mana'], state['max_mana'], data['rarity']))
        async with storage.connect() as db:
            await db.executemany('INSERT INTO player_beasts(id,user_id,beast_id,player_number,is_active,raid_slot,level,hp,max_hp,attack,defense,speed,mana,max_mana,rarity) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', inserts)
            await db.execute("INSERT INTO raids(id,boss_id,status) VALUES(1,?,'active')", (raid.boss['id'],))
            await db.commit()
        raid.build_enemy()
        raid.enemy['hp'] = raid.enemy['max_hp'] = 10**9
        # First participation includes one-time quest/achievement initialization.
        await asyncio.gather(*(raid.action(uid, 'attack') for uid in users))
        results = {}
        for kind in ('attack', 'guard'):
            samples, connections = [], []
            original = aiosqlite.connect

            def connect(*args, **kwargs):
                connections.append(1)
                return original(*args, **kwargs)

            with patch.object(aiosqlite, 'connect', side_effect=connect):
                for _ in range(6):
                    elapsed[0] += 10
                    before = time.perf_counter()
                    await asyncio.gather(*(raid.action(uid, kind) for uid in users))
                    samples.append(time.perf_counter() - before)
            results['ten_player_' + kind + '_burst'] = {
                **summarize(samples), 'connections_per_action': len(connections) / (len(samples) * len(users))}
        for party in raid.party.values():
            for state in party:
                state['hp'] = 0
        connections = []
        original = aiosqlite.connect

        def connect(*args, **kwargs):
            connections.append(1)
            return original(*args, **kwargs)

        with patch.object(aiosqlite, 'connect', side_effect=connect):
            before = time.perf_counter()
            await raid.record_knockouts()
            results['thirty_knockouts'] = {'elapsed_ms': round((time.perf_counter() - before) * 1000, 3),
                                          'connections': len(connections)}
        return results


async def main(output):
    random.seed(20261017)
    report = {'measured_at_utc': datetime.now(timezone.utc).isoformat(),
              'environment': 'Local Python 3.12; disposable SQLite WAL database, 10 trainers / 30 companions. Synthetic timings exclude Discord and network latency.',
              'resolved_hit_all_86_species': combat_bench()}
    report.update(await database_bench())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    asyncio.run(main(parser.parse_args().output))
