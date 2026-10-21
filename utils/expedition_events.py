"""Pure, saved expedition pages. Choices change the next stop, never reroll it.

All pages and their exact offers are persisted with the trip. The seeded flavour
selector varies trips without making reopening a journal a new random roll.
"""
import hashlib

from utils.habitats import biome_key, destination_key

# Discoveries deliberately trail the region's wild encounters. Even the Loom
# cannot give away Epic/Legendary/Divine incubators or the story-only seed.
REGIONAL_NEST_EGGS = {
    'woods': ('sprout_pod', 'pebble_shell'),
    'ember': ('soot_hatchling',),
    'glacial': ('gale_nest',),
    'trenches': ('dewdrop_bulb',),
    'loom': ('prism_sphere',),
}
NEST_EVENTS = frozenset(('rescue', 'discovery', 'camp', 'deep'))
MAX_NEST_CHANCE = .10
NEST_RELEASE_BUDGET = .25
NEST_SHOP_BUDGET = 2.


def prepare_nest(p, realm):
    """Freeze one optional offer at departure, with a bounded currency value.

    The upper budget uses every applicable hatch bonus and merchant resale.
    Replacing the material makes the actual added reward smaller still. No
    read, route choice, push, or restart calls this function for a saved trip.
    """
    from utils.economy_balance import RELEASE_VALUES, item_sell_price
    from utils.nursery_catalog import definition, rarity_pool

    candidates = [definition(egg_id) for egg_id in REGIONAL_NEST_EGGS[biome_key(realm)]]
    candidates = [egg for egg in candidates if egg['min_level'] <= p['level']]
    p.update(nest_version=1, nest_chance=0., egg_offer=None,
             egg_reward=None, egg_granted=False)
    if not candidates:
        return
    egg = _variant(p, candidates, 'nest-family')
    boosted = rarity_pool(egg['id'],
                          perks=({'perk_id': 'stardust_touch', 'equipped': 1},
                                 {'perk_id': 'glimmering_fortune', 'equipped': 1}),
                          sanctuary={'celestial_observatory': 1})
    release_value = sum(RELEASE_VALUES[rarity] * chance for rarity, chance in boosted.items())
    liquid_value = max(release_value, item_sell_price(egg, merchant=True))
    chance = min(MAX_NEST_CHANCE,
                 NEST_RELEASE_BUDGET * p['base_gold'] / max(1, liquid_value),
                 NEST_SHOP_BUDGET * p['base_gold'] / max(1, egg['price']))
    p['nest_chance'] = chance
    # An independent seed stream leaves material/encounter rolls unchanged.
    digest = hashlib.sha256(f"{p['seed']}:nest-discovery".encode()).digest()
    if int.from_bytes(digest[:8], 'big') / 2 ** 64 < chance:
        p['egg_offer'] = {key: egg[key] for key in ('id', 'name', 'family', 'grade', 'price')}

