"""Functional sections of the design, and distinct colours for them.

The HDL elaborator tags every gate it creates with the section of the statement being elaborated
(label 'AND @alu'); the tag survives the later optimisation passes because they only look at label
prefixes. Gates created outside any statement are tagged afterwards by their wiring neighbours.
"""
from collections import Counter, defaultdict
import colorsys
import math
import re

TITLES = dict(
    alu='ALU', branch='BRANCH', regs='REGISTERS', ram='RAM', text='PROGRAM TEXT', decode='DECODE',
    fusion='FUSION LANES', pc='PC FETCH', bus='BUS CONTROL',
    font='FONT ROM', number='NUMBERS', pixel='PIXELS', keyboard='KEYBOARD', charbuf='CHAR BUFFER', service='SERVICE')

# Preferred hue (degrees) per section; the palette is chosen for distinctness first, then matched to these.
HUE_HINT = dict(
    alu=130, branch=5, regs=225, ram=30, text=55, decode=285, fusion=185, pc=330, bus=90,
    font=285, number=30, pixel=185, keyboard=130, charbuf=225, service=55)

CPU_RULES = [
    ('alu', r'alu_|operand_b|fixed_shift|shift'),
    ('branch', r'pair_|taken|fusion_compare|branch|compare'),
    ('text', r'program_|text_'),
    ('ram', r'ram|load_word|byte_|half_|store_|write_bytes|word_'),
    ('fusion', r'fusion_'),
    ('regs', r'regs|reg_|select_[abw]|a$|b$|write_stage|result|read_'),
    ('decode', r'd_|rom_|select_pc'),
    ('pc', r'next_pc|normal_next_pc|pc_|ret_|retired|instruction'),
]
TERMINAL_RULES = [
    ('font', r'font_|glyph_'),
    ('number', r'bcd|number_'),
    ('pixel', r'draw_|pixel|collision|terminal_pixels|scroll|target_row|row_|col_|cursor'),
    ('keyboard', r'key|presence|raw_code|code|previous_code|input_'),
]
TOP_DEFAULT = dict(rv32e_cpu='bus', rv_terminal='charbuf', rv_memory_service='service')
TOP_RULES = dict(rv32e_cpu=CPU_RULES, rv_terminal=TERMINAL_RULES)
DOMAIN_SECTIONS = dict(
    cpu={'alu', 'branch', 'regs', 'ram', 'text', 'decode', 'fusion', 'pc', 'bus'},
    terminal={'font', 'number', 'pixel', 'keyboard', 'charbuf', 'service'})
INSTANCE_SECTION = dict(rv_alu32='alu', rv_branch_compare='branch')

def target_name(expression):
    while expression.op == 'call':
        expression = expression.args[0]
    return expression.args[0] if expression.op == 'name' else ''

def classify(stack, target):
    """Section of an assignment to `target` inside the entity instantiation stack, or None."""
    for entity in reversed(stack):
        if entity in INSTANCE_SECTION:
            return INSTANCE_SECTION[entity]
    top = stack[0] if stack else ''
    for section, pattern in TOP_RULES.get(top, ()):
        if re.match(pattern, target):
            return section
    return TOP_DEFAULT.get(top)

def tag_of(label):
    m = re.search(r' @(\w+)$', label)
    return m[1] if m else None

def assign_sections(labels, edges, members, rounds=12):
    """Section for each id in `members`: its own tag, else the majority tag among wired neighbours."""
    members = set(members)
    neighbours = defaultdict(set)
    for a, b in edges:
        if a in members and b in members:
            neighbours[a].add(b)
            neighbours[b].add(a)
    section = {i: tag_of(labels.get(i, '')) for i in members}
    for _ in range(rounds):
        changed = {}
        for i in members:
            if section[i] is None:
                votes = Counter(section[j] for j in neighbours[i] if section[j] is not None)
                if votes:
                    changed[i] = max(sorted(votes), key=votes.get)
        if not changed:
            break
        section.update(changed)
    return section

def _lab(rgb):
    def linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

def delta_e(p, q):
    """CIEDE2000 colour difference between two Lab colours."""
    l1, a1, b1 = p
    l2, a2, b2 = q
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    g = 0.5 * (1 - math.sqrt(((c1 + c2) / 2) ** 7 / (((c1 + c2) / 2) ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = l2 - l1, c2p - c1p
    dh = h2p - h1p
    if c1p * c2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dhp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh) / 2)
    lm, cm = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hm = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hm = (h1p + h2p) / 2
    else:
        hm = (h1p + h2p + (360 if h1p + h2p < 360 else -360)) / 2
    t = 1 - 0.17 * math.cos(math.radians(hm - 30)) + 0.24 * math.cos(math.radians(2 * hm)) + 0.32 * math.cos(math.radians(3 * hm + 6)) - 0.2 * math.cos(math.radians(4 * hm - 63))
    sl = 1 + 0.015 * (lm - 50) ** 2 / math.sqrt(20 + (lm - 50) ** 2)
    sc, sh = 1 + 0.045 * cm, 1 + 0.015 * cm * t
    rt = -2 * math.sqrt(cm ** 7 / (cm ** 7 + 25 ** 7)) * math.sin(math.radians(60 * math.exp(-(((hm - 275) / 25) ** 2))))
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dhp / sh) ** 2 + rt * (dc / sc) * (dhp / sh))

def _candidates():
    """Saturated, mid-brightness sRGB colours: a hue/saturation/value grid with grey-ish and extreme ones dropped."""
    result = []
    for step in range(72):
        hue = step * 5
        for sat in (0.55, 0.8, 1.0):
            for val in (0.6, 0.8, 1.0):
                rgb = colorsys.hsv_to_rgb(hue / 360, sat, val)
                lab = _lab(rgb)
                if 35 <= lab[0] <= 85 and math.hypot(lab[1], lab[2]) >= 35:
                    result.append((hue, rgb, lab))
    return result

def distinct_colors(count, avoid=()):
    """`count` hex colours spread as far apart as possible (greedy maximin on CIEDE2000).

    Each pick maximises its distance to the nearest colour already chosen or listed in `avoid`
    (hex strings, e.g. the base-plate and sign colours), so no two sections look alike and none
    look like the fixed colours already on the board.
    """
    candidates = _candidates()
    anchors = [_lab(tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))) for h in avoid]
    nearest = [min((delta_e(c[2], a) for a in anchors), default=1e9) for c in candidates]
    chosen = []
    for _ in range(count):
        best = max(range(len(candidates)), key=lambda k: (nearest[k], -k))
        chosen.append(candidates[best])
        nearest[best] = -1
        for k, c in enumerate(candidates):
            nearest[k] = min(nearest[k], delta_e(c[2], candidates[best][2]))
    return chosen

def section_colors(sections, avoid=()):
    """{section: 'RRGGBB'} - maximally distinct colours, matched to each section's preferred hue."""
    sections = list(sections)
    palette = distinct_colors(len(sections), avoid)

    def gap(section, colour):
        d = abs(HUE_HINT.get(section, 0) - colour[0]) % 360
        return min(d, 360 - d)
    pairs = sorted(((gap(s, c), n, k) for n, s in enumerate(sections) for k, c in enumerate(palette)))
    used_s, used_c, result = set(), set(), {}
    for _, n, k in pairs:
        if n in used_s or k in used_c:
            continue
        used_s.add(n)
        used_c.add(k)
        result[sections[n]] = '%02X%02X%02X' % tuple(round(v * 255) for v in palette[k][1])
    return result
