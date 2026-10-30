"""Deterministic celestial choreography, drawn into the battle field only.

The caster gathers a recognizable shape before .48; the resolved action lands
at .48 and its fragments settle before the next prompt. These functions only
paint: they never infer, resolve, or modify a combat result.
"""
import math


SEQUENCES = frozenset({
    'clock_fracture', 'clock_rewind', 'paradox_loop', 'event_horizon',
    'genesis_bloom', 'genesis_implosion', 'terminus_severance',
    'zodiac_alignment', 'astral_loom', 'judgment_lance', 'karmic_mirror',
    'abyss_descent', 'stellar_nursery', 'supernova_detonation', 'nirvana_lotus',
    'world_tree', 'titan_crush', 'phoenix_sunrise', 'leviathan_deluge',
    'dragon_apocalypse', 'ragnarok_fangs', 'hydra_constellation',
    'ouroboros_reset', 'horizon_gate', 'sovereign_veil', 'prism_cathedral',
    'lunar_procession', 'phantom_court',
})

TAU = math.tau


def clamp(value):
    return max(0., min(1., value))


def smooth(value):
    value = clamp(value)
    return value * value * (3 - 2 * value)


def mix(left, right, amount):
    return left + (right - left) * amount


def arc(e, x, y, rx, ry, start, stop, color, width=2, alpha=1, count=24):
    e.line([(x + math.cos(mix(start, stop, i/count))*rx,
             y + math.sin(mix(start, stop, i/count))*ry)
            for i in range(count+1)], color, width, alpha)


def diamond(e, x, y, width, height, colors, alpha=1):
    dark, mid, light = colors
    e.polygon([(x-width, y), (x, y-height), (x+width, y), (x, y+height)], mid, alpha)
    e.polygon([(x, y-height), (x+width, y), (x, y+height)], dark, alpha)
    e.line([(x-width, y), (x, y-height), (x+width, y)], light, 2, alpha)
    e.line([(x, y-height), (x, y+height)], light, 2, alpha*.6)


def rays(e, x, y, radius, length, count, turn, colors, alpha, width=3):
    for i in range(count):
        angle = turn + TAU*i/count
        e.line([(x+math.cos(angle)*radius, y+math.sin(angle)*radius),
                (x+math.cos(angle)*(radius+length), y+math.sin(angle)*(radius+length))],
               colors[i % len(colors)], width, alpha)


def feather(e, x, y, length, angle, colors, alpha):
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy*length*.16, dx*length*.16
    tip = (x+dx*length, y+dy*length)
    e.polygon([(x, y), (x+dx*length*.35+nx, y+dy*length*.35+ny), tip,
               (x+dx*length*.65-nx, y+dy*length*.65-ny)], colors[1], alpha)
    e.line([(x, y), tip], colors[2], 2, alpha)


def glyph(e, x, y, radius, turn, colors, alpha):
    """A split triangular seal, rather than the former generic orbit ring."""
    for offset in (0, math.pi):
        points = [(x+math.cos(turn+offset+i*TAU/3)*radius,
                   y+math.sin(turn+offset+i*TAU/3)*radius) for i in range(4)]
        e.line(points, colors[1], 2, alpha)
    for i in range(6):
        angle = turn+i*TAU/6
        diamond(e, x+math.cos(angle)*radius, y+math.sin(angle)*radius,
                3, 5, colors, alpha)


def shock(e, x, feet, p, radius, colors, alpha=1):
    if 0 <= p < 1:
        extent = radius*(.25+1.25*smooth(p))
        e.ring(x, feet, extent, 8+14*p, colors[1], alpha*(1-p))
        for side in (-1, 1):
            e.line([(x+side*extent*.45, feet-3), (x+side*extent*.7, feet-11),
                    (x+side*extent, feet-5)], colors[2], 3, alpha*(1-p))


