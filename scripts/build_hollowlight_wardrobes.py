"""Package individually generated seasonal armor cutouts and review contacts.

No drawing or armor compositing: each input is a complete built-in image_gen
edit of the canonical beast. Installation is a separate explicit review step.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / 'output/hollowlight-carnival/wardrobes'
SPECIES = ('patchkin', 'wickwing', 'veilmoth', 'hollow_king')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def jobs():
    equipment = read(ROOT / 'data/equipment.json')['equipment']
    beasts = read(ROOT / 'data/beasts.json')['beasts']
    result = []
    fits = {
        'patchkin': 'Fit the short round scarecrow torso and floppy arms. Keep its pumpkin face, green stem, straw hands and root boots fully recognizable.',
        'wickwing': 'Fit a short bat torso garment with wing-root slits. BOTH bat wings, BOTH candle ears with flame tips, cute face and small feet must stay fully visible and unaltered. Do not cover or shorten wings.',
        'veilmoth': 'Fit a small central torso garment with wing-root slits and an unobstructed chest lantern. Preserve BOTH enormous ivory crescent-marked veil wings, both curled antennae, lilac eyes and all tiny feet. Do not replace or shorten wings.',
        'hollow_king': 'Fit the compact pumpkin monarch torso and shoulders. Replace the purple coat fabric with the requested armor while keeping the pumpkin face, complete branch-and-candle crown, straw hands, root boots and carried iron lantern with its three spirits. Do not add another crown or another lantern.',
    }
    for species in SPECIES:
        for armor, item in equipment.items():
            prompt = (
                'Use case: identity-preserve. Asset type: one complete transparent ChibiBeasts armored creature sprite. '
                f'Primary request: edit input 1, {beasts[species]["name"]}, to wear {item["name"]}. '
                'Input image 1 is the edit target: preserve exactly its creature identity, face, anatomy, pose, proportions, silhouette extremities and chunky crisp pixel-art style. '
                'Input image 2 is supporting armor design reference ONLY: match its garment colors, materials and recognizable armor motif; do not copy its hamster body or merge creatures. '
                f'Armor design: {item["description"]} {fits[species]} '
                'Dress this specific anatomy convincingly with curved fitted seams, short straps and appropriate openings; this is a coherent tailored full sprite, not floating pasted rectangles. '
                'One character only, full body and all original extremities visible with generous clear padding. '
                'Keep hard square pixel clusters, dark stepped outline and sparse flat palette; no smooth rendering, no gradients, no background haze, no cast shadow, no platform, no UI, no labels, no text or watermark. '
                'True transparent alpha background. Return ONLY this one new armor outfit, no grid or sheet.'
            )
            result.append({'id': f'{species}--{armor}', 'species': species, 'armor': armor,
                           'references': [str(ROOT / 'data/sprites' / f'{species}.png'),
                                          str(ROOT / 'data/armor/variants' / armor / 'twine.png')],
                           'prompt': prompt})
    write(OUTPUT / 'jobs.json', result)
    return result


def stage(job_id, source):
    job = next(row for row in read(OUTPUT / 'jobs.json') if row['id'] == job_id)
    with Image.open(source) as raw:
        if raw.mode != 'RGBA':
            raise ValueError('Generated source needs its actual transparent alpha channel.')
        image = raw.copy()
    alpha = image.getchannel('A')
    lo, hi = alpha.getextrema()
    if lo != 0 or hi < 240:
        raise ValueError('Source is not transparent with an opaque character.')
    bounds = alpha.point(lambda a: 255 if a > 20 else 0).getbbox()
    if not bounds or bounds[0] <= 1 or bounds[1] <= 1 or bounds[2] >= image.width - 1 or bounds[3] >= image.height - 1:
        raise ValueError('Source is empty or touches a frame edge; regenerate with padding.')
    image = image.crop(bounds)
    image.thumbnail((360, 352), Image.Resampling.NEAREST)
    target = OUTPUT / 'staged' / f'{job_id}.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, optimize=True)
    provenance = {**job, 'source': str(source), 'size': list(image.size), 'crop': list(bounds),
                  'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'reviewed': False}
    write(OUTPUT / 'records' / f'{job_id}.json', provenance)
    return {'id': job_id, 'size': image.size}


def contacts():
    jobs_list = read(OUTPUT / 'jobs.json')
    for species in SPECIES:
        for name, color in (('dark', '#242438'), ('light', '#faf4e8')):
            contact = Image.new('RGB', (1000, 960), color)
            draw = ImageDraw.Draw(contact)
            for index, job in enumerate(row for row in jobs_list if row['species'] == species):
                path = OUTPUT / 'staged' / f'{job["id"]}.png'
                if not path.is_file():
                    continue
                with Image.open(path) as source:
                    cutout = source.convert('RGBA')
                cutout.thumbnail((235, 205), Image.Resampling.NEAREST)
                x, y = index % 4 * 250, index // 4 * 240
                contact.paste(cutout, (x + (250 - cutout.width) // 2, y + 4 + (205 - cutout.height) // 2), cutout)
                draw.text((x + 5, y + 217), job['armor'], fill='white' if name == 'dark' else '#282033')
            contact.save(OUTPUT / f'{species}-{name}.png')


def install():
    manifest_path = ROOT / 'data/armor/manifest.json'
    manifest = read(manifest_path)
    selected = read(OUTPUT / 'jobs.json')
    if len(selected) != 64:
        raise ValueError('All four complete 16-outfit wardrobes are required.')
    records = [read(OUTPUT / 'records' / f'{job["id"]}.json') for job in selected]
    if any(not row['reviewed'] for row in records):
        raise ValueError('Review each light/dark contact before marking its records reviewed.')
    for row in records:
        source = OUTPUT / 'staged' / f'{row["id"]}.png'
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Staged sprite changed after review.')
    for row in records:
        relative = f'variants/{row["armor"]}/{row["species"]}.png'
        target = ROOT / 'data/armor' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OUTPUT / 'staged' / f'{row["id"]}.png', target)
        manifest['sets'][row['armor']]['variants'][row['species']] = relative
        manifest['sha256'][relative] = row['sha256']
    write(manifest_path, manifest)
    write(ROOT / 'docs/hollowlight-armor-prompts.json', {
        'mode': 'built-in image_gen', 'purpose': 'Individually fitted seasonal beast armor',
        'variants': [{**{key: row[key] for key in ('species', 'armor', 'prompt', 'source_sha256', 'sha256')},
                      'references': [f'data/sprites/{row["species"]}.png',
                                     f'data/armor/variants/{row["armor"]}/twine.png']}
                     for row in records]})
    return {'installed': len(records)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('jobs', 'stage', 'contacts', 'install'))
    parser.add_argument('--id')
    parser.add_argument('--source')
    args = parser.parse_args()
    result = {'jobs': jobs, 'stage': lambda: stage(args.id, args.source),
              'contacts': contacts, 'install': install}[args.action]()
    print(json.dumps(result))
