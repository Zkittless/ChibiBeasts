"""Register only independently reviewed collaborator candidates, serially."""
import json
import shutil
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent / 'output/hollowlight-carnival/ghost-costumes'


def register():
    imported = []
    for record in sorted((WORK / 'collaborator/jobs').glob('*.json')):
        job = json.loads(record.read_text(encoding='utf-8'))
        if job.get('status') != 'visually_approved':
            continue
        name = job['species']
        source = WORK / 'collaborator/sources' / (name + '.png')
        target = WORK / 'sources' / source.name
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != job['source_sha256']:
            raise ValueError(f'{name}: approved source changed')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != actual:
                raise ValueError(f'{name}: a different source is already registered')
            continue
        shutil.copy2(source, target)
        shutil.copy2(WORK / 'collaborator/prompts' / (name + '.txt'), WORK / 'prompts' / (name + '.txt'))
        job['reviewer'] = 'independent collaborator'
        (WORK / 'jobs' / record.name).write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
        imported.append(name)
    print(json.dumps({'registered': imported}))


if __name__ == '__main__':
    register()
