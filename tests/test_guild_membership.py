"""Exercise real guild confirmations after the state behind them changes."""
import asyncio
import sqlite3
from unittest.mock import AsyncMock, patch

from tests.test_gameplay import DatabaseCase, interaction
from cogs.guilds import Guilds
from utils import db as storage


def click(uid):
    value = interaction(uid)
    value.user.mention = f'<@{uid}>'
    value.user.bot = False
    return value


class GuildMembership(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for uid in (43, 44, 45):
            await storage.get_or_create_player(uid, f'Trainer {uid}')
        for name in ('unlock_simple_achievement', 'notify_unlocks'):
            mock = patch('cogs.guilds.' + name, AsyncMock(return_value=False))
            mock.start()
            self.patches.append(mock)
        self.cog = Guilds(None)

    async def guild(self, gid=1, leader=42, capacity=10):
        await self.execute('INSERT INTO guilds(id,name,leader_id,max_members) VALUES(?,?,?,?)',
                           (gid, f'Guild {gid}', leader, capacity))
        await self.member(gid, leader, 'leader')

    async def member(self, gid, uid, rank='member'):
        await self.execute('INSERT INTO guild_members(guild_id,user_id,rank) VALUES(?,?,?)', (gid, uid, rank))
        await self.execute('UPDATE players SET guild_id=? WHERE user_id=?', (gid, uid))
        await self.execute('UPDATE guilds SET member_count=(SELECT COUNT(*) FROM guild_members WHERE guild_id=?) WHERE id=?', (gid, gid))

    async def invite(self, inviter, recipient):
        request = click(inviter)
        await Guilds.guild_invite.callback(self.cog, request, click(recipient).user)
        return request.followup.send.call_args.kwargs['view']

    async def leave(self, uid):
        request = click(uid)
        await Guilds.guild_leave.callback(self.cog, request)
        return request.followup.send.call_args.kwargs['view']

    async def press(self, view, uid, label):
        request = click(uid)
        button = next(item for item in view.children if item.label == label)
        await button.callback(request)
        return request

    async def test_two_pending_invites_cannot_join_two_guilds(self):
        await self.guild()
        await self.guild(2, 43)
        first, second = await self.invite(42, 44), await self.invite(43, 44)
        await asyncio.gather(self.press(first, 44, 'Accept'), self.press(second, 44, 'Accept'))
        memberships = await self.rows('SELECT guild_id FROM guild_members WHERE user_id=44')
        self.assertEqual(len(memberships), 1)
        self.assertEqual((await storage.get_player(44))['guild_id'], memberships[0]['guild_id'])

    async def test_last_slot_is_checked_when_invites_are_accepted(self):
        await self.guild(capacity=2)
        first, second = await self.invite(42, 43), await self.invite(42, 44)
        await asyncio.gather(self.press(first, 43, 'Accept'), self.press(second, 44, 'Accept'))
        self.assertEqual(len(await self.rows('SELECT * FROM guild_members WHERE guild_id=1')), 2)
        self.assertEqual((await self.rows('SELECT member_count FROM guilds'))[0]['member_count'], 2)

    async def test_invite_cannot_recreate_membership_in_disbanded_guild(self):
        await self.guild()
        invitation = await self.invite(42, 43)
        await self.press(await self.leave(42), 42, 'Disband Guild')
        await self.press(invitation, 43, 'Accept')
        self.assertEqual(await self.rows('SELECT * FROM guild_members'), [])
        self.assertIsNone((await storage.get_player(43))['guild_id'])

    async def test_demoted_officers_old_invite_cannot_add_members(self):
        await self.guild()
        await self.member(1, 43, 'officer')
        invitation = await self.invite(43, 44)
        await self.execute("UPDATE guild_members SET rank='member' WHERE user_id=43")
        await self.press(invitation, 44, 'Accept')
        self.assertEqual(await self.rows('SELECT * FROM guild_members WHERE user_id=44'), [])

    async def test_disband_prompt_cannot_delete_a_guild_that_gained_members(self):
        await self.guild()
        prompt = await self.leave(42)
        await self.member(1, 43)
        await self.press(prompt, 42, 'Disband Guild')
        self.assertEqual(len(await self.rows('SELECT * FROM guilds')), 1)
        self.assertEqual(len(await self.rows('SELECT * FROM guild_members')), 2)

    async def test_departed_successor_cannot_inherit_leadership(self):
        await self.guild()
        await self.member(1, 43, 'officer')
        await self.member(1, 44)
        prompt = await self.leave(42)
        await self.press(await self.leave(43), 43, 'Leave Guild')
        await self.press(prompt, 42, 'Transfer & Leave')
        guild = (await self.rows('SELECT * FROM guilds'))[0]
        self.assertEqual(guild['leader_id'], 42)
        self.assertEqual((await self.rows("SELECT user_id FROM guild_members WHERE rank='leader'"))[0]['user_id'], 42)

    async def test_replayed_leave_cannot_clear_a_new_membership(self):
        await self.guild()
        await self.guild(2, 44)
        await self.member(1, 43)
        prompt = await self.leave(43)
        await self.press(prompt, 43, 'Leave Guild')
        await self.member(2, 43)
        await self.press(prompt, 43, 'Leave Guild')
        self.assertEqual((await storage.get_player(43))['guild_id'], 2)
        self.assertEqual((await self.rows('SELECT member_count FROM guilds WHERE id=1'))[0]['member_count'], 1)

    async def test_old_leave_prompt_cannot_remove_a_new_leader(self):
        await self.guild()
        await self.member(1, 43)
        stale = await self.leave(43)
        await self.press(await self.leave(42), 42, 'Transfer & Leave')
        await self.press(stale, 43, 'Leave Guild')
        self.assertEqual((await self.rows("SELECT user_id FROM guild_members WHERE rank='leader'"))[0]['user_id'], 43)
        self.assertEqual((await storage.get_player(43))['guild_id'], 1)

    async def test_duplicate_accepts_and_other_trainers_cannot_repeat_membership(self):
        await self.guild()
        prompt = await self.invite(42, 43)
        await self.press(prompt, 44, 'Accept')
        self.assertFalse(prompt.is_finished())
        await asyncio.gather(*(self.press(prompt, 43, 'Accept') for _ in range(5)))
        self.assertEqual(len(await self.rows('SELECT * FROM guild_members')), 2)
        self.assertEqual((await self.rows('SELECT member_count FROM guilds'))[0]['member_count'], 2)

    async def test_unregistered_trainer_cannot_get_an_orphan_membership(self):
        await self.guild()
        request = click(42)
        await Guilds.guild_invite.callback(self.cog, request, click(999).user)
        self.assertNotIn('view', request.followup.send.call_args.kwargs)
        self.assertIn('/start', request.followup.send.call_args.args[0])
        self.assertEqual(await self.rows('SELECT * FROM guild_members WHERE user_id=999'), [])

    async def test_accept_and_leadership_transfer_roll_back_if_player_update_fails(self):
        from utils import guild_membership
        await self.guild()
        await self.execute("CREATE TRIGGER reject_membership BEFORE UPDATE OF guild_id ON players "
                           "BEGIN SELECT RAISE(ABORT,'synthetic persistence failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await guild_membership.accept_invitation(42, 43, 1)
        self.assertEqual(len(await self.rows('SELECT * FROM guild_members')), 1)
        await self.execute('DROP TRIGGER reject_membership')
        await self.member(1, 43)
        await self.execute("CREATE TRIGGER reject_membership BEFORE UPDATE OF guild_id ON players "
                           "BEGIN SELECT RAISE(ABORT,'synthetic persistence failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await guild_membership.leave(42, 1, successor_id=43)
        self.assertEqual((await self.rows('SELECT leader_id,member_count FROM guilds'))[0],
                         {'leader_id': 42, 'member_count': 2})
        self.assertEqual((await self.rows("SELECT user_id FROM guild_members WHERE rank='leader'"))[0]['user_id'], 42)

    async def test_valid_transfer_and_disband_clean_membership_and_sanctuary(self):
        await self.guild()
        await self.member(1, 43)
        await self.execute('INSERT INTO guild_sanctuary(guild_id) VALUES(1)')
        await self.press(await self.leave(42), 42, 'Transfer & Leave')
        self.assertIsNone((await storage.get_player(42))['guild_id'])
        self.assertEqual((await self.rows('SELECT leader_id,member_count FROM guilds'))[0],
                         {'leader_id': 43, 'member_count': 1})
        await self.press(await self.leave(43), 43, 'Disband Guild')
        self.assertEqual(await self.rows('SELECT * FROM guilds'), [])
        self.assertEqual(await self.rows('SELECT * FROM guild_sanctuary'), [])
        self.assertEqual(await self.rows('SELECT * FROM guild_members'), [])
        self.assertIsNone((await storage.get_player(43))['guild_id'])

    async def test_rejected_promotion_releases_database_before_discord_reply(self):
        await self.guild()
        await self.member(1, 43)
        async def send(*args, **kwargs):
            # Discord can be slow; unrelated trainer writes must remain free.
            async with storage.connect(timeout=.05) as db:
                await db.execute('UPDATE players SET gold=gold+1 WHERE user_id=45')
                await db.commit()
        for caller, target in ((43, 44), (42, 44), (42, 42), (42, 43)):
            with self.subTest(caller=caller, target=target):
                request = click(caller)
                request.followup.send.side_effect = send
                await Guilds.guild_promote.callback(self.cog, request, click(target).user, 'member')
        self.assertEqual((await storage.get_player(45))['gold'], 504)

    async def test_only_current_leader_can_promote_and_demote_same_guild_member(self):
        await self.guild()
        await self.member(1, 43)
        for rank in ('officer', 'member'):
            await Guilds.guild_promote.callback(self.cog, click(42), click(43).user, rank)
            self.assertEqual((await self.rows('SELECT rank FROM guild_members WHERE user_id=43'))[0]['rank'], rank)
        await Guilds.guild_promote.callback(self.cog, click(44), click(43).user, 'officer')
        self.assertEqual((await self.rows('SELECT rank FROM guild_members WHERE user_id=43'))[0]['rank'], 'member')
