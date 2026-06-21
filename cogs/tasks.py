import discord
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timezone, time

DB_PATH = "db/chibibeast.db"

# Happiness decay rates (per day).
# Decay timeline without any care:  100 → 0 in ~33 days (active) or ~20 days (benched)
# The /play command gives +15 to the active beast once/day — fully offsets active decay.
# Fairy Garden gives +5 to benched beasts on /daily — fully offsets benched decay.
# Brambleberries (30g, +10) and Sugarsprout Cupcakes (120g, +30) are the shop remedies.
# The battle penalty kicks in at ≤30 happiness (-10% stats), so neglected beasts
# feel meaningfully weaker but not immediately — players have ~2 weeks before it bites.
HAPPINESS_DECAY_ACTIVE  = 3   # active beast: /play fully covers this
HAPPINESS_DECAY_BENCHED = 5   # benched: Fairy Garden fully covers this


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
