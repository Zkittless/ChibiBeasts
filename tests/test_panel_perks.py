import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from tests.test_gameplay import DatabaseCase
from tests.test_interactions import component
from utils import db as storage
from utils.perks_view import PerksView, activate_astral


class PerkPanel(DatabaseCase):
    async def ready(self):
        await storage.update_player(42, level=30)
        await self.execute("INSERT INTO player_perks(user_id,perk_id,equipped) VALUES(42,'bramble_walker',0)")
        view = PerksView(42)
        await view.load()
        view.render()
        self.addCleanup(view.stop)
        return view

    async def press(self, view, label, uid=42):
        button = next(item for item in view.children if getattr(item, 'label', '') == label)
        click = component(uid=uid)
        await button.callback(click)
        return click

    async def test_build_actions_apply_to_the_selected_owned_perk(self):
        view = await self.ready()
        await self.press(view, 'Equip')
        self.assertTrue(view.owned['bramble_walker'])
        await self.press(view, 'Unequip')
        self.assertFalse(view.owned['bramble_walker'])

    async def test_stale_and_foreign_controls_cannot_change_the_build(self):
        view = await self.ready()
        old = next(item.callback for item in view.children if getattr(item, 'label', '') == 'Equip')
        await self.press(view, 'Refresh')
        stale = component()
        await old(stale)
        stale.followup.send.assert_awaited_once()
        self.assertFalse(view.owned['bramble_walker'])
        foreign = await self.press(view, 'Equip', uid=43)
        foreign.response.send_message.assert_awaited_once()
        self.assertFalse(view.owned['bramble_walker'])

    async def test_missing_perk_is_rechecked_at_confirmation(self):
        view = await self.ready()
        await self.execute('DELETE FROM player_perks')
        await self.press(view, 'Equip')
        self.assertIn('Earn this perk', view.notice)
        self.assertFalse(view.owned)

    async def test_weekly_activation_is_atomic_and_failed_attempt_does_not_spend_it(self):
        await self.perk('astral_resonance')
        with self.assertRaises(ValueError):
            await activate_astral(42)
        self.assertFalse(json.loads((await storage.get_player(42))['shard_shop_week'] or '{}').get('astral_resonance_used'))
        now = datetime.now(timezone.utc)
        fmt = lambda dt: dt.strftime('%Y-%m-%d %H:%M:%S')
        await self.execute('INSERT INTO incubating_eggs(user_id,egg_type,egg_name,rarity,ready_at,next_tend_at) VALUES(42,?,?,?,?,?)',
            ('common_incubation_egg', 'Test egg', 'common', fmt(now + timedelta(hours=12)), fmt(now + timedelta(hours=8))))
        results = await asyncio.gather(activate_astral(42), activate_astral(42), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, str) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        ready = datetime.strptime((await self.rows('SELECT ready_at FROM incubating_eggs'))[0]['ready_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((ready - now).total_seconds(), 6 * 3600, delta=2)
