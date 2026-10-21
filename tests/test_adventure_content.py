"""Authoring failures are rejected before a saved adventure can offer them."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from utils import adventure_content as catalog


def choice(key, effects, next_node=None):
    result = dict(id=key, label=key.replace('_', ' ').title(), description='A bounded, saved consequence.',
                  outcome=f'The team resolves {key} and remembers this particular discovery.', effects=effects)
    if next_node:
        result['next'] = next_node
    return result


def root(key='grove', *, biome='woods', repeatable=False, chain=1, effects=None):
    root_id = f'{biome}.{key}'
    nodes = {}
    for index in range(chain):
        node_id = f'{root_id}.scene{index}'
        next_node = f'{root_id}.scene{index + 1}' if index + 1 < chain else None
        nodes[node_id] = dict(body=(f'At {key} scene {index}, roots shelter a narrow stone path. '
                                  'Your companions find a worn marker beside a clear pool. '
                                  'Someone has tied fresh reeds around its base, and a small footbridge '
                                  'leads toward the quiet trees beyond.'),
                              choices=[choice('observe', deepcopy(effects or {'bond': 1}), next_node),
                                       choice('study', {'study': 1}, next_node)])
    return dict(id=root_id, title=f'The {key} location', kind='discovery', repeatable=repeatable,
                entry=next(iter(nodes)), tags=['test'], nodes=nodes)


def document(*roots, biome='woods'):
    return dict(version=1, biome=biome, encounters=list(roots or [root(biome=biome)]))


class AdventureContent(unittest.TestCase):
    def errors(self, value):
        return catalog.validate(value, require_counts=False)

    def test_graph_and_effect_contract_is_nonmutating(self):
        value = document(root(chain=3))
        before = deepcopy(value)
        self.assertEqual(self.errors(value), [])
        self.assertEqual(value, before)
        node = value['encounters'][0]['nodes']['woods.grove.scene2']
        node['choices'][0]['next'] = 'woods.grove.scene0'
        self.assertTrue(any('cycle' in error for error in self.errors(value)))

    def test_unreachable_nodes_duplicate_choices_and_unbounded_effects_rejected(self):
        value = document(root(chain=3))
        first = value['encounters'][0]['nodes']['woods.grove.scene0']
        for item in first['choices']:
            item.pop('next')
        first['choices'][1]['effects'] = {'bond': 1}
        errors = self.errors(value)
        self.assertTrue(any('unreachable' in error for error in errors))
        self.assertTrue(any('duplicates' in error for error in errors))
        first['choices'][0]['effects'] = {'gold': 100, 'heal': 50, 'battle': 'pack9'}
        errors = self.errors(value)
        self.assertTrue(any('unsupported effect' in error for error in errors))
        self.assertTrue(any('bounded heal' in error for error in errors))
        self.assertTrue(any('battle size' in error for error in errors))

    def test_every_node_needs_an_unconditional_safe_choice(self):
        value = document(root())
        choices = value['encounters'][0]['nodes']['woods.grove.scene0']['choices']
        choices[0]['effects'] = {'battle': 'single'}
        choices[1]['requires_flags'] = ['woods.proof']
        self.assertTrue(any('unconditional noncombat' in error for error in self.errors(value)))

    def test_malformed_json_shapes_return_errors_instead_of_crashing(self):
        for field, malformed in [('biome', []), ('entry', {}), ('kind', []),
                                 ('requires_flags', [{}]), ('battle', []),
                                 ('set_flags', [{}]), ('next', [])]:
            with self.subTest(field=field):
                value = document(root())
                item = value['encounters'][0]
                if field == 'biome':
                    value[field] = malformed
                elif field in ('entry', 'kind', 'requires_flags'):
                    item[field] = malformed
                elif field == 'next':
                    item['nodes']['woods.grove.scene0']['choices'][0][field] = malformed
                else:
                    item['nodes']['woods.grove.scene0']['choices'][0]['effects'][field] = malformed
                self.assertTrue(self.errors(value))

    def test_exact_namespaces_exclude_neighboring_deep_biomes(self):
        for suffix in ('.grove', '_grove'):
            self.assertTrue(catalog.belongs('woods' + suffix, 'woods'))
            self.assertTrue(catalog.belongs('woods_deep' + suffix, 'woods_deep'))
            self.assertFalse(catalog.belongs('woods_deep' + suffix, 'woods'))
        self.assertTrue(self.errors(document(root(biome='woods_deep'))))

    def test_production_counts_and_cached_loads_are_independent_copies(self):
        roots = [root(f'location{i}', chain=3 if i < 3 else 1) for i in range(20)]
        roots += [root(f'wilderness{i}', repeatable=True) for i in range(6)]
        value = document(*roots)
        self.assertEqual(catalog.validate(value), [])
        with tempfile.TemporaryDirectory() as temporary, patch.object(catalog, 'CONTENT_ROOT', Path(temporary)):
            Path(temporary, 'woods.json').write_text(json.dumps(value), encoding='utf-8')
            first = catalog.load('woods')
            first['encounters'][0]['title'] = 'Changed in a caller'
            self.assertEqual(catalog.load('woods'), value)
        self.assertTrue(any('needs 20' in error for error in catalog.validate(document(root()))))

    def test_shipping_ten_biome_catalogs_pass_cross_file_graph_and_prose_checks(self):
        self.assertEqual(catalog.validate_all(), [])
        for biome in catalog.BIOMES:
            value = catalog.load(biome)
            self.assertEqual(value['biome'], biome)
            self.assertGreaterEqual(len(value['encounters']), 26)


if __name__ == '__main__':
    unittest.main()
