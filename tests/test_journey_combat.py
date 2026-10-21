"""Exploration-specific carryover and bounded temporary resources."""
from copy import deepcopy
import json
import unittest

from tests.test_trial_combat import fighter
from utils import journey_combat as journey, trial_combat
from utils.habitats import BIOMES, eligible
from utils import db as storage


class JourneyCombat(unittest.TestCase):
    def test_plans_are_saved_legal_deterministic_and_have_no_reward_fields(self):
        for realm, biome in BIOMES.items():
            for mode, count in [('single',1),('pack2',2),('pack3',3),('rare',1)]:
                p=journey.plan(realm,42,mode,100)
                self.assertEqual(p,journey.plan(realm,42,mode,100))
                self.assertEqual(p,json.loads(json.dumps(p)))
                self.assertEqual(p['total'],count)
                for wave in p['waves']:
                    species=storage.get_beast_data(wave['species'])
                    self.assertTrue(eligible(species))
                    self.assertIn(species['type'],biome['types'])
                    self.assertGreater(biome['weights'].get(species['rarity'],0),0)
                    self.assertEqual(wave['level'],biome['level_range'][1])
                    self.assertNotIn('reward',wave)
                    allowed=[r for r in ('common','uncommon','rare','epic','legendary','divine') if biome['weights'].get(r,0)>0]
                    if not any(r in allowed for r in ('common','uncommon')):
                        self.assertEqual(species['rarity'],allowed[0])

    def test_boons_stop_at_twenty_and_modify_passive_baselines(self):
        p={'prepared_party':[fighter()]}
        state=p['prepared_party'][0]
        original=deepcopy(state)
        for _ in range(6):
            journey.effects(p,{'attack_boon':10,'defense_boon':10})
        for stat in ('attack','defense'):
            self.assertEqual(state[stat],original[stat]+round(original[stat]*.2))
            self.assertEqual(state['base_'+stat],original['base_'+stat]+round(original[stat]*.2))

    def test_overworld_costs_never_knock_out_and_recovery_never_revives(self):
        p={'prepared_party':[fighter(identity=1),fighter(identity=2)]}
        first,second=p['prepared_party']
        first['hp']=1
        second['hp']=0
        journey.effects(p,{'hp_cost':5})
        self.assertEqual([s['hp'] for s in p['prepared_party']],[1,0])
        journey.effects(p,{'heal':10})
        self.assertGreater(first['hp'],1)
        self.assertEqual(second['hp'],0)

    def test_new_encounter_keeps_every_party_resource_and_ko(self):
        p={'prepared_party':[fighter(identity=1),fighter('gloop',identity=2),fighter('barkley',identity=3)]}
        journey.start(p,journey.plan('woods',1,'single',2))
        state=p['combat']
        for member in state['party']:
            member.update(hp=17,mana=4,status='poison',status_turns=4)
        state['party'][2]['hp']=0
        state.update(slot=0,meter=2,entered=[1,2],participated=[1,2])
        # Entry can legitimately advance one enemy turn; use a very fast lead
        # so this boundary ends at the decision before any turn drains.
        state['party'][0].update(speed=10000,base_speed=10000,status=None)
        before=deepcopy(state['party'])
        journey.start(p,journey.plan('woods',2,'pack2',2))
        for index in (1,2):
            for key in ('hp','mana','status','status_turns'):
                self.assertEqual(state['party'][index][key],before[index][key])
        self.assertEqual(state['meter'],2)
        self.assertEqual(state['entered'],[1,2])
        self.assertEqual(state['participated'],[1,2])
        self.assertEqual(p['fights_started'],2)

    def test_pack_entry_uses_the_real_animation_event_and_one_saved_cursor(self):
        member=fighter()
        member.update(attack=5000,base_attack=5000,speed=10000,base_speed=10000)
        p={'prepared_party':[member]}
        journey.start(p,journey.plan('woods',8,'pack2',2))
        self.assertEqual(p['combat']['events'][0]['action']['kind'],'enter')
        journey.action(p,trial_combat.legal_actions(p['combat'])[0]['choice'])
        self.assertEqual(p['combat']['status'],'between')
        journey.continue_pack(p)
        self.assertEqual(p['combat']['events'][0]['action']['kind'],'enter')
        self.assertEqual(p['combat']['wave_index'],1)
        self.assertEqual(p,json.loads(json.dumps(p)))
