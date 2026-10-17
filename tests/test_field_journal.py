"""Regressions for the approved UI's fitted numbers and redundant-text cleanup."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image, ImageChops

from cogs.battle import build_pve_beast_state
from tests import test_gameplay as fixtures
from utils import battle_art, db as storage, field_journal as ui, raid_art


def contains(outer, inner):
    return outer[0] <= inner[0] <= inner[2] <= outer[2] and outer[1] <= inner[1] <= inner[3] <= outer[3]


class FieldJournalImages(unittest.TestCase):
    def test_hp_and_mana_ink_fits_every_species_at_early_and_late_levels(self):
        image = Image.new('RGBA', (640, 528))
        for species in storage.load_beasts():
            for level in (6, 50):
                state = build_pve_beast_state(storage.get_beast_data(species), level)
                fighter = battle_art.snapshot([state])[0]
                health, mana, ult = ui.fighter_panel(image, fighter, None, 10, fighter.hp, fighter.mana, fighter)
                for meter in (health, mana):
                    self.assertTrue(contains(meter['content'], meter['text'].ink), (species, level, meter))
                    self.assertNotIn('~', meter['text'].value)
                    track, ink = meter['track'], meter['text'].ink
                    self.assertLessEqual(abs((track[1] + track[3]) / 2 - (ink[1] + ink[3]) / 2), .5)
                self.assertLessEqual(ult['label_text'].ink[2] + 8, ult['orbs'][0][0])

    def test_empty_full_and_large_values_keep_padding_and_complete_numbers(self):
        image = Image.new('RGBA', (640, 180))
        for value, maximum in ((0, 1), (1, 1), (9, 999), (1234567, 9876543), (10**18 - 1, 10**18)):
            for box in ((20, 20, 232, 50), (20, 70, 620, 94)):
                meter = ui.resource_bar(image, box, value, maximum, 'HP', ui.HEALTH)
                self.assertTrue(contains(meter['content'], meter['text'].ink), meter)
                self.assertNotIn('~', meter['text'].value)
                self.assertIn('/', meter['text'].value)
                if maximum <= 9876543:
                    self.assertEqual(meter['text'].value, f'{value:,} / {maximum:,}')

    def test_ult_label_orbs_and_count_have_separate_ink_at_every_charge(self):
        for x in (88, 408):
            for charges in range(4):
                image = Image.new('RGBA', (640, 220))
                parts = ui.ultimate(image, x, 178, charges)
                self.assertTrue(contains(parts['label'], parts['label_text'].ink))
                self.assertTrue(contains(parts['count'], parts['count_text'].ink))
                self.assertGreaterEqual(parts['orbs'][0][0] - parts['label_text'].ink[2], 8)
                self.assertGreaterEqual(parts['count_text'].ink[0] - parts['orbs'][-1][2], 8)
                for one, two in zip(parts['orbs'], parts['orbs'][1:]):
                    self.assertGreater(two[0] - one[2], 4)

    def test_real_log_appears_at_impact_and_journal_stays_below_characters(self):
        states = [build_pve_beast_state(storage.get_beast_data(key), 6) for key in ('twine', 'wisp')]
        before = battle_art.snapshot(states)
        states[1]['hp'] -= 20
        after = battle_art.snapshot(states)
        action = battle_art.Action(0, 'Tick Bite', damage=20, journal=('Twine uses Tick Bite: 20 damage.',))
        top = battle_art.HUD + battle_art.HEIGHT
        journal = (0, top, 640, battle_art.TOTAL_HEIGHT)
        first = battle_art.frame(before, after, 'woods', action, 0)
        windup = battle_art.frame(before, after, 'woods', action, .2)
        hit = battle_art.frame(before, after, 'woods', action, .8)
        self.assertIsNone(ImageChops.difference(first.crop(journal), windup.crop(journal)).getbbox())
        self.assertIsNotNone(ImageChops.difference(first.crop(journal), hit.crop(journal)).getbbox())
        before_ui = first.copy()
        ui.journal(first, top, 'Long log', ('g' * 500, '雪' * 500))
        self.assertIsNone(ImageChops.difference(first.crop((0, 0, 640, top)), before_ui.crop((0, 0, 640, top))).getbbox())


class FieldJournalPanels(unittest.IsolatedAsyncioTestCase):
    def embed(self):
        embed = discord.Embed(title='Battle', description='Full HP/MP text fallback')
        embed.add_field(name='Last actions', value='A repeated post-fight log')
        embed.add_field(name='Battle complete', value='Twine wins.')
        return embed

    def fighters(self):
        return battle_art.snapshot([build_pve_beast_state(storage.get_beast_data(key), 6) for key in ('twine', 'wisp')])

    async def test_visual_close_has_one_result_and_no_duplicate_log_or_stats(self):
        presentation = battle_art.BattlePresentation('woods', sleep=AsyncMock())
        original = self.embed()
        message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock(return_value=message)))
        view = SimpleNamespace(_closed=True, message=None)
        saved = self.fighters()
        with patch.object(presentation, 'art', new=AsyncMock(return_value=battle_art.Artwork(b'image', 'png'))):
            await presentation.publish(interaction, None, original, view, saved, saved)
        shown = interaction.channel.send.call_args.kwargs['embed']
        self.assertIsNone(shown.description)
        self.assertEqual([field.name for field in shown.fields], ['Battle complete'])
        self.assertEqual([field.name for field in original.fields], ['Last actions', 'Battle complete'])

    async def test_rejected_visual_restores_complete_text_including_fallback_log(self):
        presentation = battle_art.BattlePresentation('woods', sleep=AsyncMock())
        error = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'message': 'Missing Attach Files'})
        attempts = []

        async def send(**kwargs):
            attempts.append(kwargs['embed'].copy())
            if kwargs.get('file'):
                raise error
            return SimpleNamespace(edit=AsyncMock())

        interaction = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock(side_effect=send)))
        saved = self.fighters()
        with patch.object(presentation, 'art', new=AsyncMock(return_value=battle_art.Artwork(b'image', 'png'))), self.assertLogs('chibibeasts.battle.art', level='WARNING'):
            await presentation.publish(interaction, None, self.embed(), SimpleNamespace(_closed=True), saved, saved)
        self.assertIsNone(attempts[0].description)
        self.assertEqual(attempts[1].description, 'Full HP/MP text fallback')
        self.assertIn('Last actions', [field.name for field in attempts[1].fields])
        self.assertFalse(attempts[1].image.url)

    async def test_journal_persists_into_next_prompt_without_repeating_below_image(self):
        presentation = battle_art.BattlePresentation('woods')
        saved = self.fighters()
        action = battle_art.Action(0, 'Tick Bite', journal=('A real engine result.',))
        with patch.object(battle_art, 'render_async', new=AsyncMock(return_value=battle_art.Artwork(b'image', 'png'))) as render:
            await presentation.art(saved, saved, action)
            await presentation.art(saved, saved)
        self.assertEqual(render.call_args.kwargs['journal'], action.journal)

    async def test_raid_preserves_live_resources_and_warning_but_cleans_closed_panel(self):
        presentation = raid_art.RaidPresentation()
        embed = discord.Embed(description='Duplicate boss HP/time\n\n⚠️ Vent the flame\nWeave: 0/2')
        embed.add_field(name='Active companions', value='Actual trainer HP/MP and contributions')
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())))
        artwork = battle_art.Artwork(b'image', 'png')
        await presentation.publish(channel, None, embed, object(), artwork=artwork)
        active = channel.send.call_args.kwargs['embed']
        self.assertTrue(active.description.startswith('⚠️'))
        self.assertEqual(len(active.fields), 1)
        await presentation.publish(channel, None, embed, None, artwork=artwork)
        closed = channel.send.call_args.kwargs['embed']
        self.assertIsNone(closed.description)
        self.assertEqual(closed.fields, [])


class RewardSummary(fixtures.DatabaseCase):
    async def test_pve_followup_keeps_rewards_without_repeating_hp_or_battle_log(self):
        from cogs.battle import _run_pve_battle
        await self.beast('twine', starter=True)
        row = dict((await storage.get_player_beasts(42))[0])
        data = storage.get_beast_data('twine')
        player = build_pve_beast_state(data, 6)
        enemy = build_pve_beast_state(storage.get_beast_data('wisp'), 7)
        enemy['hp'] = 0
        interaction = fixtures.interaction()

        async def rewards(embed, *_):
            embed.add_field(name='Rewards', value='Gold and companion EXP')

        win = AsyncMock(side_effect=rewards)
        loss = AsyncMock()
        with patch('utils.duels.prepare_state', new=AsyncMock(return_value=player)), patch('utils.duels.duel', new=AsyncMock(return_value=(0, [player, enemy], ['A combat log already shown in the journal.'], False))):
            await _run_pve_battle(interaction, row, data, [], enemy, 'wild', 'Explore', win, loss)
        summary = interaction.channel.send.call_args.kwargs['embed']
        self.assertIsNone(summary.description)
        self.assertEqual([field.name for field in summary.fields], ['Rewards'])
        win.assert_awaited_once()
        loss.assert_not_awaited()
        self.assertEqual(len(await self.rows("SELECT * FROM battles WHERE status='completed'")), 1)
