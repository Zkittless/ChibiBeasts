"""A native Discord Field Journal, with acknowledged, revision-checked choices."""
import asyncio
from contextlib import ExitStack
import io
import logging
from uuid import uuid4

import aiosqlite
import discord

from utils import adventure_party, crafting, db as storage, expeditions, gear_art, journal_state, story
from utils.artwork import PROJECT_ROOT
from utils.catalog import load_catalog
from utils.habitats import biome_key, starter_training_destination
from utils.equipment import definitions as equipment_definitions, dormant_label

logger = logging.getLogger('chibibeasts.journal')
PAGES = ('adventure', 'companions', 'workshop', 'story', 'activities')
PAGE_SIZE = 25
REALMS = journal_state.BIOMES
LANDMARKS = {
    'woods': ('a root-wrapped shrine', 'Fireflies drift between the roots. Three paths unfold beneath the canopy.'),
    'ember': ('a cracked forge shrine', 'Warm ash swirls around your boots. Your companion notices three ways onward.'),
    'glacial': ('a frostbound cairn', 'Blue light travels through the ice. Three trails branch into the stillness.'),
    'trenches': ('a drowned waystone', 'Lantern algae light the seafloor. Three currents carry signs of life.'),
    'loom': ('a fraying star loom', 'Loose threads of starlight drift past. Three possibilities wait to be followed.'),
}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def wait_text(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f'{minutes // 60}h {minutes % 60}m' if minutes >= 60 else f'{minutes}m {seconds}s'


class JournalView(discord.ui.LayoutView):
    def __init__(self, user_id, state, *, bot=None, page='home', realm='woods', notice='', guild_id=0):
        super().__init__(timeout=300)
        self.user_id, self.state, self.bot = user_id, state, bot
        self.guild_id = guild_id
        self.page, self.realm, self.notice = page, realm, notice
        self.trip = state.trip
        self.companion_id = next((r['id'] for r in state.workshop.companions if r['is_active']), None)
        self.picker_kind, self.picker_page, self.companion_page = 'recipe', 0, 0
        self.story_state, self.chapter, self.introduce = None, None, False
        self.story_scene = ''
        self.story_lock = ''
        self.dailies = []
        self.reviewing = False
        self.awakening_scene = ''
        self.message = None
        self.closed, self.mutating, self.art_enabled = False, False, True
        self.lock = asyncio.Lock()
        self.last_input = 0
        self.prefix = 'journal:' + uuid4().hex
        self._button_serial = 0
        self.container = discord.ui.Container(accent_colour=0xb7ad87)
        self.add_item(self.container)
        self.nav = discord.ui.ActionRow(*(self.button(key.title(), 'page:' + key) for key in PAGES))
        self.tools = discord.ui.ActionRow(self.button('Home', 'page:home'), self.button('Pin a goal', 'page:goals'), self.button('Refresh', 'refresh'))
        self.realm_select = self.select('realm', 'Choose your destination')
        self.path_select = self.select('path', 'Choose your next step')
        self.companion_select = self.select('companion', 'Choose a companion')
        self.team_select = self.select('team_slot', 'Choose this companion\'s team role')
        self.goal_kind = self.select('goal_kind', 'Choose a goal category')
        self.goal_target = self.select('goal_target', 'Choose a goal to pin')
        self.story_choice = self.select('story_claim', 'Report back to complete this chapter')
        self.awakening = self.select('awaken', 'Confirm this awakening')
        self.render()

    def button(self, label, action, style=discord.ButtonStyle.secondary, *, disabled=False):
        button = discord.ui.Button(label=label[:80], style=style, custom_id=f'{self.prefix}:{action}:{self._button_serial}', disabled=disabled)
        self._button_serial += 1
        button.callback = lambda interaction: self.handle(interaction, action)
        return button

    def select(self, action, placeholder):
        select = discord.ui.Select(custom_id=self.prefix + ':' + action, placeholder=placeholder)
        select.callback = lambda interaction: self.handle(interaction, action)
        return select

    def text(self, text):
        self.container.add_item(discord.ui.TextDisplay(text))

    def row(self, *items):
        self.container.add_item(discord.ui.ActionRow(*items))

    def portrait(self, text, species, armor_id=''):
        if self.art_enabled:
            filename = gear_art.portrait_filename(species, armor_id)
            self.portraits[filename] = (species, armor_id or '')
            self.container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(
                f'attachment://{filename}', description=storage.get_beast_data(species)['name'])))
        else:
            self.text(text)

    def selected_companion(self):
        return next((r for r in self.state.workshop.companions if r['id'] == self.companion_id), None)

    def goal_text(self):
        goal = self.state.goal
        if not goal:
            return '### Your next goal\nPin a recipe, a bond awakening or a creature to find. Your journal will track it here.'
        return f"### {'Complete · ' if goal['done'] else 'Pinned · '}{safe(goal['name'])}\n{safe(goal['text'])}"

    def goal_actions(self):
        goal = self.state.goal
        if not goal:
            return
        buttons = []
        if goal['kind'] == 'recipe':
            buttons.append(self.button('Open recipe', 'goal_workshop', discord.ButtonStyle.primary))
        elif goal.get('action'):
            buttons.append(self.button('View companion' if goal['kind'] == 'awakening' or goal['done'] else 'Search habitat', 'goal_action', discord.ButtonStyle.primary))
        if goal.get('realm') and not goal['done'] and goal['kind'] == 'recipe':
            buttons.append(self.button('Go gathering', 'goal_realm'))
        buttons.append(self.button('Unpin', 'clear_goal'))
        self.row(*buttons)

    def render_home(self):
        player = self.state.player
        e = self.state.energy
        self.text(f"### A new page in your adventure\nTrainer Lv.{player['level']} · {player['gold']:,} gold · {player['celestial_shards']:,} shards\n"
                  f"**Exploration {e.charges}/{expeditions.CAPACITY}** · " + ('Bank full' if not e.next_in else 'Next charge in ' + wait_text(e.next_in)))
        active = next((r for r in self.state.workshop.companions if r['is_active']), None)
        if active:
            beast = storage.get_beast_data(active['beast_id'])
            status = 'Recovering · ' + storage.ko_time_remaining(active) if storage.is_knocked_out(active) else f"HP {active['hp']}/{active['max_hp']}"
            self.portrait(f"**{safe(active['nickname'] or beast['name'])}** · Lv.{active['level']}\n{status} · Bond {active['bond']}/100 · Happiness {active['happiness']}/100", beast['id'], active['armor_id'])
        else:
            self.text('Choose your first companion to begin exploring.')
            self.row(self.button('Meet your starter', 'launch:start', discord.ButtonStyle.success))
        self.text(self.goal_text())
        self.goal_actions()
        if self.state.trip:
            self.text(f"**Expedition saved** · {REALMS[self.state.trip['realm']]['name']}\nYour path and gathered supplies are waiting.")
            self.row(self.button('Resume expedition', 'page:adventure', discord.ButtonStyle.success))
        else:
            self.row(self.button('Set out', 'page:adventure', discord.ButtonStyle.success), self.button('Spend time together', 'launch:play', disabled=not active))
        chapters = story.load_questline()['chapters']
        next_ch = next((c for key, c in chapters.items() if key not in self.state.story['completed_chapters']), None)
        self.text('**Story** · ' + ('Reach trainer Lv.3 to meet Maren.' if player['level'] < 3 else safe(next_ch['name']) if next_ch else 'The hollow is restored.'))

    def render_adventure(self):
        trip = self.trip
        if not trip or trip['status'] == 'complete':
            if trip:
                p = trip['payload']
                material = load_catalog('materials.json')['materials'][p['material']]['name']
                quantity = int(p.get('material_granted', 1))
                loot = f" · {quantity} {material}" if quantity else ''
                self.text(f"### Back from {REALMS[trip['realm']]['name']}\n**{p['gold']:,} gold · {p.get('exp_granted', p['exp']):,} trainer EXP{loot}**\n" + '\n'.join(safe(n) for n in p['notes'][-5:]))
                if p.get('party_xp'):
                    self.text('**Team progress**\n' + '\n'.join(f"{safe(row['name'])}: +{row['amount']} EXP" + (f" · Lv.{row['level']}" if row['leveled'] else '') for row in p['party_xp']))
                if p['caught']:
                    beast = storage.get_beast_data(p['encounter'])
                    self.portrait(f"**{beast['name']} joined your collection.**\n{beast['description']}", beast['id'])
                if p.get('leveled'):
                    self.text(f"Trainer level up! You reached Lv.{p['new_level']}.")
            e = self.state.energy
            self.text(f"### Choose an expedition\n{e.charges}/{expeditions.CAPACITY} exploration charges · Regenerates every {wait_text(e.interval)}\n"
                      'Choose a route, help travellers and discover linked events. Take up to three companions; swaps and assists spend an action. Your team and every choice are saved.')
            self.render_team_summary()
            self.row(self.button('Choose adventure team', 'page:team'))
            self.realm_select.options = [discord.SelectOption(label=(b['name'] + (f" · Locked Lv.{b['min_level']}" if self.state.player['level'] < b['min_level'] else ''))[:100],
                value=k, default=k == self.realm,
                description=f"Trainer Lv.{b['min_level']}+ required; companion Lv.{b['level_range'][0]}–{b['level_range'][1]} recommended") for k, b in REALMS.items()]
            self.row(self.realm_select)
            destination = REALMS[self.realm]
            low, high = destination['level_range']
            self.text(f"**{destination['name']}** · Trainer Lv.{destination['min_level']} required · Recommended companion Lv.{low}–{high}.\n"
                      'Wild levels stay in this range. Earlier paths become easy gathering trips and grant less combat EXP to stronger companions.')
            locked = self.state.player['level'] < REALMS[self.realm]['min_level']
            traveller = next((r for r in self.state.workshop.companions if r['is_active']), None)
            if traveller:
                training = starter_training_destination(storage.get_beast_data(traveller['beast_id']), traveller['level'], self.state.player['level'])
                if training:
                    self.text(f"**Suggested starter training: {REALMS[training]['name']}**\n"
                              'Train in the Woods through companion Lv.7, then use the Depths from Lv.8 until your first awakening. '
                              'Ember creatures can resist your starter’s type; prepare an awakened companion or suitable gear before pushing onward.')
            healthy = traveller and traveller['hp'] > 0 and not storage.is_knocked_out(traveller) and not traveller['listed']
            self.row(self.button('Begin expedition · 1 charge', 'begin', discord.ButtonStyle.success,
                                 disabled=not e.charges or locked or not healthy))
            if not healthy:
                self.text('Choose a healthy active companion before setting out.')
                self.row(self.button('Choose a companion', 'page:companions'))
            if not e.charges:
                self.text('Next charge in ' + wait_text(e.next_in) + '. Up to three charges wait for you between visits.')
            return
        p = trip['payload']
        if p.get('adventure_version', 0) >= 2:
            self.render_branching_trip(trip)
            return
        landmark, flavor = LANDMARKS[biome_key(trip['realm'])]
        e = self.state.energy
        recharge = 'Bank full' if not e.next_in else 'Next charge in ' + wait_text(e.next_in)
        self.text(f"### {REALMS[trip['realm']]['name']}\nStep {p['stage'] + 1}/3 · Supplies saved\n"
                  f"**Exploration {e.charges}/{expeditions.CAPACITY}** · {recharge}\n{flavor}")
        if trip['status'] == 'battle':
            self.text('**Your encounter is in progress.** Use its battle controls below, then return to this page. You can reopen the journal without losing this trip.')
            if not p.get('battle_started'):
                self.row(self.button('Continue encounter', 'battle_continue', discord.ButtonStyle.primary))
            return
        if p['stage'] == 0:
            self.text(f'**Follow tracks** · Meet a wild creature; battle, befriend or observe.\n**Gather supplies** · Pick one of the materials you uncover.\n**Visit {landmark}** · Restore it, study it or rest together.')
        elif p['stage'] == 1 and p['route'] == 'tracks':
            beast = storage.get_beast_data(p['encounter'])
            self.portrait(f"### {beast['name']} · Lv.{p.get('encounter_level', REALMS[trip['realm']]['level_range'][0])} appears\n{beast['description']}\n**Challenge** to earn its trust in battle, **approach** for a chance to befriend it, or **observe** to gain 1 bond. A failed approach ends this encounter.", beast['id'])
        elif p['stage'] == 1 and p['route'] == 'gather':
            self.text('**A promising gathering spot**\nChoose one material to bring back. Missing supplies for an active story commission are offered here in their region. The other finds stay here for the next visitor.')
        elif p['stage'] == 1:
            self.text(f'**You reach {landmark}.**\n**Repair together:** +2 bond, +5 happiness.\n**Study its markings:** a little extra trainer EXP.\n**Share a rest:** recover 5% max HP, +3 happiness. Recovery cannot revive a fallen companion.')
        else:
            self.text('\n'.join(safe(n) for n in p['notes']) or 'Your supplies are packed.')
            self.text('**The trail home**\nReturn to bank your rewards.' + (' A nearby rustle offers one optional creature encounter.' if not p['encounter'] else ''))
        labels = {'tracks': ('Follow the tracks', 'Discover a wild creature'), 'gather': ('Gather supplies', 'Choose a material to bring home'),
                  'shrine': ('Visit the landmark', 'Bond, study or rest'), 'challenge': ('Challenge in battle', 'A win lets this creature join you'),
                  'approach': ('Approach gently', 'One chance to befriend; it may leave'), 'observe': ('Observe quietly', '+1 bond; leave the creature in peace'),
                  'repair': ('Repair together', '+2 bond and +5 happiness'), 'study': ('Study the markings', 'Extra trainer EXP'),
                  'rest': ('Share a rest', 'Recover 5% max HP; +3 happiness'), 'return': ('Return home', 'Bank your supplies and rewards'),
                  'rustle': ('Investigate the rustle', 'One optional creature encounter'), 'return_early': ('Head home now', 'Finish this trip with your saved supplies')}
        choices = list(expeditions.options(trip))
        if 'return' not in choices:
            choices.append('return_early')
        options = []
        for choice in choices:
            if choice.startswith('material:'):
                name = load_catalog('materials.json')['materials'][choice.split(':', 1)[1]]['name']
                label, description = name, 'Gather 1; other finds remain here'
            else:
                label, description = labels[choice]
            options.append(discord.SelectOption(label=label[:100], description=description, value=f"{trip['id']}:{trip['version']}:{choice}"))
        self.path_select.options = options
        self.row(self.path_select)

    def render_team_summary(self, trip=None):
        all_rows = {row['id']: row for row in self.state.workshop.companions}
        if trip:
            ids = trip['payload'].get('party_ids', [])
        else:
            lead = next((row['id'] for row in self.state.workshop.companions if row['is_active']), None)
            ids = list(dict.fromkeys(([lead] if lead else []) + list(self.state.team_slots.values())))[:3]
        lines = []
        for index, row_id in enumerate(ids):
            row = all_rows.get(row_id)
            if not row:
                continue
            name = row['nickname'] or storage.get_beast_data(row['beast_id'])['name']
            role = 'Lead' if index == 0 else 'Reserve'
            status = ('Recovering' + (' · stays home' if not trip else '')) if storage.is_knocked_out(row) or row['hp'] <= 0 else f"HP {row['hp']}/{row['max_hp']}"
            lines.append(f"{role} · **{safe(name)}** Lv.{row['level']} · {status}")
        self.text('**Adventure team**\n' + ('\n'.join(lines) if lines else 'Choose a healthy lead companion. Reserves are optional.'))

    def render_branching_trip(self, trip):
        page = expeditions.presentation(trip)
        p = trip['payload']
        progress = page.get('progress', {})
        energy = self.state.energy
        recharge = 'Bank full' if not energy.next_in else 'Next charge in ' + wait_text(energy.next_in)
        self.text(f"### {safe(page['title'])}\n{REALMS[trip['realm']]['name']} · {progress.get('completed', 0)}/{progress.get('total', 3)} stops completed\n"
                  f"**Exploration {energy.charges}/{expeditions.CAPACITY}** · {recharge}")
        self.render_team_summary(trip)
        if trip['status'] == 'battle':
            self.text('**Encounter in progress.** Use the battle controls below. Your route and team are saved.')
            if not p.get('battle_started'):
                self.row(self.button('Continue encounter', 'battle_continue', discord.ButtonStyle.primary))
            return
        if p.get('encounter') and any(choice['value'] == 'challenge' for choice in page['choices']):
            beast = storage.get_beast_data(p['encounter'])
            self.portrait(f"**{safe(beast['name'])} · Lv.{p.get('encounter_level', 1)}**\n{safe(beast['description'])}", beast['id'])
        self.text(page['body'])
        if page.get('footer'):
            self.text(page['footer'])
        self.path_select.options = [discord.SelectOption(label=choice['label'][:100], description=choice['description'][:100],
            value=f"{trip['id']}:{trip['version']}:{choice['value']}") for choice in page['choices']]
        if self.path_select.options:
            self.row(self.path_select)

    def render_team(self):
        self.text('### Your adventure team\nYour active companion leads. Choose up to two reserves for exploration and wild battles. Raid parties have their own assignments.')
        self.render_team_summary()
        self.text('All travelling companions earn bounded EXP. Lower-level reserves catch up gradually. Each keeps their own health, mana, gear and ultimate charge; swapping spends your action.')
        rows = self.state.workshop.companions
        if not rows:
            self.row(self.button('Choose your starter', 'launch:start', discord.ButtonStyle.success))
            return
        if self.state.trip:
            self.text('**Team saved for this expedition.** Return home before changing your lead or reserves.')
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.companion_page = max(0, min(self.companion_page, pages - 1))
        visible = rows[self.companion_page * PAGE_SIZE:(self.companion_page + 1) * PAGE_SIZE]
        if self.companion_id not in {row['id'] for row in visible}:
            self.companion_id = visible[0]['id']
        self.companion_select.options = [discord.SelectOption(label=f"#{row['player_number']} · {row['nickname'] or storage.get_beast_data(row['beast_id'])['name']} · Lv.{row['level']}"[:100],
            value=str(row['id']), default=row['id'] == self.companion_id) for row in visible]
        self.row(self.companion_select)
        row = self.selected_companion()
        beast = storage.get_beast_data(row['beast_id'])
        self.portrait(f"**{safe(row['nickname'] or beast['name'])}** · {beast['type'].title()}\nHP {row['hp']}/{row['max_hp']} · MP {row['mana']}/{row['max_mana']}", beast['id'], row['armor_id'])
        slots = self.state.team_slots
        occupied = next((slot for slot, row_id in slots.items() if row_id == row['id']), None)
        self.team_select.options = [discord.SelectOption(label=f'Assign reserve {slot}', value=f"{row['id']}:{slot}",
            description='Replace the current reserve in this slot') for slot in (1, 2)]
        if occupied:
            self.team_select.options.append(discord.SelectOption(label='Remove from adventure team', value=f"{row['id']}:clear:{occupied}"))
        self.team_select.disabled = bool(self.state.trip or row['listed'] or row['hp'] <= 0 or storage.is_knocked_out(row) or row['is_active'])
        # A recovering reserve can still be removed when home.
        if occupied and not self.state.trip and not row['is_active']:
            self.team_select.disabled = False
        self.row(self.team_select)
        self.row(self.button('Make team lead', 'make_active', discord.ButtonStyle.primary,
                             disabled=bool(self.state.trip or row['is_active'] or row['listed'] or row['hp'] <= 0 or storage.is_knocked_out(row))),
                 self.button('Back to adventure', 'page:adventure'))
        if pages > 1:
            self.row(self.button('Previous companions', 'comp_prev', disabled=self.companion_page == 0),
                     self.button('Next companions', 'comp_next', disabled=self.companion_page + 1 >= pages))

    def render_companions(self):
        rows = self.state.workshop.companions
        self.text('### Your travelling companions\nChoose a companion to view their bond, make them active or plan their awakening.')
        if not rows:
            self.row(self.button('Choose your starter', 'launch:start', discord.ButtonStyle.success))
            return
        pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
        self.companion_page = max(0, min(self.companion_page, pages - 1))
        visible = rows[self.companion_page * PAGE_SIZE:(self.companion_page + 1) * PAGE_SIZE]
        if self.companion_id not in {r['id'] for r in visible}:
            self.companion_id = visible[0]['id']
        self.companion_select.options = [discord.SelectOption(label=f"#{r['player_number']} · {r['nickname'] or storage.get_beast_data(r['beast_id'])['name']} · Lv.{r['level']}"[:100],
            value=str(r['id']), default=r['id'] == self.companion_id) for r in visible]
        self.row(self.companion_select)
        row = self.selected_companion()
        beast = storage.get_beast_data(row['beast_id'])
        self.portrait(f"### {safe(row['nickname'] or beast['name'])} · Lv.{row['level']}\n{'Active companion · ' if row['is_active'] else ''}HP {row['hp']}/{row['max_hp']} · MP {row['mana']}/{row['max_mana']}\nBond {row['bond']}/100 · Happiness {row['happiness']}/100\n{beast['description']}", beast['id'], row['armor_id'])
        equipment = equipment_definitions()
        dormant = [f"{equipment[key]['name']} · {dormant_label(equipment[key], self.state.player['level'])}"
                   for key in (row['armor_id'], row['rune_id']) if key in equipment and dormant_label(equipment[key], self.state.player['level'])]
        if dormant:
            self.text('**Dormant gear**\n' + '\n'.join(dormant) + '\nOwned gear stays with this companion; its effects activate at the required trainer level.')
        if self.awakening_scene:
            self.text('**A familiar friend**\n' + self.awakening_scene)
        if beast.get('divine_passive'):
            passive = beast['divine_passive']
            self.text(f"**{passive['passive_name']}** · {passive['passive_desc']}")
        self.text('**Growing your bond**\nPlay together: +3 bond · battle win: +2 · training: +1. Bond stays with this individual companion.')
        if beast.get('starter_family'):
            self.text('Every bond point adds 0.1% to combat attack, defense and speed, up to 10% at 100 bond. The bonus continues after awakening.')
        else:
            self.text('Any companion can build a bond. The four Architect starters also have unique bond awakenings.')
        self.row(self.button('Make active', 'make_active', discord.ButtonStyle.primary, disabled=bool(row['is_active'] or row['listed'] or storage.is_knocked_out(row))),
                 self.button('Play together', 'launch:play', disabled=not row['is_active']), self.button('Adventure team', 'page:team'))
        evo = beast.get('evolution') or {}
        if evo.get('form') == 'bond':
            after = storage.get_beast_data(evo['evolves_to'])
            bond_required, happiness_required = evo.get('bond_required', 40), evo.get('happiness_required', 70)
            trainer_required = evo.get('trainer_level_required', 8)
            self.text(f"**Awakening · {after['name']}**\nCompanion Lv.{row['level']}/{evo['level_required']} · Trainer Lv.{self.state.player['level']}/{trainer_required} · Bond {row['bond']}/{bond_required} · Happiness {row['happiness']}/{happiness_required}\nNo item is required. Review the awakening below when ready.")
            self.row(self.button('Pin awakening', 'pin_awakening'), self.button('Review awakening', 'review_awakening', discord.ButtonStyle.success))
            if self.reviewing:
                self.portrait(f"**{beast['name']} becomes {after['name']}**\n{after['description']}\nYour earned stats, bond and gear carry forward. No item is required.", after['id'], row['armor_id'])
                self.awakening.options = [discord.SelectOption(label=f"Awaken {beast['name']} into {after['name']}"[:100], value=f"{row['id']}:{beast['id']}")]
                self.awakening.disabled = (row['level'] < evo['level_required'] or row['bond'] < bond_required
                                           or row['happiness'] < happiness_required or self.state.player['level'] < trainer_required
                                           or row['listed'] or row['hp'] <= 0 or storage.is_knocked_out(row))
                self.row(self.awakening)
        if pages > 1:
            self.row(self.button('Previous companions', 'comp_prev', disabled=self.companion_page == 0), self.button(f'{self.companion_page + 1}/{pages}', 'page_count', disabled=True),
                     self.button('Next companions', 'comp_next', disabled=self.companion_page + 1 >= pages))

    def goal_targets(self):
        if self.picker_kind == 'recipe':
            return [(key, item['name'], f"{item['section'].title()} · Trainer Lv.{journal_state.recipe_level(item)}") for key, item in crafting.recipes().items()]
        if self.picker_kind == 'creature':
            return [(b['id'], b['name'], b['rarity'].title() + ' · ' + b['type'].title()) for b in journal_state.hunt_targets()]
        return [(str(r['id']), r['nickname'] or storage.get_beast_data(r['beast_id'])['name'], f"Lv.{r['level']} · Bond {r['bond']}/40")
                for r in self.state.workshop.companions if (storage.get_beast_data(r['beast_id']).get('evolution') or {}).get('form') == 'bond']

    def render_goals(self):
        self.text('### Give your next trip a purpose\nOne pinned goal follows you between visits. Its progress uses your current collection and materials.')
        self.text(self.goal_text())
        self.goal_kind.options = [discord.SelectOption(label=label, value=key, default=key == self.picker_kind) for key, label in
                                  (('recipe', 'Craft a recipe'), ('awakening', 'Awaken a companion'), ('creature', 'Find a creature'))]
        self.row(self.goal_kind)
        targets = self.goal_targets()
        pages = max(1, (len(targets) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.picker_page = max(0, min(self.picker_page, pages - 1))
        if targets:
            self.goal_target.options = [discord.SelectOption(label=name[:100], value=self.picker_kind + ':' + key, description=desc[:100])
                                       for key, name, desc in targets[self.picker_page * PAGE_SIZE:(self.picker_page + 1) * PAGE_SIZE]]
            self.row(self.goal_target)
        else:
            self.text('No companion in your collection has an uncompleted bond awakening.')
        self.row(self.button('Previous goals', 'goal_prev', disabled=self.picker_page == 0),
                 self.button(f'{self.picker_page + 1}/{pages}', 'page_count', disabled=True), self.button('Next goals', 'goal_next', disabled=self.picker_page + 1 >= pages),
                 self.button('Unpin goal', 'clear_goal', disabled=not self.state.goal))

    def render_story(self):
        if self.story_scene:
            self.text(self.story_scene)
            self.row(self.button('Turn the page', 'story_continue', discord.ButtonStyle.primary))
        elif self.state.player['level'] < 3:
            self.text('### A story waiting to begin\nReach trainer Lv.3 to meet Maren. Explore, challenge creatures and claim your daily reward to get there.')
        elif self.story_lock:
            self.text('### The next chapter is waiting\n' + safe(self.story_lock))
            self.row(self.button('Explore', 'page:adventure'), self.button('Activities', 'page:activities'))
        elif not self.chapter:
            self.text('### The hollow is restored\nYour companions and the people of the Loom remember what you did together. Revisit a habitat, nurture a bond or help another trainer in a raid.')
        else:
            chapter, state = self.chapter, self.story_state
            npc = story.load_npcs()[chapter['npc']]
            self.text(f"### {chapter['name']}\n{npc['name']} · {chapter['location']}")
            if self.introduce:
                self.text('\n\n'.join(chapter['intro'])[:2200])
            progress = state['step_progress'].get(chapter['id'], {})
            lines = []
            for step in chapter['steps']:
                target = story.step_target(chapter, progress, step)
                if target == 0:
                    continue
                description = step['description']
                if target != step.get('target', 1):
                    description = (f'Reach trainer level {target}' if step['type'] == 'player_level'
                                   else 'Your earlier raid participation counts for this chapter')
                lines.append(f"**{min(target, progress.get(step['id'], 0))}/{target}** · {description}")
                if step['type'] == 'relic_collect':
                    lines.append(f"Clues {progress.get(step['id'] + '_search', 0)}/3 · Search {step['biome']}.")
                elif step['type'] == 'material_collect_specific':
                    lines.append(' · '.join(f"{mid.replace('_', ' ').title()} {progress.get(step['id'] + '_' + mid, 0)}/{n}" for mid, n in step['materials'].items()))
            self.text('\n'.join(lines))
            if story.complete(chapter, state):
                choices = chapter.get('choices') or {'': {'label': 'Report back · Complete chapter'}}
                self.story_choice.options = [discord.SelectOption(label=c['label'][:100], value=chapter['id'] + ':' + key) for key, c in choices.items()]
                self.row(self.story_choice)
            else:
                self.row(self.button('Explore', 'page:adventure'), self.button('Workshop', 'page:workshop'))

    def render_activities(self):
        player = self.state.player
        daily_ready = not player['_daily_claimed']
        self.text('### Around the camp\n' + ('Your daily reward is ready.' if daily_ready else 'Daily reward claimed. The next day begins at 00:00 UTC.'))
        if self.dailies:
            self.text('**Daily objectives**\n' + '\n'.join(f"{min(q['progress'], q['target'])}/{q['target']} · {q['desc'].format(target=q['target'])}" for q in self.dailies))
        self.row(self.button('Claim daily', 'launch:daily', discord.ButtonStyle.success, disabled=not daily_ready), self.button('Shop', 'launch:shop'), self.button('Daily quests', 'launch:dailies'))
        self.text('**Battles and community**\nChallenge a wild creature in your selected realm, check your guild or join its raid. These open their existing game panels in this channel.')
        self.row(self.button('Wild challenge', 'launch:challenge'), self.button('Guild', 'launch:guild'), self.button('Guild raid', 'launch:raid'))
        self.row(self.button('Command guide', 'commands'))

    def render(self):
        self._button_serial = 8  # The five navigation and three utility buttons are stable.
        self.portraits = {}
        self.container.clear_items()
        if self.art_enabled:
            self.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f'attachment://{self.header.name}', description='Field Journal · ' + REALMS[self.art_realm]['name'])))
        self.container.add_item(self.nav)
        self.container.add_item(self.tools)
        if self.notice:
            self.text(self.notice[:750])
        for button, key in zip(self.nav.children, PAGES):
            button.style = discord.ButtonStyle.primary if self.page == key else discord.ButtonStyle.secondary
        render = getattr(self, 'render_' + self.page, self.render_home)
        render()
        if self.closed:
            for item in self.walk_children():
                if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                    item.disabled = True
        if self.content_length() > 4000 or self.total_children_count > 40:
            raise ValueError('Journal page exceeds Discord message limits.')

    @property
    def art_realm(self):
        return biome_key(self.trip['realm'] if self.page == 'adventure' and self.trip and self.trip['status'] != 'complete' else self.realm)

    @property
    def header(self):
        return PROJECT_ROOT / 'data' / 'journal' / f'journal-{self.art_realm}.png'

    async def publish(self, interaction, *, opening=False):
        previous = {a.filename: a for a in getattr(self.message, 'attachments', [])}
        portraits = {}
        if self.art_enabled:
            for filename, appearance in self.portraits.items():
                if filename not in previous:
                    portrait = await gear_art.render_portrait(*appearance)
                    if portrait is None:
                        self.art_enabled = False
                        self.render()
                        break
                    portraits[filename] = portrait
        with ExitStack() as stack:
            attachments, files = [], []
            if self.art_enabled:
                # Only send artwork actually referenced on this page.
                paths = {self.header.name: self.header}
                for name, path in paths.items():
                    if name in previous:
                        attachments.append(previous[name])
                    else:
                        file = discord.File(path)
                        stack.callback(file.close)
                        files.append(file)
                for filename in self.portraits:
                    if filename in previous:
                        attachments.append(previous[filename])
                    else:
                        buffer = io.BytesIO(portraits[filename].data)
                        stack.callback(buffer.close)
                        file = discord.File(buffer, filename=filename)
                        stack.callback(file.close)
                        files.append(file)
            async def deliver(with_art):
                async with asyncio.timeout(15):
                    if opening:
                        return await interaction.followup.send(view=self, files=files if with_art else [], allowed_mentions=discord.AllowedMentions.none(), wait=True)
                    return await interaction.edit_original_response(view=self, attachments=attachments + files if with_art else [], allowed_mentions=discord.AllowedMentions.none())
            try:
                self.message = await deliver(self.art_enabled)
            except discord.HTTPException as error:
                if isinstance(error, discord.NotFound) or not files:
                    raise
                logger.warning('Journal artwork rejected (%s); using native text controls', error.status)
                self.art_enabled = False
                self.render()
                self.message = await deliver(False)

    async def load_page(self):
        if self.page == 'story' and not self.story_scene and self.state.player['level'] >= 3:
            self.story_lock = ''
            try:
                self.story_state, self.chapter, self.introduce = await story.journal(self.user_id, self.guild_id)
            except ValueError as error:
                self.story_state, self.chapter, self.introduce = None, None, False
                self.story_lock = str(error)
        if self.page == 'activities':
            from utils.progress import get_daily_quests
            self.dailies = await get_daily_quests(self.user_id)

    async def launch(self, interaction, name):
        command = self.bot.tree.get_command(name) if self.bot else None
        if not command:
            return await interaction.response.send_message('This activity is unavailable. Open the journal again after the bot finishes loading.', ephemeral=True)
        kwargs = {'biome': self.realm} if name == 'challenge' else {}
        # Existing command callbacks acknowledge immediately and retain their
        # own atomic cooldown, permission and battle guards.
        if command.binding is not None:
            return await command.callback(command.binding, interaction, **kwargs)
        return await command.callback(interaction, **kwargs)

    async def handle(self, interaction, action):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('Open /journal for your own adventure.', ephemeral=True)
        if self.closed or self.is_finished():
            return await interaction.response.send_message('This page has closed. Open /journal to resume your saved expedition.', ephemeral=True)
        if action.startswith('launch:'):
            try:
                return await self.launch(interaction, action.split(':', 1)[1])
            except ValueError as error:
                if interaction.response.is_done():
                    return await interaction.followup.send(safe(error), ephemeral=True)
                return await interaction.response.send_message(safe(error), ephemeral=True)
        mutation = action in {'begin', 'path', 'battle_continue', 'make_active', 'team_slot', 'awaken', 'story_claim'}
        duplicate = mutation and (self.mutating or self.lock.locked())
        if mutation and not duplicate:
            self.mutating = True
        battle = None
        try:
            await interaction.response.defer()
            if duplicate:
                return await interaction.followup.send('Your journal is updating. Use the refreshed page.', ephemeral=True)
            async with self.lock:
                if self.closed:
                    return
                input_id = getattr(interaction, 'id', 0)
                if input_id and input_id < self.last_input:
                    return await interaction.followup.send('That input came from an older page. Use the current controls.', ephemeral=True)
                self.last_input = max(self.last_input, input_id)
                self.state = await journal_state.snapshot(self.user_id)
                if self.state.trip:
                    self.trip = self.state.trip
                self.notice = ''
                try:
                    if action in {'page:workshop', 'goal_workshop'}:
                        from utils.workshop import open_workshop
                        recipe = self.state.goal['target'] if action == 'goal_workshop' and self.state.goal and self.state.goal['kind'] == 'recipe' else None
                        result = await open_workshop(interaction, recipe_id=recipe, already_deferred=True, replace_message=True)
                        if result:
                            self.closed = True
                            self.stop()
                        return
                    if action.startswith('page:'):
                        self.page = action.split(':', 1)[1]
                        self.reviewing = False
                        self.awakening_scene = ''
                        if self.page == 'adventure':
                            self.trip = self.state.trip or self.trip
                    elif action == 'realm':
                        key = interaction.data['values'][0]
                        if key not in REALMS:
                            raise ValueError('Choose a destination from this journal.')
                        self.realm = key
                    elif action == 'begin':
                        self.trip = await expeditions.begin(self.user_id, self.realm, guild_id=getattr(interaction.guild, 'id', 0))
                    elif action == 'path':
                        trip_id, version, choice = interaction.data['values'][0].split(':', 2)
                        self.trip = await expeditions.choose(self.user_id, trip_id, int(version), choice)
                        if self.trip['status'] == 'battle':
                            battle = self.trip
                        elif self.trip['status'] == 'complete' and self.trip['payload']['caught']:
                            from cogs.hatch import check_collection_completion
                            await check_collection_completion(interaction, storage.get_beast_data(self.trip['payload']['encounter']), storage.load_beasts())
                    elif action == 'battle_continue':
                        if not self.state.trip or self.state.trip['status'] != 'battle':
                            raise ValueError('This encounter has already ended. Refresh your saved trip.')
                        battle = self.trip = self.state.trip
                    elif action == 'companion':
                        selected = int(interaction.data['values'][0])
                        if selected not in {r['id'] for r in self.state.workshop.companions}:
                            raise ValueError('That companion has left your collection.')
                        self.companion_id, self.reviewing, self.awakening_scene = selected, False, ''
                    elif action == 'team_slot':
                        values = interaction.data['values'][0].split(':')
                        row_id = int(values[0])
                        if row_id != self.companion_id:
                            raise ValueError('That team choice belongs to an older companion page.')
                        if values[1] == 'clear':
                            slot = int(values[2])
                            if self.state.team_slots.get(slot) != row_id:
                                raise ValueError('That reserve assignment changed. Refresh your team.')
                            await adventure_party.clear_slot(self.user_id, slot)
                            self.notice = 'Companion removed from the adventure team.'
                        else:
                            await adventure_party.set_slot(self.user_id, row_id, int(values[1]))
                            self.notice = 'Your adventure reserve is ready.'
                    elif action in {'make_active', 'pin_awakening', 'review_awakening'}:
                        row = self.selected_companion()
                        if not row:
                            raise ValueError('Choose a companion from your collection.')
                        if action == 'make_active':
                            await storage.set_active_companion(self.user_id, row['player_number'], expected_row_id=row['id'])
                            self.notice = 'Your travelling companion is ready.'
                        elif action == 'pin_awakening':
                            await journal_state.pin_goal(self.user_id, 'awakening', row['id'])
                            self.notice = 'Awakening goal pinned.'
                        else:
                            self.reviewing = True
                    elif action == 'awaken':
                        row_id, species = interaction.data['values'][0].split(':', 1)
                        row = next((r for r in self.state.workshop.companions if str(r['id']) == row_id), None)
                        if not row or (storage.get_beast_data(species).get('evolution') or {}).get('form') != 'bond':
                            raise ValueError('Review a current companion awakening first.')
                        from utils.gameplay import evolve_beast
                        before, after, _ = await evolve_beast(self.user_id, row['player_number'], expected_row_id=row['id'], expected_species=species)
                        self.notice = f"**{before['name']} became {after['name']}!**"
                        self.awakening_scene = '\n\n'.join(after.get('awakening_scene') or [])
                        self.companion_id = row['id']
                        if interaction.guild:
                            from utils.progress import record_bestiary_sighting
                            await record_bestiary_sighting(interaction.guild.id, after['id'], self.user_id)
                        from cogs.hatch import check_collection_completion
                        await check_collection_completion(interaction, after, storage.load_beasts())
                        self.reviewing = False
                    elif action == 'goal_kind':
                        kind = interaction.data['values'][0]
                        if kind not in {'recipe', 'awakening', 'creature'}:
                            raise ValueError('Choose a goal category from this page.')
                        self.picker_kind, self.picker_page = kind, 0
                    elif action == 'goal_target':
                        kind, target = interaction.data['values'][0].split(':', 1)
                        await journal_state.pin_goal(self.user_id, kind, target)
                        self.notice, self.page = 'Goal pinned. Your next step is ready below.', 'home'
                    elif action == 'clear_goal':
                        await journal_state.clear_goal(self.user_id)
                    elif action in {'goal_action', 'goal_realm'}:
                        goal = self.state.goal
                        if not goal:
                            raise ValueError('Pin a goal first.')
                        if action == 'goal_action' and goal['kind'] == 'awakening':
                            self.page, self.companion_id = 'companions', int(goal['target'])
                            self.companion_page = next((i // PAGE_SIZE for i, r in enumerate(self.state.workshop.companions) if r['id'] == self.companion_id), 0)
                        elif action == 'goal_action' and goal['kind'] == 'creature' and goal['done']:
                            row = next((r for r in self.state.workshop.companions if r['beast_id'] == goal['target']), None)
                            if not row:
                                raise ValueError('That creature has left your collection. Refresh your goal.')
                            self.page, self.companion_id = 'companions', row['id']
                            self.companion_page = next(i // PAGE_SIZE for i, r in enumerate(self.state.workshop.companions) if r['id'] == row['id'])
                        else:
                            self.page = 'adventure'
                            if goal.get('realm'):
                                self.realm = goal['realm']
                    elif action in {'goal_prev', 'goal_next'}:
                        self.picker_page += -1 if action.endswith('prev') else 1
                    elif action in {'comp_prev', 'comp_next'}:
                        self.companion_page += -1 if action.endswith('prev') else 1
                        self.reviewing = False
                    elif action == 'story_claim':
                        chapter_id, choice = interaction.data['values'][0].split(':', 1)
                        chapter = story.load_questline()['chapters'][chapter_id]
                        _, reward = await story.claim_chapter(self.user_id, chapter_id, getattr(interaction.guild, 'id', 0), choice or None)
                        lines = ([chapter['choices'][choice]['scene']] if choice else []) + chapter['completion_dialogue']
                        rewards = [f"{reward.get('gold', 0):,} gold", f"{reward.get('exp', 0)} EXP", f"{reward.get('celestial_shards', 0)} shards"]
                        rewards += [f"{n} × {key.replace('_', ' ').title()}" for group in ('items', 'materials') for key, n in reward.get(group, {}).items()]
                        if reward.get('relic'):
                            rewards.append(reward['relic'].replace('_', ' ').title())
                        if choice:
                            rewards.append('Title: ' + chapter['choices'][choice]['title'])
                        self.story_scene = f"### {chapter['name']} · Complete\n" + '\n\n'.join(lines) + '\n\n**Rewards** · ' + ' · '.join(rewards)
                    elif action == 'story_continue':
                        self.story_scene = ''
                    elif action == 'commands':
                        from cogs.utilities import command_guide
                        return await command_guide(interaction)
                    self.state = await journal_state.snapshot(self.user_id)
                    if self.state.trip:
                        self.trip = self.state.trip
                    await self.load_page()
                except (ValueError, KeyError) as error:
                    self.notice = safe(error)
                    self.state = await journal_state.snapshot(self.user_id)
                    self.trip = self.state.trip or self.trip
                self.render()
                await self.publish(interaction)
            if battle:
                try:
                    self.trip = await expeditions.run_battle(interaction, battle)
                except (ValueError, discord.HTTPException, TimeoutError) as error:
                    logger.info('Expedition encounter ended early: %s', error)
                    self.notice = 'The encounter closed. Your expedition and gathered supplies are saved.'
                async with self.lock:
                    if not self.closed:
                        self.trip = await expeditions.read(self.user_id, battle['id'])
                        self.state = await journal_state.snapshot(self.user_id)
                        self.render()
                        # The original component token may expire during a
                        # slow battle; the bot-owned journal remains editable.
                        if self.message:
                            async with asyncio.timeout(15):
                                await self.message.edit(view=self)
        except discord.NotFound:
            self.closed = True
            self.stop()
            await interaction.followup.send('This journal message was removed. Open /journal to resume your saved trip.', ephemeral=True)
        finally:
            try:
                if battle:
                    # A deleted page or failed upload must not strand a battle
                    # reservation before its runner has even started.
                    await asyncio.shield(expeditions.battle_result(self.user_id, battle['id'], battle['payload']['battle_token'], False,
                                                                  interrupted=True, only_unstarted=True))
            finally:
                if mutation and not duplicate:
                    self.mutating = False

    async def on_timeout(self):
        self.closed = True
        async with self.lock:
            self.render()
            if self.message:
                try:
                    async with asyncio.timeout(15):
                        await self.message.edit(view=self)
                except (discord.HTTPException, TimeoutError):
                    pass

    async def on_error(self, interaction, error, item):
        logger.error('Journal input failed', exc_info=error)
        text = 'Your journal could not refresh. Reopen /journal; saved choices and completed rewards are kept.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass


async def open_journal(interaction, *, bot=None, page='home', realm='woods', notice='', already_deferred=False, replace_message=False,
                       begin_expedition=False):
    if not already_deferred:
        await interaction.response.defer()
    await storage.get_or_create_player(interaction.user.id, str(interaction.user))
    state = await journal_state.snapshot(interaction.user.id)
    # Retry post-commit progression for recently completed trips after a
    # disconnect. Item, gold and companion grants are never repeated here.
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM expeditions WHERE user_id=? AND status='complete' AND reported=0 ORDER BY updated_at LIMIT 5", (interaction.user.id,)) as c:
            pending = [expeditions.decode(r) for r in await c.fetchall()]
    for trip in pending:
        await expeditions.report(trip)
    if pending:
        state = await journal_state.snapshot(interaction.user.id)
    if realm not in REALMS:
        realm = 'woods'
        notice = 'Choose an available destination from your journal.'
    elif begin_expedition and not state.trip:
        try:
            await expeditions.begin(interaction.user.id, realm, guild_id=getattr(interaction.guild, 'id', 0))
        except ValueError as error:
            # Missing charges, recovery, locked realms and a simultaneous
            # opener all remain visible in the journal instead of a dead end.
            notice = str(error)
        state = await journal_state.snapshot(interaction.user.id)
    view = JournalView(interaction.user.id, state, bot=bot or getattr(interaction, 'client', None), page=page, realm=realm, notice=notice, guild_id=getattr(interaction.guild, 'id', 0))
    if not view.trip:
        view.trip = await expeditions.latest(interaction.user.id)
    if replace_message:
        view.message = interaction.message
    try:
        await view.load_page()
        view.render()
        await view.publish(interaction, opening=not replace_message)
    except BaseException:
        view.stop()
        raise
    return view
