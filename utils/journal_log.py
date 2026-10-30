"""Read-only, owner-scoped expedition recaps for the Field Journal."""
from datetime import datetime, timezone

import aiosqlite
import discord

from utils import db as storage, expeditions, hollowlight, journal_history
from utils.catalog import load_catalog
from utils.habitats import BIOMES

PAGE_SIZE = 3
OUTCOMES = {
    'won': 'Victory', 'escaped': 'Creature escaped', 'interrupted': 'Battle interrupted',
    'befriended': 'Befriended', 'observed': 'Observed',
}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def destination_name(trip):
    return '🎃 The Hollowlight Carnival' if trip['payload'].get('event_id') == hollowlight.EVENT_ID else BIOMES[trip['realm']]['name']


def summary(trip):
    payload = trip['payload']
    status = 'Returned' if trip['status'] == 'complete' else 'Battle in progress' if trip['status'] == 'battle' else 'On the trail'
    outcome = OUTCOMES.get(payload.get('encounter_result'), 'No battle recorded')
    return status + ' · ' + outcome


async def load(view):
    if view.log_kind != 'expeditions':
        view.log_total, view.log_page, view.log_entries = await journal_history.load(view.user_id, view.log_kind, view.log_page)
        view.log_selected, view.log_selected_id = None, None
        return
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        where = "user_id=? AND status IN ('complete','active','battle')"
        async with db.execute('SELECT COUNT(*) FROM expeditions WHERE ' + where, (view.user_id,)) as cursor:
            view.log_total = (await cursor.fetchone())[0]
        view.log_page = max(0, min(view.log_page, max(0, (view.log_total - 1) // PAGE_SIZE)))
        async with db.execute('SELECT * FROM expeditions WHERE ' + where + ' ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
                              (view.user_id, PAGE_SIZE, view.log_page * PAGE_SIZE)) as cursor:
            view.log_entries = [expeditions.decode(row) for row in await cursor.fetchall()]
    view.log_selected = await expeditions.read(view.user_id, view.log_selected_id) if view.log_selected_id else None
    if view.log_selected_id and (not view.log_selected or view.log_selected['status'] not in {'complete', 'active', 'battle'}):
        view.log_selected_id, view.log_selected = None, None
        view.notice = 'That entry is no longer available. Choose another trip.'


def render(view):
    categories = view.select('log_kind', 'History · ' + journal_history.KINDS[view.log_kind])
    categories.options = [discord.SelectOption(label=name, value=key, default=key == view.log_kind) for key, name in journal_history.KINDS.items()]
    view.row(categories)
    view.row(view.button('Back to Adventure', 'page:adventure', discord.ButtonStyle.primary))
    if view.log_kind != 'expeditions':
        view.text('### History · ' + journal_history.KINDS[view.log_kind])
        for entry in view.log_entries:
            view.text(journal_history.text(view.user_id, view.log_kind, entry, safe))
        if not view.log_entries:
            view.text('No records in this category yet.')
        render_pagination(view)
        return
    trip = view.log_selected
    if trip:
        render_detail(view, trip)
        view.row(view.button('All trips', 'log_back'))
        return
    view.text('### Battle log\nPast encounters, expedition notes and rewards. Newest trips first.')
    if not view.log_entries:
        view.text('No expeditions recorded yet. Your first trip will appear here.')
        return
    for trip in view.log_entries:
        view.text(f"**{destination_name(trip)}** · <t:{int(trip['updated_at'])}:d>\n{summary(trip)}")
    picker = view.select('log_entry', 'Open an expedition recap')
    picker.options = [discord.SelectOption(
        label=f"{destination_name(trip)} · {datetime.fromtimestamp(trip['updated_at'], timezone.utc):%d %b %H:%M} UTC"[:100],
        description=summary(trip)[:100], value=trip['id']) for trip in view.log_entries]
    view.row(picker)
    render_pagination(view)


def render_pagination(view):
    pages = max(1, (view.log_total + PAGE_SIZE - 1) // PAGE_SIZE)
    view.row(view.button('Newer trips', 'log_prev', disabled=view.log_page == 0),
             view.button(f'{view.log_page + 1}/{pages}', 'log_count', disabled=True),
             view.button('Older trips', 'log_next', disabled=view.log_page + 1 >= pages))


def render_detail(view, trip):
    p = trip['payload']
    complete = trip['status'] == 'complete'
    view.text(f"### Battle log · {destination_name(trip)}\n<t:{int(trip['updated_at'])}:f> · {summary(trip)}")
    if p.get('party'):
        view.text('**Expedition team** · ' + ' · '.join(safe(row['name']) for row in p['party']))
    if p.get('adventure_version', 0) >= 2:
        view.text(expeditions.presentation(trip)['footer'])
    if p.get('encounter'):
        beast = storage.get_beast_data(p['encounter'])
        catch = (' · Joined your collection' if complete else ' · Catch pending return') if p.get('caught') else ''
        view.portrait(f"**{safe(beast['name'])} · Lv.{p.get('encounter_level', 1)}**\n"
                      + OUTCOMES.get(p.get('encounter_result'), 'Encounter in progress' if trip['status'] == 'battle' else 'Encounter recorded') + catch, beast['id'])
    if complete:
        rewards = [f"{p.get('gold', 0):,} gold", f"{p.get('exp_granted', p.get('exp', 0)):,} trainer EXP"]
        if p.get('event_id') == hollowlight.EVENT_ID:
            rewards.append(f"{p.get('hollowlight_embers', 0)} Lantern Embers")
        quantity = int(p.get('material_granted', 1))
        if quantity and p.get('material'):
            rewards.append(f"{quantity} {load_catalog('materials.json')['materials'][p['material']]['name']}")
        if p.get('egg_granted') and p.get('egg_reward'):
            from utils.nursery_catalog import definition
            rewards.append(definition(p['egg_reward'])['name'])
        view.text('**Rewards received**\n' + ' · '.join(rewards))
        if p.get('party_xp'):
            view.text('**Team progress**\n' + '\n'.join(
                f"{safe(row['name'])}: +{row['amount']} EXP" + (f" · Lv.{row['level']}" if row['leveled'] else '') for row in p['party_xp']))
        if p.get('leveled'):
            view.text(f"Trainer level up! You reached Lv.{p['new_level']}.")
    else:
        view.text('**Rewards pending return**\nReturn home to bank this trip. Final rewards are calculated then.')
    notes = '\n'.join(safe(note) for note in p.get('notes', []))
    # Normal trips fit in full; bound imported/older oversized records too.
    if len(notes) > 1800:
        notes = '… Earlier notes omitted.\n' + notes[-1800:]
    view.text('**Expedition notes**\n' + (notes or 'No encounters resolved yet.'))
