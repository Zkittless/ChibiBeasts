"""Field Journal duel composition; animations consume resolved snapshots only."""
from dataclasses import replace
import math

from PIL import ImageDraw

from utils import battle_art as art, field_journal as ui, move_animations as fx


def duration(before, after, action):
    if action is None:
        return 0
    return action.milliseconds + (fx.RETREAT_MS if any(new.hp <= 0 < old.hp for old, new in zip(before, after)) else 0)


def timing(before, after, action, t):
    if action is None:
        return 1, 0
    elapsed = fx.clamp(t) * duration(before, after, action)
    retreating = any(new.hp <= 0 < old.hp for old, new in zip(before, after))
    return (fx.clamp(elapsed / action.milliseconds),
            fx.clamp((elapsed-action.milliseconds)/fx.RETREAT_MS) if retreating else 0)


def entering_pose(home, side, t):
    """Bring the next opponent from outside the field into its settled pose."""
    progress = fx.smooth((t - .08) / .72)
    edge = art.WIDTH + 110 if side else -110
    return replace(home, x=fx.mix(edge, home.x, progress),
                   feet=home.feet - abs(math.sin(progress * math.pi * 3)) * 6)


def draw_frame(before, after, realm, action=None, t=1, journal=()):
    if any(f.party for f in before + after):
        return draw_team_frame(before, after, realm, action, t, journal)
    image = art.field_background(realm, art.TOTAL_HEIGHT).copy()
    move_t, departure_t = timing(before, after, action, t)
    entering = bool(action and action.kind == 'enter')
    complete = any(f.hp <= 0 for f in after)
    title = 'BATTLE COMPLETE' if complete and (not action or t >= 1 or any(f.hp <= 0 for f in before)) else 'RETREAT' if departure_t else 'NEXT OPPONENT' if entering else 'ULTIMATE' if action and action.ultimate else 'IN BATTLE' if action else 'YOUR TURN'
    ui.heading(image, title, art.REALMS[realm])
    for i, (old, new) in enumerate(zip(before, after)):
        visible = new if move_t >= fx.IMPACT else old
        ui.fighter_panel(image, new, art.sprite(new.species, new.armor_id), 10+i*320,
                         art.interpolate(old.hp, new.hp, move_t), art.interpolate(old.mana, new.mana, move_t), visible)
    field = image.crop((0, art.HUD, art.WIDTH, art.HUD+art.HEIGHT))
    homes = [fx.Pose(162 if i==0 else 478,218,flip=bool(i),height=source.height if source else 130)
             for i,fighter in enumerate(after) for source in (art.sprite(fighter.species, fighter.armor_id),)]
    poses = list(homes)
    actor = action.actor if action else 0
    animation = fx.profile(before[actor].species, action.name, action.kind, action.ultimate) if action and not entering else None
    if animation:
        poses[actor] = fx.motion(animation, homes[actor], homes[1-actor], move_t)
        if after[1-actor].hp < before[1-actor].hp:
            poses[1-actor] = fx.reaction(animation, homes[1-actor], homes[actor], move_t)
        back=fx.Effects(field.size)
        back.effect(animation, poses[actor], poses[1-actor], move_t, back=True)
        field.alpha_composite(back.image)
        fx.paint_echoes(field, art.sprite(before[actor].species, before[actor].armor_id),
                       animation, homes[actor], homes[1-actor], move_t)
    visible_poses=[]
    for i,(old,new) in enumerate(zip(before,after)):
        pose=poses[i]
        if entering and i == actor:
            pose = entering_pose(homes[i], i, move_t)
        # An already defeated fighter stays offstage in every later prompt,
        # final PNG and status clip. A newly defeated fighter first takes the
        # hit, recovers its normal pose, turns, then leaves in its own segment.
        hidden=new.hp<=0 and (old.hp<=0 or not action or t>=1)
        if new.hp<=0<old.hp and departure_t:
            pose=fx.retreat(homes[i],departure_t,new.species)
        if old.hp<=0<new.hp and action:
            sign=-1 if i==0 else 1
            pose=replace(homes[i],x=homes[i].x+sign*240*(1-fx.smooth((move_t-.48)/.42)),alpha=1 if move_t>=.48 else 0)
        visible_poses.append(None if hidden else pose)
        if hidden:
            continue
        draw=ImageDraw.Draw(field)
        shadow_alpha=round(95*pose.alpha*(1-departure_t*.8 if new.hp<=0 else 1))
        if shadow_alpha:
            layer=fx.Effects(field.size)
            layer.draw.ellipse((pose.x-58,homes[i].feet-6,pose.x+58,homes[i].feet+10),fill=(10,20,28,shadow_alpha))
            field.alpha_composite(layer.image)
        source=art.sprite(new.species, new.armor_id)
        if source is None:
            draw=ImageDraw.Draw(field)
            draw.ellipse((pose.x-35,pose.feet-75,pose.x+35,pose.feet),fill='#b7a8dc',outline='#ede6ff',width=3)
            art.label(draw,(pose.x-7,pose.feet-52),'?',25,'#242139')
        else:
            fx.paint_sprite(field,source,pose)
    front=fx.Effects(field.size)
    if animation and not departure_t:
        front.effect(animation,poses[actor],poses[1-actor],move_t,scale=1.12 if action.ultimate else 1)
    for i,(old,new) in enumerate(zip(before,after)):
        pose=visible_poses[i]
        if pose is None:
            continue
        visible=new if move_t>=.48 else old
        if visible.hp>0 and (visible.guard or visible.shield):
            front.ring(pose.x,pose.feet-88,86,94,'#cfbef0' if visible.guard else '#a8d6ed',.55)
        if new.hp<=0 and departure_t:
            front.departure(pose,departure_t,new.species)
    field.alpha_composite(front.image)
    if action and .48<=move_t<.96 and not departure_t:
        draw=ImageDraw.Draw(field)
        for i,(old,new) in enumerate(zip(before,after)):
            pose=visible_poses[i]
            if pose is None:
                continue
            delta=new.hp-old.hp
            if delta:
                source=art.sprite(new.species, new.armor_id)
                height=source.height if source else 130
                value=f'{delta:+,}'
                width=draw.textlength(value,font=art.font(23))
                x=max(4,min(art.WIDTH-width-4,pose.x-width/2))
                y=max(3,pose.feet-height-20-(move_t-.48)*36)
                art.label(draw,(x,y),value,23,'#b5fac5' if delta>0 else '#fff0ce',outline=True)
            if new.revived and not old.revived:
                art.label(draw,(pose.x-38,pose.feet-190),'REVIVED',14,'#ffdf9e')
        if action.kind in {'attack','skill'} and not action.damage and after[1-actor].hp==before[1-actor].hp:
            pose=poses[1-actor]
            text='BLOCKED' if before[1-actor].shield>after[1-actor].shield else '0 DAMAGE'
            art.label(draw,(pose.x-38,max(4,pose.feet-190)),text,16,'#e0d5f9')
    image.alpha_composite(field,(0,art.HUD))
    caption=f'{after[actor].name} / {action.name}' if action else 'Battle complete.' if complete else 'Choose a move.'
    departed=[f'{new.name} retreats.' for old,new in zip(before,after) if new.hp<=0 and (not action or departure_t)]
    entries=action.journal or journal if action else journal
    lines=tuple(departed) if departed else entries[-2:] if entries and (not action or move_t>=.48) else (caption,)
    ui.journal(image,art.HUD+art.HEIGHT,caption,lines)
    return image.convert('RGB')


