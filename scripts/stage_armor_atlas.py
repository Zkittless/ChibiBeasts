"""Validate and package species-specific, generated 4-by-4 armor wardrobes.

    python scripts/stage_armor_atlas.py ../output/armor-refit/jobs.json

Each row-major cell is a complete creature wearing one armor set. This tool
only extracts cells, crops by alpha > 20, and scales down with nearest-neighbor
sampling. It never paints, removes backgrounds, or stretches armor components.
The default writes review candidates under output/armor-refit, outside the game.
Production registration requires both --install-approved and approved: true on
each source job. Approved Gnome-Iron starter sprites remain untouched by default.
"""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE = ROOT.parent / 'output' / 'armor-refit'
PROTECTED_GNOME = frozenset({'twine', 'barkley', 'gloop'})
ALPHA_THRESHOLD = 20
GRID = 4


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def armor_order(root: Path = ROOT) -> list[str]:
    return list(_json(root / 'data' / 'equipment.json')['equipment'])


def _component_areas(mask: Image.Image) -> list[int]:
    """Measure 8-connected silhouettes; isolated sparks are intentionally small."""
    width, height = mask.size
    pixels = bytearray(mask.tobytes())
    areas = []
    for start, value in enumerate(pixels):
        if not value:
            continue
        pixels[start] = 0
        pending = deque([start])
        area = 0
        while pending:
            position = pending.popleft()
            area += 1
            x, y = position % width, position // width
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbour = ny * width + nx
                    if pixels[neighbour]:
                        pixels[neighbour] = 0
                        pending.append(neighbour)
        areas.append(area)
    return sorted(areas, reverse=True)


