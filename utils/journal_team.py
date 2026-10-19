"""Role-first exploration roster and a bounded companion picker.

This page owns only presentation and selection state. The journal dispatches
validated assignments through the existing atomic team services.
"""
import discord

from utils import db as storage


PAGE_SIZE = 25
ROLES = ('lead', '1', '2')
ROLE_NAMES = {'lead': 'Lead', '1': 'Reserve 1', '2': 'Reserve 2'}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def frozen(view):
    trip = view.state.trip
    return bool(trip and trip['status'] != 'complete')


def companions(view):
    return view.state.workshop.companions


def roster(view):
    """Keep the exact saved travelling roster when an expedition is active."""
    rows = {row['id']: row for row in companions(view)}
    if frozen(view) and 'party_ids' in view.state.trip['payload']:
        ids = view.state.trip['payload']['party_ids'][:3]
        return [(role, rows.get(ids[index]) if index < len(ids) else None)
                for index, role in enumerate(ROLES)]
    lead = next((row for row in rows.values() if row['is_active']), None)
    return [('lead', lead)] + [(str(slot), rows.get(view.state.team_slots.get(slot))) for slot in (1, 2)]


def status(row):
    if row.get('listed'):
        return 'Listed · delist to travel'
    if row['hp'] <= 0 or storage.is_knocked_out(row):
        return 'Recovering · unavailable'
    return 'Ready'


def _eligible(row, role):
    return status(row) == 'Ready' and (role == 'lead' or not row['is_active'])


def _name(row):
    return row['nickname'] or storage.get_beast_data(row['beast_id'])['name']


def _role(value):
    if value not in ROLES:
        raise ValueError('Choose the lead or one of the two reserve slots.')
    return value


def select_role(view, role):
    _role(role)
    if frozen(view):
        raise ValueError('Return from your saved expedition before editing your exploration team.')
    view.team_role = role
    current = dict(roster(view))[role]
    view.team_candidate_id = current['id'] if current else None
    view.team_page = next((index // PAGE_SIZE for index, row in enumerate(companions(view))
                           if row['id'] == view.team_candidate_id), 0)


def select_candidate(view, value):
    role, row_id = _choice(view, value)
    if not any(row['id'] == row_id for row in companions(view)):
        raise ValueError('That companion has left your collection. Choose another companion.')
    view.team_candidate_id = row_id


def page_candidates(view, delta):
    if frozen(view):
        raise ValueError('Return home before editing your saved exploration team.')
    view.team_page = max(0, getattr(view, 'team_page', 0) + delta)
    view.team_candidate_id = None


def _choice(view, value):
    if frozen(view):
        raise ValueError('Return from your saved expedition before editing your exploration team.')
    try:
        role, row_id = value.split(':')
        _role(role)
        row_id = int(row_id)
    except (AttributeError, TypeError, ValueError):
        raise ValueError('Choose a companion from the current team page.') from None
    if role != getattr(view, 'team_role', None):
        raise ValueError('That choice belongs to another team slot. Use the current picker.')
    return role, row_id


def assignment(view, value):
    """Return the selected role and owned row ID, without mutating the roster."""
    role, row_id = _choice(view, value)
    if row_id != getattr(view, 'team_candidate_id', None):
        raise ValueError('That companion choice changed. Use the current team page.')
    row = next((row for row in companions(view) if row['id'] == row_id), None)
    if row is None:
        raise ValueError('That companion has left your collection. Choose another companion.')
    if not _eligible(row, role):
        raise ValueError('Choose a healthy, unlisted companion. Your lead cannot also fill a reserve slot.')
    return role, row_id


def removal(view, value):
    role, row_id = _choice(view, value)
    if role == 'lead':
        raise ValueError('Choose another lead instead of removing your active companion.')
    if view.state.team_slots.get(int(role)) != row_id:
        raise ValueError('That reserve assignment changed. Refresh your team.')
    return int(role)


