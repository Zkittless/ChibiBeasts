"""The Carnival lives inside the existing Field Journal message."""
import discord

from utils import hollowlight


def state(view):
    return getattr(view.state, 'carnival', {}) or {}


def visible(view):
    saved = state(view)
    return bool(saved and (saved.get('phase') in {'active', 'redeeming'} or saved.get('embers')
                           or saved.get('eggs') or saved.get('unlocks')))


def active(view):
    return state(view).get('phase') == 'active'


def reset(view):
    view.carnival_offer = None
    view.carnival_confirmation = None
    view.carnival_egg = None
    view.carnival_reveal = None


def render(view):
    saved = state(view)
    view.row(view.button('Back to Adventure', 'page:adventure', discord.ButtonStyle.primary))
    if not saved:
        view.text('### The Hollowlight Carnival\nThe Carnival is unavailable. Refresh your journal to check again.')
        return
    phase = saved['phase']
    timing = (f"Gates close <t:{int(saved['ends_at'])}:f>." if phase == 'active' else
              'The gates are closed. Spend earned Embers during the redemption period.' if phase == 'redeeming' else
              'The Carnival and its shop are closed. Your companions, eggs and cosmetics stay yours.')
    view.text(f"### {saved['name']}\n{timing}\n**Lantern Embers · {saved['embers']:,}**"
              + (f" · Shop until <t:{int(saved['redeem_until'])}:f>." if phase != 'ended' else ''))
    if view.carnival_reveal:
        hatch = view.carnival_reveal
        view.portrait(f"**{hatch['name']} · Lv.{hatch['level']} · {hatch['rarity'].title()}**\nYour new companion is in your collection.",
                      hatch['beast_id'])
    if phase == 'active':
        view.text('**10 Embers per completed adventure** · 1 exploration charge. Partial credit for required victories.')
    if not saved['gift_claimed']:
        if phase == 'active':
            view.text('**Welcome-back gift**\nHalloween egg · 3 recovery potions · 1 revival elixir · 10 Embers.\n'
                      'Patchkin or Wickwing, matched to your progress.')
            view.row(view.button('Claim welcome gift', 'carnival_gift', discord.ButtonStyle.success,
                                 disabled=not view.state.workshop.companions))
            if not view.state.workshop.companions:
                view.text('Choose your starter before claiming your welcome gift.')
    else:
        view.text('Welcome gift claimed.')

    if phase != 'ended':
        view.text('**Carnival shop** · One of each offer per trainer. 2026 cosmetics stay yours.')
        picker = view.select('carnival_offer', 'Choose an egg or exclusive cosmetic')
        picker.options = [discord.SelectOption(
            label=f"{offer['name']} · {offer['cost']} Embers"[:100], value=offer['id'],
            description=(offer['reason'] or 'Available · Review before confirming')[:100],
            default=offer['id'] == view.carnival_offer) for offer in saved['offers']]
        view.row(picker)
        offer = next((item for item in saved['offers'] if item['id'] == view.carnival_offer), None)
        if offer:
            detail = f"**{offer['name']} · {offer['cost']} Embers**"
            if offer['kind'] == 'egg':
                detail += '\nOne guaranteed companion. Its hatch level is fixed when you redeem it.'
            elif offer['kind'] == 'cosmetic':
                detail += '\nA fitted ghost costume for every companion. Equip it in Gear; it changes appearance only.'
            else:
                detail += '\nUnlock the Lanternkeeper · 2026 title. Wear or remove it here at any time.'
            if offer['reason']:
                detail += '\n' + offer['reason']
            view.text(detail)
            quoted = (offer['id'], offer['cost'])
            if view.carnival_confirmation == quoted:
                view.row(view.button(f"Confirm · {offer['cost']} Embers", f"carnival_buy:{offer['id']}:{offer['cost']}",
                                     discord.ButtonStyle.success, disabled=not offer['available']),
                         view.button('Cancel', 'carnival_cancel'))
            else:
                view.row(view.button('Review purchase', 'carnival_review', disabled=not offer['available']))

    eggs = [egg for egg in saved['eggs'] if not egg['hatched']]
    if eggs:
        view.text('**Your Halloween eggs** · Hatch anytime. Levels are fixed.')
        picker = view.select('carnival_egg', 'Choose an egg to hatch')
        picker.options = [discord.SelectOption(label=f"{egg['name']} · Lv.{egg['level']}"[:100],
                                              value=egg['id'], default=egg['id'] == view.carnival_egg) for egg in eggs]
        view.row(picker)
        chosen = next((egg for egg in eggs if egg['id'] == view.carnival_egg), None)
        if chosen:
            view.row(view.button(f"Hatch {chosen['name']}"[:80], 'carnival_hatch:' + chosen['id'], discord.ButtonStyle.success))
    elif saved['gift_claimed']:
        view.text('No unopened Halloween eggs. Your hatched companions are in your collection.')

    owned = set(saved['unlocks'])
    buttons = []
    if hollowlight.GHOST_UNLOCK in owned:
        buttons.append(view.button('Dress companions · Gear', 'launch:gear'))
    if hollowlight.TITLE_UNLOCK in owned:
        enabled = saved['title_equipped']
        buttons.append(view.button('Remove title' if enabled else 'Wear 2026 title',
                                   'carnival_title:' + ('0' if enabled else '1')))
    if buttons:
        view.row(*buttons)


