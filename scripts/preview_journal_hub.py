"""Build packaged journal banners and export real Discord pages for offline QA."""
import argparse
import asyncio
import base64
import io
import json
import random
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import aiosqlite
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import db as storage, expeditions, field_journal as ui, journal_state
from utils.artwork import card_artwork_path
from utils.journal import JournalView, REALMS
from utils.workshop import WorkshopView
from utils.crafting import workshop_state


def build_headers():
    for realm, data in REALMS.items():
        with Image.open(ROOT / 'data' / 'battle_realms' / f'{realm}.png') as source:
            image = ui.backdrop(source, (640, 126))
            scene = ui.backdrop(source, (624, 110))
        ui.panel(image, (5, 5, 635, 121), corners=False)
        image.alpha_composite(scene, (8, 8))
        draw = ImageDraw.Draw(image)
        # Realm names in native Discord may contain emoji; bitmap labels use
        # only supported text, just like the existing battle journal.
        name = data['name'].split(' The ', 1)[-1]
        ui.text(draw, name.upper(), (22, 17, 470, 33), size=11, fill=ui.GOLD)
        ui.text(draw, 'Field Journal', (20, 39, 487, 88), size=35, display=True)
        ui.text(draw, 'A small companion. A world to discover.', (23, 94, 495, 111), size=12)
        with Image.open(card_artwork_path('twine')) as source:
            portrait = source.convert('RGBA')
        portrait = portrait.crop(portrait.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox())
        portrait.thumbnail((98, 98), Image.Resampling.NEAREST)
        image.alpha_composite(portrait, (524, 119 - portrait.height))
        target = ROOT / 'data' / 'journal' / f'journal-{realm}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        image.convert('RGB').save(target, optimize=True)


async def capture(output):
    random.seed(17)  # Stable example pages; this script runs in its own process.
    with tempfile.TemporaryDirectory() as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Journal preview')
        await storage.add_beast_to_player(42, storage.get_beast_data('twine'), starter=True)
        await storage.update_player(42, level=6, gold=1250)
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM player_beasts WHERE user_id=42') as cursor:
                companion = dict(await cursor.fetchone())
            await storage.apply_beast_levelup(db, companion, 6, 0)
            await db.execute('INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,?,?)', ('gnome_iron_ore', 2))
            await db.execute('INSERT INTO exploration_energy VALUES(42,3,?)', (time.time(),))
            await db.commit()
        await journal_state.pin_goal(42, 'recipe', 'gnome_iron_plating')
        pages = {}
        view = JournalView(42, await journal_state.snapshot(42))
        try:
            for page in ('home', 'adventure', 'companions', 'goals', 'story', 'activities'):
                view.page = page
                await view.load_page()
                view.render()
                pages[page] = view.to_components()
            trip = await expeditions.begin(42, 'woods')
            view.trip, view.page = trip, 'adventure'
            view.state = await journal_state.snapshot(42)
            view.render()
            pages['expedition'] = view.to_components()
            with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
                view.trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks')
            view.render()
            pages['encounter'] = view.to_components()
            trip = view.trip
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'observe')
            view.trip = trip
            view.render()
            pages['trail_home'] = view.to_components()
            view.trip = await expeditions.choose(42, trip['id'], trip['version'], 'return')
            view.state = await journal_state.snapshot(42)
            view.render()
            pages['return'] = view.to_components()
            for key, route in (('gather', 'gather'), ('landmark', 'shrine')):
                view.trip = await expeditions.begin(42, 'woods')
                view.trip = await expeditions.choose(42, view.trip['id'], view.trip['version'], route)
                view.render()
                pages[key] = view.to_components()
                choice = 'material:' + view.trip['payload']['offered'][0] if route == 'gather' else 'repair'
                view.trip = await expeditions.choose(42, view.trip['id'], view.trip['version'], choice)
                view.render()
                pages[key + '_home'] = view.to_components()
                view.trip = await expeditions.choose(42, view.trip['id'], view.trip['version'], 'return')
                view.state = await journal_state.snapshot(42)
                view.render()
                pages[key + '_return'] = view.to_components()
            workshop = WorkshopView(42, await workshop_state(42), selected='gnome_iron_plating')
            pages['workshop'] = workshop.to_components()
            workshop.stop()
        finally:
            view.stop()
        output.mkdir(parents=True, exist_ok=True)
        (output / 'components.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        images = {}
        candidates = list((ROOT / 'data' / 'journal').glob('*.png')) + [ROOT / 'data' / 'workshop' / 'header.png'] + [card_artwork_path(k) for k in ('twine', 'wisp')]
        for path in candidates:
            with Image.open(path) as source:
                image = source.convert('RGBA')
            image.thumbnail((640, 180) if path.name.startswith(('journal-', 'header')) else (256, 150), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            image.save(buffer, 'PNG', optimize=True)
            images[path.name] = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        template = (ROOT / 'scripts' / 'journal-preview-template.html').read_text(encoding='utf-8')
        fragment = template.replace('__JOURNAL_DATA__', json.dumps(pages, ensure_ascii=False).replace('</', '<\\/')).replace('__ART_DATA__', json.dumps(images))
        (output / 'field-journal.html').write_text(fragment, encoding='utf-8')
        assert len(fragment.encode('utf-8')) < 1_000_000
        print(json.dumps({'status': 'passed', 'pages': list(pages), 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'journal-preview')
    args = parser.parse_args()
    build_headers()
    asyncio.run(capture(args.output))
