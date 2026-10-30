"""A native Discord Field Journal, with acknowledged, revision-checked choices."""
import asyncio
from contextlib import ExitStack
import io
import logging
from uuid import uuid4

import aiosqlite
import discord

from utils import adventure_party, battle_art, crafting, db as storage, expeditions, gear_art, journal_state, story, journal_team, journal_sessions, journal_team_art, journal_log, journal_history
from utils.artwork import PROJECT_ROOT
from utils.catalog import load_catalog
from utils.habitats import biome_key, starter_training_destination
from utils.equipment import definitions as equipment_definitions, dormant_label

logger = logging.getLogger('chibibeasts.journal')
PAGES = ('adventure', 'team', 'companions', 'workshop', 'story', 'activities')
PAGE_SIZE = 25
REALMS = journal_state.BIOMES
LANDMARKS = {
    'woods': ('a root-wrapped shrine', 'Fireflies drift between the roots. Three paths unfold beneath the canopy.'),
    'ember': ('a cracked forge shrine', 'Warm ash swirls around your boots. Your companion notices three ways onward.'),
    'glacial': ('a frostbound cairn', 'Blue light travels through the ice. Three trails branch into the stillness.'),
    'trenches': ('a drowned waystone', 'Lantern algae light the seafloor. Three currents carry signs of life.'),
    'loom': ('a fraying star loom', 'Loose threads of starlight drift past. Three possibilities wait to be followed.'),
    'woods_deep': ('a lantern-lit marsh shrine', 'Moonlight glimmers in the reeds. Three paths wind across the still water.'),
    'ember_deep': ('a half-buried sunforge marker', 'Warm wind traces the dunes. Three paths lead between the sandstone ridges.'),
    'glacial_deep': ('a stormglass summit cairn', 'Distant thunder rolls across the peaks. Three trails follow the sheltered ridgelines.'),
    'trenches_deep': ('a coral-wrapped caldera beacon', 'Warm currents stir above dark volcanic stone. Three channels weave between the reefs.'),
    'loom_deep': ('a starfall garden waystone', 'Fallen starlight settles among luminous blooms. Three paths cross the floating gardens.'),
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
        self.show_story_dialogue, self.show_daily_details = False, False
        self.story_lock = ''
        self.dailies = []
        self.log_page, self.log_total = 0, 0
        self.log_kind = 'expeditions'
        self.log_entries = []
        self.log_selected_id, self.log_selected = None, None
        self.reviewing = False
        self.awakening_scene = ''
        self.message = None
        self.closed, self.mutating, self.art_enabled = False, False, True
        self.message_repurposed = False
        self._returned_battles = set()
        self.team_art = None
        self.journey_media, self.journey_presentation = None, None
        self.journey_playing = False
        self.team_mode = 'exploration'
        self.lock = asyncio.Lock()
        self.last_input = 0
        self.prefix = 'journal:' + uuid4().hex
        self._button_serial = 0
        self.container = discord.ui.Container(accent_colour=0xb7ad87)
        self.add_item(self.container)
        self.tools = discord.ui.ActionRow(self.button('Pin a goal', 'page:goals'), self.button('Refresh', 'refresh'))
        self._static_button_count = self._button_serial
        self.page_menu = self.select('page_menu', 'Field Journal')
        self.menu = discord.ui.ActionRow(self.page_menu)
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
        if self.journey_playing and trip and trip['payload'].get('adventure_version') == 3:
            from utils import journal_adventure
            journal_adventure.render(self, trip)
            return
        self.row(self.button('Battle log', 'page:battle_log'), self.button('Tower Trials', 'launch:challenge'))
        if not trip or trip['status'] == 'complete':
            if trip:
                self.text(f"**Back from {REALMS[trip['realm']]['name']}** · Results in Battle log.")
            e = self.state.energy
            self.text(f"### Choose an expedition\n{e.charges}/{expeditions.CAPACITY} exploration charges · Regenerates every {wait_text(e.interval)}\n"
                      'Confirm your destination to set out with your saved team.')
            self.render_compact_team()
            self.realm_select.options = [discord.SelectOption(label=(b['name'] + (f" · Locked Lv.{b['min_level']}" if self.state.player['level'] < b['min_level'] else ''))[:100],
                value=k, default=k == self.realm,
                description=f"Trainer Lv.{b['min_level']}+ required; companion Lv.{b['level_range'][0]}–{b['level_range'][1]} recommended") for k, b in REALMS.items()]
            self.row(self.realm_select)
            destination = REALMS[self.realm]
            low, high = destination['level_range']
            self.text(f"**{destination['name']}** · Trainer Lv.{destination['min_level']} required · Companion Lv.{low}–{high} recommended.")
            locked = self.state.player['level'] < REALMS[self.realm]['min_level']
            saved = self.state.explore_default
            saved_name = REALMS[saved]['name'] if saved in REALMS else 'Unavailable destination' if saved else 'None'
            self.text(f'**Saved destination · {saved_name}**')
            self.row(self.button('Save destination', 'save_destination:' + self.realm,
                                 disabled=locked or saved == self.realm),
                     self.button('Clear default', 'clear_destination', disabled=saved is None))
            traveller = next((r for r in self.state.workshop.companions if r['is_active']), None)
            if traveller:
                training = starter_training_destination(storage.get_beast_data(traveller['beast_id']), traveller['level'], self.state.player['level'])
                if training:
                    self.text(f"Suggested starter training: **{REALMS[training]['name']}**")
            healthy = traveller and traveller['hp'] > 0 and not storage.is_knocked_out(traveller) and not traveller['listed']
            self.row(self.button('Start adventure · 1 charge', 'begin', discord.ButtonStyle.success,
                                 disabled=not e.charges or locked or not healthy))
            if not healthy:
                self.text('Choose a healthy active companion before setting out.')
                self.row(self.button('Choose a companion', 'page:roster'))
            if not e.charges:
                self.text('Next charge in ' + wait_text(e.next_in) + '. Up to three charges wait for you between visits.')
            return
        p = trip['payload']
        if p.get('adventure_version') == 3:
            from utils import journal_adventure
            journal_adventure.render(self, trip)
            return
        if p.get('adventure_version', 0) >= 2:
            self.render_branching_trip(trip)
            return
        landmark, flavor = LANDMARKS.get(trip['realm'], LANDMARKS[biome_key(trip['realm'])])
        e = self.state.energy
        recharge = 'Bank full' if not e.next_in else 'Next charge in ' + wait_text(e.next_in)
        self.text(f"### {REALMS[trip['realm']]['name']}\nStep {p['stage'] + 1}/3 · Supplies saved\n"
                  f"**Exploration {e.charges}/{expeditions.CAPACITY}** · {recharge}")
        self.render_compact_team(trip)
        if trip['status'] == 'battle':
            self.text('**Encounter in progress.** Use the battle controls below. Your route is saved.')
            if not p.get('battle_started'):
                self.row(self.button('Continue encounter', 'battle_continue', discord.ButtonStyle.primary))
            return
        if p['stage'] == 0:
            self.text(flavor)
        elif p['stage'] == 1 and p['route'] == 'tracks':
            beast = storage.get_beast_data(p['encounter'])
            self.portrait(f"### {beast['name']} · Lv.{p.get('encounter_level', REALMS[trip['realm']]['level_range'][0])} appears", beast['id'])
        elif p['stage'] == 1 and p['route'] == 'gather':
            self.text('**A promising gathering spot**\nChoose one material to bring home.')
        elif p['stage'] == 1:
            self.text(f'**You reach {landmark}.**\n**Repair together:** +2 bond, +5 happiness.\n**Study its markings:** a little extra trainer EXP.\n**Share a rest:** recover 5% max HP, +3 happiness. Recovery cannot revive a fallen companion.')
        else:
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
        from utils.journal_adventure import render_choices
        render_choices(self, trip, [dict(value=option.value.split(':', 2)[2], label=option.label,
                                        description=option.description) for option in options])

    def render_battle_log(self):
        journal_log.render(self)

    def render_compact_team(self, trip=None):
        rows = {row['id']: row for row in self.state.workshop.companions}
        lead = next((row['id'] for row in rows.values() if row['is_active']), None)
        ids = trip['payload'].get('party_ids', [trip['companion_id']]) if trip else list(dict.fromkeys(
            ([lead] if lead else []) + list(self.state.team_slots.values())))[:3]
        names = [safe(rows[key]['nickname'] or storage.get_beast_data(rows[key]['beast_id'])['name']) for key in ids if key in rows]
        team = ' · '.join([names[0] + ' (lead)', *names[1:]]) if names else 'Choose a healthy lead in the Team tab.'
        self.text('**Team** · ' + team)

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
        self.render_compact_team(trip)
        if trip['status'] == 'battle':
            self.text('**Encounter in progress.** Use the battle controls below. Your route and team are saved.')
            if not p.get('battle_started'):
                self.row(self.button('Continue encounter', 'battle_continue', discord.ButtonStyle.primary))
            return
        if p.get('encounter') and any(choice['value'] == 'challenge' for choice in page['choices']):
            beast = storage.get_beast_data(p['encounter'])
            self.portrait(f"**{safe(beast['name'])} · Lv.{p.get('encounter_level', 1)}**", beast['id'])
        self.text(page['body'])
        from utils.journal_adventure import render_choices
        render_choices(self, trip, page['choices'])

    def render_team(self):
        self.team_select.disabled = journal_team.frozen(self)  # Retain the older dispatch guard.
        journal_team.render(self)

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
                 self.button('Play together', 'launch:play', disabled=not row['is_active']),
                 self.button('Care', f"care:{row['id']}"))
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
            if chapter.get('intro') and self.show_story_dialogue:
                self.text('\n\n'.join(chapter['intro'])[:2200])
            if chapter.get('intro'):
                self.row(self.button('Hide dialogue' if self.show_story_dialogue else 'Read dialogue', 'story_dialogue'))
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
            complete = sum(q['progress'] >= q['target'] for q in self.dailies)
            self.text(f"**Daily objectives · {complete}/{len(self.dailies)} complete**")
            if self.show_daily_details:
                self.text('\n\n'.join(f"{min(q['progress'], q['target'])}/{q['target']} · {q['desc'].format(target=q['target'])}\n"
                                      f"Reward · {q['reward_gold']:,} gold · {q['reward_exp']} EXP" for q in self.dailies))
        self.row(self.button('Claim daily', 'launch:daily', discord.ButtonStyle.success, disabled=not daily_ready), self.button('Shop', 'launch:shop'), self.button('Hide objectives' if self.show_daily_details else 'Daily objectives', 'daily_details'))
        self.text('**Battles and community**')
        self.row(self.button('Tower Trials', 'launch:challenge'), self.button('Guild', 'launch:guild'), self.button('Guild raid', 'launch:raid'))
        self.row(self.button('Nursery', 'page:nursery'), self.button('Perks', 'panel:perks'), self.button('Command guide', 'commands'))

    def render(self):
        self.page_menu.disabled = self.path_select.disabled = False
        for control in self.tools.children:
            control.disabled = False
        self._button_serial = self._static_button_count
        self.portraits = {}
        self.container.clear_items()
        if self.art_enabled and not (self.page == 'team' and self.team_art) and not (self.page == 'adventure' and self.journey_media):
            self.container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f'attachment://{self.header.name}', description='Field Journal · ' + REALMS[self.art_realm]['name'])))
        current = 'adventure' if self.page == 'battle_log' else self.page
        self.page_menu.options = [discord.SelectOption(label=key.title(), value=key, default=key == current)
                                  for key in ('home',) + PAGES]
        self.page_menu.placeholder = 'Field Journal · ' + self.page.replace('_', ' ').title()
        self.container.add_item(self.menu)
        self.container.add_item(self.tools)
        if self.notice:
            self.text(self.notice[:750])
        render = getattr(self, 'render_' + self.page, self.render_home)
        render()
        if self.closed or self.journey_playing:
            for item in self.walk_children():
                if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                    item.disabled = True
        if self.content_length() > 4000 or self.total_children_count > 40:
            raise ValueError('Journal page exceeds Discord message limits.')

    @property
    def art_realm(self):
        return battle_art.realm_key(self.trip['realm'] if self.page in {'adventure', 'team'} and self.trip and self.trip['status'] != 'complete' else self.realm)

    @property
    def header(self):
        return PROJECT_ROOT / 'data' / 'journal' / f'journal-{self.art_realm}.png'

    async def publish(self, interaction, *, opening=False, channel_send=False):
        if self.page == 'team' and self.art_enabled:
            self.team_art = await journal_team_art.render_team([row for _, row in journal_team.roster(self)], self.art_realm)
            self.render()
        previous = {} if opening else {a.filename: a for a in getattr(self.message, 'attachments', [])}
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
                stage = self.team_art if self.page == 'team' else self.journey_media if self.page == 'adventure' else None
                paths = {} if stage else {self.header.name: self.header}
                for name, path in paths.items():
                    if name in previous:
                        attachments.append(previous[name])
                    else:
                        file = discord.File(path)
                        stack.callback(file.close)
                        files.append(file)
                if stage:
                    if stage.name in previous:
                        attachments.append(previous[stage.name])
                    else:
                        buffer = stack.enter_context(io.BytesIO(stage.data))
                        file = discord.File(buffer, filename=stage.name)
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
                        if channel_send:
                            return await interaction.channel.send(view=self, files=files if with_art else [], allowed_mentions=discord.AllowedMentions.none())
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
        if self.page == 'battle_log':
            await journal_log.load(self)

    async def launch(self, interaction, name):
        if name == 'challenge':
            from utils.trial_panel import open_trials
            async with self.lock:
                return await open_trials(interaction, bot=self.bot, destination=self.realm, source=self)
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
        if action == 'page_menu':
            value = interaction.data.get('values', [''])[0]
            if value not in ('home',) + PAGES:
                return await interaction.response.send_message('Choose a page from the current journal.', ephemeral=True)
            action = 'page:roster' if value == 'companions' else 'page:' + value
        if action == 'panel:perks':
            from utils.perks_view import open_perks
            return await open_perks(interaction, ephemeral=True)
        if action == 'commands':
            await interaction.response.defer(ephemeral=True)
            from cogs.utilities import command_guide
            return await command_guide(interaction)
        if action.startswith('care:') or action == 'launch:care':
            from utils.care_view import open_care
            if action == 'launch:care':
                row = self.selected_companion() or next((row for row in self.state.workshop.companions if row['is_active']), None)
                row_id = row['id'] if row else None
            else:
                try:
                    row_id = int(action.split(':', 1)[1])
                except (ValueError, IndexError):
                    return await interaction.response.send_message('Choose a companion from your current journal.', ephemeral=True)
            return await open_care(interaction, beast_row_id=row_id)
        if action.startswith('launch:'):
            try:
                return await self.launch(interaction, action.split(':', 1)[1])
            except ValueError as error:
                if interaction.response.is_done():
                    return await interaction.followup.send(safe(error), ephemeral=True)
                return await interaction.response.send_message(safe(error), ephemeral=True)
        mutation = action in {'begin', 'path', 'battle_continue', 'make_active', 'team_slot', 'awaken', 'story_claim', 'clear_destination', 'journey_action'} or action.startswith(('team_assign:', 'team_clear:', 'save_destination:', 'path:', 'journey_act:', 'journey_next:', 'journey_return:'))
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
                    if action == 'page:roster':
                        from utils.roster_view import open_roster
                        result = await open_roster(interaction, already_deferred=True)
                        if result:
                            self.closed = True
                            self.stop()
                            await journal_sessions.discard(self, interaction)
                        return
                    if action == 'page:nursery':
                        from utils.nursery_view import open_nursery
                        result = await open_nursery(interaction, bot=self.bot, already_deferred=True)
                        if result:
                            self.closed = True
                            self.stop()
                            await journal_sessions.discard(self, interaction)
                        return
                    if action in {'page:workshop', 'goal_workshop'}:
                        from utils.workshop import open_workshop
                        recipe = self.state.goal['target'] if action == 'goal_workshop' and self.state.goal and self.state.goal['kind'] == 'recipe' else None
                        result = await open_workshop(interaction, recipe_id=recipe, already_deferred=True, replace_message=True)
                        if result:
                            self.message_repurposed = True
                            await journal_sessions.forget(self, interaction)
                            self.closed = True
                            self.stop()
                        return
                    if action.startswith('page:'):
                        self.page = action.split(':', 1)[1]
                        if self.page == 'team':
                            self.team_role, self.team_candidate_id, self.team_page = None, None, 0
                        if self.page == 'battle_log':
                            self.log_page, self.log_selected_id = 0, None
                            self.log_kind = 'expeditions'
                        self.reviewing = False
                        self.awakening_scene = ''
                        if self.page == 'adventure':
                            self.trip = self.state.trip or self.trip
                    elif action in {'log_prev', 'log_next', 'log_entry', 'log_back', 'log_kind'}:
                        if self.page != 'battle_log':
                            raise ValueError('Open the Battle log before choosing a past trip.')
                        if action == 'log_kind':
                            kind = interaction.data['values'][0]
                            if kind not in journal_history.KINDS:
                                raise ValueError('Choose a current history category.')
                            self.log_kind, self.log_page, self.log_selected_id = kind, 0, None
                        elif action == 'log_entry':
                            selected = interaction.data['values'][0]
                            if selected not in {trip['id'] for trip in self.log_entries}:
                                raise ValueError('Choose a trip from the current log page.')
                            self.log_selected_id = selected
                        elif action == 'log_back':
                            self.log_selected_id = None
                        else:
                            self.log_page += -1 if action == 'log_prev' else 1
                            self.log_selected_id = None
                    elif action == 'realm':
                        key = interaction.data['values'][0]
                        if key not in REALMS:
                            raise ValueError('Choose a destination from this journal.')
                        self.realm = key
                    elif action == 'begin':
                        self.trip = await expeditions.begin(self.user_id, self.realm, guild_id=getattr(interaction.guild, 'id', 0), adventure_version=3)
                    elif action.startswith('save_destination:'):
                        realm = action.split(':', 1)[1]
                        if self.state.trip or realm != self.realm:
                            raise ValueError('Choose a destination on the current selection page before saving it.')
                        await journal_state.save_explore_default(self.user_id, realm)
                        self.notice = 'Default destination saved. /explore will select it for you to confirm.'
                    elif action == 'clear_destination':
                        await journal_state.clear_explore_default(self.user_id)
                        self.notice = 'Default destination cleared. Future /explore openings select the Woods.'
                    elif action == 'path' or action.startswith('path:'):
                        if action == 'path':
                            trip_id, version, choice = interaction.data['values'][0].split(':', 2)
                        else:
                            _, trip_id, version, index = action.split(':')
                            current = self.state.trip
                            if not current or current['id'] != trip_id or current['version'] != int(version):
                                raise ValueError('That choice belongs to an older page. Use the current adventure buttons.')
                            choices = list(expeditions.options(current))
                            if current['payload'].get('adventure_version', 0) < 2 and 'return' not in choices:
                                choices.append('return_early')
                            if not 0 <= int(index) < len(choices):
                                raise ValueError('Choose a current adventure action.')
                            choice = choices[int(index)]
                        self.trip = await expeditions.choose(self.user_id, trip_id, int(version), choice)
                        if self.trip['status'] == 'battle' and self.trip['payload'].get('adventure_version') != 3:
                            battle = self.trip
                        elif self.trip['status'] == 'complete' and self.trip['payload']['caught']:
                            from cogs.hatch import check_collection_completion
                            await check_collection_completion(interaction, storage.get_beast_data(self.trip['payload']['encounter']), storage.load_beasts())
                    elif action == 'journey_action' or action.startswith(('journey_act:', 'journey_next:', 'journey_return:')):
                        from utils import journey_service, trial_combat
                        if action == 'journey_action' or action.startswith('journey_act:'):
                            trip_id, version, index = (interaction.data['values'][0].split(':') if action == 'journey_action'
                                                       else action.split(':')[1:])
                            current = self.state.trip
                            if not current or current['id'] != trip_id or current['version'] != int(version):
                                raise ValueError('Use the refreshed adventure controls.')
                            actions = trial_combat.legal_actions(current['payload']['combat'])
                            if not 0 <= int(index) < len(actions) or not actions[int(index)]['enabled']:
                                raise ValueError('Choose a current combat action.')
                            choice = actions[int(index)]['choice']
                        else:
                            verb, trip_id, version = action.split(':')
                            choice = 'next' if verb == 'journey_next' else 'return'
                        self.trip = await journey_service.battle_action(self.user_id, trip_id, int(version), choice)
                    elif action == 'battle_continue':
                        if not self.state.trip or self.state.trip['status'] != 'battle':
                            raise ValueError('This encounter has already ended. Refresh your saved trip.')
                        battle = self.trip = self.state.trip
                    elif action == 'companion':
                        selected = int(interaction.data['values'][0])
                        if selected not in {r['id'] for r in self.state.workshop.companions}:
                            raise ValueError('That companion has left your collection.')
                        self.companion_id, self.reviewing, self.awakening_scene = selected, False, ''
                    elif action.startswith('team_role:'):
                        journal_team.select_role(self, action.split(':', 1)[1])
                    elif action.startswith('team_mode:'):
                        mode = action.split(':', 1)[1]
                        if mode not in {'exploration', 'raid'}:
                            raise ValueError('Choose Exploration or Raids.')
                        self.page, self.team_mode = 'team', mode
                        self.team_role, self.team_candidate_id, self.team_page = None, None, 0
                        self.team_art = None
                    elif action == 'team_candidate':
                        journal_team.select_candidate(self, interaction.data['values'][0])
                    elif action in {'team_prev', 'team_next'}:
                        journal_team.page_candidates(self, -1 if action == 'team_prev' else 1)
                    elif action.startswith('team_assign:'):
                        role, row_id = journal_team.assignment(self, action.split(':', 1)[1])
                        if role in journal_team.RAID_ROLES:
                            await storage.set_raid_slot(self.user_id, row_id, int(role[-1]))
                        elif role == 'lead':
                            row = next(r for r in self.state.workshop.companions if r['id'] == row_id)
                            await storage.set_active_companion(self.user_id, row['player_number'], expected_row_id=row_id)
                        else:
                            await adventure_party.set_slot(self.user_id, row_id, int(role))
                        self.notice = journal_team.ROLE_NAMES[role] + ' assigned. Your team is saved.'
                    elif action.startswith('team_clear:'):
                        slot = journal_team.removal(self, action.split(':', 1)[1])
                        if isinstance(slot, str):
                            await storage.clear_raid_slot(self.user_id, int(slot[-1]))
                        else:
                            await adventure_party.clear_slot(self.user_id, slot)
                        self.team_candidate_id = None
                        self.notice = 'Slot cleared. Your other team slots stay assigned.'
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
                        self.show_story_dialogue = False
                    elif action == 'story_dialogue':
                        self.show_story_dialogue = not self.show_story_dialogue
                    elif action == 'daily_details':
                        self.show_daily_details = not self.show_daily_details
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
                if self.page == 'adventure' and self.trip and self.trip['payload'].get('adventure_version') == 3:
                    from utils import journal_adventure
                    animate = not self.notice and (action == 'journey_action' or action.startswith(('journey_act:', 'journey_next:'))
                        or (action in {'path', 'begin'} or action.startswith('path:')) and self.trip['status'] == 'battle')
                    await journal_adventure.present(self, interaction, animate=animate)
                else:
                    self.render()
                    await self.publish(interaction)
            if battle:
                receipt = {}
                try:
                    self.trip = await expeditions.run_battle(interaction, battle, receipt=receipt)
                except (ValueError, discord.HTTPException, TimeoutError) as error:
                    logger.info('Expedition encounter ended early: %s', error)
                    self.notice = 'The encounter closed. Your expedition and gathered supplies are saved.'
                if receipt.get('owned'):
                    await self.resume_after_battle(interaction, battle)
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

    async def resume_after_battle(self, interaction, battle):
        """Publish below the result, after the runner has settled every reward."""
        token = battle['payload']['battle_token']
        async with self.lock:
            if token in self._returned_battles:
                return
            settled = await expeditions.read(self.user_id, battle['id'])
            if not settled or settled['status'] == 'battle':
                return
            self._returned_battles.add(token)
        try:
            return await open_journal(interaction, bot=self.bot, page='adventure', realm=battle['realm'],
                already_deferred=True, channel_send=True, source=self,
                notice='Your expedition is ready to continue below.')
        except (discord.HTTPException, TimeoutError):
            # Publishing failed: the previous journal is still the recovery
            # point. Do not delete it or retry a send with an uncertain result.
            logger.exception('Could not bring the saved expedition journal below its encounter')
            return None

    async def on_timeout(self):
        self.closed = True
        async with self.lock:
            if self.message_repurposed:
                return
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


