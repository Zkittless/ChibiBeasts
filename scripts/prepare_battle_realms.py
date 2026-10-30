"""Package the approved imagegen realm artwork and its prompt provenance.

Usage: python scripts/prepare_battle_realms.py path/to/preview/source-manifest.json
This developer tool does not run on bot startup or require an image API key.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# These persisted destination IDs are also the packaged asset names. Do not
# accept arbitrary paths from a source manifest as output filenames.
REALM_KEYS = frozenset(key + suffix for key in ('woods', 'ember', 'glacial', 'trenches', 'loom')
                       for suffix in ('', '_deep')) | {'hollowlight'}


def prepare(source, *, root=ROOT):
    """Add approved source records without removing previously packaged realms."""
    source = Path(source)
    manifest = json.loads(source.read_text(encoding='utf-8'))
    if not manifest.get('realms') or not set(manifest['realms']) <= REALM_KEYS:
        raise ValueError('Source manifest must contain known destination IDs only.')
    packaged_path = root / 'data' / 'battle-artwork.json'
    packaged = json.loads(packaged_path.read_text(encoding='utf-8')) if packaged_path.exists() else {}
    records = dict(packaged.get('realms', {}))
    if not set(records) <= REALM_KEYS:
        raise ValueError('Packaged manifest contains an unknown destination ID.')
    target = root / 'data' / 'battle_realms'
    outputs = {}
    for key, record in manifest['realms'].items():
        original = source.parent / record['workspace_file']
        with Image.open(original) as image:
            image = image.convert('RGB').resize((480, 270), Image.Resampling.NEAREST)
            image = image.resize((640, 360), Image.Resampling.NEAREST).quantize(colors=128)
            with io.BytesIO() as buffer:
                image.save(buffer, format='PNG', optimize=True)
                outputs[key] = buffer.getvalue()
        records[key] = {'file': f'battle_realms/{key}.png', 'prompt': record['prompt'],
                        'source_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                        'sha256': hashlib.sha256(outputs[key]).hexdigest()}
    # Validate every source before publishing anything, so one bad new image
    # cannot replace the old manifest or leave an earlier replacement unhashed.
    target.mkdir(parents=True, exist_ok=True)
    for key, data in outputs.items():
        (target / f'{key}.png').write_bytes(data)
    packaged_path.write_text(json.dumps({
        'mode': manifest.get('mode', packaged.get('mode', 'built-in image_gen')),
        'purpose': 'Approved ChibiBeasts battle realm backgrounds',
        'dimensions': [640, 360], 'realms': records,
    }, indent=2) + '\n', encoding='utf-8')
    return {key: len(data) for key, data in outputs.items()}


def main():
    print(json.dumps(prepare(Path(sys.argv[1]))))


if __name__ == '__main__':
    main()
