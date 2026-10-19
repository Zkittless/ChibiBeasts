"""Shared, read-only selection of a trainer's individual companions."""
from dataclasses import dataclass

import discord
from discord import app_commands

from utils import db as storage

PAGE_SIZE = 10
RARITIES = ('common', 'uncommon', 'rare', 'epic', 'legendary', 'divine',
            'altered_divine', 'corrupted', 'ancient', 'dev')
SPECIAL = frozenset(('altered_divine', 'corrupted', 'ancient', 'dev'))


@dataclass
class PickerState:
    query: str = ''
    rarity: str = 'all'
    sort: str = 'number'
    page: int = 0


def number(row):
    """Only legacy unnumbered companions display their internal ID."""
    return row.get('player_number') or row['id']


def name(row):
    beast = storage.get_beast_data(row['beast_id']) or {}
    return row.get('nickname') or beast.get('name', row['beast_id'].replace('_', ' ').title())


def label(row):
    return f"#{number(row)} · {name(row)} · Lv.{row['level']}"[:100]


def filtered(rows, state):
    query = state.query.strip().casefold()
    numeric = query.removeprefix('#')
    matches = []
    for row in rows:
        rarity = row.get('rarity', 'common')
        if state.rarity == 'special':
            if rarity not in SPECIAL:
                continue
        elif state.rarity != 'all' and rarity != state.rarity:
            continue
        beast = storage.get_beast_data(row['beast_id']) or {}
        if query:
            if numeric.isdecimal():
                if int(numeric) != number(row):
                    continue
            elif query not in f"{name(row)} {beast.get('name', '')} {row['beast_id'].replace('_', ' ')}".casefold():
                continue
        matches.append(row)
    if state.sort == 'level':
        key = lambda row: (-row['level'], number(row))
    elif state.sort == 'rarity':
        rank = {rarity: index for index, rarity in enumerate(RARITIES)}
        key = lambda row: (-rank.get(row.get('rarity'), -1), number(row))
    else:
        key = number
    return sorted(matches, key=key)


def visible(rows, state, *, page_size=PAGE_SIZE):
    if not 1 <= page_size <= 25:
        raise ValueError('A companion picker page must contain 1 to 25 entries.')
    matches = filtered(rows, state)
    pages = max(1, (len(matches) + page_size - 1) // page_size)
    state.page = max(0, min(state.page, pages - 1))
    start = state.page * page_size
    return matches[start:start + page_size], pages


def options(rows, selected_id=None):
    result = []
    for row in rows[:25]:
        beast = storage.get_beast_data(row['beast_id']) or {}
        status = ' · Active' if row.get('is_active') else ''
        if row.get('hp', 1) <= 0 or storage.is_knocked_out(row):
            status += ' · Recovering'
        description = f"{beast.get('name', 'Companion')} · HP {row['hp']}/{row['max_hp']}{status}"
        result.append(discord.SelectOption(label=label(row), value=str(row['id']),
                                          default=row['id'] == selected_id,
                                          description=description[:100]))
    return result


async def autocomplete(interaction, current):
    rows = await storage.get_player_beasts(interaction.user.id)
    matches = filtered(rows, PickerState(query=str(current or '')))
    return [app_commands.Choice(name=label(row), value=number(row)) for row in matches[:25]]
