"""Render the real shared raid arena and measure it without a bot login/DB."""
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
from cogs.guilds import RAID_BOSSES
from utils import battle_art, combat, db as storage, raid_art as art
from utils.raids import RaidSession, MECHANICS

SPECIES = ('timestitch', 'nebuloom', 'prismara', 'rootwarden', 'griffin',
           'basilisk', 'wyvern', 'yeti', 'sphinx', 'thunderbird')


def make_raid(boss, count=4):
    raid = RaidSession(None, boss, set(range(count)), 5 if boss['type'] == 'corrupted' else None)
    raid.started = 0
    raid.clock = lambda: 96
    for uid in raid.users:
        raid.party[uid] = []
        for slot in range(3):
            state = build_pve_beast_state(storage.get_beast_data(SPECIES[(uid + slot * 4) % 10]), 20)
            state['hp'] = round(state['max_hp'] * (.73 + uid % 3 * .07))
            state['mana'] = state['max_mana']
            state['ultimate_charges'] = 3
            raid.party[uid].append(state)
        raid.totems[uid] = False
    raid.build_enemy()
    raid.enemy['hp'] = round(raid.enemy['max_hp'] * .762)
    for uid in raid.users:
        combat.start_battle(raid.state(uid), raid.enemy, [])
    return raid


def capture(raid):
    return art.capture(raid, MECHANICS[raid.boss['id']][0])


def clash(raid):
    before = capture(raid)
    for uid in raid.users:
        raid.resolve_action(uid, ('guard', 'ultimate', 'attack', 'skill')[uid % 4])
    return before, capture(raid), tuple(raid._art_events)


async def benchmark(cases):
    running, delays = True, []

    async def heartbeat():
        while running:
            start = time.perf_counter()
            await asyncio.sleep(.01)
            delays.append(max(0, time.perf_counter() - start - .01) * 1000)

    pulse = asyncio.create_task(heartbeat())
    timings = []
    try:
        for before, after, events in cases:
            start = time.perf_counter()
            result = await battle_art.render_job(art.render, before, after, events)
            if result is None:
                raise RuntimeError('Raid renderer unexpectedly unavailable')
            timings.append((time.perf_counter() - start) * 1000)
    finally:
        running = False
        await pulse
    return {'samples': len(timings), 'render_median_ms': round(statistics.median(timings), 2),
            'render_max_ms': round(max(timings), 2),
            'event_loop_delay_p95_ms': round(sorted(delays)[int((len(delays) - 1) * .95)], 2),
            'event_loop_delay_max_ms': round(max(delays), 2),
            'scope': 'Synthetic local rendering, excludes Discord upload and client playback latency.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'raid-preview')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    random.seed(20261017)
    report, cases = {}, []
    bosses = [boss for group in RAID_BOSSES.values() for boss in group]
    contact = Image.new('RGB', (960, len(bosses) * 626), '#171e32')
    draw = ImageDraw.Draw(contact)
    for i, boss in enumerate(bosses):
        for count in (4, 10):
            raid = make_raid(boss, count)
            scene = capture(raid)
            still = art.render(scene, scene)
            (args.output / f"{boss['id']}-{count}.png").write_bytes(still.data)
            with Image.open(io.BytesIO(still.data)) as image:
                if count == 4:
                    draw.text((6, i * 626 + 5), boss['name'] + ' / 4 trainers (left), 10 at 320 px (right)',
                              font=battle_art.font(16), fill='#edf1ff')
                    contact.paste(image, (0, i * 626 + 30))
                else:
                    contact.paste(image.resize((320, 298), Image.Resampling.NEAREST), (640, i * 626 + 30))
            before, after, events = clash(raid)
            if count == 10:
                cases.append((before, after, events))
            clip = art.render(before, after, events)
            with Image.open(io.BytesIO(clip.data)) as gif:
                record = {'bytes': len(clip.data), 'frames': gif.n_frames,
                          'milliseconds': sum(frame.info['duration'] for frame in ImageSequence.Iterator(gif)),
                          'one_shot': 'loop' not in gif.info}
            report[f"{boss['id']}-{count}"] = record
            if boss['id'] == 'corrupted_dragon':
                (args.output / f'team-clash-{count}.gif').write_bytes(clip.data)
                art.frame(before, after, events, .6).save(args.output / f'team-clash-{count}-impact.png')
    contact.save(args.output / 'realms-mobile.png')
    report = {'size': [art.WIDTH, art.HUD + art.HEIGHT], 'scenes': report,
              'performance': asyncio.run(benchmark(cases))}
    (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
