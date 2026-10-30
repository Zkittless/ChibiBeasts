"""Export the production team stage with equipped full-sprite ghost costumes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import cosmetics, journal_team_art as art


def main():
    output = ROOT / 'docs/hollowlight-preview'
    output.mkdir(parents=True, exist_ok=True)
    for name, species in (
            ('ghost-team-starters', ('twine', 'gloop', 'barkley')),
            ('ghost-team-carnival', ('patchkin', 'wickwing', 'veilmoth')),
            ('ghost-team-legends', ('hydra', 'leviathan', 'fenrir'))):
        members = tuple(art.Member(beast, cosmetics.appearance_id(cosmetics.GHOST), False) for beast in species)
        picture = art._render(members, 'hollowlight', art._revision(members, 'hollowlight'))
        path = output / (name + '.png')
        path.write_bytes(picture.data)
        print(path)


if __name__ == '__main__':
    main()