# The team layout keeps both reserves in their own lane, to the left of the
# active fighter. All motion/effects remain inside the field crop, so neither
# resource tracks nor the journal can be covered by a visiting companion.
TEAM_HOMES = ((232, 212, 164), (490, 212, 164))
RESERVE_BOXES = ((10, 146, 76, 243), (82, 146, 148, 243))
ASSIST_HOME = fx.Pose(91, 140, size=90)


def same_member(left, right):
    if left.member_id != '' or right.member_id != '':
        return left.member_id == right.member_id
    return left.species == right.species


def team_reserves(fighter):
    return tuple(f for f in fighter.party if not same_member(f, fighter))[:2]


def panel_state(old, new, t):
    """A replacement owns its own portrait and bars; never tween identities."""
    if not same_member(old, new):
        shown = new if t >= .5 else old
        return shown, shown.hp, shown.mana, shown
    return new, art.interpolate(old.hp, new.hp, t), art.interpolate(old.mana, new.mana, t), new if t >= fx.IMPACT else old


def swap_cast(old, new, home, t):
    """Separate exit, empty-stage hold, entrance and settled hold."""
    if t < .4:
        p = fx.smooth((t - .12) / .28)
        pose = replace(home, x=fx.mix(home.x, -110, p), flip=p > .02,
                       feet=home.feet - abs(math.sin(p * math.pi * 3)) * 8)
        return ((old, pose),)
    if t < .5:
        return ()
    p = fx.smooth((t - .5) / .34)
    pose = replace(home, x=fx.mix(-110, home.x, p),
                   feet=home.feet - abs(math.sin(p * math.pi * 3)) * 8)
    return ((new, pose),)