REGIONS = {
    'woods': dict(trail='fern-covered fork', tracks='small prints beneath bent ferns',
                  gather='a mossy grove', landmark='a root-bound wayside shrine',
                  obstacle='A stream has washed away the little footbridge.',
                  ability={'water': 'find a shallow crossing', 'wind': 'scout a dry crossing',
                           'earth': 'brace a stepping-stone path', 'nature': 'weave a vine crossing'},
                  rescue='A hatchling is tangled in abandoned twine.',
                  deep='A windstorm has exposed a hollow beneath an ancient oak.'),
    'ember': dict(trail='split basalt trail', tracks='warm claw marks in the ash',
                  gather='a sheltered mineral seam', landmark='a soot-covered pilgrim marker',
                  obstacle='Loose cinders slide across the narrow ravine path.',
                  ability={'fire': 'read the cooling cinders', 'earth': 'steady the loose rock',
                           'wind': 'clear the ash from the safe path', 'water': 'cool a short crossing'},
                  rescue='A hatchling is stranded on a cooling basalt shelf.',
                  deep='A cooling vent has revealed an old firekeeper vault.'),
    'glacial': dict(trail='snow-dusted fork', tracks='fresh prints beside wind-carved ice',
                    gather='a sheltered frost garden', landmark='a frozen trailkeeper monument',
                    obstacle='A frozen bridge creaks above a snow-filled gully.',
                    ability={'ice': 'reinforce the ice', 'fire': 'reveal the buried handrail',
                             'wind': 'find the sheltered ledge', 'earth': 'anchor a safe rope route'},
                    rescue='A hatchling is stuck in a drift beside its empty nest.',
                    deep='The clouds part above an abandoned summit observatory.'),
    'trenches': dict(trail='fork in the reef passage', tracks='fresh ripples beside a coral arch',
                     gather='a sheltered tide pool', landmark='a barnacle-covered beacon',
                     obstacle='A strong current cuts across the reef passage.',
                     ability={'water': 'guide the team through a still channel',
                              'shadow': 'find shelter beneath the reef',
                              'light': 'signal the safe reef markers', 'earth': 'anchor a guide rope'},
                     rescue='A hatchling is caught in a discarded fishing net.',
                     deep='The tide reveals a sealed chamber in a sunken watchtower.'),
    'loom': dict(trail='fork between drifting starstones', tracks='faint motes around a broken constellation',
                 gather='a quiet crystal outcrop', landmark='an unlit celestial waystone',
                 obstacle='Floating stones drift out of alignment across the gap.',
                 ability={'cosmic': 'read the orbit of the stones', 'arcane': 'steady their rhythm',
                          'light': 'reveal the old crossing marks', 'wind': 'scout the next safe orbit'},
                 rescue='A hatchling is trapped inside a flickering starstone ring.',
                 deep='A passing constellation opens an ancient stargazer chamber.'),
}

# Regional families above remain stable for old trips. New trips save a distinct
# destination version, keeping already-saved pages and their choices untouched.
DESTINATION_REGIONS = {
    'woods_deep': dict(trail='fork among silver reeds', tracks='tiny ripples beside moonlit lily pads',
                       gather='a lantern-moss hummock', landmark='a half-sunken reedkeeper bell',
                       obstacle='The marsh water has shifted beneath two marked crossings.',
                       ability={'water': 'read the shallow channels', 'earth': 'brace the copper-topped posts',
                                'nature': 'weave a floating reed mat', 'wind': 'scout the dry hummocks'},
                       rescue='A hatchling is caught in loose reeds beside a drifting nest.',
                       deep='The moon reveals a little pavilion beneath the still water.',
                       merchant='A mapmaker is fishing a soggy chart out of the silver reeds.',
                       camp='A dry hummock shelters the team above the moonlit pools.',
                       discovery='Bell markings lead to a lantern grove reflected twice in the still water.'),
    'ember_deep': dict(trail='fork between amber dunes', tracks='small prints around heat-glass arches',
                       gather='a shaded sunforge seam', landmark='a buried mirror shrine',
                       obstacle='A sun mirror marks two routes through the glass dunes.',
                       ability={'fire': 'read the heat-glass without disturbing it', 'earth': 'steady the buried mirror',
                                'water': 'cool the mirror housing', 'wind': 'clear sand from the old markers'},
                       rescue='A hatchling has slipped into a cool hollow beneath a glass arch.',
                       deep='The setting sun outlines a buried glass conservatory.',
                       merchant='A mapmaker is trying to free a chart pinned beneath a little sand drift.',
                       camp='A broad glass arch casts a quiet patch of cinnamon-scented shade.',
                       discovery='The shrine reflects a sheltered path through a garden of weathered glass.'),
    'glacial_deep': dict(trail='fork along an aurora-lit ridge', tracks='fresh prints beside a stormglass cairn',
                         gather='a sheltered cloud-pine ledge', landmark='an old windsock station',
                         obstacle='A gust sweeps across the two paths beside a rope bridge.',
                         ability={'wind': 'read the gust before the crossing', 'ice': 'brace the frosted handrail',
                                  'earth': 'anchor the bridge rope', 'fire': 'thaw the sheltered railing'},
                         rescue='A hatchling is huddled behind a rattling trail flag.',
                         deep='An aurora lights the terraces of a long-quiet weather observatory.',
                         merchant='A mapmaker is untangling a route chart from a stormglass cairn.',
                         camp='Cloud pines shelter the team while the aurora moves above the ridge.',
                         discovery='Old wind records reveal a balcony above a sea of clouds.'),
    'trenches_deep': dict(trail='fork beside a crimson coral shelf', tracks='warm bubbles around black basalt arches',
                          gather='a cooled vent garden', landmark='a coral-covered vent regulator',
                          obstacle='Two vent channels pass beneath a warm coral nursery.',
                          ability={'water': 'guide cool water around the nursery', 'light': 'reveal the old vent diagram',
                                   'shadow': 'find the sheltered basalt channel', 'earth': 'brace the old regulator'},
                          rescue='A hatchling is stranded between two gently bubbling vents.',
                          deep='A quiet vent opens a window into an ancient coral greenhouse.',
                          merchant='A mapmaker is rescuing a waterproof chart from a bubbling vent ring.',
                          camp='A cool basalt shelf shelters the team beside softly glowing coral.',
                          discovery='Regulator marks reveal an untouched garden of red fan coral.'),
    'loom_deep': dict(trail='fork between floating garden terraces', tracks='starlit prints beneath luminous fruit trees',
                      gather='a fallen-star orchard', landmark='a silver branch bearing little memory bells',
                      obstacle='Two branches unfold between the floating terraces.',
                      ability={'cosmic': 'read the stable orbit of the branches', 'arcane': 'steady a memory branch',
                               'light': 'reveal the old orchard path', 'wind': 'scout the next steady branch'},
                      rescue='A hatchling is tangled in a loose ribbon beneath a starfruit tree.',
                      deep='A drifting blossom reveals a tiny seed library between the stars.',
                      merchant='A mapmaker is gathering chart pages from the branches of a luminous tree.',
                      camp='The team rests beneath a tree whose starfruit glows like small lanterns.',
                      discovery='The memory bells reveal a terrace where old paths bloom as constellations.'),
}