async def open_journal(interaction, *, bot=None, page='home', realm=None, notice='', already_deferred=False, replace_message=False,
                       begin_expedition=False, channel_send=False, source=None, team_mode='exploration', log_kind='expeditions'):
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
        await expeditions.report_safely(trip)
    if pending:
        state = await journal_state.snapshot(interaction.user.id)
    requested_realm = realm
    realm, realm_notice = journal_state.explore_destination(state, requested_realm)
    notice = realm_notice or notice
    if begin_expedition and not state.trip:
        try:
            await expeditions.begin(interaction.user.id, realm, guild_id=getattr(interaction.guild, 'id', 0), adventure_version=3)
        except ValueError as error:
            # Missing charges, recovery, locked realms and a simultaneous
            # opener all remain visible in the journal instead of a dead end.
            notice = str(error)
        state = await journal_state.snapshot(interaction.user.id)
    view = JournalView(interaction.user.id, state, bot=bot or getattr(interaction, 'client', None), page=page, realm=realm, notice=notice, guild_id=getattr(interaction.guild, 'id', 0))
    view.team_mode = team_mode if team_mode in {'exploration', 'raid'} else 'exploration'
    view.log_kind = log_kind if log_kind in journal_history.KINDS else 'expeditions'
    if not view.trip:
        view.trip = await expeditions.latest(interaction.user.id)
    if replace_message:
        view.message = interaction.message
    try:
        async def send():
            # Openers serialize by trainer/channel. Refresh after waiting for
            # that lock so an earlier snapshot cannot replace newer rewards.
            view.state = await journal_state.snapshot(view.user_id)
            if requested_realm is None:
                view.realm, realm_notice = journal_state.explore_destination(view.state)
                view.notice = realm_notice or view.notice
            view.trip = view.state.trip or await expeditions.latest(view.user_id)
            await view.load_page()
            from utils import journal_adventure
            await journal_adventure.prepare_still(view)
            view.render()
            await view.publish(interaction, opening=not replace_message, channel_send=channel_send)
            return view.message
        await journal_sessions.publish(view, interaction, send, replace=replace_message, source=source)
    except BaseException:
        view.stop()
        raise
    return view
