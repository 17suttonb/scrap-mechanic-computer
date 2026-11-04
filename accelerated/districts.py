"""Gate placement as a map of the machine: named districts with internal structure, see docs/layout.md.

Every gate gets a (district, block, bit) from its label: the HDL signal after '@section:', or for
storage the signal assigned on the source line in 'line N'. Gates with no name of their own (relays,
buffers) follow the majority of their wiring neighbours. Nothing here knows gate counts: a district's
rectangle comes from how many gates it holds, and buildings stack upward from z=1 as far as they need.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import colorsys
import json
import math
import re
from accelerated.signs import choose_sign

TARGET_HEIGHT = dict(cpu=12, terminal=11, cartridge=4)   # average storeys a column is widened to reach
CORE_Y = (37, 71)
CORE_LEFT = -15
CORE_KEY_Y = 34
CORE_TIMER_Y = 73
CONSOLE_LEFT = -16          # the left edge of the screen's pixels: character cells stand behind their pixels
CONSOLE_Y = (5, 27)
CONSOLE_KEY_Y = 2
CONSOLE_ANNEX_X = 58
CONSOLE_ANNEX_Y = (6, 27)
CART_LEFT = -15
CART_KEY_Y = 82
CART_TOP = 85
CART_MIN_DEPTH = 14
MAP_FILE = 'cartridge-map.json'
POS_ORDER = ('z', 'y', 'x')    # towers rise one z at a time, so a position differs from the one before at its start

@dataclass
class Spec:
    title: str
    colour: str
    per_row: int = None     # tiles across; None picks the count closest to `aspect`
    aspect: float = 3.0     # wanted tile width / depth
    gap: tuple = (1, 1)
    street: int = 0         # an extra gap after every so many tiles across
    tone: str = 'row'       # which tiles get the lighter shade: 'row', 'checker' or 'street'
    width: int = 0          # smallest body width
    depth: int = 0          # smallest body depth
    tags: str = None        # sign at the left of each row of tiles, with this text before the block
    bits: int = 0           # the block is a bit number: gates with no bit of their own share them out by rank
    title_sign: bool = True
    solid: bool = False     # one building, its signals side by side, instead of a tile per block
    strips: bool = False    # every block a one-thick strip as long as it needs: a bar chart

CORE = dict(
    pc=Spec('PC FETCH', 'E0559B', solid=True),
    rom=Spec('ROM LADDER', 'B7A6F2', per_row=1, gap=(1, 0), aspect=8),
    fields=Spec('DECODE', '8F5BE0', solid=True),
    fusion=Spec('FUSION LANES', '19C3D6', strips=True, width=24),
    branch=Spec('BRANCH', 'E5533D', solid=True),
    regs=Spec('REGISTERS', '3F7FEA', per_row=1, gap=(0, 1), tags='X', width=24, depth=31),
    ports=Spec('READ PORTS', '86B3F7', bits=32, per_row=16, street=4, tone='street', gap=(0, 1)),
    result=Spec('RESULT BUS', 'F2A7C3', bits=32, per_row=16, street=4, tone='street', gap=(0, 1)),
    alu=Spec('ALU', '35C759', bits=32, per_row=32, gap=(0, 0), street=8, tone='street', width=35, depth=7),
    shifter=Spec('SHIFTER', 'A6E22E', bits=32, per_row=32, gap=(0, 0), street=8, tone='street', width=35),
    ram=Spec('RAM 64 BYTES', 'F58A1F', per_row=4, gap=(1, 1), tone='checker', width=20, depth=31),
    memport=Spec('MEMORY PORT', 'FFC58A', solid=True),
    text=Spec('PROGRAM TEXT', 'F2D21F', per_row=1, gap=(0, 1), width=18, depth=31),
    textmux=Spec('TEXT READ', 'C2A80E', per_row=16, gap=(0, 1), tone='checker'),
    textctl=Spec('TEXT CONTROL', 'FFF07A', solid=True),
    bus=Spec('BUS CONTROL', '9FB0C4', solid=True),
)
CORE_COLUMNS = [['pc', 'rom', 'fields'], ['fusion', 'branch'], ['regs'], ['ports', 'result'], ['alu', 'shifter', 'memport'], ['ram'], ['text'], ['textmux', 'textctl', 'bus']]

CONSOLE = dict(
    cells=Spec('CHARACTER CELLS', 'D65CFF', per_row=12, gap=(1, 1), tone='checker', title_sign=False),
    queue=Spec('KEY QUEUE', '35C759', solid=True),
    keyboard=Spec('KEYBOARD', 'A6E22E', solid=True),
    linebuf=Spec('LINE BUFFER', '3F7FEA', solid=True),
    cursor=Spec('CURSOR', '19C3D6', solid=True),
    number=Spec('NUMBERS', 'F58A1F', solid=True),
    service=Spec('SERVICE', 'F2D21F', solid=True),
)
CONSOLE_COLUMNS = [['cells'], ['queue', 'keyboard'], ['linebuf', 'cursor'], ['number', 'service']]

def first_int(match):
    return next(int(g) for g in match.groups() if g is not None)

def bit_of(signal):
    m = re.search(r'\((\d+)\)$', signal)
    return int(m[1]) if m else None

def family(signal):
    """A block name for an unindexed group: the signal without its bit."""
    return re.sub(r'\(\d+\)$', '', signal)

CORE_RULES = [
    (r'regs\((\d+)\)|reg_sparse_(\d+)|reg_equiv_(\d+)|fusion_(?:stage|result|write)_(\d+)|write_stage\((\d+)\)|select_(?:write|a|b)\((\d+)\)', 'regs', first_int),
    (r'(?:a|b)\((\d+)\)', 'ports', first_int),
    (r'fusion_ram_(\d+)|ram\((\d+)\)|write_bytes\((\d+)\)', 'ram', first_int),
    (r'ram_read_lanes\((\d+)\)', 'memport', lambda m: 'lane %02d' % int(m[1])),
    (r'ram_|byte_|half_value|store_', 'memport', None),
    (r'load_word\((\d+)\)', 'result', first_int),
    (r'load_|result|d_link\((\d+)\)', 'result', 'bit'),
    (r'program_chars\((\d+)\)|program_select\((\d+)\)', 'text', lambda m: first_int(m) // 18),
    (r'text_slots_mux\((\d+)\)', 'textmux', first_int),
    (r'text_columns_delta|program_|text_', 'textctl', None),
    (r'fusion_(?:value|store|way|taken|compare)_(\d+)', 'fusion', first_int),
    (r'fusion_equal_(\d+)', 'fusion', lambda m: f'equal {int(m[1]):03d}'),
    (r'fusion_', 'fusion', 'control'),
    (r'fixed_shift|shift_', 'shifter', 'bit'),
    (r'alu\.|alu_|operand_b', 'alu', 'bit'),
    (r'pair_(?:check|less|site)_(\d+)', 'branch', first_int),
    (r'pair_|compare\.|taken|eq$', 'branch', 'compare'),
    (r'select_pc\((\d+)\)', 'rom', lambda m: 'rung %04d' % (int(m[1]) // 16)),
    (r'rom_', 'rom', 'rom'),
    (r'd_|select_op', 'fields', None),
    (r'(?:normal_)?next_pc|pc_|pc\b|ret_|retired|instruction', 'pc', None),
    (r'ACK|READY|XOR CPU reset|TEXT COM|PULSE reset', 'bus', 'timing'),
]
CONSOLE_RULES = [
    (r'font_(?:out|union)_(\d+)|glyph_select\((\d+)\)|chars_(?:q|data|value)\((\d+)\)|pixel_write\((\d+)\)|mirror_(?:select|write)\((\d+)\)|scroll_select\((\d+)\)|draw_select\(\d+\)\((\d+)\)', 'cells', first_int),
    (r'TAP Q (?:slot|head|index |load key )(\d+)', 'queue', first_int),
    (r'TAP', 'queue', 'control'),
    (r'key|presence|raw_code|input_|collision|code|previous_|sample_|valid_|stable_|any_', 'keyboard', None),
    (r'bcd|number_', 'number', None),
    (r'col_|row_|target_row|cursor|draw_|back_|print_row|command_row', 'cursor', None),
    (r'char|line|length|clear_|before_|after_|emit_|tx|ready|rotate|page|mirror_|score_|result_|cpu_print|prompt_|newline_|error_|fault_', 'linebuf', None),
    (r'gpio|read_mux|phase_|index_|PULSE reset|XOR CPU reset|bus_|Async', 'service', None),
]
TAG_DEFAULT = dict(alu='alu', branch='branch', regs='result', ram='memport', text='textmux', decode='fields', fusion='fusion', pc='pc', bus='bus', font='cells', number='number', pixel='cursor', keyboard='keyboard', charbuf='linebuf', service='service')
UNNAMED = r'Feedback relay|fan-out buffer|Async phase buffer|Module |terminal_pixels|timer|spacer'

def source_lines(text):
    """{(prefix, line): signal assigned there}, preferring the first latch on the line. Line numbers
    restart in each design joined into source.vhd, so the prefix on the label picks the design."""
    lines = text.split('\n')
    start = dict(Service=0, Terminal=0)
    for n, line in enumerate(lines):
        if line.startswith('entity rv_memory_service'):
            start['Service'] = n
        elif line.startswith('entity rv_terminal'):
            start['Terminal'] = n
    result = {}
    names = r'([A-Za-z_]\w*(?:\([^()]*\))*)\s*<=\s*'
    for n, line in enumerate(lines):
        m = re.search(names + r'latch\(', line) or re.search(names, line)
        if m:
            for prefix, offset in (('', 0), ('Service', start['Service']), ('Terminal', start['Terminal'])):
                if n >= offset and (prefix or not start['Service'] or n < start['Service']):
                    result[prefix, n - offset + 1] = m[1]
    return result

def name_of(label, lines):
    """(tag, signal, bit) of a gate label."""
    m = re.search(r' @(\w+)(?::(\S*))?$', label)
    if m:
        signal = m[2] or ''
        return m[1], signal, bit_of(signal)
    text = re.sub(r'^(?:timing )?(?:(?:Service|Terminal|Cartridge) )?', '', label)
    prefix = next((p for p in ('Service', 'Terminal') if p + ' ' in label[:15]), '')
    m = re.search(r'(?:latch /?Q|TEXT|XOR CPU|PULSE) line (\d+)', text)
    if m:
        bit = re.search(r' bit (\d+)', text)
        signal = lines.get((prefix, int(m[1])), '')
        return None, signal, int(bit[1]) if bit else bit_of(signal)
    return None, text, bit_of(text)

def classify(rules, tag, signal):
    for pattern, district, how in rules:
        m = re.match(pattern, signal)
        if not m:
            continue
        if callable(how):
            return district, how(m)
        if how == 'bit':
            return district, 'bit'
        return district, how or family(signal)
    return (TAG_DEFAULT[tag], family(signal)) if tag in TAG_DEFAULT else (None, None)

def spread_votes(found, ids, edges):
    """Unnamed gates take the (district, block) most of their wired neighbours have."""
    neighbours = defaultdict(set)
    members = set(ids)
    for a, b in edges:
        if a in members and b in members:
            neighbours[a].add(b)
            neighbours[b].add(a)
    for _ in range(12):
        changed = {}
        for i in ids:
            if found[i][0] is None:
                votes = Counter(found[j][:2] for j in neighbours[i] if found[j][0] is not None)
                if votes:
                    changed[i] = (*max(sorted(votes, key=str), key=votes.get), found[i][2])
        if not changed:
            break
        found.update(changed)

def natural(block):
    return (1, 0, [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', block)]) if isinstance(block, str) else (0, block, [])

def assign_blocks(found, ids, specs, default):
    """{district: {block: [(bit, id, signal)]}}. In a bit-sliced district the block is the bit, and gates
    that know no bit of their own are shared out over the bits by rank within their signal."""
    districts = defaultdict(lambda: defaultdict(list))
    for i in sorted(ids):
        district, block, bit, signal = found[i]
        district = district or default
        districts[district][block].append((bit, i, signal))
    for key, blocks in districts.items():
        spec = specs[key]
        if spec.bits:
            # 'bit' blocks: gates sit in their bit's tile; other signals are shared out in id order
            merged = defaultdict(list)
            loose = defaultdict(list)
            for block, gates in blocks.items():
                for bit, i, signal in gates:
                    if block == 'bit' and bit is not None:
                        merged[bit % spec.bits].append((bit, i, signal))
                    elif isinstance(block, int):
                        merged[block % spec.bits].append((block, i, signal))
                    else:
                        loose[signal or str(block)].append(i)
            for signal, gates in loose.items():
                for n, i in enumerate(sorted(gates)):
                    b = n * spec.bits // len(gates)
                    merged[b].append((b, i, signal))
            districts[key] = merged
    return districts

def shade(colour, amount):
    """Lighter (amount > 0) or darker, keeping the hue."""
    r, g, b = (int(colour[k:k + 2], 16) / 255 for k in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.12, min(0.92, l + amount))
    return '%02X%02X%02X' % tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))

@dataclass
class Rect:
    x: int
    y: int
    w: int
    d: int

@dataclass
class Plan:
    position: dict = field(default_factory=dict)
    colour: dict = field(default_factory=dict)
    signs: list = field(default_factory=list)      # (text, x, y, colour)
    report: dict = field(default_factory=dict)

def choose_grid(spec, count, rect, tallest=0, limit=1e9):
    """(tiles across, rows, tile width, tile depth, gaps) that fit; the gaps give way first if they must."""
    def fit(across, gx, gy):
        rows = math.ceil(count / across)
        streets = (across - 1) // spec.street if spec.street else 0
        w = (rect.w - (across - 1) * gx - streets) // across
        d = (rect.d - (rows - 1) * gy) // rows
        return (across, rows, w, d, gx, gy) if w >= 1 and d >= 1 else None
    for gx, gy in (spec.gap, (spec.gap[0], 0), (0, 0)):
        if spec.per_row:
            found = fit(min(spec.per_row, max(count, 1)), gx, gy)
            if found:
                return found
    for gx, gy in (spec.gap, (spec.gap[0], 0), (0, 0)):
        options = [f for across in range(1, count + 1) if (f := fit(across, gx, gy))]
        if options:
            return min(options, key=lambda f: (max(0, tallest / (f[2] * f[3]) - limit), abs(math.log(f[2] / f[3] / spec.aspect))))
    raise ValueError(f'{spec.title}: {count} tiles do not fit in {rect}')

def lay_strips(spec, blocks, ordered, rect, plan, limit):
    """Rows of strips, as many side by side as it takes. A block too big for one strip takes several."""
    rows = (rect.d + 1) // 2
    height = min(limit - 1, max(len(g) for g in blocks.values()))
    for across in range(1, rect.w + 1):
        length = (rect.w - (across - 1)) // across
        if length < 1:
            return limit + 1
        pieces = sum(math.ceil(len(blocks[b]) / (length * height)) for b in ordered)
        if pieces <= rows * across:
            break
    else:
        return limit + 1
    n = 0
    for shaded, block in enumerate(ordered):
        base = shade(spec.colour, 0.08 if shaded % 2 == 0 else -0.08)
        gates = sorted(blocks[block], key=lambda g: g[1])
        for piece in range(0, len(gates), length * height):
            col, row = divmod(n, rows)
            n += 1
            for k, (bit, i, signal) in enumerate(gates[piece:piece + length * height]):
                column, z = divmod(k, height)
                plan.position[i] = (rect.x + col * (length + 1) + column, rect.y + row * 2, 1 + z)
                plan.colour[i] = base
    return 1 + height

def lay_out(spec, blocks, rect, plan, limit=10 ** 6):
    """Fill the rectangle with one tile per block, each growing upward. Returns the tallest z."""
    ordered = sorted(blocks, key=natural)
    if spec.solid:
        # in id order: the gates are consecutive in the file, so this is the cheapest shape in the packet
        gates = sorted((g for b in ordered for g in blocks[b]), key=lambda g: g[1])
        height = max(1, math.ceil(len(gates) / (rect.w * rect.d)))
        for k, (bit, i, signal) in enumerate(gates):
            column, z = divmod(k, height * rect.d)
            depth, z = divmod(z, height)
            plan.position[i] = (rect.x + column, rect.y + depth, 1 + z)
            plan.colour[i] = spec.colour
        return 1 + height
    if spec.strips:
        return lay_strips(spec, blocks, ordered, rect, plan, limit)
    margin = 4 if spec.tags else 0
    body = Rect(rect.x + margin, rect.y, rect.w - margin, rect.d)
    room = ((body.w + spec.gap[0]) // (1 + spec.gap[0])) * ((body.d + spec.gap[1]) // (1 + spec.gap[1]))
    if len(ordered) > room:
        # more small signals than the rectangle has tiles: neighbours in name order share one
        chunks = [ordered[n * len(ordered) // room:(n + 1) * len(ordered) // room] for n in range(room)]
        blocks = {chunk[0]: [g for b in chunk for g in blocks[b]] for chunk in chunks}
        ordered = [chunk[0] for chunk in chunks]
    across, rows, w, d, gx, gy = choose_grid(spec, len(ordered), body, max(len(g) for g in blocks.values()), limit)
    spare_y = body.d - (rows * d + (rows - 1) * gy)
    pitch_y = d + gy + (spare_y // (rows - 1) if rows > 1 else 0)
    top = 0
    for n, block in enumerate(ordered):
        row, col = divmod(n, across)
        streets = col // spec.street if spec.street else 0
        x0, y0 = body.x + col * (w + gx) + streets, body.y + row * pitch_y
        lighter = {'row': row % 2 == 0, 'checker': (row + col) % 2 == 0, 'street': (col // spec.street if spec.street else col) % 2 == 0}[spec.tone]
        base = shade(spec.colour, 0.08 if lighter else -0.08)
        gates = sorted(blocks[block], key=lambda g: g[1])
        height = max(1, math.ceil(len(gates) / (w * d)))
        for k, (bit, i, signal) in enumerate(gates):
            column, z = divmod(k, height * d)
            depth, z = divmod(z, height)
            plan.position[i] = (x0 + column, y0 + depth, 1 + z)
            plan.colour[i] = base
        top = max(top, 1 + height)
        if spec.tags:
            plan.signs.append((f'{spec.tags}{block}', rect.x, y0, shade(spec.colour, 0.08)))
    return top

def arrange(columns, specs, districts, x, y0, y1, target, gap=2, y_for=None, plan=None, width_for=None):
    """Columns of districts from x rightward. A column is as wide as its gates need at the target height."""
    for keys in columns:
        keys = [k for k in keys if k in districts]
        if not keys:
            continue
        counts = {k: sum(len(g) for g in districts[k].values()) for k in keys}
        top, bottom = y_for(x) if y_for else (y0, y1)
        signs = sum(2 for k in keys if specs[k].title_sign)
        usable = bottom - top + 1 - signs - (len(keys) - 1)
        mins = {k: max(specs[k].depth, 1) for k in keys}
        total = sum(counts.values())
        width = max([specs[k].width for k in keys] + [sign_width(specs[k].title) for k in keys if specs[k].title_sign] + [math.ceil(total / target / usable)])
        if width_for:
            width = width_for(keys, width)
        share = {k: max(mins[k], usable * counts[k] // total) for k in keys}
        while sum(share.values()) > usable:
            largest = max((k for k in keys if share[k] > mins[k]), key=lambda k: share[k], default=None)
            if largest is None:
                raise ValueError(f'{keys} need more than {usable} rows')
            share[largest] -= 1
        share[keys[-1]] += usable - sum(share.values())
        # widen until nothing towers over the rest of the module
        widest = 3 * width
        while not width_for and width < widest and max(lay_out(specs[k], districts[k], Rect(x, top, width, share[k]), Plan(), 2 * target) for k in keys) > 2 * target:
            width += max(1, width // 8)
        y = top
        for k in keys:
            if specs[k].title_sign:
                plan.signs.append((specs[k].title, x, y, shade(specs[k].colour, 0.1)))
                y += 2
            rect = Rect(x, y, width, share[k])
            high = lay_out(specs[k], districts[k], rect, plan, 2 * target)
            plan.report[k] = dict(title=specs[k].title, colour=specs[k].colour, logic_blocks=counts[k], blocks=len(districts[k]), x=x, y=y, width=width, depth=share[k], top_z=high)
            y += share[k] + 1
        x += width + gap
    return x

def sign_width(text):
    return choose_sign(text).size[0]

def key_row(specs, districts, x, y, plan):
    """Colour key: one sign in each district's colour."""
    for key, spec in specs.items():
        if key in districts:
            text = spec.title
            plan.signs.append((text, x, y, shade(spec.colour, 0.1)))
            x += sign_width(text) + 1