def _region(p, realm):
    destination = destination_key(p.get('destination', realm))
    if p.get('destination_version') == 1 and destination in DESTINATION_REGIONS:
        return DESTINATION_REGIONS[destination]
    return REGIONS[biome_key(destination)]


def _crossing(p, realm):
    """Save one readable clue and exactly the original detour/shortcut outcomes."""
    destination = destination_key(p.get('destination', realm))
    if p.get('destination_version') != 1 or destination not in DESTINATION_REGIONS:
        return None
    if 'biome_crossing' in p:
        return p['biome_crossing']
    flip = _variant(p, (False, True), 'destination-crossing')
    if destination == 'woods_deep':
        title = 'Read the waterline'
        markers = ('blue-plank boardwalk', 'copper-post walkway')
        safe = int(flip)
        clue = (f"Moonlight shows fresh water lapping over the {markers[1-safe]}. "
                f"The {markers[safe]} stands above the wet reeds.")
        labels = ['Take the blue boardwalk', 'Take the copper walkway']
        note = 'Your team followed the dry waterline markers through Moonveil Marsh.'
    elif destination == 'ember_deep':
        title = 'Turn the sun mirror'
        markers = ('crescent', 'star')
        safe = int(flip)
        clue = (f"The old plaque reads: ‘Follow the shade.’ The mirror's {markers[safe]} mark "
                f"casts a cool shadow through the dunes; the {markers[1-safe]} mark lights a short, hot ridge.")
        labels = ['Turn toward the crescent', 'Turn toward the star']
        note = 'Your team aligned the sun mirror and followed its shaded path through Sunforge Dunes.'
    elif destination == 'glacial_deep':
        title = 'Read the ridge wind'
        safe = int(flip)
        direction = ('east', 'west')[safe]
        clue = (f"The windsock and blowing snow show gusts arriving from the {direction}. "
                "The cairn's sketch marks the opposite side as sheltered: "
                f"the {('west', 'east')[safe]} ledge. The exposed span is shorter.")
        labels = ['Follow the west ledge', 'Follow the east ledge']
        note = 'Your team read the windsock and crossed the sheltered side of Stormglass Peaks.'
    elif destination == 'trenches_deep':
        title = 'Keep the nursery warm'
        safe = int(flip)
        markers = ('shell', 'coral')
        clue = (f"The regulator diagram joins the {markers[safe]} valve to a cool side passage. "
                f"The {markers[1-safe]} valve opens the shorter bubbling channel beside the nursery. "
                'Both leave its warm pool undisturbed.')
        labels = ['Open the shell valve', 'Open the coral valve']
        note = 'Your team opened the cool side passage and left the Cinderreef nursery undisturbed.'
    else:
        title = 'Remember your first path'
        remembered = {'tracks': 'little footprints', 'gather': 'a bundle of leaves',
                      'shrine': 'a ringing bell'}.get(p.get('route'), 'a ringing bell')
        safe = int(flip)
        clue = (f"A memory bell repeats your first choice: {remembered}. The branch bearing that sign "
                'holds steady. The other is a shorter ribbon of shifting starlight.')
        labels = ['Follow the shifting ribbon', 'Follow the shifting ribbon']
        labels[safe] = {'tracks': 'Follow the footprint branch', 'gather': 'Follow the leaf branch',
                        'shrine': 'Follow the bell branch'}.get(p.get('route'), 'Follow the bell branch')
        note = 'Your team remembered its first trail and followed the steady branch through Starfall Gardens.'
    actions = [_choice('crossing:' + str(index), label,
                       'Sheltered route: no HP cost; keeps your saved bundle.' if index == safe else
                       'Short route: up to 3% max HP cost; adds 5% to your one bundle.')
               for index, label in enumerate(labels)]
    result = dict(destination=destination, title=title, clue=clue, choices=actions,
                  safe='crossing:' + str(safe), risky='crossing:' + str(1-safe),
                  safe_note=note)
    p['biome_crossing'] = result
    return result


