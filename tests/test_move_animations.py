"""Roster-wide presentation coverage, retreat continuity and raid move capture."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageSequence

from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from scripts.preview_raid_arena import make_raid, capture
from utils import battle_art as art, combat, db as storage, move_animations as fx, raid_art
from utils import battle_scene, raid_scene


def states(left='twine', right='barkley'):
    return [build_pve_beast_state(storage.get_beast_data(k), 8) for k in (left,right)]


class MoveCatalog(unittest.TestCase):
    def test_every_species_and_move_has_an_explicit_valid_profile(self):
        roster=storage.load_beasts()
        self.assertEqual(set(fx.manifest()),set(roster))
        unique=set()
        supports={'guard','shelter','wind_guard','illusion','rewind'}
        for species,data in roster.items():
            self.assertEqual(set(fx.manifest()[species]),set(data['moves']+[data['ultimate']]))
            for name in data['moves']+[data['ultimate']]:
                with self.subTest(species=species,move=name):
                    spec=combat.move_spec(data,name,name==data['ultimate'])
                    a=fx.profile(species,name)
                    self.assertIn(a.family,fx.FAMILIES)
                    self.assertIn(a.palette,fx.PALETTES)
                    self.assertIn(a.motion,{'cast','bite','dash','headbutt','slash','time','rewind','bounce','stomp','hover','brace'})
                    self.assertIn(a.variant,range(4))
                    self.assertIn(a.motif,fx.MOTIFS)
                    if spec.get('heal_percent'):self.assertEqual(a.family,'heal')
                    if spec.get('guard_percent'):self.assertIn(a.family,supports)
                    if spec.get('power',1)>0:self.assertNotIn(a.family,supports|{'heal'})
                    unique.add(name)
        self.assertEqual(len(unique),334)

    def test_approved_moves_keep_their_distinct_themes_and_mechanics(self):
        expected={('twine','Tick Bite'):('bite','clock'),('twine','Thread Snap'):('thread','thread'),
                  ('twine','Rewind Nibble'):('rewind','clock'),('twine','Borrowed Moment'):('time','clock'),
                  ('gloop','Galaxy Splash'):('splash','galaxy'),('gloop','Glitter Bounce'):('heal','galaxy'),
                  ('barkley','Unmovable Root'):('roots','wood'),('wyvern','Fire Breath'):('flame','fire'),
                  ('glacius','Ice Shell'):('ice','ice'),('harpy','Wind Shriek'):('wind','wind')}
        for (species,move),result in expected.items():
            a=fx.profile(species,move)
            self.assertEqual((a.family,a.palette),result)
        self.assertEqual(fx.profile('timestitch','Borrowed Moment').family,'rewind')
        self.assertEqual(fx.profile('twine','Tick Bite (bonus)'),fx.profile('twine','Tick Bite'))
        self.assertEqual(fx.profile('twine','Tick Bite','status').family,'status')
        self.assertEqual(fx.profile('missing','Unknown','heal').family,'heal')
        self.assertEqual(fx.profile('centry','Arrow Rain').motif,'arrow')
        self.assertEqual(fx.profile('imp','Nightmare Swarm').motif,'bat')
        self.assertEqual(fx.profile('prismara','Facet Lance').motif,'lance')
        self.assertEqual(fx.profile('genesis','Genesis Explosion').motif,'nova')

    def test_all_profiles_draw_on_both_sides_without_touching_the_catalog(self):
        original=deepcopy(fx.manifest())
        for species,moves in original.items():
            for name in moves:
                animation=fx.profile(species,name)
                for flipped in (False,True):
                    home=fx.Pose(475 if flipped else 165,218,flip=flipped)
                    target=fx.Pose(165 if flipped else 475,218,flip=not flipped)
                    for t in (.2,.44,.56,.8):
                        layer=fx.Effects((640,248))
                        pose=fx.motion(animation,home,target,t)
                        layer.effect(animation,pose,target,t,back=True)
                        layer.effect(animation,pose,target,t)
                        self.assertEqual(layer.image.size,(640,248))
        self.assertEqual(fx.manifest(),original)

    def test_effect_families_have_different_geometry_with_the_same_palette(self):
        hashes=set()
        for family in ('bite','thread','rewind','time','splash','heal','roots','flame','ice','wind'):
            # Color is held constant: differences must come from the animation.
            sheet=Image.new('RGBA',(640,248*4))
            for i,t in enumerate((.3,.44,.52,.65)):
                layer=fx.Effects((640,248))
                a=fx.Animation(family,'arcane')
                layer.effect(a,fx.Pose(165,218),fx.Pose(475,218),t,back=True)
                layer.effect(a,fx.Pose(165,218),fx.Pose(475,218),t)
                sheet.paste(layer.image,(0,i*248))
            hashes.add(hashlib.sha256(sheet.tobytes()).hexdigest())
        self.assertEqual(len(hashes),10)

    def test_literal_move_motifs_differ_from_a_generic_swarm_or_orbit(self):
        for family,motif in (('swarm','arrow'),('swarm','bat'),('slash','lance'),('orbit','flower'),('orbit','nova')):
            frames=[]
            for value in ('spark',motif):
                strip=Image.new('RGB',(640,248*4),'#102b2c')
                for i,t in enumerate((.32,.45,.56,.72)):
                    layer=fx.Effects((640,248))
                    layer.effect(fx.Animation(family,'arcane',motif=value),fx.Pose(165,218),fx.Pose(475,218),t)
                    field=Image.new('RGBA',(640,248),'#102b2c')
                    field.alpha_composite(layer.image)
                    strip.paste(field.convert('RGB'),(0,i*248))
                frames.append(strip)
            self.assertIsNotNone(ImageChops.difference(*frames).getbbox(),(family,motif))

    def test_frame_ticks_are_even_and_preserve_the_full_clip_duration(self):
        for milliseconds,count in ((1650,24),(1950,24),(2650,30),(4050,44),(1200,18),(3000,36)):
            values=art.frame_durations(milliseconds,count)
            self.assertEqual(sum(values),milliseconds)
            self.assertLessEqual(max(values)-min(values),10)
            self.assertTrue(all(value%10==0 and value>=20 for value in values))


class Retreats(unittest.TestCase):
    def test_ground_and_floating_retreats_keep_aspect_and_leave_both_edges(self):
        source=Image.new('RGBA',(40,80),'#d6b88a')
        for species in ('barkley','wisp','twine'):
            for x in (162,478):
                home=fx.Pose(x,218,size=120,flip=x>320)
                for p in (.2,.4,.6,.8):
                    pose=fx.retreat(home,p,species)
                    self.assertEqual(pose.size,home.size)
                    field=Image.new('RGBA',(900,400))
                    # A rotation changes the bounding rectangle but never
                    # changes the source's width/height scale independently.
                    fx.paint_sprite(field,source,replace(pose,angle=0,x=450))
                    box=field.getbbox()
                    self.assertAlmostEqual((box[2]-box[0])/(box[3]-box[1]),.5,places=2)
                end=fx.retreat(home,1,species)
                self.assertTrue(end.x+end.size/2<0 if x<320 else end.x-end.size/2>640)
                self.assertEqual(end.flip,x<320)

    def test_finishing_hit_has_a_separate_retreat_segment_and_no_reappearance(self):
        pair=states();before=art.snapshot(pair)
        pair[1]['hp']=0;after=art.snapshot(pair)
        action=art.Action(0,'Tick Bite',damage=before[1].hp)
        total=battle_scene.duration(before,after,action)
        self.assertEqual(total,action.milliseconds+fx.RETREAT_MS)
        move_t,retreat_t=battle_scene.timing(before,after,action,action.milliseconds/total)
        self.assertAlmostEqual(move_t,1)
        self.assertAlmostEqual(retreat_t,0)
        self.assertEqual(battle_scene.timing(before,after,action,1),(1,1))
        first=art.frame(before,after,'woods',action,0)
        last=art.frame(before,after,'woods',action,1)
        still=art.frame(after,after,'woods')
        stage=(380,art.HUD,640,art.HUD+art.HEIGHT)
        self.assertIsNotNone(ImageChops.difference(first.crop(stage),last.crop(stage)).getbbox())
        self.assertIsNone(ImageChops.difference(last.crop(stage),still.crop(stage)).getbbox())
        repeated=art.frame(after,after,'woods',art.Action(0,'End-of-turn effects','status'),.2)
        self.assertIsNone(ImageChops.difference(still.crop(stage),repeated.crop(stage)).getbbox())
        clip=art.render(before,after,'woods',action)
        self.assertEqual(clip.seconds,total/1000)
        self.assertLess(len(clip.data),art.MAX_BYTES)
        with Image.open(io.BytesIO(clip.data)) as gif:
            self.assertNotIn('loop',gif.info)
            self.assertEqual(sum(f.info['duration'] for f in ImageSequence.Iterator(gif)),total)

    def test_both_defeated_fighters_leave_and_revival_returns_a_full_body(self):
        pair=states();before=art.snapshot(pair)
        for state in pair:state['hp']=0
        after=art.snapshot(pair)
        action=art.Action(1,'Tick Bite',damage=10)
        final=art.frame(before,after,'loom',action,1)
        field=(0,art.HUD,640,art.HUD+art.HEIGHT)
        background=art.field_background('loom',art.TOTAL_HEIGHT).convert('RGB')
        self.assertIsNone(ImageChops.difference(final.crop(field),background.crop(field)).getbbox())
        revived=tuple(replace(f,hp=50,revived=True) if i==0 else f for i,f in enumerate(after))
        returned=art.frame(after,revived,'loom',art.Action(0,'Revival','status'),1)
        self.assertIsNotNone(ImageChops.difference(returned.crop(field),background.crop(field)).getbbox())

    def test_boss_and_party_departures_survive_final_static_and_status_updates(self):
        session=make_raid(RAID_BOSSES['corrupted'][2],3)
        before=capture(session)
        session.enemy['hp']=0
        after=replace(capture(session),outcome='victory')
        events=(raid_art.Event(1,0,'ultimate',10,move='A Stitch in Time',species='timestitch',slot=0),)
        self.assertEqual(raid_scene.duration(before,after,events),1200+fx.BOSS_RETREAT_MS)
        still=raid_art.frame(after,after)
        final=raid_art.frame(before,after,events,1)
        boss_area=(220,raid_art.HUD+40,420,raid_art.HUD+240)
        self.assertIsNone(ImageChops.difference(final.crop(boss_area),still.crop(boss_area)).getbbox())
        static_status=raid_art.frame(after,after,(raid_art.Event(2,None,'status'),),.2)
        self.assertIsNone(ImageChops.difference(still,static_status).getbbox())
        clip=raid_art.render(before,after,events)
        self.assertLess(len(clip.data),art.MAX_BYTES)
        self.assertEqual(clip.seconds,3)
        defeat=replace(before,outcome='defeat')
        self.assertTrue(raid_scene.departing(before,defeat))
        self.assertIsNotNone(raid_art.render(before,defeat,(raid_art.Event(3,None,'status'),)).data)
        timeout=raid_art.render(before,defeat)
        self.assertEqual(timeout.extension,'gif')
        self.assertEqual(timeout.seconds,3)
        self.assertEqual(raid_art.render(defeat,defeat).extension,'png')


class RaidMoveEvents(unittest.TestCase):
    def test_real_raid_actions_capture_move_species_and_slot_before_swap(self):
        session=make_raid(RAID_BOSSES['corrupted'][2],3)
        actor=session.state(0)
        session.resolve_action(0,'skill')
        event=session._art_events[-1]
        self.assertEqual((event.move,event.species,event.slot),(actor['moves'][1],actor['beast_id'],0))
        session.last.clear()
        session.resolve_action(0,'swap')
        swapped=session._art_events[-1]
        self.assertEqual((swapped.species,swapped.slot),(actor['beast_id'],0))
        self.assertNotEqual(session.state(0)['beast_id'],swapped.species)
        self.assertEqual(raid_art.events_by_member(tuple(session._art_events))[0]['event'],swapped)

    def test_a_skipped_turn_never_records_the_move_as_an_attack(self):
        session=make_raid(RAID_BOSSES['corrupted'][2],1)
        session.state(0).update(status='freeze',status_turns=1)
        session.resolve_action(0,'attack')
        event=session._art_events[-1]
        self.assertEqual((event.kind,event.move),('status',''))

    def test_solved_harmless_pattern_does_not_animate_a_boss_attack(self):
        session=make_raid(RAID_BOSSES['corrupted'][0],2)
        session.pending=True;session.phase=1
        session.weaves=session.weave_required()
        session.pattern_started=0
        session.resolve_boss_turn()
        self.assertEqual(session._art_events[-1].move,'')
        session.pending=None
        session.resolve_boss_turn()
        self.assertEqual(session._art_events[-1].move,session.enemy['moves'][0])

    def test_snapshots_and_combat_randomness_are_untouched_by_every_frame(self):
        session=make_raid(RAID_BOSSES['corrupted'][2],10)
        before=capture(session)
        for uid in session.users:session.resolve_action(uid,'attack')
        after=capture(session)
        saved=deepcopy((before,after,session.party,session.enemy))
        with patch.object(combat,'resolve_move',side_effect=AssertionError('Rendering cannot resolve combat')):
            for t in (0,.25,.55,.8,1):raid_art.frame(before,after,tuple(session._art_events),t)
        self.assertEqual(saved,(before,after,session.party,session.enemy))


if __name__=='__main__':
    unittest.main()
