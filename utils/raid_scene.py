"""Concurrent raid animation composition on the shared bounded renderer."""
from dataclasses import replace
import math

from PIL import ImageDraw

from utils import battle_art as art, field_journal as ui, move_animations as fx
from utils import raid_art as raid


def departing(before, after):
    old={m.uid:m for m in before.members}
    party_exit=bool(after.outcome and after.outcome!='victory')
    return after.boss.hp<=0<before.boss.hp or any(
        (m.fighter.hp<=0<old[m.uid].fighter.hp or party_exit and not before.outcome and old[m.uid].fighter.hp>0)
        and old[m.uid].slot==m.slot and old[m.uid].fighter.species==m.fighter.species
        for m in after.members if m.uid in old)


def departure_events(before, after, events):
    # Timeouts can close a healthy party between action batches. A display-only
    # marker lets that departure play without inventing a move or combat event.
    return events or ((raid.Event(0,None,'retreat'),) if departing(before,after) else ())


def duration(before, after, events):
    return round(raid.CLIP_SECONDS*1000)+(fx.BOSS_RETREAT_MS if departing(before,after) else 0) if events else 0


def draw_frame(before, after, events=(), t=1):
    before=before or after
    events=departure_events(before,after,events)
    animated=bool(events) and not (after.outcome and before.outcome==after.outcome)
    elapsed=fx.clamp(t)*duration(before,after,events) if animated else 0
    move_t=fx.clamp(elapsed/(raid.CLIP_SECONDS*1000)) if animated else 1
    retreat_t=fx.clamp((elapsed-raid.CLIP_SECONDS*1000)/fx.BOSS_RETREAT_MS) if animated else 1
    progress=art.transition(move_t) if animated else 1
    image=art.field_background(after.realm,raid.TOTAL_HEIGHT).copy()
    field=raid.stage(after.realm).copy()
    title='VICTORY' if after.outcome=='victory' and (not animated or t>=1) else 'RETREAT' if retreat_t and animated else 'RAID COMPLETE' if after.outcome and not animated else 'RAID BATTLE'
    ui.heading(image,title,art.REALMS[after.realm])
    ui.panel(image,(10,66,630,151))
    ui.portrait(image,raid.boss_portrait(after.boss.species),(18,75,78,135))
    draw=ImageDraw.Draw(image)
    ui.text(draw,after.boss.name,(88,73,410,96),size=21,display=True)
    ui.text(draw,f'Lv.{after.boss.level:g} / {len(after.members)} trainers / {after.remaining//60}:{after.remaining%60:02}',(420,74,620,95),size=12,minimum=10,align='right',ellipsis=False)
    hp=round(fx.mix(before.boss.hp,after.boss.hp,progress))
    ui.resource_bar(image,raid.BOSS_HEALTH,hp,after.boss.max_hp,'HP',ui.HEALTH,markers=(.75,.45,.2))
    warning=after.warning if after.pending else 'The weave settles' if after.outcome=='victory' else 'The party steps back' if after.outcome else 'The weave is steady'
    box=(raid.WARNING[0],raid.WARNING[1],482 if after.pending else raid.WARNING[2],raid.WARNING[3])
    ui.text(draw,warning,box,size=14,fill='#f2d39a' if after.pending else ui.MUTED)
    if after.pending:ui.text(draw,f'Weave {after.weaves}/{after.required}',(490,132,620,148),size=14,fill='#f2d39a',align='right')
    groups=raid.events_by_member(events)
    old={m.uid:m for m in before.members}
    boss_source=raid.boss_portrait(after.boss.species)
    boss_home=fx.Pose(320,259,size=282,height=boss_source.height if boss_source else 200)
    boss_pose=boss_home
    boss_event=next((e for e in reversed(events) if e.uid is None and e.kind=='boss' and e.move),None)
    boss_animation=fx.profile(after.boss.species,boss_event.move,'attack') if boss_event else None
    if animated and boss_animation:
        boss_pose=replace(boss_home,feet=boss_home.feet-5*math.sin(move_t*math.pi),angle=-2*math.sin(move_t*math.pi*2))
    if after.boss.hp<=0:
        if not animated or before.boss.hp<=0 or t>=1:boss_pose=None
        elif retreat_t:boss_pose=fx.retreat(boss_home,retreat_t,after.boss.species,boss=True)
    places=raid.placements(len(after.members))
    actors=[]
    for member,place in zip(after.members,places):
        prior=old.get(member.uid,member)
        changed=prior.slot!=member.slot or prior.fighter.species!=member.fighter.species
        active=prior.fighter if animated and move_t<.48 else member.fighter
        source=raid.fitted(active.species,place.size,active.armor_id)
        home=fx.Pose(place.x,place.feet,size=place.size,flip=place.x>320,height=source.height)
        pose=home
        group=groups.get(member.uid,{})
        event=group.get('event')
        valid=event and (not event.species or event.species==active.species) and (event.slot is None or event.slot==(prior.slot if move_t<.48 else member.slot))
        animation=None
        if animated and valid and not changed and event.kind not in {'swap','weave','tend','status'}:
            animation=fx.profile(event.species or active.species,event.move,event.kind,event.kind=='ultimate')
            pose=fx.motion(animation,home,boss_home,move_t)
        if changed and animated:
            if move_t<.48:
                pose=fx.retreat(home,move_t/.45,active.species)
            else:
                edge=-place.size if place.x<320 else raid.WIDTH+place.size
                pose=replace(home,x=fx.mix(edge,home.x,fx.smooth((move_t-.48)/.42)))
        withdrawn=bool(after.outcome and after.outcome!='victory')
        leaving=member.fighter.hp<=0 or withdrawn
        newly_leaving=(prior.fighter.hp>0 and (member.fighter.hp<=0 or withdrawn and not before.outcome))
        if leaving and not changed:
            if not animated or not newly_leaving or t>=1:pose=None
            elif retreat_t:pose=fx.retreat(home,retreat_t,active.species)
        if prior.fighter.hp<=0<member.fighter.hp and not changed and animated:
            edge=-place.size if place.x<320 else raid.WIDTH+place.size
            pose=replace(home,x=fx.mix(edge,home.x,fx.smooth((move_t-.48)/.42)),alpha=1 if move_t>=.48 else 0)
        if active.hp<=0 and changed and move_t<.48:pose=None
        actors.append((member,prior,active,home,pose,source,animation,group))
    back=fx.Effects(field.size)
    if animated and not retreat_t:
        for member,prior,active,home,pose,source,animation,group in actors:
            if pose and animation:back.effect(animation,pose,boss_home,move_t,back=True,scale=.65)
            if pose and boss_animation and member.fighter.hp < prior.fighter.hp:
                back.effect(boss_animation,boss_home,home,move_t,back=True,scale=.55)
    field.alpha_composite(back.image)
    draw=ImageDraw.Draw(field)
    if boss_pose:
        draw.ellipse((250,252,390,267),fill='#101729')
        if boss_source:fx.paint_sprite(field,boss_source,boss_pose)
        else:draw.ellipse((230,90,410,259),fill='#82679e')
    if after.pending:
        draw=ImageDraw.Draw(field);draw.ellipse((235,242,405,276),outline='#e4c68f',width=3)
    for member,prior,active,home,pose,source,animation,group in actors:
        if pose is None:continue
        if animated and animation and not retreat_t:
            fx.paint_echoes(field, source, animation, home, boss_home, move_t)
        draw=ImageDraw.Draw(field)
        draw.ellipse((pose.x-home.size*.31,home.feet-3,pose.x+home.size*.31,home.feet+9),fill='#172132')
        fx.paint_sprite(field,source,pose)
    front=fx.Effects(field.size)
    if boss_pose and retreat_t and after.boss.hp<=0:front.departure(boss_pose,retreat_t,after.boss.species,boss=True)
    for member,prior,active,home,pose,source,animation,group in actors:
        if pose is None:continue
        if retreat_t and (member.fighter.hp<=0 or after.outcome and after.outcome!='victory'):
            front.departure(pose,retreat_t,active.species)
        if active.hp>0 and (active.guard or active.shield):
            front.ring(pose.x,pose.feet-home.size*.52,home.size*.49,home.size*.57,'#d6baff' if active.guard else '#a5d7ef',.7)
        if not animated or retreat_t:continue
        if animation:front.effect(animation,pose,boss_home,move_t,scale=.65 if len(actors)>4 else .8)
        kinds=group.get('kinds',set())
        if 'weave' in kinds and .12<move_t<.9:
            front.line([(pose.x,pose.feet-50),(320,310),(320,250)],'#e8d393',3,.75)
        if 'tend' in kinds:
            targets=group.get('targets',set())
            for target in actors:
                if target[0].uid in targets and target[4]:front.effect(fx.Animation('heal','nature',sequence='petal_mend'),target[4],target[4],move_t,scale=.7)
        if boss_animation and member.fighter.hp<prior.fighter.hp:
            front.effect(boss_animation,boss_home,home,move_t,scale=.55)
    field.alpha_composite(front.image)
    draw=ImageDraw.Draw(field)
    if animated and .48<=move_t<.9 and not retreat_t:
        for member,prior,active,home,pose,source,animation,group in actors:
            if prior.slot!=member.slot:continue
            delta=member.fighter.hp-prior.fighter.hp
            if delta:
                text=f'{delta:+,}';width=draw.textlength(text,font=art.font(20))
                art.label(draw,(max(2,min(raid.WIDTH-width-2,home.x-width/2)),max(3,home.feet-home.size-20-(move_t-.48)*30)),text,20,'#b8fac5' if delta>0 else '#ffe0bd',outline=True)
        damage=sum(group['damage'] for group in groups.values())
        if damage:
            text=f'-{damage:,}';width=draw.textlength(text,font=art.font(20))
            art.label(draw,(320-width/2,90-round((move_t-.48)*60)),text,20,'#fff0d0',outline=True)
    # Stable trainer numbers remain legible above every effect layer.
    for index,box in enumerate(raid.marker_boxes(after)):
        draw.rectangle(box,fill='#1a243a')
        text=str(index+1);width=draw.textlength(text,font=art.font(28))
        art.label(draw,((box[0]+box[2]-width)/2,box[1]+1),text,28)
    image.alpha_composite(field,(0,raid.HUD))
    detail=f'{after.boss.name} retreats.' if retreat_t and after.boss.hp<=0 else 'The party steps back.' if retreat_t and after.outcome and after.outcome!='victory' else 'The party acts together.' if animated and move_t<.48 and before.boss.hp>0 else after.last_event or warning
    ui.journal(image,raid.HUD+raid.HEIGHT,warning,(detail,))
    return image.convert('RGB')
