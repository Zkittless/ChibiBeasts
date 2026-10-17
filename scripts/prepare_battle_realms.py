"""Package the approved imagegen realm artwork and its prompt provenance.

Usage: python scripts/prepare_battle_realms.py path/to/preview/source-manifest.json
This developer tool does not run on bot startup or require an image API key.
"""
import hashlib
import json
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = Path(sys.argv[1])
    manifest = json.loads(source.read_text(encoding='utf-8'))
    target = ROOT / 'data' / 'battle_realms'
    target.mkdir(exist_ok=True)
    records = {}
    for key, record in manifest['realms'].items():
        original = source.parent / record['workspace_file']
        with Image.open(original) as image:
            image = image.convert('RGB').resize((480, 270), Image.Resampling.NEAREST)
            image = image.resize((640, 360), Image.Resampling.NEAREST).quantize(colors=128)
            image.save(target / f'{key}.png', optimize=True)
        records[key] = {'file': f'battle_realms/{key}.png', 'prompt': record['prompt'],
                        'source_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                        'sha256': hashlib.sha256((target / f'{key}.png').read_bytes()).hexdigest()}
    (ROOT / 'data' / 'battle-artwork.json').write_text(json.dumps({
        'mode': manifest['mode'], 'purpose': 'Approved ChibiBeasts battle realm backgrounds',
        'dimensions': [640, 360], 'realms': records,
    }, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: (target / f'{key}.png').stat().st_size for key in records}))


if __name__ == '__main__':
    main()