async def handle(view, interaction, action):
    """Called under the journal's owner, input-order and duplicate guards."""
    if view.page != 'carnival':
        raise ValueError('Open the current Carnival page before using those controls.')
    saved = state(view)
    key = f"{view.prefix}:{action}:{getattr(interaction, 'id', 0)}"
    if action == 'carnival_offer':
        selected = interaction.data['values'][0]
        if saved.get('phase') == 'ended' or selected not in {offer['id'] for offer in saved.get('offers', [])}:
            raise ValueError('Choose an offer from the current Carnival shop.')
        view.carnival_offer, view.carnival_confirmation = selected, None
    elif action == 'carnival_review':
        offer = next((item for item in saved['offers'] if item['id'] == view.carnival_offer), None)
        if not offer or not offer['available']:
            raise ValueError(offer['reason'] if offer else 'Choose an offer before reviewing a purchase.')
        view.carnival_confirmation = (offer['id'], offer['cost'])
    elif action == 'carnival_cancel':
        view.carnival_confirmation = None
    elif action.startswith('carnival_buy:'):
        _, offer_id, cost = action.split(':')
        cost = int(cost)
        offer = next((item for item in saved['offers'] if item['id'] == offer_id), None)
        if (view.carnival_offer != offer_id or view.carnival_confirmation != (offer_id, cost)
                or not offer or offer['cost'] != cost):
            view.carnival_confirmation = None
            raise ValueError('That purchase choice or price changed. Review the current offer before confirming.')
        result = await hollowlight.redeem(view.user_id, offer_id, request_key=key, quoted_cost=cost)
        view.carnival_confirmation = None
        view.carnival_egg = result.get('egg', {}).get('id') or view.carnival_egg
        view.notice = f"{offer['name']} redeemed for {result['cost']} Lantern Embers."
    elif action == 'carnival_gift':
        result = await hollowlight.claim_gift(view.user_id, request_key=key)
        view.carnival_egg = result['egg']['id']
        view.notice = ('Your welcome gift was already claimed. Your egg is saved below.' if result['already_claimed'] else
                       'Welcome back! Your Halloween egg, recovery supplies and Lantern Embers are ready.')
    elif action == 'carnival_egg':
        selected = interaction.data['values'][0]
        if selected not in {egg['id'] for egg in saved['eggs'] if not egg['hatched']}:
            raise ValueError('Choose one of your unopened Halloween eggs.')
        view.carnival_egg = selected
    elif action.startswith('carnival_hatch:'):
        egg_id = action.split(':', 1)[1]
        if view.carnival_egg != egg_id or egg_id not in {egg['id'] for egg in saved['eggs'] if not egg['hatched']}:
            raise ValueError('That egg choice changed. Choose your current egg before hatching.')
        result = await hollowlight.hatch_egg(view.user_id, egg_id, request_key=key)
        view.carnival_egg = None
        view.carnival_reveal = result
        view.notice = f"**{result['name']} hatched!** · Lv.{result['level']} · {result['rarity'].title()}\nYour new companion is in your collection."
    elif action.startswith('carnival_title:'):
        value = action.split(':', 1)[1]
        if value not in {'0', '1'} or saved['title_equipped'] == (value == '1'):
            raise ValueError('That title choice has changed. Use the current Carnival controls.')
        result = await hollowlight.set_title(view.user_id, value == '1', request_key=key)
        view.notice = 'Lanternkeeper · 2026 equipped.' if result['enabled'] else 'Carnival title removed.'
    else:
        raise ValueError('Use a current Carnival control.')
