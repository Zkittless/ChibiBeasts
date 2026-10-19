"""One acknowledged, recoverable Discord Nursery for every egg command."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import io
import logging
from uuid import uuid4

import discord

from utils import db as storage, gear_art, nursery
from utils.nursery_catalog import RESEARCH_COST, RESEARCH_MAX

logger = logging.getLogger('chibibeasts.nursery')
PAGE_SIZE = 10


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def request_key(interaction, action):
    """A retried Discord delivery addresses the same transaction."""
    return f"nursery:{getattr(interaction, 'id', None) or uuid4().hex}:{action}"


def deadline(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return 'Its saved deadline'
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return f'<t:{int(parsed.timestamp())}:R>'


def odds_text(odds):
    return ' · '.join(f"{str(r).replace('_', ' ').title()} {p * 100:.4g}%" for r, p in odds.items() if p > 0)


async def record_sightings(interaction, grants):
    """Guild sightings are idempotent; a committed hatch never depends on media."""
    if not getattr(interaction, 'guild', None):
        return
    from utils.progress import record_bestiary_sighting
    for grant in grants:
        try:
            await record_bestiary_sighting(interaction.guild.id, grant['beast_id'], interaction.user.id)
        except Exception:
            logger.exception('Hatch saved; could not update the guild bestiary')


class NurseryView(discord.ui.LayoutView):
    def __init__(self, user_id, state, *, bot=None, egg_id=None, page='eggs', notice='', grants=None, offer=None):
        super().__init__(timeout=300)
        self.user_id, self.state, self.bot = user_id, state, bot
        self.egg_id = egg_id or next((e['id'] for e in state['eggs'] if e.get('quantity')), state['eggs'][0]['id'] if state['eggs'] else None)
        self.page, self.notice = page, notice
        self.egg_page = self.pool_page = self.research_page = self.reveal_page = 0
        self.incubation_id = None
        self.grants, self.offer, self.targets, self.target = grants or [], offer, [], None
        self.research_offer = False
        self.message, self.closed, self.art_enabled = None, False, True
        self.lock, self.last_input, self.revision = asyncio.Lock(), 0, 0
        self.button_serial = 0
        self.prefix = 'nursery:' + uuid4().hex
        self.container = discord.ui.Container(accent_colour=0xb7ad87)
        self.add_item(self.container)
        # Select identities survive edits so Discord keeps dispatching them.
        self.egg_select = self.select('egg', 'Choose an egg grade and origin')
        self.incubation_select = self.select('incubation', 'Choose an incubating egg')
        self.target_select = self.select('target', 'Choose a companion to research')
        self.render()

    def selected(self):
        return next((e for e in self.state['eggs'] if e['id'] == self.egg_id), None)

    def select(self, action, placeholder):
        select = discord.ui.Select(custom_id=f'{self.prefix}:{action}', placeholder=placeholder)
        select.callback = lambda interaction: self.handle(interaction, action)
        return select

    def button(self, label, action, style=discord.ButtonStyle.secondary, *, disabled=False):
        revision = self.revision
        self.button_serial += 1
        button = discord.ui.Button(label=label[:80], custom_id=f'{self.prefix}:{revision}:{action}:{self.button_serial}', style=style, disabled=disabled)
        button.callback = lambda interaction: self.handle(interaction, action, revision=revision)
        return button

    def text(self, content):
        self.container.add_item(discord.ui.TextDisplay(content))

    def row(self, *items):
        self.container.add_item(discord.ui.ActionRow(*items))

    def portrait(self, text, beast_id):
        if self.art_enabled:
            filename = gear_art.portrait_filename(beast_id)
            self.portraits[filename] = beast_id
            self.container.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=discord.ui.Thumbnail(
                f'attachment://{filename}', description='Your ChibiBeasts companion.')))
        else:
            self.text(text)

    def paging(self, kind, page, count, size):
        total = max(1, (count + size - 1) // size)
        if total > 1:
            self.row(self.button('Previous', kind + ':prev', disabled=page == 0),
                     self.button(f'{page + 1} / {total}', 'noop', disabled=True),
                     self.button('Next', kind + ':next', disabled=page >= total - 1))

    def render_egg(self):
        eggs = self.state['eggs']
        self.egg_page = max(0, min(self.egg_page, max(0, (len(eggs) - 1) // 25)))
        choices = eggs[self.egg_page * 25:(self.egg_page + 1) * 25]
        if choices:
            self.egg_select.options = [discord.SelectOption(label=f"{e['name']} · {e.get('quantity', 0)} owned"[:100], value=e['id'],
                description=f"{e['grade'].title()} · {e['family']} · Trainer Lv.{e['min_level']}"[:100], default=e['id'] == self.egg_id) for e in choices]
            self.row(self.egg_select)
        self.paging('egg_page', self.egg_page, len(eggs), 25)
        egg = self.selected()
        if not egg:
            return self.text('Your Nursery is ready. Refresh to see available eggs.')
        low, high = egg['level_range']
        timed = bool(egg.get('incubation_hours'))
        self.text(f"### {safe(egg['name'])}\n**{safe(egg['grade'].title())} grade · {safe(egg['family'].replace('_', ' ').title())} origin**\n"
                  f"Hatches at **Lv.{low}–{high}** · Trainer Lv.{egg['min_level']} · **{egg.get('quantity', 0)} owned**\n"
                  f"{'Incubates passively for ' + str(egg['incubation_hours']) + 'h; choose one of up to three companions.' if timed else 'Instant hatch; one companion per egg.'}\n"
                  f"**Actual rarity odds** · {odds_text(egg.get('odds', egg['pool']))}\n{safe(egg.get('flavor', ''))}")
        if egg.get('attunement'):
            self.text(f"**Astral attunement · {safe(egg['attunement'].title())}**\nYour next matching instant hatch uses this element at the rolled rarity. If that tier has no match, your attunement stays saved.")
        locked = egg.get('locked', False)
        owned = egg.get('quantity', 0)
        buttons = []
        if egg.get('price', 0) > 0 and egg['id'] != 'world_tree_seed':
            buttons.append(self.button(f"Buy one · {egg['price']:,}g", 'buy', disabled=locked))
        buttons.append(self.button('Begin incubation' if timed else 'Hatch one', 'incubate' if timed else 'hatch', discord.ButtonStyle.success, disabled=locked or not owned))
        if not timed:
            buttons.append(self.button('Hatch quantity…', 'quantity', disabled=locked or not owned))
        buttons.append(self.button('Possible companions', 'pool'))
        self.row(*buttons)
        self.text(f"**Family research · {egg.get('research', 0)} points · {egg.get('research_cost', RESEARCH_COST)} required · Bank cap {RESEARCH_MAX}**\n"
                  'Each duplicate gives one point in this egg family. Spend 8 points to select a companion on your next eligible egg; the egg is still consumed.')
        if timed:
            self.text('Family research can be used when choosing a ready hatchling. It selects this grade’s guaranteed rarity floor.')
        else:
            self.row(self.button('Choose research target', 'research', disabled=egg.get('research', 0) < egg.get('research_cost', RESEARCH_COST) or not owned or locked))
        if locked:
            self.text(f"Unlocks at trainer Lv.{egg['min_level']}. Eggs already in your inventory are kept safely.")

    def render_pool(self):
        egg = self.selected()
        candidates = egg.get('candidates', []) if egg else []
        self.pool_page = max(0, min(self.pool_page, max(0, (len(candidates) - 1) // PAGE_SIZE)))
        self.text(f"### Possible companions · {safe(egg['name'])}\n{odds_text(egg.get('odds', egg['pool']))}\n"
                  'Rarity rolls first; eligible companions within that rarity have equal odds. Incubation offers distinct companions from the same rolled rarity.')
        self.text('\n'.join(f"**{safe(b['name'])}** · {b['rarity'].replace('_', ' ').title()} · {safe(b.get('type', '').title())}"
                            for b in candidates[self.pool_page * PAGE_SIZE:(self.pool_page + 1) * PAGE_SIZE]) or 'No eligible companions. Your egg will be kept.')
        self.paging('pool_page', self.pool_page, len(candidates), PAGE_SIZE)
        self.row(self.button('Back to egg', 'page:eggs'))

    def render_incubations(self):
        rows = self.state.get('incubations', [])
        self.text('### Incubators\nEggs finish at their saved deadline, even while you are away. Optional care adds up to **3 starting bond**; missing care never delays a hatch or changes combat stats.')
        if not rows:
            return self.text('No eggs incubating. Choose an origin egg to begin. All three incubators are available.')
        self.incubation_id = next((r['id'] for r in rows if r['id'] == self.incubation_id), rows[0]['id'])
        self.incubation_select.options = [discord.SelectOption(label=f"#{r['id']} {r.get('egg_name', r['egg_type'])}"[:100], value=str(r['id']),
            description='Ready to choose a companion' if r.get('ready') else 'Finishes ' + str(r['ready_at']), default=r['id'] == self.incubation_id) for r in rows[:25]]
        self.row(self.incubation_select)
        for r in rows[:3]:
            selected = r['id'] == self.incubation_id
            self.text(f"{'**Selected · ' if selected else '**'}#{r['id']} {safe(r.get('egg_name', r['egg_type']))}**\n"
                      f"{'Ready · choose your companion' if r.get('ready') else 'Finishes ' + deadline(r['ready_at'])} · Care bond {min(3, r.get('care_count', r.get('tends_done', 0)) or 0)}/3")
        row = next(r for r in rows if r['id'] == self.incubation_id)
        self.row(self.button('Meet the hatchlings', 'offer', discord.ButtonStyle.success, disabled=not row.get('ready')),
                 self.button('Offer optional care', 'care', disabled=not row.get('care_available')))

    def render_offer(self):
        offer = self.offer
        if not offer or offer.get('claimed'):
            self.text('This choice has been completed. Your companion is saved in your collection; recent hatches show the result.')
            return
        self.text('### A little company, a choice to make\nChoose **one** companion. This saved selection stays the same if you reopen the Nursery. The others remain in the Nursery’s care.')
        for c in offer['candidates'][:3]:
            self.portrait(f"**{safe(c['name'])}** · Lv.{c['level']} · {c['rarity'].replace('_', ' ').title()}\n"
                          f"{safe(c.get('type', '').title())} · {safe(c.get('description', ''))[:190]}", c['beast_id'])
        self.row(*(self.button('Choose ' + c['name'], 'claim:' + str(c['index']), discord.ButtonStyle.success) for c in offer['candidates'][:3]))
        egg = next((e for e in self.state['eggs'] if e['id'] == offer['egg_id']), None)
        if egg and egg.get('research', 0) >= RESEARCH_COST:
            self.text(f"**{egg['research']} family research points** · Spend 8 to select a {egg['grade'].title()} companion from this egg’s family instead of these hatchlings.")
            self.row(self.button('Use family research instead', 'research_offer'))

    def render_research(self):
        egg = self.selected()
        targets = self.targets
        self.research_page = max(0, min(self.research_page, max(0, (len(targets) - 1) // 25)))
        payment = 'this incubating egg' if self.research_offer else f"one {safe(egg['name'])}"
        self.text(f"### {safe(egg['family'].replace('_', ' ').title())} research · {egg.get('research', 0)} points\n"
                  f"Select a **{egg['grade'].title()}** companion from this egg’s existing family pool. Confirmation consumes **{payment} and {RESEARCH_COST} points**. Research selects the guaranteed rarity floor; it gives no free companion or rarity upgrade.")
        selected = next((b for b in targets if b.get('beast_id', b.get('id')) == self.target), None)
        if selected:
            self.text(f"**Selected companion: {safe(selected['name'])}** · {selected['rarity'].replace('_', ' ').title()}")
        if targets:
            self.target_select.options = [discord.SelectOption(label=b['name'][:100], value=b.get('beast_id', b.get('id')),
                description=b['rarity'].replace('_', ' ').title(), default=b.get('beast_id', b.get('id')) == self.target)
                for b in targets[self.research_page * 25:(self.research_page + 1) * 25]]
            self.row(self.target_select)
        self.paging('research_page', self.research_page, len(targets), 25)
        self.row(self.button('Confirm researched hatch', 'research_hatch', discord.ButtonStyle.success, disabled=not self.target),
                 self.button('Back to hatchlings' if self.research_offer else 'Back to egg', 'page:offer' if self.research_offer else 'page:eggs'))

    def render_reveal(self):
        grants = self.state.get('recent', []) if self.page == 'recent' else self.grants or self.state.get('recent', [])
        if not grants:
            return self.text('### Recent hatches\nYour next companion’s arrival will be saved here. Completed hatches survive a disconnect.')
        self.reveal_page = max(0, min(self.reveal_page, len(grants) - 1))
        grant = grants[self.reveal_page]
        beast = storage.get_beast_data(grant['beast_id']) or {}
        earned = grant.get('research_earned', 1 if grant.get('duplicate') and not grant.get('researched') else 0)
        research_line = ('Family research complete · 8 points spent.' if grant.get('researched') else
                         f'A familiar face · +{earned} family research.' if earned else
                         f'A familiar face · Family research bank full ({RESEARCH_MAX}).' if grant.get('duplicate') else
                         'A new face for your collection.')
        self.portrait(f"### Welcome, {safe(grant.get('name', beast.get('name', grant['beast_id'])))}!\n"
                      f"**Lv.{grant['level']} · {grant['rarity'].replace('_', ' ').title()}**\n{safe(beast.get('description', 'Your new companion has arrived.'))[:300]}\n"
                      f"{research_line}"
                      f"\nCare bond bonus **+{grant.get('bond', 0)}** · Saved to your collection.", grant['beast_id'])
        self.paging('reveal_page', self.reveal_page, len(grants), 1)
        self.row(self.button('Choose another egg', 'page:eggs'), self.button('Open incubators', 'page:incubations'))

    def render(self):
        self.revision += 1
        self.container.clear_items()
        self.portraits = {}
        player = self.state['player']
        self.text(f"## The Nursery\nTrainer Lv.{player['level']} · **{player['gold']:,} gold** · {len(self.state.get('incubations', []))}/3 incubators")
        if self.closed:
            self.text('This page has closed. Open /nursery to continue; eggs, choices and companions are saved.')
            return
        self.row(self.button('Eggs', 'page:eggs'), self.button('Incubators', 'page:incubations'), self.button('Recent hatches', 'page:recent'), self.button('Refresh', 'refresh'))
        if self.notice:
            self.text(safe(self.notice)[:420])
        {'eggs': self.render_egg, 'pool': self.render_pool, 'incubations': self.render_incubations,
         'offer': self.render_offer, 'research': self.render_research, 'recent': self.render_reveal, 'reveal': self.render_reveal}.get(self.page, self.render_egg)()

    async def publish(self, interaction, *, opening=False):
        portraits = {}
        if self.art_enabled:
            for name, species in self.portraits.items():
                rendered = await gear_art.render_portrait(species)
                if rendered is None:
                    self.art_enabled = False
                    self.render()
                    break
                portraits[name] = rendered
        with ExitStack() as stack:
            files = []
            if self.art_enabled:
                for name, portrait in portraits.items():
                    buffer = stack.enter_context(io.BytesIO(portrait.data))
                    file = discord.File(buffer, filename=name)
                    stack.callback(file.close)
                    files.append(file)
            async def deliver(with_art):
                async with asyncio.timeout(15):
                    if opening:
                        return await interaction.followup.send(view=self, files=files if with_art else [], allowed_mentions=discord.AllowedMentions.none(), wait=True)
                    return await interaction.edit_original_response(view=self, attachments=files if with_art else [], allowed_mentions=discord.AllowedMentions.none())
            try:
                self.message = await deliver(self.art_enabled)
            except discord.HTTPException as error:
                if isinstance(error, discord.NotFound) or not files:
                    raise
                logger.warning('Nursery artwork rejected (%s); showing text controls', error.status)
                self.art_enabled = False
                self.render()
                self.message = await deliver(False)

    async def handle(self, interaction, action, *, revision=None, quantity=None):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('Open /nursery to care for your own eggs.', ephemeral=True)
        if self.closed or self.is_finished():
            return await interaction.response.send_message('This page has closed. Open /nursery to continue.', ephemeral=True)
        # A modal must be the initial interaction response. It does no database
        # or artwork work; its submission uses the same atomic service checks.
        if action == 'quantity':
            if revision != self.revision or self.lock.locked():
                return await interaction.response.send_message('Use the refreshed Nursery controls.', ephemeral=True)
            from utils.modals import QuantityModal
            egg_id = self.egg_id
            async def submitted(click, amount):
                return await self.handle(click, 'batch:' + egg_id, quantity=amount)
            return await interaction.response.send_modal(QuantityModal('Hatch eggs · up to 25', self.selected()['name'], min(25, self.selected().get('quantity', 0)), submitted))
        await interaction.response.defer()
        async with self.lock:
            input_id = getattr(interaction, 'id', 0) or 0
            if self.closed:
                return
            if (input_id and input_id < self.last_input) or (revision is not None and revision != self.revision):
                return await interaction.followup.send('That control belongs to an older page. Use the refreshed Nursery.', ephemeral=True)
            self.last_input = max(self.last_input, input_id)
            self.notice = ''
            try:
                if action.startswith('page:'):
                    self.page = action.split(':', 1)[1]
                elif action == 'egg':
                    self.egg_id = interaction.data['values'][0]
                    self.pool_page = self.research_page = 0
                    self.target = None
                    self.research_offer = False
                    self.page = 'eggs'
                elif action == 'incubation':
                    self.incubation_id = int(interaction.data['values'][0])
                elif action == 'target':
                    self.target = interaction.data['values'][0]
                elif action in {'pool', 'research', 'research_offer'}:
                    self.page = 'pool' if action == 'pool' else 'research'
                    if action in {'research', 'research_offer'}:
                        self.research_offer = action == 'research_offer'
                        if self.research_offer:
                            if not self.offer or self.offer.get('claimed'):
                                raise ValueError('Choose a ready incubation first.')
                            self.egg_id = self.offer['egg_id']
                        self.targets = await nursery.research_targets(self.user_id, self.egg_id)
                        self.target = None
                elif action.startswith(('egg_page:', 'pool_page:', 'research_page:', 'reveal_page:')):
                    kind, direction = action.split(':')
                    setattr(self, kind, getattr(self, kind) + (-1 if direction == 'prev' else 1))
                elif action == 'buy':
                    from utils.shops import purchase_gold
                    receipt_key = request_key(interaction, 'buy')
                    result = await purchase_gold(self.user_id, self.egg_id, request_key=receipt_key)
                    from utils.events import record_event
                    await record_event(self.user_id, 'spend_gold', amount=result['total'], event_key='nursery-purchase:' + receipt_key)
                    self.notice = f"Bought one {result['item']['name']} for {result['total']:,} gold."
                elif action == 'incubate':
                    row = await nursery.incubate(self.user_id, self.egg_id, request_key=request_key(interaction, action))
                    self.incubation_id, self.page = row['id'], 'incubations'
                    self.notice = 'Your egg is settling in. It finishes automatically; care is optional.'
                elif action in {'hatch', 'research_hatch'} or action.startswith('batch:'):
                    egg_id = action.split(':', 1)[1] if action.startswith('batch:') else self.egg_id
                    if action == 'research_hatch' and self.research_offer:
                        self.grants = [await nursery.claim_offer(self.user_id, self.offer['id'], 0,
                            request_key=request_key(interaction, 'claim_research'), research_target=self.target)]
                    else:
                        self.grants = await nursery.hatch(self.user_id, egg_id, quantity or 1, request_key=request_key(interaction, action),
                            research_target=self.target if action == 'research_hatch' else None)
                    await record_sightings(interaction, self.grants)
                    self.reveal_page, self.page = 0, 'reveal'
                elif action == 'care':
                    await nursery.care(self.user_id, self.incubation_id)
                    self.notice = 'A quiet moment together. Optional care adds starting bond; your hatch deadline stays the same.'
                elif action == 'offer':
                    self.offer = await nursery.prepare_offer(self.user_id, self.incubation_id)
                    self.page = 'offer'
                elif action.startswith('claim:'):
                    if not self.offer:
                        raise ValueError('Open a saved hatchling selection first.')
                    grant = await nursery.claim_offer(self.user_id, self.offer['id'], int(action.split(':')[1]), request_key=request_key(interaction, 'claim'))
                    await record_sightings(interaction, [grant])
                    self.grants, self.reveal_page, self.page = [grant], 0, 'reveal'
            except (ValueError, KeyError) as error:
                self.notice = str(error)
            self.state = await nursery.snapshot(self.user_id)
            self.render()
            await self.publish(interaction)

    async def on_timeout(self):
        self.closed = True
        async with self.lock:
            self.render()
            if self.message:
                try:
                    async with asyncio.timeout(15):
                        await self.message.edit(view=self, attachments=[])
                except (discord.HTTPException, TimeoutError):
                    pass

    async def on_error(self, interaction, error, item):
        logger.error('Nursery interaction failed', exc_info=error)
        content = 'The Nursery could not refresh. Open /nursery; completed hatches and saved choices are kept.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            pass


async def open_nursery(interaction, *, bot=None, already_deferred=False, egg_id=None, page='eggs', notice='', grants=None, offer=None):
    if not already_deferred:
        await interaction.response.defer()
    await storage.get_or_create_player(interaction.user.id, str(interaction.user))
    if grants:
        await record_sightings(interaction, grants)
    state = await nursery.snapshot(interaction.user.id)
    view = NurseryView(interaction.user.id, state, bot=bot, egg_id=egg_id, page=page, notice=notice, grants=grants, offer=offer)
    try:
        await view.publish(interaction, opening=True)
    except BaseException:
        view.stop()
        raise
    return view
