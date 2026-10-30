"""Offline full-roster production export; no game data or network writes."""
import copy
import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import random
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / 'output/battle-choreography'
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageSequence
from scripts.preview_move_animations import case
from scripts.preview_raid_arena import make_raid, capture
from cogs.guilds import RAID_BOSSES
from utils import battle_art as art, battle_scene, db, field_journal as ui, move_animations as fx, raid_art, raid_scene
from utils.raids import MECHANICS
from utils.artwork import card_artwork_path

MODULE_PATHS = {m.__name__: str(Path(m.__file__).resolve())
                for m in (art, fx, battle_scene, raid_art, raid_scene, ui, db)}
MODULE_PATHS['preview_case'] = str(Path(case.__code__.co_filename).resolve())
assert all(Path(path).is_relative_to(ROOT.resolve()) for path in MODULE_PATHS.values()), MODULE_PATHS
assert art.PROJECT_ROOT.resolve() == ROOT.resolve(), art.PROJECT_ROOT

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sources():
    paths = [ROOT / name for name in (
        'utils/move_animations.py', 'utils/divine_effects.py',
        'utils/kinetic_effects.py', 'utils/battle_scene.py',
        'utils/battle_art.py', 'utils/field_journal.py',
        'utils/raid_art.py', 'utils/raid_scene.py', 'utils/raids.py', 'cogs/guilds.py',
        'data/move-animations.json', 'data/beasts.json',
        'data/fonts/PixelifySans.ttf', 'scripts/preview_move_animations.py')]
    paths += [Path(card_artwork_path(k)) for k in db.load_beasts()]
    paths += list((ROOT/'data/battle_realms').glob('*.png'))
    return {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p)
            for p in sorted(set(paths))}

def metadata(clip):
    with Image.open(io.BytesIO(clip.data)) as image:
        return {'bytes': len(clip.data), 'frames': image.n_frames,
                'milliseconds': sum(f.info.get('duration', 0) for f in ImageSequence.Iterator(image)),
                'one_shot': 'loop' not in image.info,
                'size': list(image.size)}

def tile(species, name, side):
    before, after, action = case(species, name, side=side)
    realm = art.TYPE_REALMS.get(db.get_beast_data(species)['type'], 'woods')
    total = battle_scene.duration(before, after, action)
    image = Image.new('RGB', (640, 552), '#081b23')
    draw = ImageDraw.Draw(image)
    ui.text(draw, fx.profile(species, name).sequence, (8, 3, 632, 26), size=19, fill=ui.MANILLA)
    ui.text(draw, f'{species} / {name}', (8, 27, 632, 49), size=16)
    for row, moment in enumerate((.32, .55)):
        frame = art.frame(before, after, realm, action, moment*action.milliseconds/total)
        image.paste(frame.crop((0, art.HUD, 640, art.HUD+art.HEIGHT)), (0, 56+row*248))
    return image