def _variant(p, values, salt):
    value = hashlib.sha256(f"{p['seed']}:{salt}".encode()).digest()
    return values[int.from_bytes(value[:4], 'big') % len(values)]


def _choice(value, label, description):
    return dict(value=value, label=label, description=description)


def initialize(p, realm, seed, party):
    p.update(adventure_version=2, destination_version=1, destination=destination_key(realm),
             seed=str(seed), event='trailhead', completed=0,
             target_stops=3, history=[], flags={}, camp_used=False, push_used=False,
             bonus=0, base_gold=p['gold'], party_ids=[r['id'] for r in party],
             party=[dict(id=r['id'], name=r.get('nickname') or r.get('name') or r['beast_id'],
                         type=r.get('type', '')) for r in party],
             participations={}, party_xp=[], material_granted=False, battle_awarded=False)
    page(p, realm, 'trailhead')


def page(p, realm, event):
    r = _region(p, realm)
    choices = []
    if event == 'trailhead':
        title = 'Choose your trail'
        body = f"Your team reaches a {r['trail']}. Decide what to investigate together."
        choices = [_choice('tracks', 'Follow the tracks', f"Investigate {r['tracks']}; one wild encounter."),
                   _choice('gather', 'Search for supplies', f"Search {r['gather']}; choose your one material bundle."),
                   _choice('shrine', 'Visit the landmark', f"Investigate {r['landmark']}; study, repair, or make camp.")]
    elif event == 'creature':
        title = 'A creature on the trail'
        body = f"Following {r['tracks']}, you spot a wild companion. Choose one approach; it will not wait for another try."
        choices = [_choice('challenge', 'Challenge together', 'Fight with your saved team; a win befriends this creature.'),
                   _choice('approach', 'Approach gently', 'Use the usual friendship chance; it may leave.'),
                   _choice('observe', 'Observe quietly', 'Learn its habits and leave it undisturbed.')]
    elif event == 'gathering':
        from utils.catalog import load_catalog
        materials = load_catalog('materials.json')['materials']
        title, body = 'One bundle for the journey', f"At {r['gather']}, your team finds several useful materials. You have room for one bundle."
        choices = [_choice('material:' + mid, materials[mid]['name'], 'Save this material for your return; no extra bundle is added.') for mid in p['offered']]
    elif event == 'landmark':
        title, body = 'A little care goes a long way', f"You arrive at {r['landmark']}. The trail beyond it is quiet."
        choices = [_choice('repair', 'Repair the landmark', 'Build bond together; restored markers reveal a sheltered route.'),
                   _choice('study', 'Study its markings', 'Earn a small expedition XP bonus and find a forgotten trail.')]
        if not p['camp_used']:
            choices.append(_choice('rest', 'Make a short camp', 'Restore 5% max HP to healthy teammates; once per trip, no revives.'))
    elif event == 'obstacle':
        title, body = 'A crossing to solve', r['obstacle']
        crossing = _crossing(p, realm)
        if crossing:
            title, body = crossing['title'], crossing['clue']
        if p['flags'].get('directions'):
            body += ' Your earlier discovery marks a sheltered way across.'
        choices = [_choice('detour', 'Take the marked detour', 'A safe route for every team; your saved supplies stay secure.'),
                   _choice('cross', 'Cross carefully', 'Scout together, then cross; costs up to 3% max HP to healthy teammates.')]
        if crossing:
            choices = [dict(choice) for choice in crossing['choices']]
        for member in p['party']:
            ability = r['ability'].get(member['type'])
            if ability:
                choices.insert(0, _choice('help:' + str(member['id']), f"Ask {member['name']}"[:100],
                                          ability.capitalize() + '; no HP cost, a small teamwork bonus.'))
    elif event == 'rescue':
        title, body = 'A small traveller needs help', r['rescue'] + ' Helping it will not add another catch.'
        if p['flags'].get('observed'):
            body += ' The habits you observed show you how to calm it.'
        choices = [_choice('rescue', 'Free it patiently', 'Build team bond; its family reveals a sheltered place deeper in.'),
                   _choice('calm', 'Calm it from a distance', 'Learn safely; its calls reveal a trail clue.'),
                   _choice('leave', 'Leave it space', 'Avoid disturbing the nest and continue home.')]
    elif event == 'merchant':
        title = 'A travelling mapmaker'
        body = r.get('merchant', 'A stranded mapmaker is trying to recover a windblown route chart.')
        choices = [_choice('assist', 'Help recover the chart', 'Build bond and mark a safer route deeper in.'),
                   _choice('listen', 'Trade trail stories', 'Learn a little and hear about a hidden landmark.')]
    elif event == 'camp':
        title, body = 'A sheltered clearing', r.get('camp', 'Your earlier choice leads the team to a quiet shelter beside the trail.')
        choices = [_choice('practice', 'Practise as a team', 'Build bond and prepare for the journey home.'),
                   _choice('survey', 'Survey the way ahead', 'Record a useful trail clue for a deeper stop.')]
        if not p['camp_used']:
            choices.insert(0, _choice('camp', 'Share a short camp', 'Restore 5% max HP to healthy teammates; once per trip, no revives.'))
    elif event == 'discovery':
        title, body = 'A forgotten trail', r.get('discovery', 'The old markings lead to a hidden lookout with a view of the region.')
        choices = [_choice('map', 'Map the hidden trail', 'Record a small expedition reward bonus and a safer deep route.'),
                   _choice('watch', 'Watch the wildlife', 'Build bond together; this is not a second catch encounter.')]
    elif event == 'deep':
        title, body = 'One more discovery', r['deep']
        if p['flags'].get('directions') or p['flags'].get('sanctuary'):
            body += ' Your earlier trail clue reveals a sheltered entrance.'
            choices.append(_choice('sheltered', 'Use the sheltered entrance', 'Earn a 10% bundle bonus without another HP cost.'))
        choices.extend([_choice('venture', 'Investigate the unstable chamber', 'Earn a 15% bundle bonus; costs up to another 4% max HP, no extra catch or material.'),
                        _choice('sketch', 'Sketch from the entrance', 'Earn a 5% bundle bonus and return safely.')])
    else:
        title, body = 'The path home', 'Your team has finished its planned stops. Bank the journey, or risk one final discovery.'
        choices = [_choice('return', 'Return and bank rewards', 'Claim the single earned reward bundle for this trip.')]
        if not p['push_used']:
            choices.append(_choice('push', 'Push a little deeper', 'One more stop; costs up to 4% max HP, up to 25% total bonus, no extra catch or material.'))
        else:
            body = 'Your team has finished the deeper stop. It is time to head home.'
    egg = p.get('egg_offer')
    if egg and not p.get('egg_reward') and event in NEST_EVENTS:
        from utils.catalog import load_catalog
        materials = load_catalog('materials.json')['materials']
        material_name = materials[p['material']]['name']
        clues = {
            'rescue': 'Helping the hatchling reveals a small abandoned nest nearby.',
            'discovery': 'The old trail markings lead to a small abandoned nest.',
            'camp': 'Your companions spot a small abandoned nest beside the shelter.',
            'deep': 'The hidden chamber shelters a small abandoned nest.',
        }
        body += (f" {clues[event]} A {egg['name']} needs shelter. You can carry it "
                 f"instead of your {material_name} bundle, or leave it safe and keep your supplies.")
        choices.append(_choice('recover_egg', f"Shelter the {egg['name']}"[:100],
                               f"One incubation egg instead of {material_name}; gold and XP unchanged."[:100]))
    p.update(event=event, page=dict(title=title, body=body, choices=choices), stage=p['completed'])


