"""20 production worker jobs with a live event-loop heartbeat."""
import asyncio
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'output/battle-choreography'
sys.path.insert(0,str(ROOT))
from scripts.preview_move_animations import case, gif_record
from scripts.preview_raid_arena import make_raid, clash
from cogs.guilds import RAID_BOSSES
from utils import battle_art as art, raid_art

MODULE_PATHS = {m.__name__: str(Path(m.__file__).resolve()) for m in (art, raid_art)}
MODULE_PATHS['preview_case'] = str(Path(case.__code__.co_filename).resolve())
assert all(Path(p).is_relative_to(ROOT.resolve()) for p in MODULE_PATHS.values()), MODULE_PATHS
assert art.PROJECT_ROOT.resolve() == ROOT.resolve(), art.PROJECT_ROOT

def sources():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'utils/battle_art.py', 'utils/battle_scene.py', 'utils/move_animations.py',
        'utils/divine_effects.py', 'utils/kinetic_effects.py', 'utils/raid_art.py',
        'utils/raid_scene.py', 'utils/field_journal.py', 'data/move-animations.json',
        'data/beasts.json', 'data/fonts/PixelifySans.ttf')}

async def main():
    initial = sources()
    samples=[]
    for species,name,realm,finish in (
        ('twine','Tick Bite','woods',False),
        ('twine','Thread Snap','woods',False),
        ('twine','Rewind Nibble','loom',False),
        ('twine','Borrowed Moment','loom',False),
        ('gloop','Glitter Bounce','woods',False),
        ('glacius','Ice Shell','glacial',False),
        ('wyvern','Fire Breath','ember',False),
        ('dragon','World Ender','ember',False),
        ('genesis','Genesis Explosion','loom',False),
        ('singularity','Big Bang','loom',False),
        ('astraea','Weave of Eternity','loom',False),
        ('nirvana','State of Nirvana','loom',False),
        ('twine','Tick Bite','woods',True),
        ('fenrir','Ragnarok Bite','trenches',True)):
        before,after,action=case(species,name,finish=finish)
        samples.append((f'{species}/{name}/ko={finish}',art.render,(before,after,realm,action)))
    for group in RAID_BOSSES.values():
        for boss in group:
            random.seed(917)
            before,after,events=clash(make_raid(boss,10))
            samples.append((f'{boss["id"]}/10trainers',raid_art.render,(before,after,events)))
    running=True;delays=[]
    async def heartbeat():
        while running:
            tick=time.perf_counter()
            await asyncio.sleep(.01)
            delays.append(max(0,time.perf_counter()-tick-.01)*1000)
    pulse=asyncio.create_task(heartbeat())
    records=[]
    try:
        for name,renderer,args in samples:
            frozen=copy.deepcopy(args)
            rng=random.getstate()
            tick=time.perf_counter()
            result=await art.render_job(renderer,*args)
            assert result is not None,name
            elapsed=(time.perf_counter()-tick)*1000
            assert args==frozen,(name,'mutation')
            assert random.getstate()==rng,(name,'combat RNG')
            records.append(dict(name=name,render_ms=round(elapsed,2),**gif_record(result)))
    finally:
        running=False
        await pulse
    values=sorted(x['render_ms'] for x in records)
    lag=sorted(delays)
    final = sources()
    result=dict(samples=len(records),all_workers_available=True,
        snapshots_unchanged=True,combat_rng_unchanged=True,
        render_max_ms=max(values),render_median_ms=(values[9]+values[10])/2,
        event_loop_p95_ms=round(lag[int((len(lag)-1)*.95)],2),
        event_loop_max_ms=round(max(lag),2),records=records,
        module_paths=MODULE_PATHS, source_fingerprints=initial,
        source_changes_during_benchmark=[k for k,v in initial.items() if final.get(k)!=v],
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Actual bounded production workers,14duels+6ten-trainerraids; local rendering includes CPU time; Discord upload/client playback excluded.')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'worker-benchmark.json').write_text(json.dumps(result,indent=2)+'\n')
    assert not result['source_changes_during_benchmark'], result['source_changes_during_benchmark']
    print(json.dumps({k:v for k,v in result.items() if k!='records'}))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    OUT=parser.parse_args().output.resolve()
    asyncio.run(main())