def render(view):
    view.text('### Exploration team\nChoose a lead and up to two reserves for exploration and wild battles.')
    stage = getattr(view, 'team_art', None)
    if view.art_enabled and stage:
        view.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
            f'attachment://{stage.name}', description='Your equipped exploration team. The lead stands in front, with the two reserves behind.')))
    assigned = dict(roster(view))
    for role in ROLES:
        row = assigned[role]
        if row:
            beast = storage.get_beast_data(row['beast_id'])
            text = (f"**{ROLE_NAMES[role]} · {safe(_name(row))}** · Lv.{row['level']}\n"
                    f"HP {row['hp']}/{row['max_hp']} · {status(row)}")
            if getattr(view, 'team_art', None):
                view.text(text)
            else:
                view.portrait(text, beast['id'], row.get('armor_id', ''))
        else:
            view.text(f"**{ROLE_NAMES[role]} · Empty**\n" + ('Choose a healthy lead to set out.' if role == 'lead' else 'Optional · choose a companion to travel with you.'))

    role = getattr(view, 'team_role', None)
    if frozen(view) or role not in ROLES:
        care_buttons = [view.button('Care · ' + ROLE_NAMES[key].lower(), f"care:{assigned[key]['id']}")
                        for key in ROLES if assigned[key]]
        if care_buttons:
            view.row(*care_buttons)

    if frozen(view):
        view.text('**Saved for this expedition**\nThis is the team travelling with you. Return home before changing its members.')
        view.row(view.button('Resume expedition', 'page:adventure', discord.ButtonStyle.primary))
        return
    if not companions(view):
        view.row(view.button('Meet your starter', 'launch:start', discord.ButtonStyle.success))
        return

    view.row(*(view.button('Edit ' + ROLE_NAMES[key].lower(), 'team_role:' + key,
                          discord.ButtonStyle.primary if key == role else discord.ButtonStyle.secondary) for key in ROLES))
    if role not in ROLES:
        view.text('Choose a slot above to edit your team. Travelling reserves earn shared EXP; swaps and assists spend an action. Raid teams have separate assignments.')
        view.row(view.button('Choose an expedition', 'page:adventure', discord.ButtonStyle.success))
        return

    rows = companions(view)
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    view.team_page = max(0, min(getattr(view, 'team_page', 0), pages - 1))
    visible = rows[view.team_page * PAGE_SIZE:(view.team_page + 1) * PAGE_SIZE]
    selected = next((row for row in visible if row['id'] == getattr(view, 'team_candidate_id', None)), None)
    if selected is None:
        selected = visible[0]
        view.team_candidate_id = selected['id']
    view.text(f"**Editing {ROLE_NAMES[role].lower()}** · Collection page {view.team_page + 1}/{pages}\nChoose a companion, then confirm the assignment.")
    picker = view.select('team_candidate', 'Choose your ' + ROLE_NAMES[role].lower())
    picker.options = [discord.SelectOption(
        label=f"#{row['player_number']} · {_name(row)} · Lv.{row['level']}"[:100],
        value=f"{role}:{row['id']}", default=row['id'] == selected['id'],
        description=(f"HP {row['hp']}/{row['max_hp']} · " + ('Already leads your team' if role != 'lead' and row['is_active'] else status(row)))[:100]) for row in visible]
    view.row(picker)
    current = assigned[role]
    same = current and current['id'] == selected['id']
    placement = next((key for key, member in assigned.items() if member and member['id'] == selected['id']), None)
    note = ''
    if placement and placement != role and role == 'lead':
        note = f"\nPromoting this reserve moves your current lead into {ROLE_NAMES[placement].lower()}."
    elif placement and placement != role and role != 'lead' and placement != 'lead':
        note = f"\nThis companion moves from {ROLE_NAMES[placement].lower()} into the selected slot."
    view.text(f"**Selected · {safe(_name(selected))}** · Lv.{selected['level']}\n"
              f"HP {selected['hp']}/{selected['max_hp']} · {status(selected)}{note}")
    actions = [view.button('Assign ' + ROLE_NAMES[role].lower(), f"team_assign:{role}:{selected['id']}",
                           discord.ButtonStyle.success, disabled=bool(same or not _eligible(selected, role)))]
    if role != 'lead':
        actions.append(view.button('Clear slot', f"team_clear:{role}:{current['id'] if current else 0}", disabled=current is None))
    actions.append(view.button('Care selected', f"care:{selected['id']}"))
    view.row(*actions)
    if pages > 1:
        view.row(view.button('Previous companions', 'team_prev', disabled=view.team_page == 0),
                 view.button('Next companions', 'team_next', disabled=view.team_page + 1 >= pages))
