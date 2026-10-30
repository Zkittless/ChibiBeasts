"""Package AI-illustrated full-sprite ghost fits; never draw substitute costumes."""
from pathlib import Path
import argparse
import hashlib
import json

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent / 'output/hollowlight-carnival/ghost-costumes'
ASSETS = ROOT / 'data/cosmetics'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cutout(path):
    with Image.open(path) as source:
        image = source.convert('RGBA')
    low, high = image.getchannel('A').getextrema()
    if low != 0 or high < 240:
        raise ValueError(f'{path.name} is not a true transparent cutout')
    box = image.getchannel('A').point(lambda alpha: 255 if alpha > 20 else 0).getbbox()
    if not box:
        raise ValueError(f'{path.name} has no visible sprite')
    return image.crop(box)


def build(species=None):
    roster = json.loads((ROOT / 'data/beasts.json').read_text(encoding='utf-8'))['beasts']
    cards = json.loads((ROOT / 'data/card-artwork.json').read_text(encoding='utf-8'))['beasts']
    previous = json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8')) if (ASSETS / 'manifest.json').exists() else {}
    record = dict(name='Little Ghost Costume', unlock_id='ghost_costume_2026', stats={}, variants={})
    hashes, source_hashes, provenance, reviews = {}, {}, {}, {}
    target = ASSETS / 'ghost_costume_2026'
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted((WORK / 'sources').glob('*.png')):
        if source.stem not in roster:
            continue
        destination = target / source.name
        relative = destination.relative_to(ASSETS).as_posix()
        source_hashes[source.stem] = digest(source)
        unchanged = (destination.exists() and previous.get('source_sha256', {}).get(source.stem) == source_hashes[source.stem]
                     and previous.get('sha256', {}).get(relative) == digest(destination))
        if not unchanged:
            image = cutout(source)
            image.thumbnail((360, 352), Image.Resampling.NEAREST)
            image.save(destination, 'PNG', optimize=True)
        record['variants'][source.stem] = relative
        hashes[relative] = digest(destination)
        job_file = WORK / 'jobs' / (source.stem + '.json')
        job = json.loads(job_file.read_text(encoding='utf-8')) if job_file.exists() else {}
        if job.get('status') == 'visually_approved':
            job.update(production_sha256=hashes[relative], source_sha256=source_hashes[source.stem])
            job_file.write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
            reviews[source.stem] = job
            canonical = ROOT / 'data' / cards[source.stem]['file']
            early = {'twine', 'barkley', 'hydra', 'leviathan', 'prismite', 'gloop', 'fairy', 'pixie', 'goblin', 'imp'}
            reference = Path(job['reference']) if job.get('reference') else canonical if source.stem in early else WORK / 'references' / source.name
            provenance[source.stem] = dict(method='built-in image_gen__imagegen identity-preserve edit',
                source_artifact=Path(job.get('original_generated_path') or job.get('source', '')).name,
                source_sha256=source_hashes[source.stem], canonical_file=cards[source.stem]['file'],
                canonical_sha256=digest(canonical), edit_reference_sha256=digest(reference),
                prompt=(WORK / 'prompts' / (source.stem + '.txt')).read_text(encoding='utf-8').strip(),
                review_notes=job['review_notes'], reviewer=job.get('reviewer', 'costume art agent'),
                reviewed_at=job['reviewed_at'], production_sha256=hashes[relative])
    manifest = dict(version=1, sets={'ghost_costume_2026': record}, sha256=hashes, source_sha256=source_hashes)
    (ASSETS / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (ASSETS / 'provenance.json').write_text(json.dumps(dict(version=1, assets=provenance), indent=2) + '\n', encoding='utf-8')
    review_status = dict(expected=len(roster), reviewed=len(reviews), missing=sorted(set(roster) - set(reviews)), reviews=reviews)
    (WORK / 'visual-review.json').write_text(json.dumps(review_status, indent=2) + '\n', encoding='utf-8')
    present = set(record['variants'])
    status = dict(expected=len(roster), illustrated=len(present), missing=sorted(set(roster)-present),
                  complete=set(roster) == present, source_method='built-in imagegen, one edit per species',
                  note='Illustrated count is file/alpha coverage; visual review is recorded separately.')
    (WORK / 'coverage.json').write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
    if species:
        names = [name for name in species if name in present]
        contact = Image.new('RGBA', (720, len(names) * 250), '#263035')
        draw = ImageDraw.Draw(contact)
        for index, name in enumerate(names):
            draw.text((12, index * 250 + 6), name + ' / canonical', fill='#ffffff')
            draw.text((372, index * 250 + 6), name + ' / fitted ghost', fill='#ffffff')
            for column, path in enumerate((ROOT / 'data' / cards[name]['file'], target / (name + '.png'))):
                image = cutout(path)
                image.thumbnail((330, 220), Image.Resampling.NEAREST)
                contact.alpha_composite(image, (column*360 + (360-image.width)//2,
                                                 index*250 + 25 + (220-image.height)//2))
        contacts = WORK / 'contacts'
        contacts.mkdir(parents=True, exist_ok=True)
        contact.convert('RGB').save(contacts / ('-'.join(names) + '.png'))
    print(json.dumps(status))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contact', nargs='*')
    build(parser.parse_args().contact)
