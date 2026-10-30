"""Seasonal additions cannot make permanent collection goals unattainable."""
from unittest.mock import patch

from tests.test_gameplay import DatabaseCase
from utils import db as storage, progress


class PermanentCollection(DatabaseCase):
    async def test_complete_record_requires_permanent_species_without_seasonal_hatches(self):
        await self.beast('fairy')
        catalog = {key: storage.get_beast_data(key) for key in
                   ('fairy', 'patchkin', 'wickwing', 'veilmoth', 'hollow_king')}
        with patch.object(storage, 'load_beasts', return_value=catalog):
            unlocked = await progress.check_achievements(42)
            self.assertIn('collector_all', unlocked)
            # Neither a refresh nor the event closing pays the completion again.
            self.assertNotIn('collector_all', await progress.check_achievements(42))

    async def test_owning_the_event_set_does_not_replace_missing_permanent_beasts(self):
        for key in ('patchkin', 'wickwing', 'veilmoth'):
            await self.beast(key)
        catalog = {key: storage.get_beast_data(key) for key in
                   ('fairy', 'patchkin', 'wickwing', 'veilmoth', 'hollow_king')}
        with patch.object(storage, 'load_beasts', return_value=catalog):
            self.assertNotIn('collector_all', await progress.check_achievements(42))
