"""Refresh the eight Carnival move previews with the production renderer."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.preview_move_animations import case, gif_record
from utils import battle_art as art, field_journal as ui, move_animations as fx


EXAMPLES = (
    ('patchkin', 'Straw Snare', 0), ('patchkin', 'Harvest Brace', 0),
    ('wickwing', 'Ember Flutter', 0), ('wickwing', 'Candleburst', 0),
    ('veilmoth', 'Funeral Glimmer', 0), ('veilmoth', 'Moonveil Mend', 0),
    ('hollow_king', 'Lantern Decree', 1), ('hollow_king', 'Last Procession', 1),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/hollowlight-preview/moves')
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    records = []
    row_height = art.TOTAL_HEIGHT + 24
    sheets = [Image.new('RGB', (art.WIDTH * 3, row_height * 4), '#242438') for _ in range(2)]
    midflight = Image.new('RGB', (art.WIDTH * 2, art.TOTAL_HEIGHT * 2), '#242438')
    motif_indices = (2, 3, 4, 7)
    for index, (species, move, side) in enumerate(EXAMPLES):
        before, after, action = case(species, move, side=side)
        clip = art.render(before, after, 'hollowlight', action)
        record = gif_record(clip)
        assert record['one_shot'] and record['frames'] > 1 and record['bytes'] < art.MAX_BYTES
        assert record['milliseconds'] == action.milliseconds
        filename = species + '--' + move.lower().replace(' ', '-') + '.gif'
        (output / filename).write_bytes(clip.data)
        sheet = sheets[index // 4]
        row_y = index % 4 * row_height
        for column, t in enumerate((.22, .5, .78)):
            frame = art.frame(before, after, 'hollowlight', action, t)
            ui.text(ImageDraw.Draw(sheet), f'{species} / {move} / {t:.2f}',
                    (column * art.WIDTH + 8, row_y + 2,
                     (column + 1) * art.WIDTH - 8, row_y + 22), size=14)
            sheet.paste(frame, (column * art.WIDTH, row_y + 24))
        if index in motif_indices:
            position = motif_indices.index(index)
            midflight.paste(art.frame(before, after, 'hollowlight', action, .5),
                            (position % 2 * art.WIDTH, position // 2 * art.TOTAL_HEIGHT))
        records.append(dict(species=species, move=move, side=side, filename=filename,
                            profile=asdict(fx.profile(species, move)), **record))
    for index, sheet in enumerate(sheets, 1):
        sheet.save(output / f'moves-contact-{index}.png', optimize=True)
    midflight.save(output / 'midflight-motifs.png', optimize=True)
    (output / 'validation.json').write_text(json.dumps(dict(status='passed',
        scope='Production combat.resolve_move, snapshots, Carnival battlefield renderer and GIF encoder; illustrative level8 actors and level10 Behemoth, not live Discord playback.',
        records=records), indent=2) + '\n', encoding='utf-8')
    cards = ''.join(f'<article><h2>{row["species"]}: {row["move"]}</h2><img src="{row["filename"]}" alt="{row["move"]}"><p>{row["milliseconds"]}ms · {row["frames"]} frames · plays once</p></article>' for row in records)
    (output / 'index.html').write_text('<!doctype html><html><meta charset="utf-8"><title>Hollowlight move previews</title><style>body{background:#242438;color:#faf4e8;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px}img{max-width:100%}h2{font-size:20px}</style><h1>Production Carnival move previews</h1><main>' + cards + '</main></html>', encoding='utf-8')
    print(json.dumps(dict(output=str(output), clips=len(records),
                          milliseconds=[row['milliseconds'] for row in records],
                          largest_bytes=max(row['bytes'] for row in records))))


if __name__ == '__main__':
    main()