def _complete(p, choice):
    p['history'].append(dict(event=p['event'], choice=choice))
    p['completed'] += 1
    p['stage'] = p['completed']
    if p['party_ids']:
        lead = str(p['party_ids'][0])
        p['participations'][lead] = p['participations'].get(lead, 0) + 1


def resolve(p, realm, choice):
    """Advance a validated action and return bounded effects for the DB writer."""
    event, effects = p['event'], {}
    crossing = p.get('biome_crossing') if event == 'obstacle' else None
    route_choice = choice
    if crossing and choice in (crossing['safe'], crossing['risky']):
        route_choice = 'detour' if choice == crossing['safe'] else 'cross'
    if choice == 'push':
        p.update(push_used=True, target_stops=4)
        p['history'].append(dict(event=event, choice=choice))
        effects['damage'] = 4
        page(p, realm, 'deep')
        return effects
    if choice == 'challenge':
        return dict(battle=True)
    _complete(p, choice)
    choice = route_choice
    if event == 'trailhead':
        p['route'] = choice
        next_event = {'tracks': 'creature', 'gather': 'gathering', 'shrine': 'landmark'}[choice]
        effects['encounter'] = choice == 'tracks'
    elif event == 'creature':
        if choice == 'approach':
            p.update(caught=bool(p['friendly']), encounter_result='befriended' if p['friendly'] else 'escaped')
            p['notes'].append('The creature chose to come closer.' if p['friendly'] else 'The creature slipped back into its habitat.')
        elif choice == 'observe':
            p.update(encounter_result='observed', bond=p['bond'] + 1)
            p['flags']['observed'] = True
            p['notes'].append('You learned the local creatures\' habits without disturbing them.')
        next_event = 'rescue' if p.get('encounter_result') in ('observed', 'escaped', 'interrupted') else 'camp'
    elif event == 'gathering':
        p['material'] = choice.split(':', 1)[1]
        p['notes'].append('Your team packed one material bundle for the return journey.')
        next_event = _variant(p, ('obstacle', 'merchant', 'rescue'), 'gather-followup')
    elif event == 'landmark':
        if choice == 'repair':
            p['bond'] += 2
            p['happiness'] += 5
            p['flags']['directions'] = True
            next_event = 'obstacle'
            p['notes'].append('Restored trail markers revealed a safe way across.')
        elif choice == 'study':
            p['bonus'] += 5
            next_event = 'discovery'
            p['notes'].append('The landmark\'s markings revealed a forgotten trail.')
        else:
            p['camp_used'] = True
            p['happiness'] += 3
            effects['heal'] = 5
            next_event = 'merchant'
            p['notes'].append('Your team shared its one short recovery camp.')
    else:
        next_event = 'homeward'
        if choice == 'recover_egg':
            p['egg_reward'] = p['egg_offer']['id']
            if event == 'rescue':
                p['bond'] += 1
                p['flags']['sanctuary'] = True
            p['notes'].append(f"Your team sheltered a {p['egg_offer']['name']} instead of its material bundle.")
        elif choice.startswith('help:'):
            helper = choice.split(':', 1)[1]
            p['participations'][helper] = p['participations'].get(helper, 0) + 1
            effects['helper_id'] = int(helper)
            p['bonus'] += 5
            p['flags']['directions'] = True
            p['notes'].append('A teammate found a safe crossing with its natural ability.')
        elif choice == 'cross':
            effects['damage'] = 3
            p['bonus'] += 5
            p['notes'].append('The team crossed carefully, picking up a few scrapes.')
        elif choice == 'detour' and crossing:
            p['notes'].append(crossing['safe_note'])
        elif choice in ('rescue', 'assist'):
            p['bond'] += 1
            p['flags']['sanctuary' if choice == 'rescue' else 'directions'] = True
            p['notes'].append('Helping a traveller revealed a sheltered route deeper in.')
        elif choice in ('calm', 'listen', 'survey', 'map'):
            p['flags']['directions'] = True
            p['bonus'] += 5
            p['notes'].append('Your team recorded a useful route clue.')
        elif choice in ('practice', 'watch'):
            p['bond'] += 1
            p['notes'].append('Your companions grew closer while exploring together.')
        elif choice == 'camp':
            p['camp_used'] = True
            effects['heal'] = 5
            p['happiness'] += 3
            p['notes'].append('Your team shared its one short recovery camp.')
        elif choice in ('sheltered', 'venture', 'sketch'):
            p['bonus'] += {'sheltered': 10, 'venture': 15, 'sketch': 5}[choice]
            if choice == 'venture':
                effects['damage'] = 4
            p['notes'].append('The deeper discovery added to your single expedition bundle.')
    p['bonus'] = min(25, p['bonus'])
    page(p, realm, next_event)
    return effects


