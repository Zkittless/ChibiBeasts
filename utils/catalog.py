"""Cache deployment data; callers receive private copies they can safely modify."""
from copy import deepcopy
from functools import lru_cache
import json
from utils.artwork import PROJECT_ROOT, beast_image_url

_revision=0

@lru_cache(maxsize=16)
def _document(filename):
    if filename not in {'beasts.json','items.json','perks.json','equipment.json','materials.json','questline.json','npcs.json'}:
        raise ValueError('Unknown game catalog')
    return json.loads((PROJECT_ROOT/'data'/filename).read_text(encoding='utf-8'))

def load_catalog(filename):
    return deepcopy(_document(filename))

def beast_definition(beast_id):
    source=_document('beasts.json')['beasts'].get(beast_id)
    if source is None:return None
    beast=deepcopy(source)
    beast['image_url']=beast_image_url(beast_id)
    return beast

def catalog_revision():return _revision

def clear_catalog_cache():
    """Development/test hook. Production data refreshes when the bot restarts."""
    global _revision
    _revision+=1
    _document.cache_clear()