def domain_found(ids, labels, lines, edges, rules):
    found = {}
    for i in ids:
        tag, signal, bit = name_of(labels.get(i, ''), lines)
        district, block = (None, None) if re.match(UNNAMED, signal) else classify(rules, tag, signal)
        found[i] = (district, block, bit, signal)
    named = {i: f[:3] for i, f in found.items()}
    spread_votes(named, list(ids), edges)
    return {i: (named[i][0], named[i][1], found[i][2], found[i][3]) for i in ids}

def core(ids, labels, lines, edges, plan):
    found = domain_found(ids, labels, lines, edges, CORE_RULES)
    districts = assign_blocks(found, ids, CORE, 'bus')
    arrange(CORE_COLUMNS, CORE, districts, CORE_LEFT, *CORE_Y, TARGET_HEIGHT['cpu'], plan=plan)
    key_row(CORE, districts, CORE_LEFT, CORE_KEY_Y, plan)

def console(ids, labels, lines, edges, plan):
    found = domain_found(ids, labels, lines, edges, CONSOLE_RULES)
    districts = assign_blocks(found, ids, CONSOLE, 'service')
    def y_for(x):
        return CONSOLE_Y if x == CONSOLE_LEFT else CONSOLE_ANNEX_Y
    def width_for(keys, width):
        return 71 if keys == ['cells'] else width      # one 5-wide tile and a gap per screen column
    x = arrange(CONSOLE_COLUMNS[:1], CONSOLE, districts, CONSOLE_LEFT, 0, 0, TARGET_HEIGHT['terminal'], y_for=y_for, plan=plan, width_for=width_for, gap=0)
    arrange(CONSOLE_COLUMNS[1:], CONSOLE, districts, max(x + 1, CONSOLE_ANNEX_X), 0, 0, TARGET_HEIGHT['terminal'], y_for=y_for, plan=plan)
    key_row(CONSOLE, districts, CONSOLE_LEFT, CONSOLE_KEY_Y, plan)