def assist_pose(home, t):
    """A reserve grows into the field, casts, then returns to its own slot."""
    if t <= .12 or t >= .9:
        return None
    if t < .3:
        p = fx.smooth((t - .12) / .18)
    elif t <= .72:
        p = 1
    else:
        p = 1 - fx.smooth((t - .72) / .18)
    return replace(ASSIST_HOME, x=fx.mix(home.x, ASSIST_HOME.x, p),
                   feet=fx.mix(home.feet, ASSIST_HOME.feet, p) - math.sin(p * math.pi) * 10,
                   size=fx.mix(home.size, ASSIST_HOME.size, p))


def assist_profile(support, effect):
    palette = support.element if support.element in fx.PALETTES else 'arcane'
    family = {'heal': 'heal', 'guard': 'guard', 'shield': 'guard'}.get(effect)
    if family is None:
        family = {'water': 'splash', 'fire': 'flame', 'ice': 'ice', 'nature': 'roots',
                  'earth': 'stomp', 'wind': 'wind', 'shadow': 'rift',
                  'cosmic': 'orbit', 'light': 'bolt'}.get(palette, 'bolt')
    sequence = {'heal': 'petal_mend', 'guard': 'crystal_aegis', 'splash': 'water_spear',
                'flame': 'cinder_breath', 'ice': 'hail_lance', 'roots': 'thorn_eruption',
                'stomp': 'seismic_fault', 'wind': 'razor_gale', 'rift': 'shadow_drain',
                'orbit': 'spirit_bolt', 'bolt': 'spirit_bolt'}[family]
    return fx.Animation(family, palette, 'cast', sequence=sequence)


def _paint_fighter(field, fighter, pose, *, shadow=True):
    if shadow:
        layer = fx.Effects(field.size)
        width = pose.size * .32
        layer.draw.ellipse((pose.x-width, pose.feet-5, pose.x+width, pose.feet+7),
                           fill=(10, 20, 28, round(90 * pose.alpha)))
        field.alpha_composite(layer.image)
    source = art.sprite(fighter.species, fighter.armor_id)
    if source is None:
        draw = ImageDraw.Draw(field)
        draw.ellipse((pose.x-25, pose.feet-65, pose.x+25, pose.feet), fill='#b7a8dc', outline='#ede6ff', width=2)
        art.label(draw, (pose.x-7, pose.feet-50), '?', 25, '#242139')
    else:
        fx.paint_sprite(field, source, pose)


def _reserve_cards(field, before, after, t, *, support_id='', visiting=False):
    reserves = team_reserves(after)
    previous = {f.member_id: f for f in before.party}
    draw = ImageDraw.Draw(field)
    ui.text(draw, 'ADVENTURE TEAM', (12, 5, 148, 22), size=11, fill=ui.GOLD)
    meter = art.interpolate(before.assist_meter, after.assist_meter, t)
    ui.text(draw, f'ASSIST {meter}%', (12, 25, 148, 40), size=11, fill=ui.HEALTH if meter >= 100 else ui.MUTED)
    draw.rectangle((12, 44, 146, 48), fill='#0a2025', outline='#748178')
    if meter:
        draw.rectangle((13, 45, 13+round(132*meter/100), 47), fill=ui.HEALTH)
    for fighter, box in zip(reserves, RESERVE_BOXES):
        left, top, right, bottom = box
        old = previous.get(fighter.member_id, fighter)
        visible = fighter if t >= fx.IMPACT else old
        ui.panel(field, box, corners=False)
        source = art.sprite(fighter.species, fighter.armor_id)
        if not (visiting and fighter.member_id == support_id):
            if source is not None:
                pose = fx.Pose((left+right)/2, top+58, size=50, alpha=.35 if visible.hp <= 0 else 1)
                fx.paint_sprite(field, source, pose)
            else:
                ui.text(draw, '?', (left+6, top+9, right-6, top+54), size=24, align='center')
        else:
            ui.text(draw, 'ASSIST', (left+4, top+15, right-4, top+48), size=10, fill=ui.HEALTH, align='center')
        ui.text(draw, fighter.name, (left+4, top+59, right-4, top+76), size=16, minimum=11, align='center')
        hp = art.interpolate(old.hp, fighter.hp, t)
        track = (left+6, top+78, right-6, top+92)
        draw.rectangle(track, fill='#071c24', outline='#748178')
        if hp > 0:
            draw.rectangle((track[0]+1, track[1]+1, track[0]+1+round((track[2]-track[0]-2)*min(1, hp/fighter.max_hp)), track[3]-1), fill=ui.HEALTH)
        ui.text(draw, f'{ui._compact(hp)}/{ui._compact(fighter.max_hp)}',
                (track[0]+2, track[1]+1, track[2]-2, track[3]-1), size=12, minimum=10,
                align='center', outline=True)
        if visible.hp <= 0:
            ui.text(draw, 'KO', (left+8, top+28, right-8, top+46), size=15, fill='#ecc4ab', align='center', outline=True)


