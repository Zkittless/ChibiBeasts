"""Verify the sprite set and write content hashes without modifying image pixels."""
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent.parent

def finalize():
    roster = json.loads((ROOT / "data/beasts.json").read_text(encoding="utf-8"))["beasts"]
    entries = {}
    for beast_id in roster:
        path = ROOT / "data/sprites" / f"{beast_id}.png"
        content = path.read_bytes()
        if content[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{path.name}: not a PNG")
        width, height, depth, color = struct.unpack(">IIBB", content[16:26])
        if color != 6:
            raise ValueError(f"{path.name}: expected RGBA artwork")
        entries[beast_id] = {
            "file": path.name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "width": width,
            "height": height,
            "source": "image_gen",
        }
    extras = {path.name for path in (ROOT / "data/sprites").glob("*.png")} - {value["file"] for value in entries.values()}
    if extras:
        raise ValueError(f"Unreferenced sprites: {sorted(extras)}")
    manifest = {
        "style": "Chunky pixel art, based on user-approved starter concepts",
        "generation": "built-in image_gen",
        "grid": "Pixel-style renders; not native 32x32 exports",
        "beasts": entries,
    }
    (ROOT / "data/artwork.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(entries)} RGBA sprites; refreshed the artwork manifest.")

if __name__ == "__main__":
    finalize()
