"""Repeatable local microbenchmarks using synthetic data, never a player database."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import aiosqlite
from utils import db as storage
from utils.habitats import pick_encounter


def timing(samples):
    values=sorted(samples)
    return {'median_ms':round(statistics.median(values)*1000,3),'p95_ms':round(values[int((len(values)-1)*.95)]*1000,3),'samples':len(values)}


def sync_bench(function,repeats):
    function()
    values=[]
    for _ in range(repeats):
        start=time.perf_counter();function();values.append(time.perf_counter()-start)
    return timing(values)


async def database_bench():
    from utils.events import record_event
    results={}
    with tempfile.TemporaryDirectory() as directory:
        target=str(Path(directory)/'synthetic.db')
        with patch.object(storage,'DB_PATH',target):
            await storage.init_db()
            await storage.get_or_create_player(42,'Benchmark')
            async with aiosqlite.connect(target) as db:
                await db.executemany("INSERT INTO player_beasts(user_id,beast_id,player_number,is_active,level,hp,max_hp,attack,defense,speed,mana,max_mana,happiness,rarity) VALUES(?,'slime',?, ?,1,40,40,20,20,20,40,40,70,'common')",((user,number,int(number==1)) for user in range(1,5001) for number in range(1,11)))
                await db.commit()
            for label,function in [('collection_10_of_50000',lambda:storage.get_player_beasts(42)),('beast_number_of_50000',lambda:storage.get_beast_by_player_number(42,5)),('battle_win_event',lambda:record_event(42,'battle_win'))]:
                samples=[];connections=0
                original=aiosqlite.connect
                def connect(*args,**kwargs):
                    nonlocal connections
                    connections+=1
                    return original(*args,**kwargs)
                with patch.object(aiosqlite,'connect',connect):
                    for _ in range(20):
                        start=time.perf_counter();await function();samples.append(time.perf_counter()-start)
                results[label]={**timing(samples),'connections_per_action':connections/len(samples)}
            async with aiosqlite.connect(target) as db:
                async with db.execute('EXPLAIN QUERY PLAN SELECT * FROM player_beasts WHERE user_id=42 AND player_number=5') as c:results['lookup_query_plan']=[r[3] for r in await c.fetchall()]
    return results


async def run(output):
    report={'measured_at_utc':datetime.now(timezone.utc).isoformat(),'environment':'Local Python 3.12; synthetic SQLite database with 50,000 companions. Timings exclude Discord/network latency.','catalog_lookup':sync_bench(lambda:storage.get_beast_data('prismite'),300),'loom_encounter':sync_bench(lambda:pick_encounter({'rare':.35,'epic':.35,'legendary':.2,'divine':.1},'loom'),100)}
    report.update(await database_bench())
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args().output.resolve()))
