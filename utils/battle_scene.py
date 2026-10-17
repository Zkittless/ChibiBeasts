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
        return 1, 1
    elapsed = fx.clamp(t) * duration(before, after, action)
    return fx.clamp(elapsed / action.milliseconds), fx.clamp((elapsed-action.milliseconds)/fx.RETREAT_MS)


def draw_frame(before, after, realm, action=None, t=1, journal=()):
    image = art.field_background(realm, art.TOTAL_HEIGHT).copy()
    move_t, departure_t = timing(before, after, action, t)
    complete = any(f.hp <= 0 for f in after)
    title = 'BATTLE COMPLETE' if complete and (not action or t >= 1 or any(f.hp <= 0 for f in before)) else 'RETREAT' if departure_t else 'ULTIMATE' if action and action.ultimate else 'IN BATTLE' if action else 'YOUR TURN'
    ui.heading(image, title, art.REALMS[realm])
    for i, (old, new) in enumerate(zip(before, after)):
        visible = new if move_t >= fx.IMPACT else old
        ui.fighter_panel(image, new, art.sprite(new.species), 10+i*320,
                         art.interpolate(old.hp, new.hp, move_t), art.interpolate(old.mana, new.mana, move_t), visible)
    field = image.crop((0, art.HUD, art.WIDTH, art.HUD+art.HEIGHT))
    homes = [fx.Pose(162 if i==0 else 478,218,flip=bool(i),height=source.height if source else 130)
             for i,fighter in enumerate(after) for source in (art.sprite(fighter.species),)]
    poses = list(homes)
    actor = action.actor if action else 0
    animation = fx.profile(before[actor].species, action.name, action.kind, action.ultimate) if action else None
    if action:
        poses[actor] = fx.motion(animation, homes[actor], homes[1-actor], move_t)
        if .48<=move_t<.76 and after[1-actor].hp<before[1-actor].hp:
            sign = 1 if actor==0 else -1
            p=(move_t-.48)/.28
            poses[1-actor] = replace(poses[1-actor],x=poses[1-actor].x+sign*13*math.sin(p*math.pi),angle=sign*5*math.sin(p*math.pi))
        back=fx.Effects(field.size)
        back.effect(animation, poses[actor], poses[1-actor], move_t, back=True)
        field.alpha_composite(back.image)
        if animation.family in {'time','rewind','illusion'} and .06<move_t<.82:
            for i in range(3):
                p=fx.clamp((move_t-.32)/.16-.13*(i+1)) if animation.family=='time' else fx.clamp(1-(move_t-.32)/.3)
                ghost=replace(homes[actor],x=fx.mix(homes[actor].x,poses[actor].x,p)-(i+1)*12*(1 if actor==0 else -1),
                              feet=fx.mix(homes[actor].feet,poses[actor].feet,p),alpha=(.18-i*.04)*math.sin(move_t*math.pi))
                fx.paint_sprite(field,art.sprite(before[actor].species),ghost)
    visible_poses=[]
    for i,(old,new) in enumerate(zip(before,after)):
        pose=poses[i]
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
        source=art.sprite(new.species)
        if source is None:
            draw=ImageDraw.Draw(field)
            draw.ellipse((pose.x-35,pose.feet-75,pose.x+35,pose.feet),fill='#b7a8dc',outline='#ede6ff',width=3)
            art.label(draw,(pose.x-7,pose.feet-52),'?',25,'#242139')
        else:
            fx.paint_sprite(field,source,pose)
    front=fx.Effects(field.size)
    if action and not departure_t:
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
                source=art.sprite(new.species)
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