def finish_battle(p, realm, won, interrupted):
    p.update(caught=bool(won), encounter_result='interrupted' if interrupted else 'won' if won else 'escaped')
    _complete(p, 'challenge')
    p['notes'].append('The encounter was interrupted; your supplies are safe.' if interrupted else
                      'The creature recognised your team\'s strength and chose to follow.' if won else
                      'The creature withdrew. Your saved supplies are safe.')
    if won:
        p['bonus'] = min(25, p['bonus'] + 10)
    page(p, realm, 'camp' if won else 'rescue')


def presentation(trip):
    p = trip['payload']
    result = dict(p['page'])
    result['choices'] = [dict(c) for c in result['choices']]
    if trip['status'] != 'active':
        result['choices'] = []
    elif p['event'] != 'homeward':
        result['choices'].append(_choice('return_early', 'Head home now', 'Bank the earned fraction of your one reward bundle.'))
    bundle = 'one incubation egg instead of material' if p.get('egg_reward') else 'one material bundle'
    nest = f" · nest clue {p['nest_chance']:.1%} max/trip" if p.get('nest_version') else ''
    result.update(progress=dict(completed=p['completed'], total=p['target_stops']),
                  party_ids=list(p['party_ids']),
                  footer=f"{p['completed']}/{p['target_stops']} stops · {bundle} · one possible catch · bonus {p['bonus']}%{nest}")
    return result
