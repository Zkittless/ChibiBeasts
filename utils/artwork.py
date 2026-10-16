"""Canonical URLs for the versioned beast artwork shipped with the bot."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE_BASE_URL = (
    "https://raw.githubusercontent.com/Zkittless/ChibiBeasts/main/data/sprites"
)

@lru_cache(maxsize=1)
def artwork_manifest():
    with (PROJECT_ROOT / "data" / "artwork.json").open(encoding="utf-8") as stream:
        return json.load(stream)["beasts"]

def beast_image_url(beast_id: str) -> str:
    """Resolve one roster ID, with a content version to refresh Discord's cache."""
    if not re.fullmatch(r"[a-z0-9_]+", beast_id):
        raise ValueError(f"Invalid beast ID: {beast_id!r}")
    entry = artwork_manifest().get(beast_id)
    if entry is None:
        raise KeyError(f"No artwork registered for {beast_id!r}")
    base = os.getenv("BEAST_IMAGE_BASE_URL", DEFAULT_IMAGE_BASE_URL).rstrip("/")
    url = f"{base}/{quote(entry['file'])}"
    digest = entry.get("sha256")
    return f"{url}?v={digest[:16]}" if digest else url
