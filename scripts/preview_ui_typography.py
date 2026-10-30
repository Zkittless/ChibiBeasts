"""Build a compact review of the exact packaged and runtime image lettering."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import field_journal as ui


SOURCES = {
    'Journal banner': 'data/journal/journal-hollowlight.png',
    'Workshop banner': 'data/workshop/header.png',
    'Battle HUD and combat log': 'docs/hollowlight-preview/ghost-strike-final.png',
    'Exploration team roles': 'docs/hollowlight-preview/ghost-team-carnival.png',
    'Starter selection labels': 'data/card_art/starter-lineup.png',
}


def raid_examples(output):
    from cogs.battle import build_pve_beast_state
    from cogs.guilds import RAID_BOSSES
    from scripts.preview_raid_arena import capture, make_raid
    from utils import db as storage, raid_art

    boss = next(row for row in RAID_BOSSES['corrupted'] if row['id'] == 'corrupted_leviathan')
    records, images = [], []
    original_text = ui.text
    for label in ('whole', 'fractional'):
        raid = make_raid(boss, 10)
        if label == 'fractional':
            # Five level20 and five level21 parties produce the real level20.5
            # raid budget, rather than patching a label onto an old image.
            for uid in sorted(raid.users)[5:]:
                raid.party[uid] = [build_pve_beast_state(storage.get_beast_data(state['beast_id']), 21)
                                   for state in raid.party[uid]]
            raid.build_enemy()
            raid.enemy['hp'] = round(raid.enemy['max_hp'] * .762)
            raid.clock = lambda: 0
        scene = capture(raid)
        painted = []

        def record_text(draw, value, box, **kwargs):
            fitted = original_text(draw, value, box, **kwargs)
            painted.append(dict(requested=str(value), value=fitted.value,
                                size=fitted.size, ink=list(fitted.ink), box=list(box)))
            return fitted

        with patch.object(ui, 'text', side_effect=record_text):
            image = raid_art.frame(scene, scene)
        metadata = next(row for row in painted if row['requested'].startswith('Lv.'))
        name = next(row for row in painted if row['requested'] == scene.boss.name)
        for text in (metadata, name):
            assert text['value'] == text['requested'], text
            assert all(text['ink'][i] >= text['box'][i] for i in (0, 1)), text
            assert all(text['ink'][i] <= text['box'][i] for i in (2, 3)), text
        filename = f'raid-10-{label}.png'
        image.save(output / filename, optimize=True)
        images.append(image.convert('RGBA'))
        records.append(dict(file=filename, trainers=len(scene.members), level=scene.boss.level,
                            timer=f'{scene.remaining // 60}:{scene.remaining % 60:02}',
                            boss_name=name, metadata=metadata,
                            sha256=hashlib.sha256((output / filename).read_bytes()).hexdigest()))
    return records, images


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    raids, raid_images = raid_examples(output)
    contact = Image.new('RGB', (1280, 1456), '#15232a')
    draw = ImageDraw.Draw(contact)
    for label, box in (
            ('JOURNAL BANNER', (12, 0, 628, 24)),
            ('WORKSHOP BANNER', (652, 0, 1268, 24)),
            ('BATTLE HUD AND COMBAT LOG', (12, 158, 628, 182)),
            ('EXPLORATION TEAM ROLES', (652, 158, 1268, 182)),
            ('STARTER SELECTION LABELS', (652, 500, 1268, 524)),
            ('10-TRAINER RAID / WHOLE LEVEL', (12, 728, 628, 752)),
            ('10-TRAINER RAID / FRACTIONAL LEVEL', (652, 728, 1268, 752))):
        ui.text(draw, label, box, size=18, display=True)
    records = []
    images = []
    sources = [(name, ROOT / relative) for name, relative in SOURCES.items()]
    sources.append(('Ten-trainer raid', output / raids[0]['file']))
    for name, path in sources:
        with Image.open(path) as source:
            images.append(source.convert('RGBA'))
            records.append(dict(surface=name, file=Path(os.path.relpath(path, ROOT)).as_posix(), size=list(source.size),
                                bytes=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    for image, xy in zip(images[:4], ((0, 28), (640, 28), (0, 188), (640, 188))):
        contact.paste(image, xy, image)
    # Keep the real starter lettering at 1:1 pixels in the compact sheet.
    # The full unmodified selection artwork is linked from the HTML review.
    for box, xy in (((64, 240, 704, 314), (640, 530)),
                    ((64, 560, 704, 634), (640, 612))):
        strip = images[4].crop(box)
        contact.paste(strip, xy, strip)
    for column, image in enumerate(raid_images):
        contact.paste(image, (column * 640, 756), image)
    contact.save(output / 'surfaces.png', optimize=True)
    links = ''.join('<li><a href="' + Path(os.path.relpath(path, output)).as_posix() + '">' + name + '</a></li>'
                    for name, path in sources)
    links += '<li><a href="raid-10-fractional.png">Ten-trainer raid · fractional level</a></li>'
    document = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts image typography</title><style>
:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;color:#f4ecdc;background:#171522}*{box-sizing:border-box}body{margin:0}main{max-width:1328px;margin:auto;padding:24px}h1{font-size:clamp(26px,5vw,40px);line-height:1.15}p{max-width:780px;color:#c9c0d0}a{color:#ffe0a2}img{display:block;max-width:100%;height:auto;image-rendering:pixelated;border:1px solid #66566c}li{margin:6px 0}nav{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}@media(max-width:420px){main{padding:12px}}
</style></head><body><main><h1>ChibiBeasts image typography</h1><p>Pixelify Sans lettering across six production image surfaces: warm manilla headings, white values and body text, and a dark pixel outline. The sheet keeps every text sample at its native pixels. Starter artwork is cropped to its label rows here; the complete image is linked below.</p><p>The ten-trainer raids include a whole level and the actual fractional budget from five level20 and five level21 parties. Both show the complete boss name, trainer count and timer.</p><nav><a href="../hollowlight-preview/journal/hollowlight.html">Journal preview</a><a href="../hollowlight-preview/index.html">Costumes and battle</a><a href="../hollowlight-preview/moves/index.html">Eight Carnival moves</a></nav><img src="surfaces.png" width="1280" height="1456" alt="Actual Journal and Workshop banners, battle HUD and log, team roles, starter labels and ten-trainer raids using the approved rounded pixel type"><p>Open a source image for its full-size view:</p><ul>''' + links + '</ul><p>Native Discord text and browser interface fonts retain their existing styling.</p></main></body></html>'
    (output / 'index.html').write_text(document, encoding='utf-8')
    report = dict(status='passed', font='Pixelify Sans', image_surfaces=len(records),
                  scope='Exact production artwork; starter label crops retain native pixels.',
                  sources=records, ten_trainer_raids=raids)
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status='passed', image_surfaces=len(records),
                          contact_bytes=(output / 'surfaces.png').stat().st_size)))
    return output / 'index.html'


async def capture(path):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        for width in (1328, 390, 320):
            await page.set_viewport_size(dict(width=width, height=1000))
            await page.wait_for_function('Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            checks.append(dict(width=width, no_overflow=True, all_art_loaded=True))
        await browser.close()
    assert not errors, errors
    (path.parent / 'browser-validation.json').write_text(json.dumps(dict(
        status='passed', javascript_errors=errors, checks=checks), indent=2) + '\n', encoding='utf-8')
    print(f'Typography review passed {len(checks)} viewport checks.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/typography-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path = build(args.output)
    if args.capture:
        asyncio.run(capture(path))