def raid_checks():
    records=[]
    for group in RAID_BOSSES.values():
        for boss in group:
            session=make_raid(boss,10)
            before=capture(session)
            random.seed(917)
            session.resolve_boss_turn()
            after=capture(session)
            events=tuple(session._art_events)
            assert events[-1].move==session.enemy['moves'][0]
            assert fx.profile(boss['id'],events[-1].move).sequence!='legacy'
            frozen=copy.deepcopy((before,after,events,session.party,session.enemy))
            rng=random.getstate()
            tick=time.perf_counter()
            clip=raid_art.render(before,after,events)
            meta=metadata(clip)
            assert meta['one_shot'] and meta['bytes']<=art.MAX_BYTES
            assert meta['milliseconds']==raid_scene.duration(before,after,events)
            assert frozen==(before,after,events,session.party,session.enemy)
            assert rng==random.getstate()
            filename=f'raid-{boss["id"]}-boss-action.gif'
            (OUT/filename).write_bytes(clip.data)
            raid_art.frame(before,after,events,.55).save(OUT/f'raid-{boss["id"]}-boss-impact.png')
            records.append(dict(boss=boss['id'],actual_recorded_move=events[-1].move,
                sequence=fx.profile(boss['id'],events[-1].move).sequence,
                render_ms=round((time.perf_counter()-tick)*1000,2),path=filename,**meta))
            # A frozen boss has no recorded attack, and no profile may be
            # requested while composing that real status event.
            frozen_session=make_raid(boss,10)
            frozen_session.enemy.update(status='freeze',status_turns=1)
            start=capture(frozen_session)
            frozen_session.resolve_boss_turn()
            end=capture(frozen_session)
            frozen_events=tuple(frozen_session._art_events)
            assert frozen_events[-1].kind=='status' and frozen_events[-1].move==''
            with patch.object(fx,'profile',side_effect=AssertionError('Invented frozen-boss attack')):
                for t in (.2,.55,.8):
                    raid_art.frame(start,end,frozen_events,t)
            records[-1]['frozen_boss_no_attack']=True
            if MECHANICS[boss['id']][2] in {'cleanse','rift','ember','stars'}:
                harmless=make_raid(boss,10)
                harmless.pending=True
                harmless.phase=1
                harmless.weaves=harmless.weave_required()
                harmless.pattern_started=0
                begin=capture(harmless)
                harmless.resolve_boss_turn()
                finish=capture(harmless)
                harmless_events=tuple(harmless._art_events)
                assert harmless_events[-1].move==''
                with patch.object(fx,'profile',side_effect=AssertionError('Invented solved-pattern attack')):
                    for t in (.2,.55,.8):
                        raid_art.frame(begin,finish,harmless_events,t)
                records[-1]['solved_harmless_pattern_no_attack']=True
    return records

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    clips_dir = OUT / 'roster-gifs'
    clips_dir.mkdir(exist_ok=True)
    initial = sources()
    records = []
    exemplars = {}
    start = time.perf_counter()
    for species, beast in db.load_beasts().items():
        for name in beast['moves']+[beast['ultimate']]:
            index = len(records)
            side = index % 2
            before, after, action = case(species, name, side=side)
            sequence = fx.profile(species, name).sequence
            realm = art.TYPE_REALMS.get(beast['type'], 'woods')
            frozen = copy.deepcopy((before, after, action))
            rng = random.getstate()
            tick = time.perf_counter()
            clip = art.render(before, after, realm, action)
            render_ms = (time.perf_counter()-tick)*1000
            meta = metadata(clip)
            expected = battle_scene.duration(before, after, action)
            assert frozen == (before, after, action), (species, name, 'snapshot mutated')
            assert rng == random.getstate(), (species, name, 'combat RNG consumed')
            assert meta['one_shot'], (species, name, 'loop extension')
            assert meta['milliseconds'] == expected, (species, name, meta, expected)
            assert clip.seconds == expected/1000, (species, name, clip.seconds, expected)
            assert meta['bytes'] <= art.MAX_BYTES, (species, name, meta)
            assert render_ms < art.RENDER_TIMEOUT*1000, (species, name, render_ms)
            assert meta['size'] == [640, art.TOTAL_HEIGHT]
            filename = f'{index:03d}-{species}-{sequence}.gif'
            (clips_dir/filename).write_bytes(clip.data)
            records.append(dict(index=index, species=species, move=name,
                sequence=sequence, actor_side=side, realm=realm, kind=action.kind,
                ultimate=action.ultimate, damage=action.damage,
                knocked_out=any(n.hp<=0<o.hp for o,n in zip(before,after)),
                expected_milliseconds=expected, render_ms=round(render_ms, 2),
                sha256=hashlib.sha256(clip.data).hexdigest(), path=f'roster-gifs/{filename}',
                snapshots_unchanged=True, rng_unchanged=True, **meta))
            # Prefer normal moves as ordinary-sequence examples and ultimates
            # for divine spectacle; preserve a deterministic first tie.
            score = int(action.ultimate)*10 + int(beast['rarity'] in {'divine','altered_divine','ancient'})
            if sequence not in exemplars or score > exemplars[sequence][0]:
                exemplars[sequence] = (score, species, name, side)
            if len(records) % 20 == 0:
                print(json.dumps({'progress': len(records), 'elapsed_seconds': round(time.perf_counter()-start,1)}), flush=True)
    contact_records = []
    items = sorted(exemplars.items())
    for offset in range(0, len(items), 8):
        sheet = Image.new('RGB', (1280, 2208), '#081b23')
        for i, (sequence, (_, species, name, side)) in enumerate(items[offset:offset+8]):
            image = tile(species, name, side)
            sheet.paste(image, (i%2*640, i//2*552))
            contact_records.append(dict(sequence=sequence, species=species, move=name,
                                        actor_side=side, sheet=f'sequences-{offset//8+1:02d}.png',
                                        tile=i))
        sheet.save(OUT/f'sequences-{offset//8+1:02d}.png')
    raids=raid_checks()
    final = sources()
    changed = [k for k in initial.keys() | final.keys() if initial.get(k) != final.get(k)]
    report = dict(roster=len(db.load_beasts()), entries=len(records),
        distinct_sequences=len(exemplars), unique_moves=len({x['move'] for x in records}),
        actor_sides={str(side): sum(x['actor_side']==side for x in records) for side in (0,1)},
        all_one_shot=all(x['one_shot'] for x in records), all_snapshots_unchanged=True,
        combat_rng_unchanged=True, all_duration_sums_correct=True,
        max_bytes=max(x['bytes'] for x in records),
        max_render_ms=max(x['render_ms'] for x in records),
        median_render_ms=round(statistics.median(x['render_ms'] for x in records),2),
        elapsed_seconds=round(time.perf_counter()-start,2),
        knockout_clips=sum(x['knocked_out'] for x in records),
        source_fingerprints=initial, source_changes_during_audit=changed,
        module_paths=MODULE_PATHS, valid_current_source=not changed,
        tool_sha256=digest(Path(__file__)), contacts=contact_records, clips=records, raid_boss_checks=raids,
        scope='Offline production renderer at level8 against level10behemoth, combat seed917. No Discord upload/client playback or player data changes.')
    (OUT/'roster-validation.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    assert not changed, changed
    print(json.dumps({k:v for k,v in report.items() if k not in {'clips','contacts','source_fingerprints'}}), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    OUT = parser.parse_args().output.resolve()
    main()
