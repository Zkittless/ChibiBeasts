"""Render production Field Journal HUD spacing, including extreme totals."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import battle_art as art, db as storage, field_journal as ui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'field-journal')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    states = [build_pve_beast_state(storage.get_beast_data(key), level) for key, level in (('twine', 6), ('wisp', 7))]
    states[0].update(hp=87, mana=55, ultimate_charges=3)
    states[1].update(mana=47, ultimate_charges=1)
    saved = art.snapshot(states)
    image = art.frame(saved, saved, 'woods')
    image.save(args.output / 'duel.png')
    image.resize((320, art.TOTAL_HEIGHT // 2), Image.Resampling.NEAREST).save(args.output / 'duel-320.png')
    sheet = Image.new('RGB', (640, art.HUD * 3))
    cases = [saved,
             (replace(saved[0], hp=1234567, max_hp=9876543, mana=9876, max_mana=12345),
              replace(saved[1], hp=9999999, max_hp=9999999, mana=1, max_mana=1, charges=2)),
             tuple(replace(fighter, hp=0, mana=0, charges=0) for fighter in saved)]
    report = []
    for row, pair in enumerate(cases):
        scene = art.frame(pair, pair, 'woods')
        sheet.paste(scene.crop((0, 0, 640, art.HUD)), (0, row * art.HUD))
        for i, fighter in enumerate(pair):
            canvas = Image.new('RGBA', (640, art.HUD))
            health, mana, ultimate = ui.fighter_panel(canvas, fighter, art.sprite(fighter.species), 10 + i * 320, fighter.hp, fighter.mana, fighter)
            for kind, meter in (('HP', health), ('MP', mana)):
                content, ink = meter['content'], meter['text'].ink
                assert content[0] <= ink[0] <= ink[2] <= content[2]
                assert content[1] <= ink[1] <= ink[3] <= content[3]
                report.append({'case': row, 'fighter': i, 'kind': kind, 'value': meter['text'].value,
                               'font_size': meter['text'].size, 'content': content, 'ink': ink})
            gap = ultimate['orbs'][0][0] - ultimate['label_text'].ink[2]
            assert gap >= 8
    sheet.save(args.output / 'number-spacing.png')
    (args.output / 'spacing-validation.json').write_text(json.dumps({
        'status': 'passed', 'numeric_ink': report, 'minimum_ult_label_gap_px': gap,
        'scope': 'Actual production renderer at 640 px; mobile still at 320 px. No Discord login.'
    }, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'passed', 'numeric_cases': len(report), 'ult_gap_px': gap}))


if __name__ == '__main__':
    main()
