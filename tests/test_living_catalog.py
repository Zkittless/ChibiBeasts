"""Deployment checks against the complete authored catalog, not toy fixtures."""
import unittest

from utils import adventure_content
from utils.habitats import BIOMES


class LivingCatalog(unittest.TestCase):
    def test_all_destinations_ship_valid_unique_authored_content(self):
        documents={key:adventure_content.load(key) for key in BIOMES}
        self.assertEqual(adventure_content.validate_all(documents),[])
        for key,document in documents.items():
            roots=document['encounters']
            with self.subTest(biome=key):
                self.assertGreaterEqual(sum(not r['repeatable'] for r in roots),20)
                self.assertGreaterEqual(sum(r['repeatable'] for r in roots),6)
                self.assertGreaterEqual(sum(not r['repeatable'] and 3<=len(r['nodes'])<=5 for r in roots),3)
                for root in roots:
                    for node in root['nodes'].values():
                        self.assertTrue(35<=len(node['body'].split())<=80,(key,root['id']))

    def test_followup_discoveries_have_a_reachable_source_for_their_flags(self):
        for key in BIOMES:
            roots=adventure_content.load(key)['encounters']
            available=set()
            flags=set()
            while True:
                old=(set(available),set(flags))
                for root in roots:
                    if set(root.get('requires_flags',()))<=flags:
                        available.add(root['id'])
                        # A possibility check, deliberately allowing alternative
                        # branches. It catches mutually locked follow-up islands.
                        for node in root['nodes'].values():
                            for choice in node['choices']:
                                if set(choice.get('requires_flags',()))<=flags:
                                    flags.update(choice['effects'].get('set_flags',()))
                if old==(available,flags):
                    break
            self.assertEqual(available,{r['id'] for r in roots},key)

    def test_familiar_wilderness_preserves_captures_and_pack_variety_after_stories_end(self):
        for key in BIOMES:
            modes={choice['effects']['battle'] for root in adventure_content.load(key)['encounters']
                   if root['repeatable'] for node in root['nodes'].values() for choice in node['choices']
                   if choice['effects'].get('battle')}
            with self.subTest(biome=key):
                self.assertTrue(modes.intersection({'single','rare'}), 'No repeatable capture encounter remains.')
                self.assertTrue(modes.intersection({'pack2','pack3'}), 'No repeatable pack encounter remains.')
