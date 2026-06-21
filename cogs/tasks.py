import discord
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timezone, time

DB_PATH = "db/chibibeast.db"

# Happiness decays by this amount each day per beast.
# The Fairy Garden counters it (+2 per benched beast on /daily).
# At full decay a beast goes from 100 → 0 in 20 days of neglect.
# At 30 happiness the battle penalty kicks in (-10% stats).
# A player who claims /daily + has Fairy Garden stays net positive or neutral.
HAPPINESS_DECAY_ACTIVE = 3   # active beast loses 3/day (used in battle, still needs care)
HAPPINESS_DECAY_BENCHED = 5  # benched beasts lose more — they're being ignored


class Tasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.happiness_decay.start()

    def cog_unload(self):
        self.happiness_decay.cancel()

    @tasks.loop(time=time(hour=0, minute=0, tzinfo=timezone.utc))
    async def happiness_decay(self):
        """
        Runs at midnight UTC every day.
        Decays happiness for all beasts — active beasts lose less since they're
        engaged in battles, benched beasts lose more since they're being ignored.
        Clamped to a minimum of 0.
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Active beasts: smaller decay
                await db.execute("""
                    UPDATE player_beasts
                    SET happiness = MAX(0, happiness - ?)
                    WHERE is_active = 1
                """, (HAPPINESS_DECAY_ACTIVE,))
                # Benched beasts: larger decay
                await db.execute("""
                    UPDATE player_beasts
                    SET happiness = MAX(0, happiness - ?)
                    WHERE is_active = 0
                """, (HAPPINESS_DECAY_BENCHED,))
                await db.commit()
        except Exception as e:
            import logging
            logging.getLogger("chibibeasts.tasks").exception("Happiness decay task failed", exc_info=e)

    @happiness_decay.before_loop
    async def before_decay(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Tasks(bot))
