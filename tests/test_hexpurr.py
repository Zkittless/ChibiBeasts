"""Personal grant transactions, ordinary legendary growth and temporary form rules."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from PIL import Image, ImageChops, ImageSequence
from discord import app_commands
from cogs.battle import build_pve_beast_state
from cogs.dev import Dev
from tests.test_gameplay import DatabaseCase, interaction
from utils import battle_art as art, combat, cosmetics, db as storage, hexpurr_art, transfers, trial_combat
from utils.dispositions import apply_disposition
from utils.exclusive_companions import give_hexpurr, discord_user_id, migrate_owner_bindings
from utils.progression import companion_growth, rarity_growth


def fighters():
    states = [build_pve_beast_state(storage.get_beast_data(k), 12) for k in ('hexpurr','dragon')]
    for i, state in enumerate(states):
        state.update(id=i+1, hp=1000, max_hp=1000, mana=150, max_mana=150)
    return states


class ExclusiveGrant(DatabaseCase):
    async def lead(self, uid=42, level=13):
        row_id = await self.beast('prismite', uid, starter=True)
        await self.execute('UPDATE player_beasts SET level=? WHERE id=?', (level, row_id))
        return row_id

    async def use_legacy_binding_schema(self):
        # Reproduce the deployed single-recipient table and global receipt key.
        async with storage.connect() as db:
            await db.executescript('''
                ALTER TABLE exclusive_companions RENAME TO saved_bindings;
                CREATE TABLE exclusive_companions (
                    species TEXT PRIMARY KEY,user_id INTEGER NOT NULL,
                    beast_row_id INTEGER UNIQUE,granted_by INTEGER NOT NULL,
                    granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO exclusive_companions SELECT * FROM saved_bindings;
                DROP TABLE saved_bindings;
                UPDATE gameplay_grants SET grant_key='exclusive:hexpurr'
                    WHERE grant_key='exclusive:hexpurr:42';
            ''')

    async def test_requires_existing_trainer_and_current_lead_without_partial_binding(self):
        with self.assertRaisesRegex(ValueError, '/start'):
            await give_hexpurr('999', granted_by=7)
        with self.assertRaisesRegex(ValueError, 'lead companion'):
            await give_hexpurr('42', granted_by=7)
        self.assertEqual(await self.rows('SELECT * FROM exclusive_companions'), [])
        self.assertEqual(await self.rows('SELECT * FROM gameplay_grants'), [])

    async def test_grant_matches_lead_not_highest_level_and_retry_preserves_progress(self):
        lead = await self.lead(level=13)
        reserve = await self.beast('twine')
        await self.execute('UPDATE player_beasts SET level=50 WHERE id=?', (reserve,))
        row, created = await give_hexpurr('42', granted_by=7)
        self.assertTrue(created)
        self.assertEqual((row['level'], row['rarity'], row['is_active']), (13, 'dev', 0))
        self.assertEqual((await storage.get_active_beast(42))['id'], lead)
        base = apply_disposition(storage.get_beast_data('hexpurr')['base_stats'], row['disposition'])
        growth = rarity_growth('legendary', 12)
        for stat in base:
            self.assertEqual(row[{'hp':'max_hp','mana':'max_mana'}.get(stat,stat)], base[stat]+growth[stat])
        await self.execute('UPDATE player_beasts SET level=20,hp=17,mana=9,exp=14 WHERE id=?', (row['id'],))
        retry, created = await give_hexpurr('42', granted_by=7)
        self.assertFalse(created)
        self.assertEqual((retry['id'], retry['level'], retry['hp'], retry['mana'], retry['exp']), (row['id'],20,17,9,14))
        self.assertEqual(len(await self.rows("SELECT * FROM player_beasts WHERE beast_id='hexpurr'")), 1)
        self.assertEqual(len(await self.rows("SELECT * FROM gameplay_grants WHERE grant_key='exclusive:hexpurr:42'")), 1)

    async def test_concurrent_recipients_each_receive_one_copy_at_their_own_lead_level(self):
        await self.lead()
        await storage.get_or_create_player(43, 'Other')
        await self.lead(43, level=27)
        recipients = (42, 43, 42, 43, 42, 43)
        results = await asyncio.gather(*(give_hexpurr(str(owner), granted_by=7) for owner in recipients))
        for owner, level in ((42, 13), (43, 27)):
            grants = [(row, created) for row, created in results if row['user_id'] == owner]
            self.assertEqual(sum(created for _, created in grants), 1)
            self.assertEqual(len({row['id'] for row, _ in grants}), 1)
            self.assertTrue(all(row['level'] == level for row, _ in grants))
        rows = await self.rows("SELECT * FROM player_beasts WHERE beast_id='hexpurr'")
        self.assertEqual(len(rows), 2)
        bindings = await self.rows('SELECT * FROM exclusive_companions')
        self.assertEqual({(row['user_id'], row['beast_row_id']) for row in bindings},
                         {(row['user_id'], row['id']) for row in rows})
        receipts = await self.rows("SELECT * FROM gameplay_grants WHERE grant_key LIKE 'exclusive:hexpurr:%'")
        self.assertEqual({(row['grant_key'], row['user_id'], row['beast_row_id']) for row in receipts},
                         {(f"exclusive:hexpurr:{row['user_id']}", row['user_id'], row['id']) for row in rows})

    async def test_startup_migrates_legacy_owner_without_changing_progress_or_gear(self):
        await self.lead()
        row, _ = await give_hexpurr('42', granted_by=7)
        await self.execute('UPDATE player_beasts SET level=20,hp=17,mana=9,exp=14,nickname=? WHERE id=?', ('Test Hexpurr', row['id']))
        await self.execute('INSERT INTO player_equipment(user_id,beast_row_id,equipment_id) VALUES(42,?,?)', (row['id'], 'emberstone_vest'))
        await self.use_legacy_binding_schema()
        original = (await self.rows('SELECT * FROM player_beasts WHERE id=?', (row['id'],)))[0]
        bindings = await self.rows('SELECT * FROM exclusive_companions')
        equipment = await self.rows('SELECT * FROM player_equipment')
        await storage.init_db()
        retry, created = await give_hexpurr('42', granted_by=7)
        self.assertFalse(created)
        self.assertEqual(retry, original)
        self.assertEqual(await self.rows('SELECT * FROM exclusive_companions'), bindings)
        self.assertEqual(await self.rows('SELECT * FROM player_equipment'), equipment)
        await storage.get_or_create_player(43, 'Recipient')
        await self.lead(43, level=27)
        other, created = await give_hexpurr('43', granted_by=7)
        self.assertTrue(created)
        self.assertEqual((other['user_id'], other['level']), (43, 27))
        self.assertNotEqual(other['id'], original['id'])
        # A second startup must retain both bindings and both grant receipts.
        await storage.init_db()
        for owner, expected in ((42, original), (43, other)):
            retry, created = await give_hexpurr(str(owner), granted_by=7)
            self.assertFalse(created)
            self.assertEqual(retry, expected)
        receipts = await self.rows("SELECT * FROM gameplay_grants WHERE grant_key LIKE 'exclusive:hexpurr%'")
        self.assertEqual({row['grant_key'] for row in receipts}, {'exclusive:hexpurr:42', 'exclusive:hexpurr:43'})

    async def test_empty_legacy_database_can_grant_to_multiple_trainers_after_startup(self):
        await self.use_legacy_binding_schema()
        await storage.init_db()
        for owner in (42, 43):
            await storage.get_or_create_player(owner, 'Recipient')
            await self.lead(owner)
            row, created = await give_hexpurr(str(owner), granted_by=7)
            self.assertTrue(created)
            self.assertEqual(row['user_id'], owner)

    async def test_failed_receipt_migration_rolls_back_binding_schema_and_data(self):
        await self.lead()
        row, _ = await give_hexpurr('42', granted_by=7)
        await self.use_legacy_binding_schema()
        bindings = await self.rows('SELECT * FROM exclusive_companions')
        receipts = await self.rows('SELECT * FROM gameplay_grants')
        async with storage.connect() as db:
            execute = db.execute

            def fail_receipt_update(sql, *args, **kwargs):
                if sql.startswith('UPDATE gameplay_grants'):
                    raise RuntimeError('Simulated migration failure')
                return execute(sql, *args, **kwargs)

            with patch.object(db, 'execute', new=fail_receipt_update), self.assertRaisesRegex(RuntimeError, 'Simulated'):
                await migrate_owner_bindings(db)
        columns = await self.rows('PRAGMA table_info(exclusive_companions)')
        self.assertEqual([column['name'] for column in columns if column['pk']], ['species'])
        self.assertEqual(await self.rows('SELECT * FROM exclusive_companions'), bindings)
        self.assertEqual(await self.rows('SELECT * FROM gameplay_grants'), receipts)
        await storage.init_db()
        retry, created = await give_hexpurr('42', granted_by=7)
        self.assertFalse(created)
        self.assertEqual(retry, row)

    async def test_normal_grant_and_transfer_paths_cannot_bypass_exclusivity(self):
        await self.lead()
        with self.assertRaisesRegex(ValueError, 'give_hexpurr'):
            await self.beast('hexpurr')
        row, _ = await give_hexpurr('42', granted_by=7)
        for action in (lambda: transfers.create_listing(42,row['player_number'],100),
                       lambda: transfers.release_companion(42,row['id'],expected_species='hexpurr')):
            with self.assertRaisesRegex(ValueError, 'personal Developer'):
                await action()
        await storage.get_or_create_player(43,'Other')
        with self.assertRaisesRegex(ValueError, 'personal Developer'):
            await transfers.complete_trade(42,43,row['id'],offer_key='hexpurr-test')
        with self.assertRaisesRegex(ValueError, 'give_hexpurr'):
            await storage.add_beast_to_player(42,storage.get_beast_data('hexpurr'),grant_key='exclusive:hexpurr')
        for owner, key in ((42, 'exclusive:hexpurr:42'), (43, 'exclusive:hexpurr:42'), (43, 'exclusive:hexpurr:43')):
            with self.subTest(owner=owner, key=key), self.assertRaisesRegex(ValueError, 'give_hexpurr'):
                await storage.add_beast_to_player(owner, storage.get_beast_data('hexpurr'), grant_key=key)

    async def test_growth_to_cap_stays_legendary_and_existing_developer_growth_is_unchanged(self):
        await self.lead(level=1)
        row,_=await give_hexpurr('42',granted_by=7)
        async with storage.connect() as db:
            await storage.apply_beast_levelup(db,row,50,0)
            await db.commit()
        result=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row['id'],)))[0]
        for stat,amount in rarity_growth('legendary',49).items():
            column={'hp':'max_hp','mana':'max_mana'}.get(stat,stat)
            self.assertEqual(result[column]-row[column],amount)
        self.assertEqual(result['rarity'],'dev')
        self.assertEqual(companion_growth('ouroboros',50,storage.load_beasts()),rarity_growth('dev',49))

    async def test_command_accepts_text_id_without_guild_and_remains_owner_only(self):
        await self.lead()
        click=interaction(7)
        await Dev.give_hexpurr.callback(Dev(None),click,'42')
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertIn('Lv. 13',click.followup.send.call_args.args[0])
        self.assertEqual(Dev.give_hexpurr.parameters[0].type.value,3)  # STRING, not a guild Member transformer.
        with patch.dict(os.environ,{'OWNER_ID':'7'}):
            self.assertTrue(await Dev.give_hexpurr.checks[0](interaction(7)))
            with self.assertRaises(app_commands.CheckFailure):
                await Dev.give_hexpurr.checks[0](interaction(42))
        for bad in ('','abc','-42','42.0','<@42>','0',str(2**63),'４２'):
            with self.subTest(value=bad),self.assertRaises(ValueError):
                discord_user_id(bad)

    async def test_training_and_knockout_use_legendary_rules_behind_developer_badge(self):
        from cogs.economy import Economy
        await self.lead(level=1)
        row,_=await give_hexpurr('42',granted_by=7)
        await storage.update_player(42,gold=2000)
        click=interaction()
        await Economy.train.callback(Economy(None),click,row['player_number'],'attack')
        trained=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row['id'],)))[0]
        self.assertEqual(trained['attack']-row['attack'],3)
        self.assertEqual(trained['train_atk'],1)
        self.assertIn('400g',click.followup.send.call_args.kwargs['embed'].description)
        await storage.knockout_beast(row['id'],'dev')
        knocked=(await self.rows('SELECT * FROM player_beasts WHERE id=?',(row['id'],)))[0]
        self.assertEqual(knocked['hp'],0)
        self.assertEqual(knocked['rarity'],'dev')
        seconds=(datetime.fromisoformat(knocked['knocked_out_until'])-datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(3590 < seconds <= 3600)
        self.assertEqual(storage.gameplay_rarity({'beast_id':'ouroboros','rarity':'dev'}),'dev')


class HexfireCombat(unittest.TestCase):
    def test_transform_spends_mana_once_and_shares_health_gear_and_charges(self):
        cat, enemy = fighters()
        cat.update(hp=630,armor_id='dragon_lord_aegis',ultimate_charges=1)
        original=deepcopy(cat)
        combat.begin_turn(cat,enemy,[])
        combat.resolve_move(cat,enemy,'Lanterns Out',False,[])
        self.assertEqual((cat['hp'],cat['mana'],cat['ultimate_charges']),(630,130,2))
        self.assertEqual((cat['armor_id'],cat['attack'],cat['defense']),(original['armor_id'],original['attack'],original['defense']))
        self.assertTrue(combat.hexfire_active(cat))
        self.assertFalse(combat.can_use(cat,'Lanterns Out'))
        combat.end_turn(cat,enemy,[])
        self.assertEqual(cat['hexfire_turns'],3)
        for remaining in (2,1,0):
            combat.begin_turn(cat,enemy,[])
            combat.end_turn(cat,enemy,[])
            combat.end_turn(cat,enemy,[])  # duplicate end/bonus processing cannot spend another turn.
            self.assertEqual(cat['hexfire_turns'],remaining)
        self.assertFalse(combat.hexfire_active(cat))
        self.assertFalse(combat.can_use(cat,'Lanterns Out'))
        self.assertEqual(combat.effective_defense(cat),original['defense'])

    def test_form_succeeds_on_blocked_hit_and_expires_through_skipped_turns(self):
        cat,enemy=fighters()
        enemy['shield']=100000
        combat.begin_turn(cat,enemy,[])
        self.assertEqual(combat.resolve_move(cat,enemy,'Lanterns Out',False,[]),0)
        self.assertTrue(combat.hexfire_active(cat))
        for _ in range(3):
            cat.update(status='freeze',status_turns=1)
            self.assertFalse(combat.begin_turn(cat,enemy,[]))
            combat.end_turn(cat,enemy,[])
        self.assertFalse(combat.hexfire_active(cat))

    def test_damage_tradeoff_is_bounded_and_does_not_stack(self):
        cat,enemy=fighters()
        with patch.object(combat.random,'random',return_value=.99),patch.object(combat.random,'uniform',return_value=1):
            basic=combat.calc_damage(cat,enemy,'Ember Claw')[0]
            cat['hexfire_turns']=3
            boosted=combat.calc_damage(cat,enemy,'Ember Claw')[0]
            self.assertAlmostEqual(boosted/basic,1.15,delta=.02)
            self.assertEqual(combat.effective_defense(cat),cat['defense']*.9)
            self.assertLessEqual(boosted,basic*1.17)
        cat['hp']=0
        self.assertFalse(combat.hexfire_active(cat))
        self.assertEqual(combat.effective_defense(cat),cat['defense'])

    def test_form_and_once_flag_survive_json_snapshot_and_reentry(self):
        cat,enemy=fighters()
        combat.begin_turn(cat,enemy,[])
        combat.resolve_move(cat,enemy,'Lanterns Out',False,[])
        payload=json.loads(json.dumps(trial_combat._json(cat)))
        combat.start_battle_member(payload,enemy,[])
        self.assertFalse(combat.can_use(payload,'Lanterns Out'))
        before=art.snapshot([payload,enemy])
        self.assertEqual(before[0].form,'hexfire')
        event=trial_combat._encode_event(before,art.Action(0,'Ember Claw'),before)
        decoded=trial_combat.decode_event(json.loads(json.dumps(event)))
        self.assertEqual(decoded[0],before)
        self.assertFalse(combat.hexfire_active(fighters()[0]))


class HexfireRendering(unittest.TestCase):
    def scenes(self, armor=''):
        cat,enemy=fighters()
        cat['armor_id']=armor
        before=art.snapshot([cat,enemy])
        combat.begin_turn(cat,enemy,[])
        damage=combat.resolve_move(cat,enemy,'Lanterns Out',False,[])
        after=art.snapshot([cat,enemy])
        return before,after,art.Action(0,'Lanterns Out','skill',damage)

    def test_all_armor_and_ghost_forms_use_registered_distinct_cutouts(self):
        from utils import armor_art
        for relative,digest in hexpurr_art.manifest()['sha256'].items():
            self.assertEqual(hashlib.sha256((hexpurr_art.ASSETS/relative).read_bytes()).hexdigest(),digest)
        appearances=list(armor_art.manifest()['sets'])+[cosmetics.appearance_id(cosmetics.GHOST)]
        seen=set()
        for appearance in appearances:
            for form in ('','hexfire'):
                sprite=art.sprite('hexpurr',appearance,form)
                self.assertIsNotNone(sprite.getchannel('A').getbbox())
                self.assertLessEqual(max(sprite.size),180)
                digest=hashlib.sha256(sprite.tobytes()).hexdigest()
                self.assertNotIn(digest,seen)
                seen.add(digest)

    def test_action_is_one_shot_and_idle_aura_loops_without_wait_or_state_changes(self):
        before,after,action=self.scenes('dragon_lord_aegis')
        immutable=repr((before,after))
        move=art.render(before,after,'hollowlight',action)
        with Image.open(io.BytesIO(move.data)) as im:
            self.assertNotIn('loop',im.info)
            self.assertEqual(sum(f.info['duration'] for f in ImageSequence.Iterator(im)),1950)
        idle=art.render(after,after,'hollowlight',ambient_start=move.ambient_end,palette=move.palette)
        self.assertEqual(idle.seconds,0)
        self.assertLess(len(idle.data),art.MAX_BYTES)
        with Image.open(io.BytesIO(idle.data)) as im:
            self.assertEqual(im.info['loop'],0)
            self.assertEqual(sum(f.info['duration'] for f in ImageSequence.Iterator(im)),2000)
        a=art.frame(after,after,'hollowlight',ambient_seconds=.25)
        b=art.frame(after,after,'hollowlight',ambient_seconds=2.25)
        c=art.frame(after,after,'hollowlight',ambient_seconds=.75)
        self.assertIsNone(ImageChops.difference(a,b).getbbox())
        self.assertIsNotNone(ImageChops.difference(a,c).getbbox())
        self.assertIsNone(ImageChops.difference(a.crop((0,0,640,208)),c.crop((0,0,640,208))).getbbox())
        finished=(after[0],replace(after[1],hp=0))
        self.assertEqual(art.render(finished,finished,'hollowlight').extension,'png')
        self.assertEqual(repr((before,after)),immutable)

    def test_both_sides_and_parallel_frames_keep_their_own_form_and_armor(self):
        from concurrent.futures import ThreadPoolExecutor
        before,after,action=self.scenes('kelpie_scale_mail')
        jobs=[(before,after,action,t) for t in (.17,.30,.39,.48,.70,1)]
        jobs += [(tuple(reversed(before)),tuple(reversed(after)),replace(action,actor=1),t) for t in (.17,.30,.39,.48,.70,1)]
        def render(job):
            a,b,c,t=job
            return art.frame(a,b,'hollowlight',c,t,ambient_seconds=t*1.95).tobytes()
        expected=[render(j) for j in jobs]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(render,jobs)),expected)

    def test_raid_move_and_idle_keep_costume_with_aura_and_expire_on_skipped_turns(self):
        from cogs.guilds import RAID_BOSSES
        from scripts.preview_raid_arena import make_raid, capture
        from utils import raid_art
        raid=make_raid(next(iter(RAID_BOSSES.values()))[0],10)
        cat=fighters()[0]
        cat['armor_id']=cosmetics.appearance_id(cosmetics.GHOST)
        raid.party[0][0]=cat
        before=capture(raid)
        raid.resolve_action(0,'skill')
        after=capture(raid)
        clip=raid_art.render(before,after,tuple(raid._art_events))
        self.assertLess(len(clip.data),art.MAX_BYTES)
        with Image.open(io.BytesIO(clip.data)) as im:
            self.assertEqual(im.size,(640,692))
            self.assertNotIn('loop',im.info)
        idle=raid_art.render(after,after,ambient_start=clip.ambient_end,palette=clip.palette)
        with Image.open(io.BytesIO(idle.data)) as im:
            self.assertEqual(im.info['loop'],0)
        for _ in range(3):
            raid.last.clear()
            cat.update(status='freeze',status_turns=1)
            raid.resolve_action(0,'attack')
        self.assertEqual(cat['hexfire_turns'],0)
        self.assertFalse(combat.can_use(cat,'Lanterns Out'))