CART_PALETTE = ['35C759', 'E0559B', '19C3D6', 'FF7FA0', '8F5BE0', 'F2D21F', '3F7FEA', 'E5533D', 'A6E22E', 'B7A6F2']

def cartridge(ids, labels, edges, plan, groups):
    """Tagged gates go to their group's district, one column per program. The rest share ADDRESS, ROW
    DECODERS and OUTPUT BUS, recognised by signal name."""
    specs = dict(address=Spec('ADDRESS', '9FB0C4', solid=True), rows=Spec('ROW DECODERS', '19C3D6', strips=True), output=Spec('OUTPUT BUS', 'F58A1F', strips=True))
    found = {}
    for i in ids:
        tag, signal, bit = name_of(labels.get(i, ''), {})
        row = re.match(r'select_row\((\d+)\)', signal)
        group = tag[5:] if tag and tag.startswith('cart_') else None
        if group is not None:
            if group not in groups:
                groups[group] = dict(key=group, title=group.upper().replace('_', ' '))
            found[i] = ('cart_' + group, 'row %05d' % (int(row[1]) // 8) if row else family(signal) or 'logic', bit, signal)
        elif row:
            found[i] = ('rows', 'row %05d' % (int(row[1]) // 8), bit, signal)
        elif re.match(r'address|cart_address', signal):
            found[i] = ('address', family(signal), bit, signal)
        elif re.match(r'cart_', signal):
            found[i] = ('output', re.sub(r'^cart_', '', family(signal)), bit, signal)
        else:
            found[i] = (None, None, bit, signal)
    named = {i: f[:3] for i, f in found.items()}
    spread_votes(named, list(ids), edges)
    found = {i: (named[i][0], named[i][1], found[i][2], found[i][3]) for i in ids}
    columns = {}
    for n, (key, group) in enumerate(groups.items()):
        specs['cart_' + key] = Spec(group.get('title', key.upper())[:18], group.get('colour', CART_PALETTE[n % len(CART_PALETTE)]), strips=True)
        columns.setdefault(group.get('program') or key, []).append('cart_' + key)
    districts = assign_blocks(found, ids, specs, 'output')
    order = [['address', 'rows']] + list(columns.values()) + [['output']]
    order = [c for c in order if any(k in districts for k in c)]
    # about twice as wide as deep, whatever the size; strips wrap into more columns rather than run on in y
    depth = max(CART_MIN_DEPTH, 2 * max(len(c) for c in order) + 4, math.ceil(math.sqrt(len(ids) / TARGET_HEIGHT['cartridge'] / 2)))
    arrange(order, specs, districts, CART_LEFT, CART_TOP, CART_TOP + depth, TARGET_HEIGHT['cartridge'], plan=plan)
    key_row(specs, districts, CART_LEFT, CART_KEY_Y, plan)

def read_groups(folder):
    path = folder / MAP_FILE
    groups = json.loads(path.read_text(encoding='utf-8')).get('groups', []) if path.exists() else []
    return {g['key']: g for g in groups}

def plan_domain(domain, ids, labels, source, edges, folder):
    plan = Plan()
    lines = source_lines(source)
    if domain == 'cpu':
        core(ids, labels, lines, edges, plan)
    elif domain == 'terminal':
        console(ids, labels, lines, edges, plan)
    else:
        cartridge(ids, labels, edges, plan, read_groups(folder))
    assert not [c for c in plan.colour.values() if c == '222222']
    assert len(set(plan.position.values())) == len(plan.position) == len(ids)
    return plan
