"""Interactive ranked duels, dated seasons and durable results."""
from datetime import datetime, timezone, timedelta
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from utils import db as storage
from utils.theme import COLORS
RANKS = [('unranked', 0, None, '⬜', 'Finish five placements.'), ('bronze', 0, 999, '🟫', 'A beginning worth keeping.'), ('silver', 1000, 1249, '⬜', 'Finding your rhythm.'), ('gold', 1250, 1499, '🟨', 'A practiced partnership.'), ('platinum', 1500, 1749, '🩵', 'Reading the weave.'), ('diamond', 1750, 99999, '💎', 'A bond worth watching.')]
RANK_EMOJI = {r[0]: r[3] for r in RANKS}
PLACEMENT_COUNT = 5
SEASON_START = datetime(2026, 1, 5, tzinfo=timezone.utc)
pending_users = set()

def current_season(now=None):
    return max(1, ((now or datetime.now(timezone.utc)) - SEASON_START).days // 28 + 1)

def get_rank_for_rating(rating, placements_done):
    if placements_done < PLACEMENT_COUNT:
        return 'unranked'
    return next((r[0] for r in RANKS[1:] if r[1] <= max(0, rating) <= r[2]), 'diamond')

def calc_elo(winner_rating, loser_rating, k=32):
    expected = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    delta = max(8, int(k * (1 - expected)))
    return (winner_rating + delta, max(0, loser_rating - delta), delta)
from utils.rewards import shards

async def ensure_season(now=None):
    season = current_season(now)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute("SELECT value FROM game_meta WHERE key='ranked_season'") as c:
            stored = await c.fetchone()
        if stored and int(stored[0]) == season:
            return season
        async with db.execute('SELECT * FROM players WHERE pvp_wins+pvp_losses>=5') as c:
            players = await c.fetchall()
        for player in players:
            old = player['pvp_season']
            if old >= season:
                continue
            cursor = await db.execute('INSERT OR IGNORE INTO ranked_history VALUES(?,?,?,?,?)', (old, player['user_id'], player['pvp_rating'], player['pvp_wins'], player['pvp_losses']))
            if cursor.rowcount:
                rank = get_rank_for_rating(player['pvp_rating'], 5)
                reward = {'bronze': 5, 'silver': 10, 'gold': 20, 'platinum': 35, 'diamond': 50}[rank]
                await shards(db, player['user_id'], reward)
        await db.execute("UPDATE players SET pvp_rating=1000,pvp_rank='unranked',pvp_wins=0,pvp_losses=0,pvp_season=? WHERE pvp_season<?", (season, season))
        await db.execute("INSERT INTO game_meta VALUES('ranked_season',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(season),))
        await db.commit()
    return season

async def can_rate_pair(a, b):
    async with storage.connect() as db:
        async with db.execute("SELECT COUNT(*),MAX(played_at) FROM ranked_matches WHERE ((winner_id=? AND loser_id=?) OR (winner_id=? AND loser_id=?)) AND date(played_at)=date('now')", (a, b, b, a)) as c:
            count, last = await c.fetchone()
    if count >= 5:
        raise ValueError('You have played five rated matches with this opponent today. Try another trainer or /battle.')
    if last and datetime.now(timezone.utc) - datetime.strptime(last, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) < timedelta(minutes=5):
        raise ValueError('Wait five minutes before another rated match with the same trainer.')

async def record_result(match_id, winner_id, loser_id):
    season = await ensure_season()
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('INSERT OR IGNORE INTO ranked_matches(match_id,season,winner_id,loser_id) VALUES(?,?,?,?)', (match_id, season, winner_id, loser_id))
        if not cur.rowcount:
            return None
        async with db.execute('SELECT * FROM players WHERE user_id=?', (winner_id,)) as c:
            w = dict(await c.fetchone())
        async with db.execute('SELECT * FROM players WHERE user_id=?', (loser_id,)) as c:
            l = dict(await c.fetchone())
        wr, lr, delta = calc_elo(w['pvp_rating'], l['pvp_rating'])
        w_rank = get_rank_for_rating(wr, w['pvp_wins'] + w['pvp_losses'] + 1)
        l_rank = get_rank_for_rating(lr, l['pvp_wins'] + l['pvp_losses'] + 1)
        await db.execute('UPDATE players SET pvp_rating=?,pvp_rank=?,pvp_wins=pvp_wins+1,pvp_season=? WHERE user_id=?', (wr, w_rank, season, winner_id))
        await db.execute('UPDATE players SET pvp_rating=?,pvp_rank=?,pvp_losses=pvp_losses+1,pvp_season=? WHERE user_id=?', (lr, l_rank, season, loser_id))
        await db.commit()
    from utils.progress import unlock_simple_achievement, check_achievements
    if w['pvp_wins'] == 0:
        await unlock_simple_achievement(winner_id, 'first_ranked_win')
    await check_achievements(winner_id)
    return (wr, lr, delta, w_rank, l_rank)

class Ranked(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='ranked', description='An interactive ranked duel using your full build 🏅')
    async def ranked(self, interaction: discord.Interaction, opponent: discord.Member):
        await interaction.response.defer()
        ids = (interaction.user.id, opponent.id)
        if opponent.bot or ids[0] == ids[1]:
            return await interaction.followup.send('Choose another human trainer.')
        from utils.duels import active_fighters
        if any((uid in pending_users or uid in active_fighters for uid in ids)):
            return await interaction.followup.send('A trainer already has a battle or ranked invitation open.')
        for user in (interaction.user, opponent):
            await storage.get_or_create_player(user.id, str(user))
            beast = await storage.get_active_beast(user.id)
            if not beast or storage.is_knocked_out(beast):
                return await interaction.followup.send('Both trainers need a healthy active companion.')
        await ensure_season()
        try:
            await can_rate_pair(*ids)
        except ValueError as error:
            return await interaction.followup.send(str(error))
        pending_users.update(ids)
        accepted = False

        class Invitation(discord.ui.View):

            def __init__(self):
                super().__init__(timeout=90)

            @discord.ui.button(label='Accept ranked duel', style=discord.ButtonStyle.success)
            async def accept(self, bi, button):
                nonlocal accepted
                if bi.user.id != opponent.id:
                    return await bi.response.send_message('Only the challenged trainer can accept.', ephemeral=True)
                accepted = True
                self.stop()
                await bi.response.edit_message(view=None)

            @discord.ui.button(label='Decline', style=discord.ButtonStyle.secondary)
            async def decline(self, bi, button):
                if bi.user.id not in ids:
                    return await bi.response.send_message("This is another trainer's invitation.", ephemeral=True)
                self.stop()
                await bi.response.edit_message(content='The invitation is closed.', view=None)
        view = Invitation()
        try:
            message = await interaction.followup.send(f'{opponent.mention}, {interaction.user.display_name} invites you to ranked. Gear, moves, passives and items use the same rules as /battle.', view=view)
            await view.wait()
            if not accepted:
                await message.edit(content='Ranked invitation closed.', view=None)
                return
            await can_rate_pair(*ids)
            from cogs.battle import pending_battles, start_battle
            pending_battles[interaction.id] = {'challenger_id': ids[0], 'opponent_id': ids[1], 'ranked': True}
            winner = await start_battle(interaction, interaction.id)
            if winner is None:
                return await interaction.followup.send('A draw leaves both ratings unchanged.')
            loser = ids[1] if winner == ids[0] else ids[0]
            result = await record_result(interaction.id, winner, loser)
            if result:
                wr, lr, delta, rank, other_rank = result
                await interaction.followup.send(f'🏅 <@{winner}>: {wr} RP ({rank}, +{delta}) · <@{loser}>: {lr} RP ({other_rank}). Season {current_season()}.')
        except ValueError as error:
            await interaction.followup.send(str(error))
        finally:
            pending_users.difference_update(ids)

    @app_commands.command(name='ranked_leaderboard', description='Global ranked standings for the current four-week season 🏆')
    async def ranked_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        season = await ensure_season()
        async with storage.connect() as db:
            async with db.execute('SELECT username,pvp_rating,pvp_rank,pvp_wins,pvp_losses FROM players WHERE pvp_wins+pvp_losses>=5 ORDER BY pvp_rating DESC LIMIT 15') as c:
                rows = await c.fetchall()
        text = '\n'.join((f'{i + 1}. **{name}** · {rating} RP · {rank} · {wins}W/{losses}L' for i, (name, rating, rank, wins, losses) in enumerate(rows))) or 'Five placements put you on the board. Try /ranked.'
        await interaction.followup.send(embed=discord.Embed(title=f'Global ranked · Season {season}', description=text, color=COLORS['legendary']))

    @app_commands.command(name='rank', description='Your rating, placements and next season reset 📊')
    async def rank(self, interaction: discord.Interaction, player: discord.Member=None):
        await interaction.response.defer()
        season = await ensure_season()
        target = player or interaction.user
        data = await storage.get_or_create_player(target.id, str(target))
        total = data['pvp_wins'] + data['pvp_losses']
        reset = SEASON_START + timedelta(days=season * 28)
        await interaction.followup.send(embed=discord.Embed(title=f"{target.display_name} · {data['pvp_rank'].title()}", description=f"{data['pvp_rating']} RP · {data['pvp_wins']}W/{data['pvp_losses']}L\nPlacements remaining: {max(0, 5 - total)}\nSeason {season} ends <t:{int(reset.timestamp())}:R>.\nCompleted placements earn 5–50 shards at reset; past results are preserved.", color=COLORS['info']))

async def setup(bot):
    await bot.add_cog(Ranked(bot))
