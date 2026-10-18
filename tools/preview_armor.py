"""Export fitted armor contact sheets and real battle clips without a bot login.

Run from any directory:
    python tools/preview_armor.py --output ../output/armor-implementation

This utility only reads packaged artwork/catalogs and writes review artifacts.
It does not generate artwork, query player data, or contact Discord.
"""
import argparse
from collections import Counter
import hashlib
import html
import io
import json
from pathlib import Path
import random
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def catalog(name):
    return json.loads((ROOT / 'data' / name).read_text(encoding='utf-8'))


def digest(image):
    return hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()


def visible_bounds(image):
    return image.getchannel('A').point(lambda value: 255 if value > 20 else 0).getbbox()


def font(size):
    return ImageFont.load_default(size=size)


def label(draw, xy, value, *, size=13, width=182, fill='#f0ead8'):
    face = font(size)
    while value and draw.textlength(value, font=face) > width:
        value = value[:-2] + '~' if len(value) > 1 else ''
    draw.text(xy, value, font=face, fill=fill)


def tile(image, title, subtitle, *, size=(192, 218)):
    """Only scale/composite existing artwork; never alter the source image."""
    canvas = Image.new('RGB', size, '#203036')
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((1, 1, size[0] - 2, size[1] - 2), outline='#58605a')
    bounds = visible_bounds(image)
    if bounds:
        fitted = image.crop(bounds)
        fitted.thumbnail((size[0] - 14, size[1] - 54), Image.Resampling.NEAREST)
        canvas.paste(fitted, ((size[0] - fitted.width) // 2,
                              8 + (size[1] - 54 - fitted.height) // 2), fitted)
    label(draw, (7, size[1] - 41), title, size=13, width=size[0] - 14)
    label(draw, (7, size[1] - 22), subtitle, size=11, width=size[0] - 14, fill='#c6c5b3')
    return canvas


def sheet(cells, *, columns=10, cell_size=(192, 218), title=''):
    rows = (len(cells) + columns - 1) // columns
    canvas = Image.new('RGB', (columns * cell_size[0], rows * cell_size[1] + 45), '#14252a')
    label(ImageDraw.Draw(canvas), (12, 10), title, size=20, width=canvas.width - 24)
    for index, cell in enumerate(cells):
        canvas.paste(cell, (index % columns * cell_size[0], 45 + index // columns * cell_size[1]))
    return canvas


def states_with_armor(species, armor_id, level, armor):
    from utils import combat, db as storage
    data = storage.get_beast_data(species)
    growth = storage.calc_stat_growth({'rarity': data['rarity']}, max(0, level - 1))
    stats = {key: value + growth[key] for key, value in data['base_stats'].items()}
    row = {**stats, 'max_hp': stats['hp'], 'max_mana': stats['mana'],
           'level': level, 'happiness': 70}
    result = combat.build_state(data, row, gear=[armor[armor_id]], buffs={'ultimate_charges': 3})
    result['mana'] = result['max_mana']
    return result


def clip_record(artwork, path):
    path.write_bytes(artwork.data)
    with Image.open(io.BytesIO(artwork.data)) as image:
        return {
            'file': path.name, 'bytes': len(artwork.data), 'size': list(image.size),
            'frames': getattr(image, 'n_frames', 1),
            'milliseconds': sum(frame.info.get('duration', 0) for frame in ImageSequence.Iterator(image)),
            'one_shot': 'loop' not in image.info,
        }


def demos(output, armor):
    from utils import battle_art as art, combat, raid_art
    from cogs.guilds import RAID_BOSSES
    from scripts.preview_raid_arena import capture, make_raid
    records = []
    cases = (
        ('gnome-duel', 'twine', 'gnome_iron_plating', 'barkley', 'mossbark_wrapping', 'Borrowed Moment', 'woods', False),
        ('scale-duel', 'gloop', 'kelpie_scale_mail', 'wyvern', 'cinder_guard_vest', 'Galaxy Splash', 'trenches', False),
        ('equipped-retreat', 'twine', 'chronos_paradox_plate', 'barkley', 'dragon_lord_aegis', 'Tick Bite', 'loom', True),
    )
    for slug, left, left_armor, right, right_armor, move, realm, finish in cases:
        pair = [states_with_armor(left, left_armor, 8, armor),
                states_with_armor(right, right_armor, 10, armor)]
        if finish:
            pair[1]['hp'] = 1
        before = art.snapshot(pair)
        log = []
        random.seed(917)
        ultimate = move == pair[0]['ultimate']
        damage = combat.resolve_move(*pair, move, ultimate, log)
        after = art.snapshot(pair)
        action = art.Action(0, move, 'attack', damage, ultimate, tuple(log[-2:]))
        art.frame(before, before, realm).save(output / f'{slug}-ready.png')
        art.frame(before, after, realm, action, .58).save(output / f'{slug}-impact.png')
        art.frame(before, after, realm, action, 1).save(output / f'{slug}-final.png')
        record = clip_record(art.render(before, after, realm, action), output / f'{slug}.gif')
        record.update(kind='duel', move=move, damage=damage, realm=realm,
                      fighters=[{'species': left, 'armor': left_armor}, {'species': right, 'armor': right_armor}])
        records.append(record)
    session = make_raid(RAID_BOSSES['corrupted'][2], 10)
    armor_ids = list(armor)
    for index, uid in enumerate(session.users):
        for slot, previous in enumerate(session.party[uid]):
            state = states_with_armor(previous['beast_id'], armor_ids[(index + slot) % len(armor_ids)], 20, armor)
            state['hp'] = round(state['max_hp'] * .85)
            session.party[uid][slot] = state
    before = capture(session)
    random.seed(919)
    for index, uid in enumerate(session.users):
        session.resolve_action(uid, ('guard', 'ultimate', 'attack', 'skill')[index % 4])
    after = capture(session)
    events = tuple(session._art_events)
    raid_art.frame(before, after, events, .58).save(output / 'equipped-raid-impact.png')
    record = clip_record(raid_art.render(before, after, events), output / 'equipped-raid.gif')
    record.update(kind='raid', trainers=10, events=len(events))
    records.append(record)
    return records


def write_index(output, armor, records, failures, coverage):
    links = ''.join(f'<a href="#armor-{key}">{html.escape(item["name"])}</a>' for key, item in armor.items())
    sections = ''.join(
        f'<section id="armor-{key}"><h2>{html.escape(item["name"])}</h2>'
        f'<p>{html.escape(item["description"])}</p><a href="{key}.png">'
        f'<img loading="lazy" src="{key}.png" alt="Registered {html.escape(item["name"])} variants and labeled canonical fallbacks"></a></section>'
        for key, item in armor.items())
    clips = ''.join(
        f'<article><h3>{html.escape(record["file"].removesuffix(".gif").replace("-", " ").title())}</h3>'
        f'<a href="{record["file"]}"><img src="{record["file"]}" alt="Production rendered armor battle animation"></a>'
        f'<p>{record["frames"]} frames · {record["milliseconds"] / 1000:g}s · {record["bytes"]:,} bytes · plays once</p></article>'
        for record in records)
    status = (f'{coverage["dedicated_pairs"]:,} of {coverage["expected_pairs"]:,} combinations have dedicated full sprites; '
              f'{coverage["missing_pairs"]:,} show labeled canonical fallbacks.')
    if failures:
        status += f' {len(failures)} validation issue(s); see validation.json.'
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChibiBeasts fitted armor review</title><style>
:root{{color-scheme:dark light}}*{{box-sizing:border-box}}body{{margin:0;background:#14252a;color:#eee9d8;font:16px/1.5 system-ui,sans-serif}}
main{{max-width:1500px;margin:auto;padding:24px}}h1,h2,h3{{line-height:1.2}}p{{max-width:78ch}}nav{{display:flex;flex-wrap:wrap;gap:8px}}
a{{color:#c8e5c8}}nav a{{padding:7px 10px;border:1px solid #687363;border-radius:6px;text-decoration:none}}img{{max-width:100%;height:auto;display:block;image-rendering:pixelated;background:#203036}}
section{{margin-top:38px;scroll-margin:12px}}.clips{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:24px}}article{{min-width:0}}
@media(prefers-color-scheme:light){{body{{background:#eeeada;color:#203036}}a{{color:#2c6845}}nav a{{border-color:#81937f}}}}</style>
<main><h1>Armor appearance review</h1><p>{status} This offline review uses registered species-specific full sprites and the production battle renderer. Missing pairs retain canonical artwork. No player data or Discord messages are changed.</p>
<p><a href="validation.json">Validation and rendering measurements</a> · <a href="body-diversity.png">Body diversity overview</a></p>
<h2>Battle examples</h2><div class="clips">{clips}</div><h2>All armor and creatures</h2><nav>{links}</nav>{sections}</main></html>'''
    (output / 'index.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'output' / 'armor-implementation')
    parser.add_argument('--skip-demos', action='store_true', help='Export registered variants and labeled canonical fallbacks without battle clips.')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from utils import armor_art
    armor = catalog('equipment.json')['equipment']
    beasts = catalog('beasts.json')['beasts']
    profiles = catalog('armor_fits.json')['profiles']
    coverage = armor_art.coverage()
    dedicated = set(coverage['dedicated'])
    failures, records, sheets, examples = [], [], [], []
    start = time.perf_counter()
    body_samples = ('goblin', 'barkley', 'phoenix', 'leviathan', 'gloop', 'wisp', 'atlas', 'kraken', 'nirvana', 'loom_fragment')
    base_hashes = {species: digest(armor_art.portrait(species, '')) for species in beasts}
    seen = {species: {} for species in beasts}
    for armor_id, item in armor.items():
        cells = []
        for species, beast in beasts.items():
            is_dedicated = (species, armor_id) in dedicated
            source = armor_art.portrait(species, armor_id)
            if source is None:
                failures.append(f'{species}/{armor_id}: missing portrait')
                source = Image.new('RGBA', (180, 176))
            image = source.convert('RGBA')
            bounds = visible_bounds(image)
            fingerprint = digest(image)
            if not bounds:
                failures.append(f'{species}/{armor_id}: empty portrait')
            if image.getchannel('A').getextrema()[0] != 0:
                failures.append(f'{species}/{armor_id}: no transparent exterior')
            if is_dedicated and fingerprint == base_hashes[species]:
                failures.append(f'{species}/{armor_id}: identical to unequipped portrait')
            if is_dedicated and fingerprint in seen[species]:
                failures.append(f'{species}/{armor_id}: identical to {seen[species][fingerprint]}')
            if is_dedicated:
                seen[species][fingerprint] = armor_id
            records.append({'species': species, 'armor': armor_id, 'body_class': profiles[species]['body_class'],
                            'size': list(image.size), 'visible_bounds': list(bounds) if bounds else None,
                            'sha256': fingerprint,
                            'appearance': 'dedicated' if is_dedicated else 'canonical_fallback'})
            cell = tile(image, beast['name'], species if is_dedicated else f'{species} / BASE ART')
            cells.append(cell)
            if species in body_samples:
                examples.append((armor_id, species, tile(image, beast['name'], item['name'] if is_dedicated else 'BASE ART / unavailable')))
        target = output / f'{armor_id}.png'
        sheet(cells, title=f'{item["name"]} / all {len(beasts)} creatures').save(target)
        sheets.append(target.name)
        actual = sum((species, armor_id) in dedicated for species in beasts)
        print(f'{armor_id}: {actual} dedicated / {len(cells) - actual} canonical fallbacks', flush=True)
    order = {species: index for index, species in enumerate(body_samples)}
    armor_order = {key: index for index, key in enumerate(armor)}
    examples.sort(key=lambda value: (armor_order[value[0]], order[value[1]]))
    sheet([cell for _, _, cell in examples], title='Registered armor availability across 10 body classes; missing pairs show base art').save(output / 'body-diversity.png')
    clips = [] if args.skip_demos else demos(output, armor)
    failures.extend(f'{record["file"]}: animation exceeds production upload limit' for record in clips if record['bytes'] >= 2_000_000)
    report = {
        'passed': not failures, 'armor_sets': len(armor), 'species': len(beasts),
        'dedicated_combinations': coverage['dedicated_pairs'],
        'canonical_fallbacks': coverage['missing_pairs'], 'coverage_complete': coverage['complete'],
        'combinations': len(records), 'body_classes': dict(Counter(profile['body_class'] for profile in profiles.values())),
        'seconds': round(time.perf_counter() - start, 2), 'contact_sheets': sheets,
        'clips': clips, 'failures': failures, 'portraits': records,
        'scope': 'Offline packaged image composition and production battle rendering; excludes Discord transfer/client playback.',
    }
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    write_index(output, armor, clips, failures, coverage)
    summary = {key: value for key, value in report.items() if key not in {'portraits', 'failures'}}
    summary.update(failure_count=len(failures), failures=failures[:20])
    print(json.dumps(summary, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
