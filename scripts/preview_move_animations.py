"""Export actual duel/raid animations, roster coverage and render measurements.

Example: python scripts/preview_move_animations.py --output ../output/battle-overhaul --audit-roster
No Discord login or player database is needed.
"""
import argparse
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import random
import statistics
import sys
import time

from PIL import Image, ImageDraw, ImageSequence

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from scripts.preview_raid_arena import capture, clash, make_raid
from utils import battle_art as art, combat, db as storage, move_animations as fx, raid_art
from utils.battle_scene import timing


def case(species, move, *, target='behemoth', side=0, finish=False):
    data=storage.get_beast_data(species)
    actor=build_pve_beast_state(data,8)
    target=build_pve_beast_state(storage.get_beast_data(target),10)
    actor.update(hp=max(1,actor['max_hp']//2),mana=actor['max_mana'],ultimate_charges=3)
    spec=combat.move_spec(actor,move,move==data['ultimate'])
    if spec.get('heal_percent'):actor.update(status='poison',status_turns=3)
    if finish:target['hp']=8
    pair=[actor,target] if side==0 else [target,actor]
    before=art.snapshot(pair)
    log=[]
    random.seed(917)
    damage=combat.resolve_move(actor,target,move,move==data['ultimate'],log)
    kind='heal' if spec.get('heal_percent') else 'guard' if spec.get('guard_percent') else 'attack' if move==data['moves'][0] else 'skill'
    action=art.Action(side,move,kind,damage,move==data['ultimate'],tuple(log[-2:]))
    return before,art.snapshot(pair),action


def gif_record(clip):
    with Image.open(io.BytesIO(clip.data)) as image:
        return dict(bytes=len(clip.data),frames=image.n_frames,
                    milliseconds=sum(f.info['duration'] for f in ImageSequence.Iterator(image)),
                    one_shot='loop' not in image.info)


async def benchmark(cases):
    running=True;delays=[];times=[]
    async def heartbeat():
        while running:
            start=time.perf_counter()
            await asyncio.sleep(.01)
            delays.append(max(0,time.perf_counter()-start-.01)*1000)
    pulse=asyncio.create_task(heartbeat())
    try:
        for args in cases:
            start=time.perf_counter()
            result=await art.render_job(*args)
            if result is None:raise RuntimeError('Renderer fell back during local validation')
            times.append((time.perf_counter()-start)*1000)
    finally:
        running=False
        await pulse
    return dict(render_max_ms=round(max(times),2),render_median_ms=round(statistics.median(times),2),
                event_loop_p95_ms=round(sorted(delays)[int((len(delays)-1)*.95)],2),
                event_loop_max_ms=round(max(delays),2),samples=len(times),
                scope='Local synthetic rendering; excludes Discord upload/client playback.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--audit-roster',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    examples=[('twine','Tick Bite','woods'),('twine','Thread Snap','woods'),
              ('twine','Rewind Nibble','loom'),('twine','Borrowed Moment','loom'),
              ('gloop','Galaxy Splash','woods'),('gloop','Glitter Bounce','woods'),
              ('barkley','Unmovable Root','woods'),('wyvern','Fire Breath','ember'),
              ('glacius','Ice Shell','glacial'),('harpy','Wind Shriek','glacial')]
    records={};performance=[];sheet=Image.new('RGB',(1280,art.TOTAL_HEIGHT*5),'#102b2c')
    for i,(species,move,realm) in enumerate(examples):
        before,after,action=case(species,move)
        clip=art.render(before,after,realm,action)
        slug=move.lower().replace(' ','-')
        (args.output/f'{slug}.gif').write_bytes(clip.data)
        record=gif_record(clip);assert record['one_shot'] and record['bytes']<art.MAX_BYTES
        records[slug]=record
        still=art.frame(before,after,realm,action,.52)
        still.save(args.output/f'{slug}-impact.png')
        sheet.paste(still,(i%2*640,i//2*art.TOTAL_HEIGHT))
        performance.append((art.render,before,after,realm,action))
    sheet.save(args.output/'moves-contact.png')
    for target,side in (('wisp',0),('barkley',1)):
        before,after,action=case('twine','Tick Bite',target=target,side=side,finish=True)
        slug=f'retreat-{target}'
        clip=art.render(before,after,'woods',action)
        (args.output/f'{slug}.gif').write_bytes(clip.data)
        records[slug]=gif_record(clip)
        for value in (0,.6,.82,1):
            art.frame(before,after,'woods',action,value).save(args.output/f'{slug}-{round(value*100)}.png')
        performance.append((art.render,before,after,'woods',action))
    for group in RAID_BOSSES.values():
        for boss in group:
            session=make_raid(boss,10)
            before,after,events=clash(session)
            clip=raid_art.render(before,after,events)
            records[boss['id']]=gif_record(clip)
            performance.append((raid_art.render,before,after,events))
            if boss['id']=='corrupted_dragon':
                (args.output/'raid-ten-trainers.gif').write_bytes(clip.data)
                raid_art.frame(before,after,events,.52).save(args.output/'raid-ten-trainers.png')
                session.enemy['hp']=0
                final=replace(capture(session),outcome='victory')
                clip=raid_art.render(after,final,events)
                (args.output/'raid-boss-retreat.gif').write_bytes(clip.data)
                records['raid-boss-retreat']=gif_record(clip)
                performance.append((raid_art.render,after,final,events))
    audit=[]
    if args.audit_roster:
        for species,data in storage.load_beasts().items():
            for move in data['moves']+[data['ultimate']]:
                before,after,action=case(species,move)
                realm=art.TYPE_REALMS.get(data['type'],'woods')
                clip=art.render(before,after,realm,action)
                record=gif_record(clip)
                assert record['one_shot'] and record['bytes']<art.MAX_BYTES,(species,move,record)
                audit.append(dict(species=species,move=move,**record))
    roster=storage.load_beasts()
    report=dict(roster=len(roster),move_entries=sum(map(len,fx.manifest().values())),
                unique_moves=len({move for beast in roster.values() for move in beast['moves']+[beast['ultimate']]}),
                clips=records,performance=asyncio.run(benchmark(performance)),roster_gifs=audit,
                data_source='Packaged art, actual move specs, level-scaled states, seeded combat outcomes.',
                preview_only='Offline. No Discord messages or player data were changed.')
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({**report,'roster_gifs':dict(count=len(audit),largest_bytes=max((x['bytes'] for x in audit),default=0))}))


if __name__=='__main__':
    main()