def draw_team_frame(before, after, realm, action=None, t=1, journal=()):
    image = art.field_background(realm, art.TOTAL_HEIGHT).copy()
    move_t, departure_t = timing(before, after, action, t)
    switching = bool(action and action.kind == 'swap')
    assisting = bool(action and action.kind == 'assist')
    entering = bool(action and action.kind == 'enter')
    complete = after[1].hp <= 0 or not any(f.hp > 0 for f in after[0].party)
    title = ('BATTLE COMPLETE' if complete and (not action or t >= 1 or before[1].hp <= 0)
             else 'RETREAT' if departure_t else 'NEXT OPPONENT' if entering else 'TAG SWITCH' if switching else 'TEAM ASSIST' if assisting
             else 'ULTIMATE' if action and action.ultimate else 'IN BATTLE' if action else
             'CHOOSE A COMPANION' if after[0].hp <= 0 else 'YOUR TURN')
    ui.heading(image, title, art.REALMS[realm])
    for i, (old, new) in enumerate(zip(before, after)):
        shown, hp, mp, visible = panel_state(old, new, move_t)
        ui.fighter_panel(image, shown, art.sprite(shown.species, shown.armor_id), 10+i*320, hp, mp, visible)
    field = image.crop((0, art.HUD, art.WIDTH, art.HUD+art.HEIGHT))
    homes = []
    for i, fighter in enumerate(after):
        source = art.sprite(fighter.species, fighter.armor_id)
        x, feet, size = TEAM_HOMES[i]
        height = (source.height * size/max(source.size)) if source else 130
        homes.append(fx.Pose(x, feet, size=size, flip=bool(i), height=height))
    poses = list(homes)
    actor = action.actor if action else 0
    animation = fx.profile(before[actor].species, action.name, action.kind, action.ultimate) if action and not (switching or assisting or entering) else None
    if animation:
        poses[actor] = fx.motion(animation, homes[actor], homes[1-actor], move_t)
        if after[1-actor].hp < before[1-actor].hp:
            poses[1-actor] = fx.reaction(animation, homes[1-actor], homes[actor], move_t)
        back = fx.Effects(field.size)
        back.effect(animation, poses[actor], poses[1-actor], move_t, back=True)
        field.alpha_composite(back.image)
        fx.paint_echoes(field, art.sprite(before[actor].species, before[actor].armor_id),
                       animation, homes[actor], homes[1-actor], move_t)
    support = None
    support_pose = None
    assist_t = fx.clamp((move_t-.3)/.42)
    if assisting:
        support = next((f for f in before[0].party if f.member_id == action.support_id), None)
        if support is None and action.support_species:
            support = replace(before[0], species=action.support_species, name=action.support_name,
                              armor_id=action.support_armor_id, party=(), member_id=action.support_id)
        reserves = team_reserves(before[0])
        slot = next((i for i, f in enumerate(reserves) if f.member_id == action.support_id), 0)
        box = RESERVE_BOXES[slot]
        support_pose = assist_pose(fx.Pose((box[0]+box[2])/2, box[1]+58, size=50), move_t) if support else None
    # At the swap midpoint the entire reserve card changes with the active
    # portrait. A card never displays the incoming beast's HP on the old beast.
    reserve_state = after[0] if not switching or move_t >= .5 else before[0]
    visible_poses = []
    for i, (old, new) in enumerate(zip(before, after)):
        if switching and i == actor:
            cast = swap_cast(old, new, homes[i], move_t)
            visible_poses.append(cast[0][1] if cast else None)
            for fighter, pose in cast:
                if fighter.hp > 0:
                    _paint_fighter(field, fighter, pose)
            continue
        hidden = new.hp <= 0 and (old.hp <= 0 or not action or t >= 1)
        pose = poses[i]
        if entering and i == actor:
            pose = entering_pose(homes[i], i, move_t)
        if new.hp <= 0 < old.hp and departure_t:
            pose = fx.retreat(homes[i], departure_t, new.species)
        if old.hp <= 0 < new.hp and action:
            pose = replace(homes[i], x=homes[i].x+(-1 if i == 0 else 1)*240*(1-fx.smooth((move_t-.48)/.42)), alpha=1 if move_t >= .48 else 0)
        visible_poses.append(None if hidden else pose)
        if not hidden:
            _paint_fighter(field, new, pose)
    if support_pose:
        _paint_fighter(field, support, support_pose)
    front = fx.Effects(field.size)
    if animation and not departure_t:
        front.effect(animation, poses[actor], poses[1-actor], move_t, scale=1.12 if action.ultimate else 1)
    if support_pose and .3 <= move_t <= .72 and not departure_t:
        effect = action.support_effect or 'attack'
        animation = assist_profile(support, effect)
        # The existing heal/guard families center on their actor. Anchor those
        # effects to the beneficiary while the visitor remains visibly nearby.
        beneficiary = homes[0] if effect in {'heal', 'guard', 'shield'} else poses[1]
        emitter = beneficiary if effect in {'heal', 'guard', 'shield'} else support_pose
        front.effect(animation, emitter, beneficiary, assist_t, back=True)
        front.effect(animation, emitter, beneficiary, assist_t)
        if .32 < move_t < .52 and effect in {'heal', 'guard', 'shield'}:
            front.line([(support_pose.x+20, support_pose.feet-40), (homes[0].x-30, homes[0].feet-70)], fx.PALETTES[animation.palette][1], 3, .5)
    for i, (old, new) in enumerate(zip(before, after)):
        pose = visible_poses[i]
        if pose is None:
            continue
        visible = new if move_t >= .48 else old
        if visible.hp > 0 and (visible.guard or visible.shield) and not switching:
            front.ring(pose.x, pose.feet-75, 72, 80, '#cfbef0' if visible.guard else '#a8d6ed', .55)
        if new.hp <= 0 and departure_t:
            front.departure(pose, departure_t, new.species)
    field.alpha_composite(front.image)
    if action and .48 <= move_t < .96 and not departure_t and not switching:
        draw = ImageDraw.Draw(field)
        for i, (old, new) in enumerate(zip(before, after)):
            pose = visible_poses[i]
            if pose is None or not same_member(old, new):
                continue
            delta = new.hp-old.hp
            if delta:
                value = f'{delta:+,}'
                width = draw.textlength(value, font=art.font(23))
                x = max(155 if i == 0 else 4, min(art.WIDTH-width-4, pose.x-width/2))
                y = max(3, pose.feet-homes[i].height-20-(move_t-.48)*36)
                art.label(draw, (x, y), value, 23, '#b5fac5' if delta > 0 else '#fff0ce', outline=True)
            if new.revived and not old.revived:
                art.label(draw, (pose.x-38, max(4, pose.feet-190)), 'REVIVED', 14, '#ffdf9e')
        if action.kind in {'attack', 'skill'} and not action.damage and after[1-actor].hp == before[1-actor].hp:
            pose = poses[1-actor]
            text = 'BLOCKED' if before[1-actor].shield > after[1-actor].shield else '0 DAMAGE'
            art.label(draw, (pose.x-38, max(4, pose.feet-190)), text, 16, '#e0d5f9')
    # Reserve cards are foreground UI. Swapping companions travel behind them,
    # and effect particles cannot obscure the bench HP tracks or assist meter.
    _reserve_cards(field, before[0], reserve_state, move_t, support_id=action.support_id if assisting else '', visiting=support_pose is not None)
    image.alpha_composite(field, (0, art.HUD))
    caption = (f'{before[actor].name} tags in {after[actor].name}.' if switching else
               f'{support.name if support else action.support_name} / {action.name}' if assisting else
               f'{after[actor].name} / {action.name}' if action else 'Battle complete.' if complete else 'Choose a move.')
    departed = tuple(f'{new.name} retreats.' for old, new in zip(before, after) if same_member(old, new) and new.hp <= 0 and (not action or departure_t))
    entries = (action.journal or journal) if action else journal
    lines = departed if departed else entries[-2:] if entries and (not action or move_t >= .48) else (caption,)
    ui.journal(image, art.HUD+art.HEIGHT, caption, lines)
    return image.convert('RGB')
