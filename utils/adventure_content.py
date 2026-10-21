"""Authored adventure catalog with permanent IDs and bounded choice effects."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from utils.artwork import PROJECT_ROOT
from utils.habitats import BIOMES

CONTENT_ROOT = PROJECT_ROOT / 'data' / 'adventures'
KINDS = frozenset(('discovery', 'ruin', 'traveller', 'shrine', 'wild', 'pack', 'hazard', 'camp', 'legendary'))
EFFECTS = {'attack_boon': {5, 10}, 'defense_boon': {5, 10}, 'heal': {5, 10},
           'hp_cost': {3, 5}, 'bonus': {2, 5}, 'bond': {1}, 'study': {1}}
OTHER_EFFECTS = frozenset(('material', 'battle', 'set_flags', 'clear_flags'))
BATTLES = frozenset(('single', 'pack2', 'pack3', 'rare'))
SLUG = re.compile(r'^[a-z0-9][a-z0-9_.-]*$')


def belongs(value, biome):
    """Resolve the longest permanent namespace, keeping woods_deep out of woods."""
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        return False
    owner = next((key for key in sorted(BIOMES, key=len, reverse=True)
                  if value.startswith((key + '.', key + '_'))), None)
    return owner == biome


def _prefixed(value, biome):
    return belongs(value, biome)


def _flags(value, biome):
    return (isinstance(value, list) and all(_prefixed(flag, biome) for flag in value)
            and len(set(value)) == len(value))


def validate(document, *, expected_biome=None, require_counts=True):
    """Return readable schema/graph errors without mutating an author's input."""
    errors = []
    if not isinstance(document, dict):
        return ['Adventure file must be an object.']
    if '\ufffd' in json.dumps(document, ensure_ascii=False):
        errors.append('Adventure text contains a Unicode replacement character; restore the damaged punctuation.')
    biome = document.get('biome')
    if not isinstance(biome, str) or biome not in BIOMES or expected_biome is not None and biome != expected_biome:
        return [f'Unknown or mismatched biome: {biome!r}.']
    if type(document.get('version')) is not int or document['version'] != 1:
        errors.append(f'{biome}: version must be 1.')
    encounters = document.get('encounters')
    if not isinstance(encounters, list):
        return errors + [f'{biome}: encounters must be an array.']
    root_ids, node_ids, titles = set(), set(), set()
    once = repeats = chains = 0
    for root in encounters:
        if not isinstance(root, dict):
            errors.append(f'{biome}: encounter must be an object.')
            continue
        root_id = root.get('id')
        label = str(root_id or biome)
        if not _prefixed(root_id, biome) or root_id in root_ids:
            errors.append(f'{label}: root ID must be unique and biome-prefixed.')
        root_ids.add(str(root_id))
        title = root.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            errors.append(f'{label}: title must contain 1–100 characters.')
        elif title.casefold() in titles:
            errors.append(f'{label}: title duplicates another encounter.')
        else:
            titles.add(title.casefold())
        if not isinstance(root.get('kind'), str) or root['kind'] not in KINDS:
            errors.append(f'{label}: unknown encounter kind.')
        if type(root.get('repeatable')) is not bool:
            errors.append(f'{label}: repeatable must be a boolean.')
        elif root['repeatable']:
            repeats += 1
        else:
            once += 1
        if not isinstance(root.get('tags'), list) or not all(isinstance(tag, str) and tag.strip() for tag in root['tags']):
            errors.append(f'{label}: tags must be an array of nonempty strings.')
        for field in ('requires_flags', 'forbid_flags'):
            if not _flags(root.get(field, []), biome):
                errors.append(f'{label}: {field} must contain unique biome-prefixed flags.')
        nodes = root.get('nodes')
        if not isinstance(nodes, dict) or not nodes:
            errors.append(f'{label}: nodes must be a nonempty object.')
            continue
        entry = root.get('entry')
        valid_entry = isinstance(entry, str) and entry in nodes
        if not valid_entry:
            errors.append(f'{label}: entry does not name a node.')
        if root.get('repeatable') is False and 3 <= len(nodes) <= 5:
            chains += 1
        edges = {key: [] for key in nodes}
        for node_id, node in nodes.items():
            point = f'{label}/{node_id}'
            if not _prefixed(node_id, biome) or node_id in node_ids:
                errors.append(f'{point}: node ID must be unique and biome-prefixed.')
            node_ids.add(node_id)
            if not isinstance(node, dict):
                errors.append(f'{point}: node must be an object.')
                continue
            body = node.get('body')
            if not isinstance(body, str) or not 30 <= len(body.split()) <= 110:
                errors.append(f'{point}: body must have roughly 35–80 words (30–110 permitted).')
            choices = node.get('choices')
            if not isinstance(choices, list) or not 2 <= len(choices) <= 3:
                errors.append(f'{point}: needs 2–3 choices.')
                continue
            choice_ids, consequences, safe = set(), set(), False
            for choice in choices:
                if not isinstance(choice, dict):
                    errors.append(f'{point}: choice must be an object.')
                    continue
                choice_id = choice.get('id')
                place = f'{point}/{choice_id}'
                if not isinstance(choice_id, str) or not SLUG.fullmatch(choice_id) or choice_id in choice_ids:
                    errors.append(f'{place}: choice ID must be a unique permanent slug.')
                choice_ids.add(str(choice_id))
                for field, limit in (('label', 69), ('description', 99), ('outcome', 600)):
                    value = choice.get(field)
                    if not isinstance(value, str) or not value.strip() or len(value) > limit:
                        errors.append(f'{place}: {field} needs 1–{limit} characters.')
                for field in ('requires_flags', 'forbid_flags'):
                    if not _flags(choice.get(field, []), biome):
                        errors.append(f'{place}: invalid {field}.')
                effects = choice.get('effects')
                if not isinstance(effects, dict):
                    errors.append(f'{place}: effects must be an object.')
                    continue
                for effect, value in effects.items():
                    if effect in EFFECTS:
                        if type(value) is not int or value not in EFFECTS[effect]:
                            errors.append(f'{place}: invalid bounded {effect}.')
                    elif effect not in OTHER_EFFECTS:
                        errors.append(f'{place}: unsupported effect {effect!r}.')
                    elif effect == 'material' and value is not True:
                        errors.append(f'{place}: material must be true.')
                    elif effect == 'battle' and (not isinstance(value, str) or value not in BATTLES):
                        errors.append(f'{place}: invalid battle size.')
                    elif effect in ('set_flags', 'clear_flags') and not _flags(value, biome):
                        errors.append(f'{place}: invalid {effect}.')
                if (_flags(effects.get('set_flags', []), biome)
                        and _flags(effects.get('clear_flags', []), biome)
                        and set(effects.get('set_flags', ())).intersection(effects.get('clear_flags', ()))):
                    errors.append(f'{place}: cannot set and clear the same flag.')
                next_node = choice.get('next')
                if next_node is not None:
                    if not isinstance(next_node, str) or next_node not in nodes:
                        errors.append(f'{place}: next points outside this encounter.')
                    else:
                        edges[node_id].append(next_node)
                if not effects and next_node is None:
                    errors.append(f'{place}: choice has no effect, continuation or persistent consequence.')
                fingerprint = json.dumps([effects, next_node], sort_keys=True)
                if fingerprint in consequences:
                    errors.append(f'{place}: choice duplicates another mechanical consequence.')
                consequences.add(fingerprint)
                if not choice.get('requires_flags') and not choice.get('forbid_flags') and not effects.get('battle') and not effects.get('hp_cost'):
                    safe = True
            if not safe:
                errors.append(f'{point}: needs an unconditional noncombat, no-damage choice.')
        reached, visiting, visited = set(), set(), set()
        def visit(key):
            if key in visiting:
                errors.append(f'{label}: node graph contains a cycle at {key}.')
                return
            if key in visited:
                return
            visiting.add(key)
            reached.add(key)
            for child in edges.get(key, ()):
                visit(child)
            visiting.remove(key)
            visited.add(key)
        if valid_entry:
            visit(entry)
        if set(nodes) - reached:
            errors.append(f'{label}: unreachable nodes: {sorted(set(nodes) - reached)}.')
    if require_counts:
        if once < 20 or repeats < 6 or chains < 3:
            errors.append(f'{biome}: needs 20 one-time roots, 6 repeatable roots and 3 connected locations (found {once}/{repeats}/{chains}).')
    return errors


