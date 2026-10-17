"""Build the workshop banner and capture actual Discord component payloads."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import db as storage, field_journal as ui
from utils.artwork import card_artwork_path
from utils.crafting import craft_item, workshop_state
from utils.workshop import HEADER, WorkshopView


def build_header():
    with Image.open(ROOT / 'data' / 'battle_realms' / 'woods.png') as source:
        image = ui.backdrop(source, (640, 120))
    ui.panel(image, (5, 5, 635, 115), corners=False)
    # The frame encloses a realm scene, rather than a solid UI panel.
    with Image.open(ROOT / 'data' / 'battle_realms' / 'woods.png') as source:
        scene = ui.backdrop(source, (624, 104))
    image.alpha_composite(scene, (8, 8))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    ui.text(draw, 'THE WHISPERING WOODS', (22, 17, 470, 33), size=11, fill=ui.GOLD)
    ui.text(draw, 'The Workshop', (20, 39, 487, 88), size=34, display=True)
    with Image.open(card_artwork_path('twine')) as source:
        portrait = source.convert('RGBA')
        portrait = portrait.crop(portrait.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox())
        portrait.thumbnail((94, 94), Image.Resampling.NEAREST)
    image.alpha_composite(portrait, (524, 112 - portrait.height))
    HEADER.parent.mkdir(parents=True, exist_ok=True)
    image.convert('RGB').save(HEADER, optimize=True)


async def capture(output):
    with tempfile.TemporaryDirectory() as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Workshop preview')
        await storage.update_player(42, level=12)
        for species, level in (('twine', 18), ('prismite', 16), ('imp', 22), ('goblin', 28), ('kitsune_1', 32)):
            row_id = await storage.add_beast_to_player(42, {**storage.get_beast_data(species), 'caught_from': 'hatch'})
            async with storage.connect() as db:
                await db.execute('UPDATE player_beasts SET level=? WHERE id=?', (level, row_id))
                await db.commit()
        async with storage.connect() as db:
            materials = {'pixie_silk': 4, 'enchanted_bark': 2, 'gnome_iron_ore': 3, 'kelpie_scale': 1,
                         'hellhound_ember': 2, 'void_essence': 2, 'spirit_crystal': 3, 'unicorn_hair': 1, 'harpy_down': 1}
            await db.executemany('INSERT INTO player_materials(user_id,material_id,quantity) VALUES(42,?,?)', materials.items())
            await db.execute("INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) SELECT 42,id,'gnome_iron_plating' FROM player_beasts WHERE player_number=2")
            await db.execute("UPDATE player_beasts SET rune_id='pixie_ribbon' WHERE player_number=2")
            await db.commit()
        view = WorkshopView(42, await workshop_state(42))
        cases = []
        def save(name):
            view.render()
            count = sum(1 for _ in view.walk_children())
            assert count <= 40 and view.content_length() <= 4000
            cases.append({'case': name, 'characters': view.content_length(), 'components': count,
                          'payload': view.to_components()})
        try:
            save('workshop')
            view.drawer = 'materials'
            save('materials')
            view.drawer = 'sources'
            save('sources')
            view.drawer = ''
            await craft_item(42, 'sprite_silk_attire')
            view.state = await workshop_state(42)
            view.notice = 'Crafted 1 **Sprite-Silk Attire**. Ready to equip.'
            save('crafted')
            view.using = True
            view.target_id = view.state.companions[1]['id']
            view.remember_target()
            save('replace-armor')
            async with storage.connect() as db:
                from utils.gameplay import add_stack
                await db.execute('BEGIN IMMEDIATE')
                for mid, amount in (('hellhound_ember', 1), ('sunforge_residue', 5), ('dragon_scale', 1)):
                    await add_stack(db, 'player_materials', 42, mid, amount)
                await db.commit()
            await craft_item(42, 'forge_ember')
            view.state = await workshop_state(42)
            view.selected, view.section, view.using = 'forge_ember', 'evolution', False
            view.notice = 'Crafted 1 **Forge Ember**. Ready to evolve.'
            view.using, view.target_id = True, None
            save('evolution')
        finally:
            view.stop()
        output.mkdir(parents=True, exist_ok=True)
        (output / 'components.json').write_text(json.dumps(cases, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        (output / 'validation.json').write_text(json.dumps({'status': 'passed', 'cases': len(cases),
            'largest_component_count': max(c['components'] for c in cases),
            'largest_text_count': max(c['characters'] for c in cases),
            'scope': 'Production LayoutView payloads and local example inventory; no live Discord login.'}, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': 'passed', 'cases': len(cases), 'header': str(HEADER)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'workshop')
    args = parser.parse_args()
    build_header()
    asyncio.run(capture(args.output))


if __name__ == '__main__':
    main()
