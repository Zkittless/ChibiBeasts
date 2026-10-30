"""Export real Carnival battle animations with frozen cosmetic team snapshots."""
from dataclasses import replace
import io
import json
from pathlib import Path
import sys

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import battle_art as art, db as storage


def main():
    output = ROOT / 'docs/hollowlight-preview'
    output.mkdir(parents=True, exist_ok=True)
    party = [build_pve_beast_state(storage.get_beast_data(species), 30)
             for species in ('twine', 'gloop', 'barkley')]
    for index, state in enumerate(party):
        state.update(id=index + 1, armor_id='cosmetic:ghost_costume_2026', _assist_meter=100)
    enemy = build_pve_beast_state(storage.get_beast_data('hollow_king'), 30)

    def capture(index):
        return art.snapshot([dict(party[index], _party_states=party), enemy])

    before = capture(0)
    attack = (replace(before[0], mana=max(0, before[0].mana - 15)),
              replace(before[1], hp=before[1].hp - 42))
    swap = capture(1)
    healing = (replace(before[0], hp=before[0].hp - 20), before[1])
    assisted_party = list(before[0].party)
    assisted_party[1] = replace(assisted_party[1], mana=max(0, assisted_party[1].mana - 12))
    assisted = (replace(before[0], assist_meter=0, party=tuple(assisted_party)), before[1])
    cases = [
        ('ghost-strike', before, attack, art.Action(0, 'Thread Snap', 'skill', 42)),
        ('ghost-swap', before, swap, art.Action(0, 'Gloop takes the lead', 'swap', incoming_id=2, outgoing_id=1)),
        ('ghost-assist', healing, assisted, art.Action(0, 'Gloop lends a hand', 'assist',
            support_id=2, support_name='Gloop', support_species='gloop',
            support_armor_id='cosmetic:ghost_costume_2026', support_effect='heal')),
    ]
    report = []
    contacts = Image.new('RGB', (art.WIDTH * 3, art.TOTAL_HEIGHT * len(cases)))
    for row, (name, start, end, action) in enumerate(cases):
        clip = art.render(start, end, 'hollowlight', action)
        still = art.render(end, end, 'hollowlight')
        (output / f'{name}.gif').write_bytes(clip.data)
        (output / f'{name}-final.png').write_bytes(still.data)
        with Image.open(io.BytesIO(clip.data)) as image:
            frames = [frame.convert('RGB') for frame in ImageSequence.Iterator(image)]
            assert len(frames) > 1 and 'loop' not in image.info
            durations = []
            for index in range(image.n_frames):
                image.seek(index)
                durations.append(image.info['duration'])
        assert len(clip.data) < art.MAX_BYTES
        assert sum(durations) == round(clip.seconds * 1000)
        for column, frame in enumerate((frames[0], frames[len(frames) // 2], frames[-1])):
            contacts.paste(frame, (column * art.WIDTH, row * art.TOTAL_HEIGHT))
        report.append(dict(name=name, frames=len(frames), bytes=len(clip.data),
                           milliseconds=sum(durations), plays_once=True, realm='hollowlight'))
    contacts.save(output / 'animation-contacts.png', optimize=True)
    (output / 'animation-validation.json').write_text(json.dumps(dict(status='passed',
        scope='Production renderer with frozen illustrative team states; no live Discord actions.',
        costume='cosmetic:ghost_costume_2026', cases=report), indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
