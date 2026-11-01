"""Render Hexpurr through the shipped battle engine, without Discord or a DB.

Example: python scripts/preview_hexpurr.py --output ../output/hexpurr-preview --video
Video export optionally uses ffmpeg; PNG/GIF previews only require Pillow.
"""
import argparse
from dataclasses import replace
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageSequence
from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from scripts.preview_raid_arena import make_raid, capture
from utils import battle_art as art, combat, cosmetics, db as storage, raid_art
from utils.catalog import load_catalog


def fixture(appearance=''):
    party = [build_pve_beast_state(storage.get_beast_data(key), 12)
             for key in ('hexpurr', 'wickwing', 'veilmoth')]
    for i, member in enumerate(party):
        member['id'] = 101+i
    cat = party[0]
    equipment = load_catalog('equipment.json')['equipment']
    armor = appearance if appearance in equipment else 'gnome_iron_plating' if appearance else ''
    if armor:
        combat.apply_gear(cat, equipment[armor]['effect'])
    cat.update(armor_id=appearance, hp=round(cat['max_hp']*.83),
               mana=round(cat['max_mana']*.8), ultimate_charges=1,
               _party_states=party, _assist_meter=40)
    enemy = build_pve_beast_state(storage.get_beast_data('hollow_king'), 15)
    enemy['id'] = 201
    combat.begin_turn(cat, enemy, [])
    before = art.snapshot([cat, enemy])
    log = []
    with combat.using_rng(random.Random(20261101)):
        damage = combat.resolve_move(cat, enemy, 'Lanterns Out', False, log)
    after = art.snapshot([cat, enemy])
    return before, after, art.Action(0, 'Lanterns Out', 'skill', damage, journal=tuple(log[-2:]))


def encode_video(output, name, intro, clip, idle):
    folder = output/(name+'-frames')
    folder.mkdir(exist_ok=True)
    intro.save(folder/'intro.png')
    lines = [f"file '{name}-frames/intro.png'", 'duration 0.6']
    saved = {}
    for label, data in (('move',clip.data),('idle',idle.data)):
        paths = []
        with Image.open(io.BytesIO(data)) as gif:
            for i, frame in enumerate(ImageSequence.Iterator(gif)):
                path = folder/f'{label}-{i:02}.png'
                frame.convert('RGB').save(path)
                paths.extend([f"file '{name}-frames/{path.name}'",f"duration {frame.info.get('duration',80)/1000:.3f}"])
        saved[label] = paths
    lines += saved['move'] + saved['idle']*2 + [saved['idle'][0]]
    listing = output/(name+'-frames.txt')
    listing.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),
                    '-an','-vf','fps=30','-c:v','libx264','-crf','17','-pix_fmt','yuv420p',
                    '-movflags','+faststart',str(output/(name+'.mp4'))],check=True,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--video',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    report={}
    contact=Image.new('RGB',(art.WIDTH*3,art.TOTAL_HEIGHT*2))
    for col,(name,appearance) in enumerate((('unarmored',''),('armor','dragon_lord_aegis'),('ghost',cosmetics.appearance_id(cosmetics.GHOST)))):
        before,after,action=fixture(appearance)
        intro=art.frame(before,before,'hollowlight')
        start=time.perf_counter()
        clip=art.render(before,after,'hollowlight',action)
        clip_seconds=time.perf_counter()-start
        start=time.perf_counter()
        idle=art.render(after,after,'hollowlight',journal=action.journal,
                        ambient_start=clip.ambient_end,palette=clip.palette)
        idle_seconds=time.perf_counter()-start
        (output/f'{name}-move.gif').write_bytes(clip.data)
        (output/f'{name}-aura.gif').write_bytes(idle.data)
        intro.save(output/f'{name}-before.png')
        settled=art.frame(after,after,'hollowlight',journal=action.journal,ambient_seconds=clip.ambient_end)
        settled.save(output/f'{name}-after.png')
        contact.paste(intro,(col*art.WIDTH,0)); contact.paste(settled,(col*art.WIDTH,art.TOTAL_HEIGHT))
        report[name]={'appearance':appearance,'damage':action.damage,'move_bytes':len(clip.data),
                      'idle_bytes':len(idle.data),'move_render_seconds':round(clip_seconds,3),
                      'idle_render_seconds':round(idle_seconds,3),'move_duration':clip.seconds,
                      'idle_blocks_controls':bool(idle.seconds),'same_health':before[0].hp==after[0].hp,
                      'mana_spent':before[0].mana-after[0].mana,'dimensions':[640,528]}
        assert max(clip_seconds,idle_seconds)<art.RENDER_TIMEOUT
        assert max(len(clip.data),len(idle.data))<art.MAX_BYTES
        if args.video: encode_video(output,name,intro,clip,idle)
        if name=='ghost':
            keyframes=Image.new('RGB',(art.WIDTH*2,art.TOTAL_HEIGHT*4))
            for i,t in enumerate((0,.174,.304,.391,.478,.522,.696,1)):
                keyframes.paste(art.frame(before,after,'hollowlight',action,t,ambient_seconds=t*1.95),
                                (i%2*art.WIDTH,i//2*art.TOTAL_HEIGHT))
            keyframes.save(output/'ghost-choreography.png')
            # An opponent's transformation faces inward in PvP too.
            right_before=tuple(reversed(tuple(replace(f,party=()) for f in before)))
            right_after=tuple(reversed(tuple(replace(f,party=()) for f in after)))
            art.frame(right_before,right_after,'hollowlight',replace(action,actor=1),.478,
                      ambient_seconds=.478*1.95).save(output/'opponent-transform.png')
    contact.save(output/'forms-in-battle.png')
    raid=make_raid(next(iter(RAID_BOSSES.values()))[0],10)
    cat=build_pve_beast_state(storage.get_beast_data('hexpurr'),20)
    cat.update(id=777,armor_id=cosmetics.appearance_id(cosmetics.GHOST))
    raid.party[0][0]=cat
    before=capture(raid)
    raid.resolve_action(0,'skill')
    after=capture(raid)
    clip=raid_art.render(before,after,tuple(raid._art_events))
    idle=raid_art.render(after,after,ambient_start=clip.ambient_end,palette=clip.palette)
    (output/'raid-move.gif').write_bytes(clip.data)
    (output/'raid-aura.gif').write_bytes(idle.data)
    raid_art.frame(after,after,ambient_seconds=clip.ambient_end).save(output/'raid-after.png')
    report['raid']={'members':10,'move_bytes':len(clip.data),'idle_bytes':len(idle.data),'dimensions':[640,692]}
    report['scope']='Offline rendering with the integrated production code and real Hexpurr combat rules; no Discord connection or account grant.'
    (output/'validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
