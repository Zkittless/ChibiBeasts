"""Personal grants, legendary balance, and bounded storm presentation."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import io
import os
import random
import unittest
from unittest.mock import patch

from discord import app_commands
from PIL import Image, ImageSequence

from cogs.battle import build_pve_beast_state
from cogs.dev import Dev
from tests.test_gameplay import DatabaseCase, interaction
from utils import adventure_party, combat, db as storage
from utils import armor_art, battle_art as art, cosmetics, move_animations as fx, raikyr_art, transfers
from utils.exclusive_companions import give_hexpurr, give_raikyr
from utils.progression import companion_growth, rarity_growth


class PersonalGrant(DatabaseCase):
    async def lead(self, owner=42, level=19):
        await storage.get_or_create_player(owner, 'Trainer')
        row_id = await self.beast('twine', owner, starter=True)
        await self.execute('UPDATE player_beasts SET level=? WHERE id=?', (level,row_id))
        return row_id

    async def test_requires_trainer_and_current_lead_without_partial_grant(self):
        for owner, message in (('999','/start'),('42','lead companion')):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError,message):
                await give_raikyr(owner,granted_by=7)
        self.assertEqual(await self.rows('SELECT * FROM exclusive_companions'),[])
        self.assertEqual(await self.rows('SELECT * FROM gameplay_grants'),[])

    async def test_concurrent_grants_coexist_with_hexpurr_and_keep_existing_progress(self):
        lead = await self.lead()
        await self.lead(43,level=31)
        hexpurr,_ = await give_hexpurr('42',granted_by=7)
        results = await asyncio.gather(*(give_raikyr(str(owner),granted_by=7) for owner in (42,43,42,43)))
        for owner,level in ((42,19),(43,31)):
            grants = [(row,created) for row,created in results if row['user_id'] == owner]
            self.assertEqual(sum(created for _,created in grants),1)
            self.assertEqual(len({row['id'] for row,_ in grants}),1)
            self.assertTrue(all(row['level'] == level and row['rarity'] == 'dev' for row,_ in grants))
        raikyr = next(row for row,_ in results if row['user_id'] == 42)
        self.assertNotEqual(raikyr['id'],hexpurr['id'])
        await self.execute('UPDATE player_beasts SET hp=17,mana=9,exp=11,nickname=? WHERE id=?',('My Storm',raikyr['id']))
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)',(raikyr['id'],'thunderbird_storm_mail'))
        before = (await self.rows('SELECT * FROM player_beasts WHERE id=?',(raikyr['id'],)))[0]
        retry,created = await give_raikyr('42',granted_by=7)
        self.assertFalse(created)
        self.assertEqual(retry,before)
        self.assertEqual((await storage.get_active_beast(42))['id'],lead)
        self.assertEqual(len(await self.rows('SELECT * FROM exclusive_companions')),3)
        self.assertEqual(len(await self.rows('SELECT * FROM gameplay_grants')),3)
        self.assertEqual(len(await self.rows('SELECT * FROM player_equipment WHERE beast_row_id=?',(raikyr['id'],))),1)

    async def test_normal_grants_and_transfer_paths_cannot_bypass_personal_ownership(self):
        await self.lead()
        definition = storage.get_beast_data('raikyr')
        # Caller-supplied flags must never bypass the canonical catalog guard.
        for key in (None,'exclusive:raikyr:42','exclusive:hexpurr:42'):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'give_raikyr'):
                await storage.add_beast_to_player(42,{**definition,'exclusive_grant':False},grant_key=key)
        row,_ = await give_raikyr('42',granted_by=7)
        await storage.get_or_create_player(43,'Other')
        for call in (lambda:transfers.create_listing(42,row['player_number'],100),
                     lambda:transfers.release_companion(42,row['id'],expected_species='raikyr'),
                     lambda:transfers.complete_trade(42,43,row['id'],offer_key='raikyr')):
            with self.assertRaisesRegex(ValueError,'personal Developer'):
                await call()
        with self.assertRaisesRegex(ValueError,'give_raikyr'):
            await storage.add_beast_to_player(42,definition,grant_key='exclusive:raikyr:42')

    async def test_legendary_growth_survives_grant_levelups_and_team_assignment(self):
        await self.lead(level=1)
        row,_ = await give_raikyr('42',granted_by=7)
        await adventure_party.set_slot(42,row['id'],1)
        await storage.set_raid_slot(42,row['player_number'],1)
        async with storage.connect() as db:
            await storage.apply_beast_levelup(db,row,50,0)
            await db.commit()
        trained = (await self.rows('SELECT * FROM player_beasts WHERE id=?',(row['id'],)))[0]
        for stat,amount in rarity_growth('legendary',49).items():
            column = {'hp':'max_hp','mana':'max_mana'}.get(stat,stat)
            self.assertEqual(trained[column]-row[column],amount)
        self.assertEqual(trained['raid_slot'],1)
        self.assertEqual(trained['rarity'],'dev')
        self.assertEqual(storage.gameplay_rarity(trained),'legendary')

    async def test_developer_command_accepts_id_and_checks_owner(self):
        await self.lead()
        click = interaction(7)
        await Dev.give_raikyr.callback(Dev(None),click,'42')
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertIn('Raikyr',click.followup.send.call_args.args[0])
        self.assertIn('Lv. 19',click.followup.send.call_args.args[0])
        self.assertEqual(Dev.give_raikyr.parameters[0].type.value,3)
        with patch.dict(os.environ,{'OWNER_ID':'7'}):
            self.assertTrue(await Dev.give_raikyr.checks[0](interaction(7)))
            with self.assertRaises(app_commands.CheckFailure):
                await Dev.give_raikyr.checks[0](interaction(42))


class LegendaryBalance(unittest.TestCase):
    def states(self):
        fighters = [build_pve_beast_state(storage.get_beast_data(species),20) for species in ('raikyr','dragon')]
        for state in fighters:
            state.update(hp=2000,max_hp=2000,mana=150,max_mana=150,ultimate_charges=3)
        return fighters

    def test_base_and_growth_stay_within_legendary_envelope_and_out_of_wild_pool(self):
        catalog = storage.load_beasts()
        definition = catalog['raikyr']
        legends = [beast for beast in catalog.values() if beast['rarity'] == 'legendary']
        for stat,value in definition['base_stats'].items():
            self.assertGreaterEqual(value,min(beast['base_stats'][stat] for beast in legends))
            self.assertLessEqual(value,max(beast['base_stats'][stat] for beast in legends))
        self.assertLessEqual(sum(definition['base_stats'].values()),max(sum(beast['base_stats'].values()) for beast in legends))
        for level in (1,12,25,50):
            self.assertEqual(companion_growth('raikyr',level,catalog),rarity_growth('legendary',level-1))
        self.assertFalse(definition['wild_encounter'])
        self.assertEqual(definition['catch_rate'],0)
        self.assertFalse(definition.get('divine_passive'))

    def test_heavenfall_uses_standard_ultimate_cost_and_damage_without_aura_bonus(self):
        first,enemy = self.states()
        baseline = deepcopy(first)
        baseline['beast_id'] = 'dragon'
        seed = 941
        with combat.using_rng(random.Random(seed)):
            expected = combat.resolve_move(baseline,deepcopy(enemy),'Dragon Roar',True,[])
        with combat.using_rng(random.Random(seed)):
            actual = combat.resolve_move(first,enemy,'Heavenfall',True,[])
        self.assertEqual(actual,expected)
        self.assertEqual(first['mana'],100)
        self.assertEqual(first['ultimate_charges'],0)
        self.assertFalse(combat.can_use(first,'Heavenfall',True))
        first['ultimate_charges'] = 3
        first['mana'] = 49
        self.assertFalse(combat.can_use(first,'Heavenfall',True))
        first['mana'] = 50
        self.assertTrue(combat.can_use(first,'Heavenfall',True))


class StormPresentation(unittest.TestCase):
    def scenes(self, armor=''):
        actor, enemy = LegendaryBalance().states()
        actor['armor_id'] = armor
        before = art.snapshot([actor,enemy])
        with combat.using_rng(random.Random(941)):
            damage = combat.resolve_move(actor,enemy,'Heavenfall',True,[])
        return before,art.snapshot([actor,enemy]),art.Action(0,'Heavenfall','skill',damage,ultimate=True)

    def test_real_ultimate_and_idle_keep_outfit_and_stop_aura_after_battle(self):
        before,after,action = self.scenes('thunderbird_storm_mail')
        immutable = repr((before,after))
        move = art.render(before,after,'loom',action)
        with Image.open(io.BytesIO(move.data)) as image:
            self.assertNotIn('loop',image.info)
            self.assertEqual(sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)),action.milliseconds)
        idle = art.render(after,after,'loom',ambient_start=move.ambient_end,palette=move.palette)
        self.assertEqual(idle.seconds,0)
        self.assertLess(max(len(move.data),len(idle.data)),art.MAX_BYTES)
        with Image.open(io.BytesIO(idle.data)) as image:
            self.assertEqual(image.info['loop'],0)
            self.assertEqual(sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)),art.AMBIENT_MS)
        a = art.frame(after,after,'loom',ambient_seconds=.25)
        b = art.frame(after,after,'loom',ambient_seconds=2.25)
        c = art.frame(after,after,'loom',ambient_seconds=.75)
        self.assertEqual(a.tobytes(),b.tobytes())
        self.assertNotEqual(a.tobytes(),c.tobytes())
        self.assertEqual(a.crop((0,0,640,208)).tobytes(),c.crop((0,0,640,208)).tobytes())
        for finished in ((after[0],replace(after[1],hp=0)),(replace(after[0],hp=0),after[1])):
            self.assertEqual(art.render(finished,finished,'loom').extension,'png')
        self.assertEqual(repr((before,after)),immutable)

    def test_all_outfits_remain_distinct_through_parallel_shared_aura_frames(self):
        from concurrent.futures import ThreadPoolExecutor
        appearances = ['']+list(armor_art.manifest()['sets'])+[cosmetics.appearance_id(cosmetics.GHOST)]
        jobs = []
        for appearance in appearances:
            before,after,action = self.scenes(appearance)
            jobs.append((before,after,action,.28))
            jobs.append((tuple(reversed(before)),tuple(reversed(after)),replace(action,actor=1),.82))
        def render(job):
            before,after,action,t = job
            return art.frame(before,after,'loom',action,t,ambient_seconds=t*2.65).tobytes()
        expected = [render(job) for job in jobs]
        self.assertEqual(len(set(expected[::2])),len(appearances))
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(render,jobs)),expected)

    def test_raid_ultimate_and_idle_use_registered_ghost_with_bounded_attachment(self):
        from cogs.guilds import RAID_BOSSES
        from scripts.preview_raid_arena import make_raid,capture
        from utils import raid_art
        raid = make_raid(next(iter(RAID_BOSSES.values()))[0],4)
        actor = build_pve_beast_state(storage.get_beast_data('raikyr'),20)
        actor.update(ultimate_charges=3,armor_id=cosmetics.appearance_id(cosmetics.GHOST))
        raid.party[0][0] = actor
        before = capture(raid)
        raid.resolve_action(0,'ultimate')
        after = capture(raid)
        immutable = repr((before,after))
        move = raid_art.render(before,after,tuple(raid._art_events))
        idle = raid_art.render(after,after,ambient_start=move.ambient_end,palette=move.palette)
        self.assertLess(max(len(move.data),len(idle.data)),art.MAX_BYTES)
        with Image.open(io.BytesIO(move.data)) as image:
            self.assertNotIn('loop',image.info)
            self.assertEqual(image.size,(640,692))
        with Image.open(io.BytesIO(idle.data)) as image:
            self.assertEqual(image.info['loop'],0)
        self.assertEqual(repr((before,after)),immutable)

    def test_glow_cropping_preserves_full_canvas_blur(self):
        from PIL import ImageDraw, ImageFilter
        source = Image.new('RGBA',(640,248))
        draw = ImageDraw.Draw(source)
        draw.line([(171,208),(150,50),(190,23)],fill=(30,80,240,150),width=11)
        for radius in (2.2,3):
            expected = Image.new('RGBA',source.size)
            expected.alpha_composite(source.filter(ImageFilter.GaussianBlur(radius)))
            actual = Image.new('RGBA',source.size)
            raikyr_art.Ink.overlay(actual,source,radius)
            self.assertEqual(actual.tobytes(),expected.tobytes())

    def test_cached_aura_loops_is_bounded_and_fades_without_mutating_shared_frames(self):
        raikyr_art._aura_frame.cache_clear()
        pose = fx.Pose(162,218)
        first = Image.new('RGBA',(640,248))
        last = Image.new('RGBA',(640,248))
        for front in (False,True):
            raikyr_art.paint_aura(first,pose,0,front=front)
            raikyr_art.paint_aura(last,pose,2,front=front)
        self.assertEqual(first.tobytes(),last.tobytes())
        cached,_ = raikyr_art._aura_frame(0,False)
        original = cached.tobytes()
        for i in range(90):
            canvas = Image.new('RGBA',(640,248))
            for front in (False,True):
                raikyr_art.paint_aura(canvas,replace(pose,alpha=.2,size=112),i/30,front=front)
        self.assertEqual(cached.tobytes(),original)
        info = raikyr_art._aura_frame.cache_info()
        self.assertLessEqual(info.currsize,120)
        self.assertGreater(info.hits,0)

    def test_heavenfall_returns_home_on_both_sides_and_raid_scales(self):
        for size in (72,112,164,180):
            for reverse in (False,True):
                home = fx.Pose(478 if reverse else 162,218,size=size,flip=reverse)
                target = fx.Pose(162 if reverse else 478,218,size=size,flip=not reverse)
                self.assertEqual(raikyr_art.motion(home,target,0),home)
                self.assertEqual(raikyr_art.motion(home,target,1),home)
                self.assertEqual(raikyr_art.reaction(target,home,0),target)
                self.assertEqual(raikyr_art.reaction(target,home,1),target)
                recoil = raikyr_art.reaction(target,home,.55)
                self.assertGreater(abs(recoil.x-home.x),abs(target.x-home.x))
                for t in (0,1):
                    layer = fx.Effects((640,248))
                    raikyr_art.effect(layer,home,target,t,home=home,back=True)
                    raikyr_art.effect(layer,home,target,t,home=home)
                    self.assertIsNone(layer.image.getbbox())