def validate_all(catalog=None):
    errors, catalog = [], {} if catalog is None else catalog
    if not catalog:
        for biome in BIOMES:
            try:
                catalog[biome] = json.loads((CONTENT_ROOT / (biome + '.json')).read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                errors.append(f'{biome}: cannot read content: {error}')
    bodies, outcomes, known_nodes, set_flags, referenced_flags = {}, {}, set(), set(), set()
    for biome, document in catalog.items():
        local = validate(document, expected_biome=biome)
        errors.extend(local)
        if local:
            continue
        for root in document['encounters']:
            referenced_flags.update(root.get('requires_flags', ()))
            referenced_flags.update(root.get('forbid_flags', ()))
            for node_id, node in root['nodes'].items():
                if node_id in known_nodes:
                    errors.append(f'Duplicate permanent node ID: {node_id}.')
                known_nodes.add(node_id)
                body = ' '.join(node['body'].casefold().split())
                if body in bodies and bodies[body] != root['id']:
                    errors.append(f"Repeated body across stories: {bodies[body]} / {root['id']}.")
                bodies[body] = root['id']
                for choice in node['choices']:
                    outcome = ' '.join(choice['outcome'].casefold().split())
                    if outcome in outcomes and outcomes[outcome] != root['id']:
                        errors.append(f"Repeated outcome across stories: {outcomes[outcome]} / {root['id']}.")
                    outcomes[outcome] = root['id']
                    effects = choice['effects']
                    set_flags.update(effects.get('set_flags', ()))
                    referenced_flags.update(effects.get('clear_flags', ()))
                    referenced_flags.update(choice.get('requires_flags', ()))
                    referenced_flags.update(choice.get('forbid_flags', ()))
    for flag in sorted(referenced_flags - set_flags):
        errors.append(f'Flag is referenced but never set by authored content: {flag}.')
    return errors


@lru_cache(maxsize=24)
def _read(path, modified, size):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    errors = validate(document, expected_biome=Path(path).stem)
    if errors:
        raise ValueError('Invalid adventure content: ' + '; '.join(errors[:8]))
    return document


def load(biome):
    if biome not in BIOMES:
        raise ValueError('Choose a known adventure destination.')
    path = CONTENT_ROOT / (biome + '.json')
    stat = path.stat()
    return deepcopy(_read(str(path), stat.st_mtime_ns, stat.st_size))
