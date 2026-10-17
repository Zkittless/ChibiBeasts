"""Render actual combat clips and measure local rendering without a Discord login."""
import argparse
import asyncio
import io
import json
from pathlib import Path
import random
import statistics
import sys
import time
from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import battle_art as art, combat, db as storage


def states(left='twine', right='gloop'):
    return [build_pve_beast_state(storage.get_beast_data(k), 5) for k in (left, right)]


def sample(kind):
    pair = states('gloop' if kind == 'heal' else 'twine', 'barkley' if kind == 'ultimate' else 'gloop')
    pair[0].update(mana=pair[0]['max_mana'], ultimate_charges=3)
    if kind == 'heal':
        pair[0]['hp'] = 40
    if kind == 'knockout':
        pair[1]['hp'] = 8
    before = art.snapshot(pair)
    move = pair[0]['ultimate'] if kind == 'ultimate' else pair[0]['moves'][{'attack': 0, 'skill': 1, 'guard': 2, 'heal': 1, 'knockout': 0}[kind]]
    log = []
    damage = combat.resolve_move(*pair, move, kind == 'ultimate', log)
    after = art.snapshot(pair)
    action = art.Action(0, move, 'attack' if kind in {'ultimate', 'knockout'} else kind, damage, kind == 'ultimate')
    return before, after, action, log


async def benchmark(cases):
    delays = []
    running = True

    async def heartbeat():
        while running:
            start = time.perf_counter()
            await asyncio.sleep(.01)
            delays.append(max(0, time.perf_counter() - start - .01) * 1000)

    pulse = asyncio.create_task(heartbeat())
    timings = []
    try:
        for before, after, action, _ in cases:
            start = time.perf_counter()
            result = await art.render_async(before, after, 'woods', action)
            if result is None:
                raise RuntimeError('Renderer unexpectedly unavailable during benchmark')
            timings.append((time.perf_counter() - start) * 1000)
    finally:
        running = False
        await pulse
    return {'render_median_ms': round(statistics.median(timings), 2),
            'render_max_ms': round(max(timings), 2),
            'event_loop_delay_p95_ms': round(sorted(delays)[int((len(delays) - 1) * .95)], 2),
            'event_loop_delay_max_ms': round(max(delays), 2), 'samples': len(timings),
            'scope': 'Synthetic local renders; excludes Discord upload/client playback latency.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'battle-preview')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    random.seed(73)
    cases, clips = [], {}
    for kind in ('attack', 'skill', 'guard', 'heal', 'ultimate', 'knockout'):
        before, after, action, log = sample(kind)
        cases.append((before, after, action, log))
        clip = art.render(before, after, 'woods', action)
        (args.output / f'{kind}.gif').write_bytes(clip.data)
        art.frame(before, after, 'woods', action, .62).save(args.output / f'{kind}-impact.png')
        with Image.open(io.BytesIO(clip.data)) as gif:
            clips[kind] = {'bytes': len(clip.data), 'frames': gif.n_frames,
                           'duration_ms': sum(f.info['duration'] for f in ImageSequence.Iterator(gif)),
                           'one_shot': 'loop' not in gif.info, 'log': log}

    pair = states('prismite', 'barkley')
    saved = art.snapshot(pair)
    sheet = Image.new('RGB', (960, len(art.REALMS) * 478), '#171e32')
    draw = ImageDraw.Draw(sheet)
    for i, (realm, name) in enumerate(art.REALMS.items()):
        still = art.frame(saved, saved, realm)
        (args.output / f'{realm}.png').write_bytes(art.render(saved, saved, realm).data)
        draw.text((12, i * 478 + 5), name, font=art.font(16), fill='#edf0fc')
        sheet.paste(still, (0, i * 478 + 28))
        sheet.paste(still.resize((320, 224), Image.Resampling.NEAREST), (640, i * 478 + 28))
    sheet.save(args.output / 'realms-desktop-mobile.png')
    report = {'size': [640, 448], 'roster_portraits': len(storage.load_beasts()),
              'realms': list(art.REALMS), 'clips': clips,
              'performance': asyncio.run(benchmark(cases))}
    (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
