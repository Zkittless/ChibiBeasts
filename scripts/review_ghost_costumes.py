"""Record an actual visual review of identity-preserving ghost fits."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent / 'output/hollowlight-carnival/ghost-costumes'


def approve(names, notes):
    for name in names:
        asset = ROOT / 'data/cosmetics/ghost_costume_2026' / (name + '.png')
        job_path = WORK / 'jobs' / (name + '.json')
        job = json.loads(job_path.read_text(encoding='utf-8')) if job_path.exists() else {}
        job.update(species=name, status='visually_approved', reviewed_at=datetime.now(timezone.utc).isoformat(),
                   production_sha256=hashlib.sha256(asset.read_bytes()).hexdigest(), review_notes=notes)
        job_path.write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
    records = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((WORK / 'jobs').glob('*.json'))]
    approved = {row['species']: row for row in records if row.get('status') == 'visually_approved'}
    roster = json.loads((ROOT / 'data/beasts.json').read_text(encoding='utf-8'))['beasts']
    result = dict(expected=len(roster), reviewed=len(approved), missing=sorted(set(roster) - set(approved)), reviews=approved)
    (WORK / 'visual-review.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(expected=result['expected'], reviewed=result['reviewed'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('species', nargs='+')
    parser.add_argument('--notes', required=True)
    args = parser.parse_args()
    approve(args.species, args.notes)