def extract_atlas(image: Image.Image, order: list[str], *, max_size=(360, 352)):
    """Return validated full-sprite cells and crop metadata, or reject the atlas.

    Automated checks catch empty/clipped cells, solid backgrounds, and materially
    separate second silhouettes. A visual reviewer must still verify identity,
    armor design, and row-major ordering; no pixel validator can prove those.
    Original generated alpha values are retained inside each threshold crop.
    """
    if len(order) != GRID * GRID or len(set(order)) != GRID * GRID:
        raise ValueError('An atlas requires 16 distinct row-major armor IDs')
    if image.mode != 'RGBA':
        raise ValueError('Atlas must contain an actual RGBA transparency channel')
    width, height = image.size
    if width > 8192 or height > 8192 or min(width, height) < 128:
        raise ValueError('Atlas dimensions are outside the supported 128..8192 range')
    if len(max_size) != 2 or min(max_size) < 1:
        raise ValueError('Maximum sprite size must contain two positive dimensions')
    alpha_min, alpha_max = image.getchannel('A').getextrema()
    if alpha_min != 0 or alpha_max < 240:
        raise ValueError('Atlas does not have a clear transparent background and opaque artwork')
    outputs = {}
    diagnostics = {}
    for index, armor_id in enumerate(order):
        column, row = index % GRID, index // GRID
        # Generators can return dimensions such as 1254 px. Proportional integer
        # boundaries give 313/314 px cells without changing a single source pixel.
        cell_bounds = (column * width // GRID, row * height // GRID,
                       (column + 1) * width // GRID, (row + 1) * height // GRID)
        cell = image.crop(cell_bounds)
        cell_width, cell_height = cell.size
        mask = cell.getchannel('A').point(lambda alpha: 255 if alpha > ALPHA_THRESHOLD else 0)
        bounds = mask.getbbox()
        if bounds is None:
            raise ValueError(f'{armor_id}: empty atlas cell {index + 1}')
        left, top, right, bottom = bounds
        if left <= 1 or top <= 1 or right >= cell_width - 1 or bottom >= cell_height - 1:
            raise ValueError(f'{armor_id}: artwork touches an atlas cell edge; possible clipping or overlap')
        opaque = sum(mask.histogram()[1:])
        if opaque < cell_width * cell_height * .01:
            raise ValueError(f'{armor_id}: cell contains too little artwork for a full creature')
        if right - left < cell_width * .15 or bottom - top < cell_height * .15:
            raise ValueError(f'{armor_id}: cell is too small to be a full creature')
        areas = _component_areas(mask)
        # Halos, separate embers, or a floating clockwork plate remain acceptable.
        # Two comparably substantial bodies in one cell indicate a composite.
        if len(areas) > 1 and areas[1] >= opaque * .18:
            raise ValueError(f'{armor_id}: multiple substantial silhouettes in one cell')
        cutout = cell.crop(bounds)
        cutout.thumbnail(tuple(max_size), Image.Resampling.NEAREST)
        outputs[armor_id] = cutout
        diagnostics[armor_id] = {
            'row': row, 'column': column, 'cell_bounds': list(cell_bounds),
            'alpha_crop': list(bounds), 'size': list(cutout.size),
            'alpha_extrema': list(cutout.getchannel('A').getextrema()),
            'opaque_pixels': opaque, 'largest_components': areas[:4],
        }
    return outputs, diagnostics


def coverage(manifest: dict, assets: Path, order: list[str], roster: list[str]) -> dict:
    """Report real packaged dedicated sprites separately from legacy components."""
    rows = {}
    total = 0
    missing = []
    for species in roster:
        dedicated = []
        legacy = []
        for armor_id in order:
            record = manifest.get('sets', {}).get(armor_id, {})
            relative = record.get('variants', {}).get(species)
            if relative and (assets / relative).is_file():
                expected = manifest.get('sha256', {}).get(relative)
                if expected and sha256(assets / relative) != expected:
                    missing.append({'species': species, 'armor': armor_id, 'reason': 'hash_mismatch'})
                else:
                    dedicated.append(armor_id)
                    continue
            if record.get('components'):
                legacy.append(armor_id)
        total += len(dedicated)
        rows[species] = {'dedicated': dedicated, 'legacy_component_only': legacy,
                         'missing_dedicated': [key for key in order if key not in dedicated]}
    return {'expected_pairs': len(roster) * len(order), 'dedicated_pairs': total,
            'complete': total == len(roster) * len(order), 'species': rows,
            'invalid_assets': missing}


def _jobs(document: dict) -> list[dict]:
    jobs = document.get('jobs', {})
    if isinstance(jobs, list):
        return jobs
    if isinstance(jobs, dict):
        return [dict(record, species=record.get('species', species)) for species, record in jobs.items()]
    raise ValueError('jobs must be a species-keyed object or a list')


def stage_jobs(manifest_path: Path, *, stage: Path = DEFAULT_STAGE, root: Path = ROOT,
               max_size=(360, 352), replace_approved_gnome=False) -> dict:
    document = _json(manifest_path)
    order = armor_order(root)
    if document.get('armor_order') != order:
        raise ValueError('Manifest armor_order must exactly match equipment.json insertion order')
    roster = list(_json(root / 'data' / 'beasts.json')['beasts'])
    production_assets = root / 'data' / 'armor'
    packaged = deepcopy(_json(production_assets / 'manifest.json'))
    staged_assets = stage / 'data' / 'armor'
    staged_assets.mkdir(parents=True, exist_ok=True)
    # Keep the three previously approved starter sprites byte-for-byte, including
    # in previews. A new atlas never silently replaces an approved exception.
    for record in packaged['sets'].values():
        for relative in record.get('variants', {}).values():
            source = production_assets / relative
            destination = staged_assets / relative
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    result = {'version': 1, 'preview_only': True, 'armor_order': order,
              'max_sprite_size': list(max_size), 'jobs': {}, 'errors': []}
    seen = set()
    source_hashes = set()
    for job in _jobs(document):
        species = job.get('species')
        if species not in roster or species in seen:
            raise ValueError(f'Unknown or duplicate species job: {species!r}')
        seen.add(species)
        if job.get('status') not in {'generated', 'approved'}:
            result['jobs'][species] = {'status': 'pending', 'source_status': job.get('status')}
            continue
        try:
            if job.get('armor_order', order) != order:
                raise ValueError('Job armor_order differs from catalog insertion order')
            source = Path(job['source'])
            if not source.is_absolute():
                source = manifest_path.parent / source
            source_digest = sha256(source)
            if source_digest in source_hashes:
                raise ValueError('One generated atlas cannot be reused for different species')
            source_hashes.add(source_digest)
            with Image.open(source) as image:
                outputs, diagnostics = extract_atlas(image, order, max_size=max_size)
            record = {'status': 'validated_candidate', 'approved': job.get('approved') is True,
                      'source': str(source.resolve()), 'source_sha256': source_digest,
                      'prompt': job.get('prompt', ''), 'variants': {}, 'cells': diagnostics,
                      'preserved_existing': []}
            for armor_id, cutout in outputs.items():
                relative = f'variants/{armor_id}/{species}.png'
                existing = packaged['sets'][armor_id].get('variants', {}).get(species)
                if armor_id == 'gnome_iron_plating' and species in PROTECTED_GNOME and existing and not replace_approved_gnome:
                    record['preserved_existing'].append(relative)
                    continue
                destination = staged_assets / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                cutout.save(destination, optimize=True)
                packaged['sets'][armor_id].setdefault('variants', {})[species] = relative
                packaged.setdefault('sha256', {})[relative] = sha256(destination)
                record['variants'][armor_id] = relative
            result['jobs'][species] = record
        except (OSError, KeyError, ValueError) as error:
            result['jobs'][species] = {'status': 'rejected', 'reason': str(error)}
            result['errors'].append({'species': species, 'reason': str(error)})
    # This is a review manifest. It contains candidates, not an assertion of
    # approved visual quality or proof that all 1,376 pairs are finished.
    packaged['version'] = max(2, packaged.get('version', 1))
    (staged_assets / 'manifest.json').write_text(json.dumps(packaged, indent=2) + '\n', encoding='utf-8')
    result['coverage'] = coverage(packaged, staged_assets, order, roster)
    (stage / 'atlas-validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def install_approved(result: dict, *, stage: Path = DEFAULT_STAGE, root: Path = ROOT) -> int:
    """Register explicitly approved candidates, keeping all other assets intact."""
    assets = root / 'data' / 'armor'
    packaged = _json(assets / 'manifest.json')
    staged = stage / 'data' / 'armor'
    candidate_manifest = _json(staged / 'manifest.json')
    pending = []
    for species, job in result['jobs'].items():
        if job.get('status') != 'validated_candidate' or job.get('approved') is not True:
            continue
        # Validate all approved source bytes before mutating any production file.
        for armor_id, relative in job['variants'].items():
            source = staged / relative
            if sha256(source) != candidate_manifest['sha256'][relative]:
                raise ValueError(f'Staged asset changed after validation: {relative}')
            pending.append((species, armor_id, relative, source))
    for species, armor_id, relative, source in pending:
        destination = assets / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        packaged['sets'][armor_id].setdefault('variants', {})[species] = relative
        packaged.setdefault('sha256', {})[relative] = sha256(source)
    if pending:
        packaged['version'] = max(2, packaged.get('version', 1))
        (assets / 'manifest.json').write_text(json.dumps(packaged, indent=2) + '\n', encoding='utf-8')
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jobs', type=Path)
    parser.add_argument('--stage', type=Path, default=DEFAULT_STAGE)
    parser.add_argument('--max-size', nargs=2, type=int, default=(360, 352), metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--install-approved', action='store_true')
    parser.add_argument('--replace-approved-gnome', action='store_true', help='Allow reviewed replacement of the three approved Gnome-Iron starter sprites')
    args = parser.parse_args()
    result = stage_jobs(args.jobs, stage=args.stage, max_size=args.max_size,
                        replace_approved_gnome=args.replace_approved_gnome)
    installed = install_approved(result, stage=args.stage) if args.install_approved else 0
    print(json.dumps({'report': str(args.stage / 'atlas-validation.json'),
                      'dedicated_candidates': result['coverage']['dedicated_pairs'],
                      'expected_pairs': result['coverage']['expected_pairs'],
                      'rejected_jobs': len(result['errors']), 'installed_pairs': installed}, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