def paint(e, animation, actor, target, t, *, colors, back=False, scale=1):
    """Draw a registered sequence; return False for another module's IDs.

    ``scale`` reduces both coverage and detail in concurrent raid compositions.
    A duel's brief background dim leaves the sprites/HUD untouched. Concurrent
    raid effects stay local and never accumulate a stage-wide tint.
    """
    sequence = animation.sequence
    if sequence not in SEQUENCES:
        return False
    if not 0 < t < 1:
        return True
    dark, mid, light = colors
    ax, ay = actor.x, actor.feet-(actor.height or actor.size)*.52
    tx, ty = target.x, target.feet-(target.height or target.size)*.52
    sign = 1 if tx >= ax else -1
    u = max(.26, min(actor.size, target.size, 180)/180) * scale
    # Celestial silhouettes must extend beyond the beast, not disappear behind
    # its body. The raid compositor scales them back into each trainer's lane.
    r = 118*u
    charge = smooth((t-.06)/.35)
    release = clamp((t-.48)/.4)
    fade = 1-smooth((t-.77)/.2)
    glow = smooth((t-.04)/.12)*fade
    phase = (animation.accent % 19)/19*TAU
    landed = t >= .48
    supportive = animation.family in {'guard', 'shelter', 'wind_guard', 'illusion', 'rewind', 'heal'}
    if back and scale > .8 and not supportive and sequence != 'clock_rewind':
        # Hold the backdrop still while the local choreography moves. A smooth
        # full-field fade changes every pixel on every GIF frame and defeats
        # delta compression on richly painted biomes (the attachment is 2 MB).
        shade = .36 if .15 <= t <= .82 else .18 if .07 <= t <= .91 else 0
        if shade:
            e.rect(0, 0, e.image.width, e.image.height, '#070d1c', shade)

    if sequence in {'clock_fracture', 'clock_rewind'}:
        defensive = sequence == 'clock_rewind'
        x, y = (ax, ay) if defensive else (tx, ty)
        radius = r*(.25+.75*charge)
        if back:
            e.clock(x, y, radius, -t*1.5 if defensive else min(t, .4)*2, glow*.8)
            if not defensive:
                # At the strike the clock face splits along three unequal cuts.
                for i in range(3):
                    angle = -.9+i*2.1
                    d = 38*u*release
                    arc(e, x+math.cos(angle)*d, y+math.sin(angle)*d,
                        radius, radius, angle-.8, angle+.8, light, 3, glow*(1-release))
        elif defensive:
            for i in range(9):
                age = (t*1.35+i/9) % 1
                angle = -age*TAU+phase
                diamond(e, ax+math.cos(angle)*r*.8, ay+math.sin(angle)*r*.8,
                        3*u, 8*u, colors, glow*(1-age*.6))
            if landed:
                for i in range(3):
                    e.line([(ax-r*.5+i*r*.5, actor.feet-15-80*u*release),
                            (ax-r*.5+i*r*.5, actor.feet-32-80*u*release)], light, 3, fade*(1-release))
        elif landed:
            # Frozen hands become broad, radial shards on the actual impact.
            for i in range(12):
                a = i*TAU/12
                d = r*(.25+release*.95)
                x1, y1 = tx+math.cos(a)*d, ty+math.sin(a)*d
                e.polygon([(x1, y1), (x1+math.cos(a-.2)*22*u, y1+math.sin(a-.2)*22*u),
                           (x1+math.cos(a)*40*u, y1+math.sin(a)*40*u)], colors[i%3], fade*(1-release))
            e.line([(tx-r*(1+release), ty+r*.5), (tx, ty),
                    (tx+r*(1+release), ty-r*.5)], light, 5, fade*(1-release))
        return True

    if sequence == 'event_horizon':
        radius = r*(charge if not landed else 1-.75*smooth(release))
        if back:
            # A black lens occludes the field, with a flattened accretion disc.
            e.draw.ellipse((tx-radius*.7, ty-radius*.7, tx+radius*.7, ty+radius*.7),
                           fill=e.color('#08101f', glow*.9))
            for i in range(3):
                arc(e, tx, ty, radius*(1.2+i*.18), radius*(.22+i*.04),
                    .2+t*3+i, math.pi*1.8+t*3+i, colors[2-i], 3, glow*.8)
        else:
            if .16 < t < .72:
                # The lens briefly passes in front of the victim: a black hole
                # hidden entirely behind a wide beast reads as no effect at all.
                front_radius = radius*.72
                e.draw.ellipse((tx-front_radius, ty-front_radius,
                                tx+front_radius, ty+front_radius),
                               fill=e.color('#080c1d', glow*.8*(1-release*.65)))
                arc(e, tx, ty, radius*.81, radius*.81, -.5+t, 4.7+t,
                    light, max(2, round(4*u)), glow*.85*(1-release))
            for i in range(14):
                angle = phase+i*2.39996+t*1.8
                d = r*(1.9-(t*1.5+i*.07)%1*1.6)
                x, y = tx+math.cos(angle)*d, ty+math.sin(angle)*d*.65
                e.line([(x, y), (tx+math.cos(angle-.18)*d*.82, ty+math.sin(angle-.18)*d*.54)], mid, 3, glow*.8)
            if landed:
                rays(e, tx, ty, radius, r*1.2*(1-release), 8, phase, (light, mid), fade*(1-release))
                shock(e, tx, target.feet, release, r*1.5, colors)
        return True

    if sequence in {'genesis_bloom', 'genesis_implosion'}:
        implosion = sequence == 'genesis_implosion'
        x, y = tx, ty
        if back:
            glyph(e, x, y, r*(.3+charge*.8), -t*.45, colors, glow*.55)
            for i in range(7):
                a = phase+i*TAU/7
                extent = (1.6-charge*.85)*r if not landed else r*(.75+release)
                if implosion:
                    extent = r*(.9+charge*.6) if not landed else r*(1.5-1.35*release)
                diamond(e, x+math.cos(a)*extent, y+math.sin(a)*extent*.7,
                        10*u*(1+charge), 26*u*(1+charge), colors, glow*.65)
        else:
            core = r*(.12+.3*charge) if not landed else r*(.42+release*.7)
            if implosion:
                core = r*(.12+.35*charge) if not landed else r*(.48*(1-release))
            if not landed:
                # A bright four-point seed makes the creation event legible
                # even when the far-side crystal architecture is occluded.
                e.polygon([(x, y-core), (x+core*.2, y-core*.2), (x+core, y),
                           (x+core*.2, y+core*.2), (x, y+core),
                           (x-core*.2, y+core*.2), (x-core, y),
                           (x-core*.2, y-core*.2)], light, glow*.8)
                glyph(e, x, y, r*.7*charge, phase*.15, colors, glow*.85)
            else:
                e.star(x, y, core, light, glow*.85)
            if landed:
                for i in range(8):
                    a = phase+i*TAU/8
                    d = r*(.3+release*1.4) if not implosion else r*(1.8-1.65*release)
                    feather(e, x+math.cos(a)*d, y+math.sin(a)*d*.75,
                            r*.55*(1-release), a if not implosion else a+math.pi, colors, fade*(1-release))
                shock(e, tx, target.feet, release, r*1.8, colors)
        return True

    if sequence == 'terminus_severance':
        if animation.family == 'time':
            # End of Time arrests a monumental dial, then breaks each sector
            # outward. Last Twilight retains the separate diagonal world-shear.
            radius = r*(.4+.5*charge)
            if back:
                e.ring(tx, ty, radius+8*u, radius*.8+6*u, dark, glow*.8)
            elif not landed:
                e.ring(tx, ty, radius, radius*.8, mid, glow)
                for i in range(12):
                    a = i*TAU/12-math.pi/2
                    e.line([(tx+math.cos(a)*radius*.85, ty+math.sin(a)*radius*.68),
                            (tx+math.cos(a)*radius, ty+math.sin(a)*radius*.8)],
                           light, 4 if i%3 == 0 else 2, glow)
                a = min(t, .35)*TAU*1.5-math.pi/2
                e.line([(tx+math.cos(a)*radius*.8, ty+math.sin(a)*radius*.64),
                        (tx, ty), (tx+math.cos(a*.3)*radius*.5,
                                   ty+math.sin(a*.3)*radius*.4)], light, 5, glow)
                diamond(e, tx, ty, 6*u, 9*u, colors, glow)
            else:
                for i in range(6):
                    a = i*TAU/6-math.pi/2
                    shift = release*r*.65
                    x, y = tx+math.cos(a)*shift, ty+math.sin(a)*shift*.8
                    arc(e, x, y, radius, radius*.8, a-.38, a+.38,
                        mid, 4, fade*(1-release))
                    e.line([(tx+math.cos(a)*r*.12, ty+math.sin(a)*r*.1),
                            (x+math.cos(a)*radius, y+math.sin(a)*radius*.8)],
                           light, 4, fade*(1-release))
                    diamond(e, x+math.cos(a)*radius, y+math.sin(a)*radius*.8,
                            5*u*(1-release), 12*u*(1-release), colors, fade*(1-release))
            return True
        if back:
            # Twilight shutters leave a single diagonal fault between them.
            for side in (-1, 1):
                x = tx+side*r*(1.1-.65*charge)
                e.polygon([(x, ty-r), (x+side*r*.4, ty-r*.7),
                           (x+side*r*.4, ty+r), (x, ty+r*.7)], dark, glow*.5)
        else:
            extent = r*(.15+1.3*charge)
            shift = sign*release*r*.65
            e.line([(tx-extent+shift, ty+extent*.7), (tx+extent+shift, ty-extent*.7)],
                   light, 2 if not landed else 8, glow*(1-release*.8))
            if landed:
                for i in range(7):
                    x = tx-r+i*r/3
                    y = ty-(x-tx)*.7
                    e.line([(x, y), (x+sign*release*80*u, y+(-1 if i%2 else 1)*release*50*u)],
                           mid, 4, fade*(1-release))
        return True

    if sequence in {'zodiac_alignment', 'stellar_nursery'}:
        nursery = sequence == 'stellar_nursery'
        nodes = 7 if nursery else 12
        if back:
            points = []
            for i in range(nodes):
                a = -math.pi/2+i*TAU/nodes
                d = r*(.7+(i%3)*.18)*charge
                points.append((tx+math.cos(a)*d, ty+math.sin(a)*d*.85))
            if nursery:
                for i in range(1, nodes):
                    e.line([points[i//2], points[i]], mid, 2, glow*.6)
            else:
                e.line(points+[points[0]], mid, 2, glow*.65)
                for i in range(0, nodes, 3):
                    e.line([points[i], points[(i+4)%nodes], points[(i+8)%nodes], points[i]],
                           light, 2, glow*.45)
            for i, (x, y) in enumerate(points):
                if charge >= i/nodes:
                    e.star(x, y, (4+i%3)*u, light, glow)
        elif not landed and not nursery:
            # Twelve visible zodiac houses sit over the target's silhouette;
            # their connecting constellation is part of the wind-up, not damage.
            points = []
            for i in range(nodes):
                a = -math.pi/2+i*TAU/nodes
                points.append((tx+math.cos(a)*r*charge,
                               ty+math.sin(a)*r*.76*charge))
            e.line(points+[points[0]], mid, 3, glow*.85)
            for i, (x, y) in enumerate(points):
                if charge >= i/nodes:
                    diamond(e, x, y, 5*u, 8*u, colors, glow)
            if charge > .5:
                e.line([points[0], points[4], points[8], points[0]], light, 3, glow*.75)
                e.line([points[2], points[6], points[10], points[2]], light, 3, glow*.75)
        elif landed:
            for i in range(nodes):
                a = -math.pi/2+i*TAU/nodes
                d = r*(1-smooth(release/.65))
                if nursery:
                    d = r*(.15+release*1.4)
                x, y = tx+math.cos(a)*d, ty+math.sin(a)*d*.85
                e.line([(x, y), (tx+math.cos(a)*(d+22*u), ty+math.sin(a)*(d+22*u)*.85)],
                       light, 3, fade*(1-release))
                e.star(x, y, (6 if nursery else 4)*u, colors[i%3], fade*(1-release))
            if not nursery:
                e.star(tx, ty, r*.7*(1-release), light, fade*(1-release))
        return True

    if sequence == 'astral_loom':
        if back:
            # The loom is an orthogonal warp: threads physically weave a seal.
            for i in range(7):
                x = tx+(i-3)*r*.26
                top = ty-r*charge
                bottom = ty+r*charge
                e.line([(x, top), (x, bottom)], mid, 2, glow*.55)
                y = ty+(i-3)*r*.26
                e.line([(tx-r*charge, y), (tx+r*charge, y)], light, 2, glow*.45)
        else:
            if t < .48:
                needle = smooth((t-.17)/.31)
                diamond(e, mix(ax, tx, needle), mix(ay, ty, needle), 6*u, 18*u, colors, glow)
            else:
                for i in range(7):
                    a = (i-3)*r*.26
                    e.line([(tx-r, ty+a), (tx, ty+a+release*70*u), (tx+r, ty+a)],
                           colors[i%3], 3, fade*(1-release))
                    e.line([(tx+a, ty-r), (tx+a+release*70*u, ty), (tx+a, ty+r)],
                           colors[(i+1)%3], 2, fade*(1-release))
        return True

    if sequence == 'judgment_lance':
        if back:
            # Two architectural gates open over the target, framing a lance.
            for side in (-1, 1):
                x = tx+side*r*.5
                e.line([(x, target.feet), (x, ty-r*charge), (tx, ty-r*1.3*charge)], mid, 4, glow*.7)
                e.line([(x+side*8*u, target.feet), (x+side*8*u, ty-r*charge)], light, 2, glow*.7)
        else:
            tip = mix(ty-r*1.4, ty, smooth((t-.32)/.16))
            if not landed:
                diamond(e, tx, tip, 8*u, 40*u*charge, colors, glow)
            else:
                width = 12*u*(1-release)
                e.polygon([(tx-width, ty-r*1.5), (tx+width, ty-r*1.5),
                           (tx+width*.6, target.feet), (tx, target.feet+8),
                           (tx-width*.6, target.feet)], light, fade*(1-release))
                shock(e, tx, target.feet, release, r*1.5, colors)
                for side in (-1, 1):
                    for i in range(4):
                        feather(e, tx+side*(12+i*13)*u, ty+25*u+release*30*u,
                                (22-i*3)*u, -math.pi/2+side*.65, colors, fade*(1-release))
        return True

    if sequence == 'karmic_mirror':
        if back:
            # Tall opposing mirror facets exchange a reflected strike.
            for x in (ax+sign*35*u, tx-sign*35*u):
                diamond(e, x, mix(ay, ty, .5), 25*u*charge, r, colors, glow*.4)
        else:
            p = smooth((t-.16)/.32)
            x, y = mix(ax, tx, p), mix(ay, ty, p)
            e.line([(ax, ay), (x, y-r*.3), (x, y+r*.3)], mid, 3, glow*(1-release))
            if landed:
                for i in range(6):
                    a = phase+i*TAU/6
                    diamond(e, tx+math.cos(a)*r*release, ty+math.sin(a)*r*release,
                            9*u*(1-release), 25*u*(1-release), colors, fade*(1-release))
        return True

    if sequence == 'abyss_descent':
        if back:
            for i in range(4):
                depth = i/4
                e.ring(tx, target.feet-3+depth*12*u, r*(1-depth*.15)*charge,
                       (15-depth*2)*u, colors[i%3], glow*(.65-depth*.3))
        else:
            for i in range(7):
                x = tx+(i-3)*r*.29
                height = r*(.65+(i%3)*.2)*charge
                descend = release*70*u
                e.polygon([(x-7*u, target.feet-descend), (x-9*u, target.feet-height-descend),
                           (x, target.feet-height-18*u-descend), (x+9*u, target.feet-height-descend),
                           (x+7*u, target.feet-descend)], mid, glow*.65)
                if landed:
                    e.line([(x, target.feet-height-descend), (x, target.feet-descend)],
                           light, 3, fade*(1-release))
        return True

    if sequence == 'supernova_detonation':
        if back:
            # Dense star contracts before long pointed solar prominences erupt.
            radius = r*(.5-.3*charge) if not landed else r*(.2+release*1.5)
            e.ring(tx, ty, radius, radius, mid, glow*(1-release))
            if not landed:
                rays(e, tx, ty, radius+5*u, 16*u, 8, phase-t, colors, glow*.7)
        else:
            if not landed:
                # The containment lattice stays in front of the victim so the
                # Big Bang visibly compresses at .24/.43, even on a wide sprite.
                radius = r*(1.1-.25*charge)
                points = [(tx+math.cos(phase+i*TAU/8)*radius,
                           ty+math.sin(phase+i*TAU/8)*radius*.72) for i in range(9)]
                e.line(points, dark, 7, glow*.95)
                e.line(points, light, 3, glow)
                for i in range(8):
                    a = phase+i*TAU/8
                    outside = radius+20*u
                    inside = radius*(.72-.15*charge)
                    e.line([(tx+math.cos(a)*outside, ty+math.sin(a)*outside*.72),
                            (tx+math.cos(a)*inside, ty+math.sin(a)*inside*.72)], mid, 4, glow*.9)
                    diamond(e, tx+math.cos(a)*radius, ty+math.sin(a)*radius*.72,
                            4*u, 7*u, colors, glow)
                e.star(tx, ty, r*(.25-.16*charge), light, glow)
            else:
                for i in range(12):
                    a = phase+i*TAU/12
                    inner = r*(.15+release*.6)
                    outer = r*(1.05+release*.8)*(1 if i%2 else .7)
                    e.polygon([(tx+math.cos(a-.13)*inner, ty+math.sin(a-.13)*inner),
                               (tx+math.cos(a)*outer, ty+math.sin(a)*outer),
                               (tx+math.cos(a+.13)*inner, ty+math.sin(a+.13)*inner)],
                              colors[i%3], fade*(1-release))
                e.star(tx, ty, r*.4*(1-release), light, fade*(1-release))
        return True

    if sequence == 'nirvana_lotus':
        if back:
            for i in range(7):
                a = math.pi+(i+1)*math.pi/8
                x, y = ax+math.cos(a)*r*charge, actor.feet+math.sin(a)*r*.65*charge
                e.polygon([(ax, actor.feet), (x-16*u, y+12*u), (x, y-35*u),
                           (x+16*u, y+12*u)], colors[i%3], glow*.5)
            e.ring(ax, actor.feet-3, r*1.05, 14*u, light, glow*.6)
        elif supportive and landed:
            # Restoration ascends around its beneficiary only.
            for i in range(9):
                age = (release+i/9) % 1
                x = ax+math.sin(i*2.4)*r*.7*(1-age*.3)
                y = actor.feet-20*u-age*r*1.5
                diamond(e, x, y, 4*u, 9*u, colors, fade*(1-age))
            arc(e, ax, ay, r*.75, r*.8, math.pi, TAU, light, 3, fade*.6)
        elif not supportive:
            if not landed:
                progress = smooth((t-.25)/.23)
                for side in (-1, 1):
                    feather(e, mix(ax, tx, progress), mix(ay, ty, progress),
                            32*u*charge, -math.pi/2+side*.7, colors, glow)
            else:
                for i in range(8):
                    a = i*TAU/8
                    d = r*(.18+release*1.1)
                    feather(e, tx+math.cos(a)*d, ty+math.sin(a)*d*.8,
                            34*u*(1-release), a, colors, fade*(1-release))
                shock(e, tx, target.feet, release, r*1.3, colors)
        return True

    if sequence == 'world_tree':
        if back:
            trunk = smooth((t-.1)/.3)
            e.line([(tx, target.feet), (tx-10*u, ty+15*u), (tx, ty-r*trunk)], dark, max(3, round(15*u)), glow*.8)
            for i in range(8):
                side = -1 if i%2 else 1
                y = target.feet-(25+i*13)*u*trunk
                branch = [(tx, y+12*u), (tx+side*(38+i*6)*u*trunk, y-15*u),
                          (tx+side*(60+i*6)*u*trunk, y-36*u)]
                e.line(branch, mid, max(2, round(5*u)), glow*.8)
                diamond(e, branch[-1][0], branch[-1][1], 11*u, 19*u, colors, glow*.7)
        elif landed:
            for i in range(6):
                side = -1 if i%2 else 1
                x = tx+side*(30+i*8)*u
                y = target.feet-20*u
                e.line([(tx, target.feet), (x, y), (x+side*release*35*u, y-release*35*u)], light, 4, fade*(1-release))
                feather(e, x+side*release*35*u, y-release*45*u, 20*u,
                        -math.pi/2+side*.4, colors, fade*(1-release))
            shock(e, tx, target.feet, release, r*1.4, colors)
        return True

    if sequence == 'titan_crush':
        # A massive faceted plate descends, followed by angular fault lines.
        bottom = mix(ty-r*1.5, ty+25*u, smooth((t-.24)/.24))
        if back:
            for i in range(5):
                x = tx+(i-2)*26*u
                e.line([(x, bottom-r*.8), (x-sign*8*u, bottom-r*1.3)], mid, 2, glow*.45)
        else:
            if t < .64:
                e.polygon([(tx-r, bottom-32*u), (tx-r*.7, bottom-r*.85),
                           (tx+r*.65, bottom-r*.85), (tx+r, bottom-32*u),
                           (tx+r*.6, bottom), (tx-r*.6, bottom)], dark, glow*(1-release))
                e.line([(tx-r, bottom-32*u), (tx, bottom-18*u), (tx+r, bottom-32*u)], light, 4, glow*(1-release))
                e.line([(tx, bottom-18*u), (tx, bottom-r*.75)], mid, 3, glow*(1-release))
            if landed:
                for side in (-1, 1):
                    for i in range(3):
                        x = tx+side*(20+i*25)*u
                        e.line([(tx, target.feet), (x, target.feet-8*u),
                                (x+side*release*65*u, target.feet+8*u)], light, 3, fade*(1-release))
                        diamond(e, x+side*release*50*u, target.feet-32*u*math.sin(release*math.pi),
                                10*u, 14*u, colors, fade*(1-release))
        return True

    if sequence == 'phoenix_sunrise':
        x = mix(ax, tx, smooth((t-.24)/.24))
        y = mix(ay, ty, smooth((t-.24)/.24))-math.sin(charge*math.pi)*22*u
        if back:
            # A rising half sun stays behind the caster; the bird crosses front.
            arc(e, ax, actor.feet, r*charge, r*charge, math.pi, TAU, mid, 4, glow*.6)
            if landed:
                rays(e, tx, ty, r*.5, r*.45, 9, -math.pi, colors, fade*(1-release))
        else:
            for side in (-1, 1):
                for i in range(5):
                    feather(e, x+side*8*u, y+10*u+i*5*u,
                            (64-i*8)*u, -math.pi/2+side*(.4+i*.2), colors, glow*(1-release*.7))
            e.polygon([(x, y-22*u), (x+10*u, y+13*u), (x, y+38*u),
                       (x-10*u, y+13*u)], light, glow*(1-release*.7))
            for i in range(3):
                feather(e, x+(i-1)*10*u, y+23*u, 38*u,
                        math.pi/2+(i-1)*.25, colors, glow*(1-release))
            if landed:
                shock(e, tx, target.feet, release, r*1.7, colors)
        return True

    if sequence == 'leviathan_deluge':
        if back:
            for layer in range(3):
                height = (45+layer*26)*u*charge
                crest = mix(ax, tx, smooth((t-.18)/.3))-sign*(2-layer)*24*u
                points = [(ax-sign*20*u, target.feet), (crest-sign*70*u, target.feet-height),
                          (crest-sign*12*u, target.feet-height-22*u), (crest+sign*18*u, target.feet-height-10*u),
                          (crest+sign*12*u, target.feet-height+14*u), (crest-sign*8*u, target.feet-height+5*u),
                          (crest-sign*10*u, target.feet-height+27*u), (crest+sign*45*u, target.feet)]
                e.polygon(points, colors[layer], glow*(.35+layer*.12)*(1-release*.6))
        elif landed:
            for i in range(9):
                a = math.pi+(i+1)*math.pi/10
                d = r*(.25+release*1.4)
                x, y = tx+math.cos(a)*d, target.feet+math.sin(a)*d*.8+release*20*u
                e.line([(x, y+15*u), (x+math.cos(a)*14*u, y+math.sin(a)*18*u)], light, 4, fade*(1-release))
            shock(e, tx, target.feet, release, r*1.8, colors)
        return True

    if sequence == 'dragon_apocalypse':
        if back:
            # Horned, jagged maw silhouettes above the caster charge a breath.
            for side in (-1, 1):
                e.line([(ax+side*22*u, ay-12*u), (ax+side*40*u, ay-r*.7),
                        (ax+side*12*u, ay-r*.5)], mid, 5, glow*(1-release))
            glyph(e, ax, ay, r*.52*charge, phase, colors, glow*.45)
        elif t >= .2:
            front = mix(ax, tx, smooth((t-.2)/.28))
            thickness = (8+charge*24)*u*(1-release*.5)
            for layer in range(3):
                width = thickness*(1-layer*.28)
                top, bottom = [], []
                for i in range(13):
                    f = i/12
                    x = mix(ax, front, f)
                    y = mix(ay, ty, f)
                    wobble = math.sin(i*2+t*32)*width*.2*f
                    top.append((x, y-width*f+wobble))
                    bottom.append((x, y+width*f+wobble))
                e.polygon(top+bottom[::-1], colors[layer], glow*(1-release))
            if landed:
                for side in (-1, 1):
                    e.polygon([(tx, ty), (tx+side*r*.7*release, ty-r*(1-release*.5)),
                               (tx+side*r*(.4+release), ty+r*.7*release)], mid, fade*(1-release)*.7)
                shock(e, tx, target.feet, release, r*1.6, colors)
        return True

    if sequence == 'ragnarok_fangs':
        if back:
            arc(e, tx, ty, r*charge, r*.88*charge, .2, math.pi-.2, mid, 4, glow*.6)
            arc(e, tx, ty, r*charge, r*.88*charge, math.pi+.2, TAU-.2, mid, 4, glow*.6)
        else:
            gap = r*(.7-.6*charge) if not landed else r*(.1+release*.8)
            for side in (-1, 1):
                for i in range(5):
                    x = tx+(i-2)*r*.34
                    y = ty+side*gap
                    fang = (28+13*(i%2))*u*(1-release*.6)
                    e.polygon([(x-8*u, y+side*fang), (x+8*u, y+side*fang), (x, y)], light, glow*(1-release))
            if landed:
                for i in range(3):
                    e.line([(tx-r*.65+i*20*u, ty-r), (tx-r*.2+i*20*u, ty),
                            (tx+r*.2+i*20*u, ty+r)], mid, 5, fade*(1-release))
        return True

    if sequence == 'hydra_constellation':
        if back:
            for i in range(5):
                angle = math.pi+(i+1)*math.pi/6
                headx = tx+math.cos(angle)*r*charge
                heady = ty+math.sin(angle)*r*charge
                points = [(ax, actor.feet-15*u), (mix(ax, headx, .45), ay-25*u-i*6*u),
                          (headx-sign*10*u, heady+20*u), (headx, heady)]
                e.line(points, dark, max(3, round(10*u)), glow*.8)
                e.line(points, mid, 3, glow*.8)
                e.polygon([(headx-12*u, heady+4*u), (headx-7*u, heady-10*u),
                           (headx+12*u, heady-4*u), (headx+10*u, heady+7*u)], mid, glow)
                e.rect(headx+sign*4*u, heady-3*u, 3*u, 3*u, light, glow)
        elif landed:
            for i in range(5):
                a = math.pi+(i+1)*math.pi/6
                x, y = tx+math.cos(a)*r*(1-release), ty+math.sin(a)*r*(1-release)
                e.line([(x, y), (tx, ty)], light, 3, fade*(1-release))
                e.star(x, y, 6*u, light, fade*(1-release))
            shock(e, tx, target.feet, release, r*1.3, colors)
        return True

    if sequence in {'paradox_loop', 'ouroboros_reset'}:
        serpent = sequence == 'ouroboros_reset'
        if back:
            points = []
            for i in range(65):
                a = i*TAU/64
                if serpent:
                    x, y = tx+math.cos(a)*r*charge, ty+math.sin(a)*r*.8*charge
                else:
                    x, y = tx+math.cos(a)*r*charge, ty+math.sin(a*2)*r*.45*charge
                points.append((x, y))
            e.line(points, dark, max(3, round(10*u)), glow*.8)
            e.line(points, mid, 3, glow)
            for i in range(12):
                index = int((i*5+t*15)%64)
                x, y = points[index]
                diamond(e, x, y, 4*u, 6*u, colors, glow)
            if serpent:
                x, y = points[int((t*.6 % 1)*64)]
                diamond(e, x, y, 13*u, 9*u, colors, glow)
        elif landed:
            if serpent:
                # The loop folds into a square reset aperture, never UI flicker.
                extent = r*(1-release)
                e.line([(tx-extent, ty-extent*.7), (tx+extent, ty-extent*.7),
                        (tx+extent, ty+extent*.7), (tx-extent, ty+extent*.7),
                        (tx-extent, ty-extent*.7)], light, 4, fade*(1-release))
            else:
                for side in (-1, 1):
                    e.line([(tx+side*r, ty-r*.4), (tx-side*r*release, ty),
                            (tx+side*r, ty+r*.4)], light, 4, fade*(1-release))
        return True

    if sequence == 'horizon_gate':
        if back:
            for side in (-1, 1):
                x = tx+side*r*.65*charge
                e.line([(x, ty+r), (x, ty-r), (tx, ty-r*1.25)], mid, 4, glow)
                e.line([(x+side*8*u, ty+r), (x+side*8*u, ty-r)], light, 2, glow*.7)
            for i in range(4):
                y = ty-r+i*r*.6
                e.line([(tx-r*.65*charge, y), (tx+r*.65*charge, y)], mid, 2, glow*.35)
        elif landed:
            for i in range(7):
                x = tx+(i-3)*14*u
                length = r*(1-release)*(1-.12*abs(i-3))
                e.line([(x, ty-length), (x+sign*release*80*u, ty), (x, ty+length)],
                       colors[i%3], 3, fade*(1-release))
            shock(e, tx, target.feet, release, r*1.5, colors)
        return True

    if sequence == 'sovereign_veil':
        if back:
            for i in range(9):
                a = math.pi+(i+1)*math.pi/10
                x = ax+math.cos(a)*r*.9*charge
                y = actor.feet+math.sin(a)*r*1.5*charge
                e.polygon([(ax, actor.feet), (x-12*u, y+25*u), (x, y-10*u),
                           (x+12*u, y+25*u)], colors[i%3], glow*.35)
                e.line([(ax, actor.feet), (x, y)], light, 2, glow*.65)
        else:
            for i in range(5):
                x = ax+(i-2)*r*.3
                y = ay-r*.65-abs(i-2)*-8*u
                e.line([(x-6*u, y+10*u), (x-6*u, y), (x, y-10*u),
                        (x+6*u, y), (x+6*u, y+10*u)], light, 3, glow)
            if landed and supportive:
                arc(e, ax, ay, r*.85, r*1.05, math.pi*.95, TAU*1.025, mid, 3, fade*.7)
            elif not supportive:
                reach = smooth((t-.25)/.23)
                for i in range(5):
                    endx, endy = mix(ax, tx, reach), mix(ay, ty, reach)
                    bow = (i-2)*16*u
                    e.line([(ax, ay+20*u), (mix(ax, endx, .5), mix(ay, endy, .5)+bow),
                            (endx, endy)], colors[i%3], 3, glow*(1-release))
                if landed:
                    for i in range(5):
                        a = -math.pi/2+i*TAU/5
                        feather(e, tx+math.cos(a)*r*release, ty+math.sin(a)*r*release,
                                35*u*(1-release), a, colors, fade*(1-release))
        return True

    if sequence == 'prism_cathedral':
        if back:
            for i in range(5):
                x = tx+(i-2)*22*u
                height = (90-abs(i-2)*18)*u*charge
                diamond(e, x, target.feet-height*.6, 15*u*charge, height*.6, colors, glow*.6)
            e.line([(tx-r, target.feet), (tx, ty-r*charge), (tx+r, target.feet)], light, 2, glow*.65)
        elif landed:
            for i in range(9):
                a = phase+i*TAU/9
                d = r*(.25+release*1.3)
                diamond(e, tx+math.cos(a)*d, ty+math.sin(a)*d*.8,
                        7*u*(1-release), 20*u*(1-release), colors, fade*(1-release))
            for side in (-1, 1):
                e.line([(tx, ty-r), (tx+side*r*(.6+release), ty), (tx, ty+r)], light, 3, fade*(1-release))
        return True

    if sequence == 'lunar_procession':
        if back:
            # An eclipsed moon, then a descending procession of moth silhouettes.
            moon_y = ty-r*.75
            arc(e, tx, moon_y, r*.55*charge, r*.55*charge, -.9, math.pi*1.3, light, 4, glow*.8)
            arc(e, tx+10*u, moon_y-5*u, r*.47*charge, r*.47*charge, -.8, math.pi*1.2, mid, 2, glow*.6)
        else:
            for i in range(7):
                p = smooth((t-.18-i*.018)/.3)
                x = mix(ax-sign*(i%3)*18*u, tx+(i-3)*17*u, p)
                y = mix(ay-r*.8+(i%3)*18*u, ty+(i%2)*20*u, p)
                spread = (13+math.sin(t*18+i)*4)*u*(1-release*.5)
                for side in (-1, 1):
                    e.polygon([(x, y+4*u), (x+side*spread, y-12*u),
                               (x+side*spread*1.2, y+8*u), (x+side*6*u, y+14*u)],
                              colors[i%3], glow*(1-release))
                e.line([(x, y-6*u), (x, y+13*u)], light, 2, glow*(1-release))
            if landed:
                arc(e, tx, ty, r*(.5+release), r*(.5+release),
                    -.9, math.pi*1.4, light, 3, fade*(1-release))
        return True

    if sequence == 'phantom_court':
        if back:
            # An uneven row of crowned spectral lanterns rises behind the king.
            for i in range(5):
                x = tx+(i-2)*r*.48
                y = ty-(90-abs(i-2)*12)*u*charge
                width = 17*u
                e.polygon([(x-width, y+48*u), (x-width, y), (x, y-12*u),
                           (x+width, y), (x+width, y+48*u), (x+5*u, y+40*u),
                           (x, y+49*u), (x-5*u, y+40*u)], mid, glow*.8)
                e.line([(x-width, y+48*u), (x-width, y), (x, y-12*u),
                        (x+width, y), (x+width, y+48*u)], light, 3, glow*.85)
                e.rect(x-6*u, y+5*u, 3*u, 5*u, light, glow)
                e.rect(x+3*u, y+5*u, 3*u, 5*u, light, glow)
                e.line([(x-width, y-9*u), (x-width, y-20*u), (x, y-13*u),
                        (x+width, y-20*u), (x+width, y-9*u)], light, 2, glow*.8)
        elif not landed:
            # A monumental crown, framed above the sentenced opponent.
            crown_y = max(24*u, ty-r*.66*charge)
            e.line([(tx-r*.65, crown_y+23*u), (tx-r*.65, crown_y-8*u),
                    (tx-r*.3, crown_y+7*u), (tx, crown_y-22*u),
                    (tx+r*.3, crown_y+7*u), (tx+r*.65, crown_y-8*u),
                    (tx+r*.65, crown_y+23*u), (tx-r*.65, crown_y+23*u)],
                   light, 4, glow)
        elif landed:
            for i in range(5):
                x = tx+(i-2)*r*.4
                e.line([(x, ty-r*.7), (mix(x, tx, release), ty+r*release)], light, 3, fade*(1-release))
            e.polygon([(tx-r*.7, ty-8*u), (tx, ty-22*u), (tx+r*.7, ty-8*u),
                       (tx, ty+12*u)], mid, fade*(1-release)*.7)
        return True

    return True
