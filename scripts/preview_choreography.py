"""Reproducible production-renderer comparisons; never touches player data.

Run once with --source-root pointing at the baseline checkout and --phase before,
then against this checkout with --phase after. Open index.html and click Replay.
"""
import argparse
from dataclasses import asdict
import hashlib
import html
import json
from pathlib import Path
import random
import subprocess
import sys
import time


EXAMPLES = (
    ('twine', 'Tick Bite', 'woods'), ('twine', 'Thread Snap', 'woods'),
    ('twine', 'Borrowed Moment', 'loom'), ('twine', 'Rewind Nibble', 'loom'),
    ('gloop', 'Galaxy Splash', 'woods'), ('gloop', 'Glitter Bounce', 'woods'),
    ('wyvern', 'Fire Breath', 'ember'), ('glacius', 'Ice Shell', 'glacial'),
    ('harpy', 'Wind Shriek', 'glacial'), ('prismara', 'Facet Lance', 'loom'),
    ('genesis', None, 'loom'), ('singularity', None, 'loom'),
    ('chronos', None, 'loom'), ('zodiac', None, 'loom'),
    ('terminus', None, 'loom'), ('phoenix', None, 'ember'),
    ('leviathan', None, 'trenches'), ('hollow_king', None, 'hollowlight'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from PIL import Image
    from scripts.preview_move_animations import case, gif_record
    from scripts.preview_raid_arena import make_raid, clash
    from cogs.guilds import RAID_BOSSES
    from utils import battle_art as art, db as storage, move_animations as fx, raid_art
    assert Path(art.__file__).resolve().is_relative_to(args.source_root.resolve())
    output = args.output / args.phase
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for i, (species, move, realm) in enumerate(EXAMPLES):
        move = move or storage.get_beast_data(species)['ultimate']
        before, after, action = case(species, move, side=i % 2)
        start = time.perf_counter()
        clip = art.render(before, after, realm, action)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        slug = f'{i:02d}-{species}'
        (output / f'{slug}.gif').write_bytes(clip.data)
        info = gif_record(clip)
        assert info['one_shot'] and info['bytes'] < art.MAX_BYTES
        assert info['milliseconds'] == round(clip.seconds * 1000)
        sheet = Image.new('RGB', (art.WIDTH * 4, art.HEIGHT), '#102b2c')
        for column, t in enumerate((.24, .43, .52, .72)):
            frame = art.frame(before, after, realm, action, t)
            sheet.paste(frame.crop((0, art.HUD, art.WIDTH, art.HUD + art.HEIGHT)), (column * art.WIDTH, 0))
        sheet.save(output / f'{slug}.png', optimize=True)
        records.append(dict(slug=slug, species=species, move=move, profile=asdict(fx.profile(species, move)),
                            render_ms=elapsed, **info))
    random.seed(917)
    boss = next(b for group in RAID_BOSSES.values() for b in group if b['id'] == 'corrupted_dragon')
    raid = make_raid(boss, 10)
    before, after, events = clash(raid)
    start = time.perf_counter()
    clip = raid_art.render(before, after, events)
    (output / 'raid-ten.gif').write_bytes(clip.data)
    info = gif_record(clip)
    assert info['one_shot'] and info['bytes'] < art.MAX_BYTES
    records.append(dict(slug='raid-ten', species='Ten trainers', move='Concurrent raid',
                        render_ms=round((time.perf_counter()-start)*1000, 2), **info))
    files = ('utils/move_animations.py', 'utils/battle_scene.py', 'utils/raid_scene.py',
             'utils/divine_effects.py', 'utils/kinetic_effects.py', 'data/move-animations.json')
    fingerprints = {p: hashlib.sha256((args.source_root / p).read_bytes()).hexdigest()
                    for p in files if (args.source_root / p).exists()}
    report = dict(phase=args.phase, seed=917, source=str(args.source_root.resolve()),
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.source_root, text=True).strip(),
                  working_tree_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=args.source_root, text=True).strip()),
                  fingerprints=fingerprints, records=records,
                  scope='Actual seeded combat resolution, production snapshots and encoders. Offline; excludes Discord upload and client playback.')
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    cards = ''
    for r in records:
        slug = r['slug']
        title = html.escape(f"{r['species']} / {r['move']}")
        pair = ''.join(f'<div><h3>{phase.title()}</h3><img src="{phase}/{slug}.gif" data-src="{phase}/{slug}.gif" alt="{title} {phase}" loading="lazy"></div>' for phase in ('before', 'after'))
        cards += f'<article><h2>{title}</h2><button type="button">Replay both</button><div class="pair">{pair}</div></article>'
    (args.output / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Battle choreography: before and after</title>
<style>body{background:#101c29;color:#ffe1a6;font:16px system-ui;margin:24px}article{margin:32px 0;border-top:1px solid #576473;padding-top:16px}h2{font-size:20px}h3{font-size:16px}button{background:#ffe1a6;color:#101c29;border:0;padding:10px 16px;cursor:pointer}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,640px));gap:16px}img{width:100%;image-rendering:pixelated}p{max-width:900px;line-height:1.6}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>
<h1>Battle choreography: before and after</h1><p>Same seeded combat outcomes, sprites, battlefields and clip lengths. Effects are presentation only. Click Replay both to restart the one-shot clips together. The final example keeps ten trainers concurrent.</p>''' + cards + '''<script>document.querySelectorAll('button').forEach(b=>b.onclick=()=>{const n=Date.now();b.parentElement.querySelectorAll('img').forEach(i=>i.src=i.dataset.src+'?replay='+n)})</script></html>''', encoding='utf-8')
    print(json.dumps(dict(phase=args.phase, clips=len(records), largest_bytes=max(r['bytes'] for r in records), max_render_ms=max(r['render_ms'] for r in records))))


if __name__ == '__main__':
    main()
