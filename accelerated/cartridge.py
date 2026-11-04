"""External program cartridge: a separate ROM module the CPU can execute from, and read data from.

The CPU's firmware ROM is baked into its gates and knows nothing about programs: see firmware/menu.s. A
cartridge is its own blueprint, joined to the CORE by a fixed seam, so any cartridge works with the same CORE
and CONSOLE. It answers two addresses, independently:

    CPU -> cartridge   cart_address (11)            program counter bits 12..2, bit 12 set in the window
                       cart_data_address (11)       low bits of the computed address, bit 10 set in the data port
    cartridge -> CPU   cart_sa/sb/sw (14 each)      one-hot read A, read B and write register x1..x15, not x8
                       cart_imm (12)                sign-extended by the CPU
                       cart_target (10)             branch / jal target, bits 11..2; the CPU keeps bit 12
                       cart_f3 (3), cart_aluop (3)
                       cart_legal, load, store, branch, jal, jalr, immediate, compare, static (9)
                       cart_key, cart_nokey (2)     take the target if a key is / is not waiting; both: always
                       cart_lit                     the store's byte is the low bits of cart_target
                       cart_data (8)                the byte at cart_data_address

Every pin is always driven, by a gate of its own, at a fixed place on the seam. Everything else the baked
decoder knows is zero for those rows, so a cartridge cannot use LUI/AUIPC, shifts, ECALL or EBREAK, nor touch x8,
which does not exist. Nothing about a program reaches the CPU's analyses, so its registers are generic.
With no cartridge attached the pins read zero: the menu says so, and a fetch from the window traps.
docs/cartridge.md has the file format, the memory map and the rules.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import struct
from unittest.mock import patch
from accelerated import compact_core
from accelerated.hdl import Bits, Build, Builder

BASE = 0x1000                   # byte address of the first cartridge row; PC bit 12 is the window
WORDS = 1024
ADDRESS_BITS = 11
DATA_BASE = 0x400               # register-addressed loads from here read the data port
DATA_BYTES = 1024
DATA_ADDRESS_BITS = 11
TEXT_BASE = 0x200               # BASIC text store, 16 lines of 32 byte addresses
STREAM_BASE = 0x100             # the input line, read through a register
MODE_ADDRESS = -248             # a store here sets the mode
SCREEN_BASE = -512              # a store to SCREEN_BASE + row * 16 + column shows that character, in any mode
MODE_CONSOLE, MODE_TAP, MODE_BOARD = 0, 1, 3     # bit 0: keys are single taps, bit 1: the board map
MODES = dict(console=MODE_CONSOLE, tap=MODE_TAP, board=MODE_BOARD)
ROW_STRIDE = 16                 # of the directory, the screen and the board alike
DIRECTORY_MAGIC = 0xC7
# The board a register-addressed access in board mode sees: row = address / STRIDE, column = address % STRIDE.
BOARD_COLUMNS = 12
BOARD_ROWS = 7
BOARD_STRIDE = 16
BOARD_ADDRESS_BITS = 7          # the rest of a position register is ignored, so positions wrap at 128
RAM_ROWS = 4                    # rows held in the CPU's 64-byte RAM; those below get bytes of their own
WALL = 3                        # what a register-addressed load returns from any address that is not a cell
EXTRA_CELLS = (BOARD_ROWS - RAM_ROWS) * BOARD_COLUMNS
# the bottom row of the screen in board mode: (row, column, text) of the score, drawn on entry, and of the result
# for each value stored to end the game, which overwrites the word SCORE
SCORE_LINE = (7, 0, '00 SCORE')
SCORE_ADDRESS = 13              # tens, then ones, as binary digits: statically addressed RAM bytes, not cells
RESULT_ADDRESS = 29             # a statically addressed RAM byte, not a cell
RESULTS = {5: (7, 3, ' YOU LOST'), 6: (7, 3, ' YOU WON')}
KEY_ECHO = (7, 11)              # the key at the head of the queue, shown as soon as the console has it
# Input: a key waits at -240 (bit 7) and -236 reads and takes it, which is all a cartridge needs for keys.
# The steering row is an accelerator for moving a position around the board, one row per move:
#     add zero,position,zero; lb step,STEER_ADDRESS(zero); j target
# It takes any waiting key, picks the new direction (the key's, unless it is not a steering key or reverses the
# direction `step` holds), and writes `step` with the amount to add to `position` to move one cell that way,
# including the hop across the board if that cell would be off an edge. Moves never touch a wall.
STEER_ADDRESS = -234
STEER_KEYS = {'W': -BOARD_STRIDE, 'S': BOARD_STRIDE, 'A': -1, 'D': 1}

# field name -> width, in the order the interface is documented and laid out
FIELDS = dict(sa=15, sb=15, sw=15, imm=12, target=10, f3=3, aluop=3, legal=1, load=1, store=1, branch=1, jal=1, jalr=1, immediate=1, compare=1, static=1, key=1, nokey=1, lit=1)
NO_REGISTER = 8                 # x8 does not exist: reads as zero, cannot be written, and has no pin
ID_BASE = 100000                # controller ids of a cartridge start here, clear of any CORE or CONSOLE id


def pins():
    """The seam, in order: (port, bit or None, 'in' to the CORE or 'out' of it). The same for every cartridge."""
    result = [('cart_address', bit, 'out') for bit in range(ADDRESS_BITS)] + [('cart_data_address', bit, 'out') for bit in range(DATA_ADDRESS_BITS)]
    for name, width in FIELDS.items():
        for bit in range(width):
            if not (name in ('sa', 'sb', 'sw') and bit == NO_REGISTER - 1):
                result.append(('cart_' + name, bit if width > 1 else None, 'in'))
    return result + [('cart_data', bit, 'in') for bit in range(8)]


def pin_name(port, bit):
    return port if bit is None else f'{port}({bit})'


def pin_id(index):
    return ID_BASE + index


# ---- the cartridge file ---------------------------------------------------------------------------

MAGIC = b'RVCT'


@dataclass
class Group:
    key: str
    title: str = ''
    program: str = ''
    colour: str = ''
    code: list = field(default_factory=list)      # [first, end) word ranges
    data: list = field(default_factory=list)      # [first, end) byte ranges

    def meta(self):
        return {k: v for k, v in dict(key=self.key, title=self.title, program=self.program, colour=self.colour).items() if v}


@dataclass
class Image:
    """Data port bytes, code words linked for BASE, the data offsets of u16 code addresses to relocate after
    folding, the board-mode word ranges, and the groups."""
    data: bytes
    code: bytes
    relocations: list = field(default_factory=list)
    board: list = field(default_factory=list)
    groups: list = field(default_factory=list)


def write_image(image):
    meta = json.dumps(dict(relocations=image.relocations, board=image.board, groups=[dict(g.meta(), code=g.code, data=g.data) for g in image.groups])).encode()
    return MAGIC + struct.pack('<HHI', len(image.data), len(image.code) // 4, len(meta)) + image.data + image.code + meta


def read_image(raw, name='PROGRAM'):
    """A cartridge file, or a bare RV32E binary, which becomes one program in board mode."""
    if raw[:4] != MAGIC:
        return flat_image(raw, name)
    data_bytes, words, meta_bytes = struct.unpack_from('<HHI', raw, 4)
    data = raw[12:12 + data_bytes]
    code = raw[12 + data_bytes:12 + data_bytes + words * 4]
    meta = json.loads(raw[12 + data_bytes + words * 4:12 + data_bytes + words * 4 + meta_bytes])
    groups = [Group(g['key'], g.get('title', ''), g.get('program', ''), g.get('colour', ''), g.get('code', []), g.get('data', [])) for g in meta['groups']]
    return Image(data, code, meta['relocations'], meta['board'], groups)


def directory(title, entries):
    """A screen page of ROW_STRIDE byte rows: the text of each row, then in the hidden columns the magic and the
    number of programs (title row), or the mode and the entry address (program rows)."""
    def row(text, *hidden):
        return text.encode('ascii')[:BOARD_COLUMNS].ljust(BOARD_COLUMNS, b'\0') + bytes(hidden).ljust(ROW_STRIDE - BOARD_COLUMNS, b'\0')
    pages = row(title, DIRECTORY_MAGIC, len(entries))
    for n, (name, mode, entry) in enumerate(entries, 1):
        pages += row(f'{n} {name}', mode, entry & 255, entry >> 8)
    return pages


def flat_image(code, name):
    code += bytes(-len(code) % 4)
    data = directory('MENU', [(name[:10].upper(), MODE_BOARD, BASE)])
    return Image(data, code, [ROW_STRIDE + 13], [[0, len(code) // 4]], [Group(name.lower(), name.upper(), name.upper(), '', [[0, len(code) // 4]], [[0, len(data)]])])


# ---- from words to rows ---------------------------------------------------------------------------

def generic_image(firmware):
    """What the CPU's analyses see: the firmware, then a window any register can be written from in any way."""
    assert len(firmware) <= BASE, f'firmware ({len(firmware)} bytes) overlaps the cartridge window at {BASE:#x}'
    stub = b''
    for r in range(1, 16):
        if r != NO_REGISTER:
            stub += (r << 20 | r << 15 | r << 7 | 51).to_bytes(4, 'little')         # add r,r,r
            stub += (2 << 12 | r << 15 | r << 7 | 3).to_bytes(4, 'little')           # lw r,0(r)
    image = firmware + bytes(BASE - len(firmware)) + stub
    # one short of the power of two, so the PC keeps bits 12..2 only
    return image + bytes((BASE + WORDS * 4 - 4) - len(image))


def onehot(register):
    """Bit k set for register k+1; x0 selects nothing."""
    return 1 << register - 1 if register else 0


def in_ranges(ranges, value):
    return any(first <= value < end for first, end in ranges)


@dataclass
class Rows:
    rows: list          # field values per row
    owner: list         # the word each row came from
    new_pc: dict        # word's old pc -> row's pc


def rows(image):
    """Per-row field values of the code, checked against what the interface can carry.

    Rows fold into one single-cycle row where the datapath allows it. Later rows move up and branch targets
    follow, so the cartridge holds fewer rows than the image has words. In board-mode programs:

    * `lb t,0(x); sb r,0(x)` is a swap: the CPU reads during capture and writes during commit.
    * `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle.
    * `add zero,p,zero; lb r,STEER_ADDRESS(zero); j target` is the steering row: see STEER_ADDRESS.

    In any program:

    * `lb t,-240(zero); blt/bge t,zero,target` loads the status and branches on the ready flag directly.
    * `addi d,a,-K; beq/bne d,zero,target` compares a with K in the same row as the subtraction.
    * anything that is not a jump or an input load, then `j target`: the row jumps as well."""
    words = compact_core.analysis_metadata(bytes(BASE) + image.code)[BASE // 4:]
    targets = {r['target'] for r in words if r['branch'] or r['jal']}
    for offset in image.relocations:
        targets.add(int.from_bytes(image.data[offset:offset + 2], 'little'))
    merged, new_pc, skip = [], {}, 0
    for i, r in enumerate(words):
        if skip:
            skip -= 1
            continue
        new_pc[r['pc']] = BASE + 4 * len(merged)
        if r['word'] & 127 == 11:
            merged.append(dict(first=r, index=i, sbi=True))
            continue
        board = in_ranges(image.board, i)
        following = words[i + 1] if i + 1 < len(words) and words[i + 1]['pc'] not in targets else None
        if r['load'] and r['static_memory'] and r['imm'] == (STEER_ADDRESS & 0xFFFFFFFF):
            raise ValueError(f"a steering load must follow its marker: cartridge word at {r['pc']:#x}")
        if r['word'] & 127 == 51 and r['rd'] == 0 and following and following['load'] and following['static_memory'] and following['imm'] == (STEER_ADDRESS & 0xFFFFFFFF):
            load, jump = words[i + 1], words[i + 2]
            if not (jump['jal'] and jump['rd'] == 0 and load['f3'] == 0 and jump['pc'] not in targets):
                raise ValueError(f"a steering load must be `lb` followed by a jump: cartridge word at {load['pc']:#x}")
            merged.append(dict(first=load, index=i, steer=(jump, r['ra'])))
            skip = 2
            continue
        entry = dict(first=r, index=i)
        f = following
        if f:
            alu = r['word'] & 127 in (19, 51)
            if board and r['load'] and r['f3'] in (0, 4) and r['imm'] == 0 and not r['static_memory'] and f['store'] and f['f3'] == 0 and f['imm'] == 0 and f['ra'] == r['ra'] and r['rd'] not in (r['ra'], f['rb']):
                entry['swap'] = f
            elif board and r['store'] and r['f3'] == 0 and r['imm'] == 0 and not r['static_memory'] and f['word'] & 127 == 51 and f['f3'] == 0 and not f['arithmetic'] and f['rd'] == r['ra'] and {f['ra'], f['rb']} == {r['ra'], r['rb']}:
                entry['advance'] = f
            elif r['load'] and r['f3'] == 0 and r['static_memory'] and r['imm'] == (-240 & 0xFFFFFFFF) and f['branch'] and f['f3'] in (4, 5) and f['ra'] == r['rd'] and f['rb'] == 0:
                entry['status'] = f
            elif r['word'] & 127 == 19 and r['f3'] == 0 and r['rd'] and r['ra'] and f['branch'] and f['f3'] in (0, 1) and f['ra'] == r['rd'] and f['rb'] == 0 and r['imm'] != 0xFFFFF800:
                entry['compare'] = f
            elif f['jal'] and f['rd'] == 0 and not (r['load'] and r['static_memory'] and r['imm'] >= 0xFFFFFF00) and (alu or r['load'] or r['store']):
                entry['jump'] = f
        merged.append(entry)
        skip = int(any(k in entry for k in ('swap', 'advance', 'status', 'compare', 'jump')))
    result, owner = [], []
    for m in merged:
        r = m['first']
        w = r['word']
        where = f"cartridge word at {r['pc']:#x} ({w:#010x})"
        owner.append(m['index'])
        if m.get('sbi'):
            result.append(dict.fromkeys(FIELDS, 0) | dict(legal=1, store=1, static=1, lit=1, imm=w >> 20, target=w >> 12 & 255, aluop=1, immediate=1))
            continue
        if not r['legal']:
            raise ValueError(f'illegal instruction in {where}')
        if r['lui'] or r['auipc'] or r['shiftleft'] or r['shiftright'] or r['stop']:
            raise ValueError(f'LUI, AUIPC, shifts, ECALL and EBREAK are not available to a cartridge: {where}')
        if NO_REGISTER in (r['rd'], r['ra'], r['rb']):
            raise ValueError(f'x8 does not exist: {where}')
        if r['load'] and r['static_memory'] and DATA_BASE <= r['imm'] < DATA_BASE + DATA_BYTES:
            raise ValueError(f'the data port needs a register-addressed load: {where}')
        steer, status, compare, jump, swap, advance = (m.get(k) for k in ('steer', 'status', 'compare', 'jump', 'swap', 'advance'))
        imm = r['imm']
        if r['immediate']:
            low = imm & 4095
            if (low - (low & 2048) * 2) & 0xFFFFFFFF != imm:
                raise ValueError(f'immediate does not fit 12 bits: {where}')
        else:
            low = 0
        branch_row = compare or status
        jump_row = steer[0] if steer else jump
        target = jump_row['target'] if jump_row else branch_row['target'] if branch_row else r['target'] if r['branch'] or r['jal'] else 0
        if target and (target & 3 or target not in new_pc):
            raise ValueError(f'branch target {target:#x} leaves the cartridge: {where}')
        target = new_pc[target] if target else 0
        row = dict(sa=onehot(r['rd'] if steer else r['ra']), sb=onehot(steer[1] if steer else swap['rb'] if swap else r['rb']), sw=onehot(advance['rd'] if advance else r['rd']), imm=low, target=target >> 2 & 1023, f3=r['f3'], aluop=r['aluop'], legal=1, load=r['load'], store=1 if swap else r['store'], branch=r['branch'], jal=r['jal'], jalr=r['jalr'], immediate=0 if advance else r['immediate'], compare=r['compare'], static=int(bool((r['load'] or r['store']) and r['ra'] == 0)), key=int(bool(steer or jump or (status and status['f3'] == 4))), nokey=int(bool(steer or jump or (status and status['f3'] == 5))), lit=0)
        if compare:
            row.update(branch=1, f3=compare['f3'], aluop=2, imm=-imm & 4095)
        result.append(row)
    return Rows(result, owner, new_pc)


def relocated_data(image, new_pc):
    data = bytearray(image.data)
    for offset in image.relocations:
        value = int.from_bytes(data[offset:offset + 2], 'little')
        data[offset:offset + 2] = new_pc[value].to_bytes(2, 'little')
    return bytes(data)


# ---- the module's gates ---------------------------------------------------------------------------

@dataclass
class Net:
    kinds: dict         # id -> gate kind
    labels: dict        # id -> label
    edges: set
    pins: dict          # name -> id
    report: dict


def netlist(image):
    """The gates, every label in the form docs/layout.md expects. Field ORs of the rows in a group, and of its
    data bytes, are that group's; they merge in the pin gates at the seam, the OUTPUT BUS."""
    folded = rows(image)
    data = relocated_data(image, folded.new_pc)
    if len(folded.rows) > WORDS:
        raise ValueError(f'{len(folded.rows)} rows do not fit the {WORDS} row window')
    if len(data) > DATA_BYTES:
        raise ValueError(f'{len(data)} data bytes do not fit the {DATA_BYTES} byte data port')
    kinds, labels, edges, ids = {}, {}, set(), {}
    table = pins()

    def gate(kind, label, inputs=(), identifier=None):
        i = identifier if identifier is not None else ID_BASE + len(table) + len(kinds)
        kinds[i], labels[i] = kind, label
        edges.update((a, i) for a in inputs)
        return i
    for n, (port, bit, way) in enumerate(table):
        ids[pin_name(port, bit)] = gate('OR', f'Cartridge {pin_name(port, bit)}', (), pin_id(n))

    def group_of(index, kind):
        for g in image.groups:
            if in_ranges(getattr(g, kind), index):
                return g.key
        return 'unassigned'

    def literals(port, wanted, name):
        """wanted: {AND gate: address}. Wire each to its address literals, at most 128 to a driver, with
        drivers of their own for the inverse and for a bit too loaded to drive them all."""
        for bit in range(ADDRESS_BITS):
            pin = ids[f'{port}({bit})']
            for polarity in (1, 0):
                users = [i for i, value in wanted.items() if (value >> bit & 1) == polarity]
                for start in range(0, len(users), 128):
                    if polarity and len(users) <= 128:
                        driver = pin
                    else:
                        kind = 'OR' if polarity else 'NOR'
                        driver = gate(kind, f"{kind} @cart:{name}_{'high' if polarity else 'low'}({bit})", (pin,))
                    edges.update((driver, i) for i in users[start:start + 128])
    row_selects, wanted = {}, {}
    for n, owner in enumerate(folded.owner):
        key = group_of(owner, 'code')
        i = gate('AND', f'AND @cart_{key}:select_row({n})')
        row_selects.setdefault(key, {})[n] = i
        wanted[i] = (BASE >> 2) + n
    literals('cart_address', wanted, 'address')
    data_selects, wanted = {}, {}
    for n, value in enumerate(data):
        if value:
            key = group_of(n, 'data')
            i = gate('AND', f'AND @cart_{key}:data_row({n})')
            data_selects.setdefault(key, {})[n] = i
            wanted[i] = DATA_BASE + n
    literals('cart_data_address', wanted, 'address_data')

    def merge(pin, key, signal, terms, shared):
        """Group `key` drives `pin` with an OR of its terms, one the group already has if the terms match."""
        for start in range(0, len(terms), 128):
            chunk = tuple(sorted(terms[start:start + 128]))
            if len(chunk) == 1:
                edges.add((chunk[0], pin))
                continue
            if chunk not in shared:
                shared[chunk] = gate('OR', f'OR @cart_{key}:{signal}', chunk)
            edges.add((shared[chunk], pin))
    for key, selected in row_selects.items():
        shared = {}
        for name, width in FIELDS.items():
            for bit in range(width):
                if name in ('sa', 'sb', 'sw') and bit == NO_REGISTER - 1:
                    continue
                terms = [i for n, i in selected.items() if folded.rows[n][name] >> bit & 1]
                if terms:
                    merge(ids[pin_name('cart_' + name, bit if width > 1 else None)], key, f'g_{name}({bit})' if width > 1 else f'g_{name}', terms, shared)
    for key, selected in data_selects.items():
        shared = {}
        for bit in range(8):
            terms = [i for n, i in selected.items() if data[n] >> bit & 1]
            if terms:
                merge(ids[f'cart_data({bit})'], key, f'g_data({bit})', terms, shared)
    counts = {}
    for label in labels.values():
        tag = re.search(r' @cart_(\w+):', label)
        if tag:
            counts[tag[1]] = counts.get(tag[1], 0) + 1
    return Net(kinds, labels, edges, ids, dict(rows=len(folded.rows), words=len(image.code) // 4, data_bytes=len(data), logic_blocks=len(kinds), wires=len(edges), group_blocks=counts))


def seam_interface(build):
    """Turn the seam ports of a finished CORE into pin gates: an input becomes an OR gate with no parents for
    the cartridge to drive, and an output's buffer goes, so the gate driving it stands at the seam."""
    circuit = build.circuit.clone()
    ports = deepcopy(build.ports)
    parents = {}
    for a, b in circuit.edges:
        parents.setdefault(b, []).append(a)
    gone = set()
    for name in ('cart_address', 'cart_data_address'):
        node_ids = []
        for i in ports[name]['node_ids']:
            # compaction may have folded the logic into the buffer already, and then it is the gate at the seam
            if len(parents[i]) == 1 and parents[i][0] not in node_ids:
                node_ids.append(parents[i][0])
                gone.add(i)
            else:
                node_ids.append(i)
        ports[name]['node_ids'] = node_ids
    circuit.remove(gone)
    wanted = {pin_name(port, bit) for port, bit, way in pins() if way == 'in'}
    consumers = {a for a, b in circuit.edges}
    for name in [n for n in ports if n.startswith('cart_') and ports[n]['mode'] == 'in']:
        kept, indices = [], []
        for index, i in zip(ports[name]['indices'], ports[name]['node_ids']):
            bit_name = name if len(ports[name]['node_ids']) == 1 else f'{name}({index})'
            if bit_name in wanted:
                circuit.nodes[i].update(kind='OR', label='cart_pin ' + bit_name)
                kept.append(i)
                indices.append(index)
            else:
                assert i not in consumers, bit_name
                circuit.remove({i})
        ports[name].update(node_ids=kept, indices=indices)
    circuit.validate()
    return Build(circuit, ports, build.report, build.source)


# ---- CPU side ---------------------------------------------------------------------------------

CPU_PORTS = 'cart_sa,cart_sb,cart_sw:in bit_vector(14 downto 0);cart_imm:in bit_vector(11 downto 0);cart_target:in bit_vector(9 downto 0);cart_f3,cart_aluop:in bit_vector(2 downto 0);cart_data:in bit_vector(7 downto 0);cart_legal,cart_load,cart_store,cart_branch,cart_jal,cart_jalr,cart_immediate,cart_compare,cart_static,cart_key,cart_nokey,cart_lit:in bit;cart_address,cart_data_address:out bit_vector(10 downto 0);cart_screen_write,cart_screen_high,cart_screen_text,cart_tap,cart_board:out bit;'


def wrapped(expression):
    """True when the parenthesis opened right after the leading name closes at the very end."""
    depth = 0
    for i in range(expression.index('('), len(expression)):
        depth += (expression[i] == '(') - (expression[i] == ')')
        if depth == 0:
            return i == len(expression) - 1
    return False


def cpu_source(text):
    """Add the cartridge pins to the finished CPU HDL and merge them into the decoder outputs."""
    def once(old, new):
        nonlocal text
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    once('port(capture,commit,reset,bus_ack,text_history:in bit;', 'port(' + CPU_PORTS + 'capture,commit,reset,bus_ack,text_history:in bit;')
    flags = dict(legal='legal', load='load', store='store', branch='branch', jal='jal', jalr='jalr', immediate='immediate', compare='compare', static_memory='static')

    def add(name, bit, term):
        nonlocal text
        pattern = re.compile(rf'^(  {re.escape(name)}{"" if bit is None else rf"\({bit}\)"} <= )(.*);$', re.M)
        found = pattern.findall(text)
        assert len(found) == 1, (name, bit, len(found))

        def merge(m):
            rhs = m[2]
            if rhs == "'0'":
                return f'{m[1]}{term};'
            if rhs.startswith('or_reduce(') and wrapped(rhs):
                return f'{m[1]}or_reduce({rhs[len("or_reduce("):-1]} & {term});'
            return f'{m[1]}({rhs}) or {term};'
        text = pattern.sub(merge, text)
    for name, width in (('f3', 3), ('aluop', 3)):
        for bit in range(width):
            add('d_' + name, bit, f'cart_{name}({bit})')
    for bit in range(32):
        add('d_imm', bit, f'cart_imm({min(bit, 11)})')
    for bit in range(2, 12):
        add('d_target', bit, f'cart_target({bit - 2})')
    add('d_target', 12, 'pc_q(12)')     # the window is left only by jalr, so the target's top bit stays as it is
    for bit in range(8):
        add('rom_word', bit, f'cart_data({bit})')
    for name, field_name in flags.items():
        add('d_' + name, None, 'cart_' + field_name)
    for r in range(1, 16):
        if r == NO_REGISTER:
            continue
        for key, field_name in (('a', 'sa'), ('b', 'sb')):
            pattern = re.compile(rf'\b(select_{key}\({r}\) <= )([^;]*);')
            assert len(pattern.findall(text)) == 1, (key, r, len(pattern.findall(text)))
            text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or cart_{field_name}({r - 1});', text)
        pattern = re.compile(rf'\b(select_write\({r}\) <= )([^;]*);')
        assert len(pattern.findall(text)) == 1, ('write', r, len(pattern.findall(text)))
        text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or (cart_sw({r - 1}) and execute_ok);', text)
    # the memory map: everything a program addresses fits a 12-bit immediate, see docs/cartridge.md
    once('  region_ram <= address(31 downto 6) = bits(64,26);', '  region_ram <= (address(31 downto 6) = bits(0,26)) or board_access;')
    once('  region_program <= address(31 downto 9) = bits(9,23);', f'  region_program <= address(31 downto 9) = bits({TEXT_BASE >> 9},23);')
    once('  region_stream <= address(31 downto 8) = bits(16777201,24);', f'  region_stream <= address(31 downto 8) = bits({STREAM_BASE >> 8},24);')
    once("  region_rom <= '0';", f'  region_rom <= address(31 downto {DATA_ADDRESS_BITS}) = bits(0,{32 - DATA_ADDRESS_BITS}) and address({DATA_ADDRESS_BITS - 1});  region_screen <= address(31 downto 7) = bits({(SCREEN_BASE & 0xFFFFFFFF) >> 7},25);  region_mode <= d_static_memory and d_store and (d_imm(11 downto 0) = bits({MODE_ADDRESS & 4095},12));')
    once(' or region_gpio or ((region_program', ' or (d_store and region_screen) or region_mode or region_gpio or ((region_program')
    # in board mode a register-addressed access is a board access, whatever its upper address bits happen to hold
    once('  byte_cases(0) <= region_program;', '  byte_cases(0) <= region_program and not board_mode;')
    once('  byte_cases(1) <= region_stream or (region_input and address(2));', '  byte_cases(1) <= (region_stream and not board_mode) or (region_input and address(2));')
    # Without this, reading through a position register that has drifted into the input stream
    # window takes the waiting key, and the program never sees it.
    once('d_load and (region_stream or (region_input and address(2))) and ready_seen', 'd_load and ((region_stream and not board_mode) or (region_input and address(2))) and ready_seen')
    # the board's address reach: seven bits, with the hidden columns and bottom row reading as walls
    for word in range(16):
        hidden = ' and not board_access' if word % 4 == 3 else ''
        once(f'  ram_read_word_select({word}) <= address(5 downto 2) = bits({word},4);', f'  ram_read_word_select({word}) <= (address({BOARD_ADDRESS_BITS - 1} downto 2) = bits({word},{BOARD_ADDRESS_BITS - 2})){hidden};')
    for bit in range(8):
        wall = ' & board_wall' if WALL >> bit & 1 else ''
        pattern = re.compile(rf'^(  ram_word\({bit}\) <= or_reduce\()(.*)\);$', re.M)
        text, n = pattern.subn(lambda m: f'{m[1]}{m[2]} & board_read({bit}){wall});', text)
        assert n == 1, bit
    once('  store_stage <= latch(execute_ok and d_store and region_ram,cap_en,reset);', '  store_stage <= latch(execute_ok and d_store and region_ram and not board_off,cap_en,reset);')
    once("  byte_ascii_data <= '0' & input_byte(6 downto 0);", "  byte_ascii_data <= steer_byte when steer else '0' & input_byte(6 downto 0);")
    # a store of a constant takes its byte from the target field
    once('  data_stage <= latch(b(7 downto 0),cap_en,reset);', '  data_stage <= latch(store_byte,cap_en,reset);')
    once('  store_data <= latch(b,cap_en,reset);', '  store_data <= latch(b(31 downto 8) & store_byte,cap_en,reset);')
    # A branch can compare with its immediate, which reads as port B: a term of the port's own OR adds no delay.
    for bit in range(32):
        add('b', bit, f'(compare_immediate and d_imm({min(bit, 11)}))')
    text = text.replace('  signal regs:registers_t;', f'  signal compare_immediate,region_screen,region_mode,board_mode,mode_write,screen_stage,screen_text_stage,board_access,board_off,board_wall,board_store_stage,board_high_stage,steer,steer_take,steer_valid,steer_reverse,steer_key_horizontal,steer_key_negative,steer_now_horizontal,steer_now_negative,steer_horizontal,steer_negative:bit;signal mode_stage,mode_q:bit_vector(1 downto 0);signal store_byte,steer_byte:bit_vector(7 downto 0);signal steer_key:bit_vector({len(STEER_KEYS) - 1} downto 0);signal steer_way,steer_edge:bit_vector(3 downto 0);signal board_read:bit_vector(7 downto 0);signal board_row_read,board_row_write:bit_vector({BOARD_ROWS - RAM_ROWS - 1} downto 0);signal board_column_read,board_column_open:bit_vector({BOARD_COLUMNS - 1} downto 0);type board_cells_t is array(0 to {EXTRA_CELLS - 1}) of bit_vector(8 downto 0);signal board_cells:board_cells_t;  signal regs:registers_t;', 1)
    extra = f'  cart_address <= pc_q({ADDRESS_BITS + 1} downto 2);\n  cart_data_address <= address({DATA_ADDRESS_BITS - 1} downto 0);\n  compare_immediate <= d_branch and d_immediate;\n'
    extra += ''.join(f'  store_byte({bit}) <= or_reduce(b({bit}) & (cart_lit and cart_target({bit})));\n' for bit in range(8))
    # The mode, set by a store. A change of mode blanks the screen and drops any waiting line, in the console.
    extra += '  mode_write <= execute_ok and region_mode;\n'
    for bit in range(2):
        extra += f'  mode_stage({bit}) <= latch((mode_write and store_byte({bit})) or (not mode_write and mode_q({bit})),cap_en,reset);\n  mode_q({bit}) <= latch(mode_stage({bit}),com_en,reset);\n'
    extra += '  board_mode <= mode_q(1);\n  cart_tap <= mode_q(0);\n  cart_board <= mode_q(1);\n'
    # a store to the board or the screen region also writes the matching screen cell, during this instruction's commit
    extra += '  screen_stage <= latch(execute_ok and d_store and ((board_mode and region_ram) or region_screen),cap_en,reset);\n  screen_text_stage <= latch(region_screen,cap_en,reset);\n  cart_screen_write <= screen_stage and com_en;\n  cart_screen_text <= screen_text_stage;\n  cart_screen_high <= board_high_stage;\n'
    return text.rsplit('end;', 1)[0] + extra + board_source() + steer_source() + 'end;\n'


def extra_cell(row, column):
    return (row - RAM_ROWS) * BOARD_COLUMNS + column


def board_source():
    """The board rows below the RAM, reachable only by register-addressed accesses in board mode.

    Nothing else can write them and RESET is the only way out of a program, so they are always
    clear on entry. Nothing is stored for hidden columns or the bottom row, which read as walls."""
    a = BOARD_ADDRESS_BITS
    text = '  board_access <= board_mode and not d_static_memory;\n'
    text += f'  board_off <= address({a - 1}) or and_reduce(board_access & address(3) & address(2));\n'
    text += f"  board_wall <= or_reduce(and_reduce(board_access & address(3) & address(2)) & and_reduce(board_access & address({a - 1} downto 4)));\n"
    text += f'  board_high_stage <= latch(address({a - 1}),cap_en,reset);\n'
    text += f'  board_store_stage <= latch(execute_ok and d_store and board_access and address({a - 1}),cap_en,reset);\n'
    for n, row in enumerate(range(RAM_ROWS, BOARD_ROWS)):
        text += f'  board_row_read({n}) <= address({a - 1} downto 4) = bits({row},{a - 4});\n'
        text += f"  board_row_write({n}) <= and_reduce(com_en & board_store_stage & (addr_stage(5 downto 4) = bits({row & 3},2)));\n"
    for column in range(BOARD_COLUMNS):
        text += f'  board_column_read({column}) <= address(3 downto 0) = bits({column},4);\n'
        # during RESET every column is open, so the shared clear pulse reaches every cell
        text += f'  board_column_open({column}) <= (addr_stage(3 downto 0) = bits({column},4)) or reset;\n'
    for n, row in enumerate(range(RAM_ROWS, BOARD_ROWS)):
        for column in range(BOARD_COLUMNS):
            text += f'  board_cells({extra_cell(row, column)}) <= latch(board_column_open({column}) & store_data(7 downto 0),board_row_write({n}),reset);\n'
    for bit in range(8):
        terms = [f'and_reduce(board_row_read({n}) & board_column_read({column}) & board_cells({extra_cell(row, column)})({bit}))' for n, row in enumerate(range(RAM_ROWS, BOARD_ROWS)) for column in range(BOARD_COLUMNS)]
        text += f"  board_read({bit}) <= or_reduce({' & '.join(terms)});\n"
    return text


def steer_source():
    """The steering row, which has both key fields set: a = the step register, b = the position register.

    Every value a step can hold says which way it moves. It is odd if horizontal, and bit 1 (if odd) or
    bit 6 (if even) is set if leftward or upward, for the plain steps and for the hops across an edge alike."""
    names = list(STEER_KEYS)
    assert BOARD_STRIDE == 16 and BOARD_COLUMNS % 2 == 0
    hop_columns, hop_rows = BOARD_COLUMNS - 1, (BOARD_ROWS - 1) * BOARD_STRIDE
    # way: (is it horizontal, is it negative, the plain step, the step off the far edge, the position test)
    ways = [(True, False, 1, -hop_columns, f'b(3 downto 0) = bits({BOARD_COLUMNS - 1},4)'),
            (True, True, -1, hop_columns, 'b(3 downto 0) = bits(0,4)'),
            (False, False, BOARD_STRIDE, -hop_rows, f'b({BOARD_ADDRESS_BITS - 1} downto 4) = bits({BOARD_ROWS - 1},{BOARD_ADDRESS_BITS - 4})'),
            (False, True, -BOARD_STRIDE, hop_rows, f'b({BOARD_ADDRESS_BITS - 1} downto 4) = bits(0,{BOARD_ADDRESS_BITS - 4})')]
    for horizontal, negative, plain, hop, _ in ways:
        for step in (plain, hop):
            assert bool(step & 1) == horizontal and bool(step >> (1 if horizontal else 6) & 1) == negative, step
    text = '  steer <= cart_key and cart_nokey;\n'
    for n, key in enumerate(names):
        text += f'  steer_key({n}) <= input_byte(6 downto 0) = bits({ord(key)},7);\n'
    text += f"  steer_valid <= or_reduce({' & '.join(f'steer_key({n})' for n in range(len(names)))});\n"
    text += f"  steer_key_horizontal <= or_reduce({' & '.join(f'steer_key({n})' for n, key in enumerate(names) if STEER_KEYS[key] & 1)});\n"
    text += f"  steer_key_negative <= or_reduce({' & '.join(f'steer_key({n})' for n, key in enumerate(names) if STEER_KEYS[key] < 0)});\n"
    text += '  steer_now_horizontal <= a(0);\n  steer_now_negative <= (a(0) and a(1)) or (not a(0) and a(6));\n'
    text += '  steer_reverse <= (steer_now_horizontal xnor steer_key_horizontal) and (steer_now_negative xor steer_key_negative);\n'
    text += '  steer_take <= ready_seen and steer_valid and not steer_reverse;\n'
    text += '  steer_horizontal <= (steer_take and steer_key_horizontal) or (not steer_take and steer_now_horizontal);\n'
    text += '  steer_negative <= (steer_take and steer_key_negative) or (not steer_take and steer_now_negative);\n'
    terms = {bit: [] for bit in range(8)}
    for n, (horizontal, negative, plain, hop, test) in enumerate(ways):
        h, g = ('' if horizontal else 'not ') + 'steer_horizontal', ('' if negative else 'not ') + 'steer_negative'
        text += f'  steer_way({n}) <= ({h}) and ({g});\n  steer_edge({n}) <= {test};\n'
        for bit in range(8):
            if plain >> bit & 1:
                terms[bit].append(f'(steer_way({n}) and not steer_edge({n}))')
            if hop >> bit & 1:
                terms[bit].append(f'(steer_way({n}) and steer_edge({n}))')
    for bit in range(8):
        text += f"  steer_byte({bit}) <= or_reduce({' & '.join(terms[bit])});\n"
    return text


_board_lines = set()


@contextmanager
def board_storage():
    """Build the extra board cells as XOR bits with a write pulse shared per row, using the RAM's reset
    pulse, instead of a whole latch per cell. Bit 8 of each cell is its column's enable, passed through."""
    original = Builder.latch

    def latch(b, data, enable, reset, token):
        if token.line not in _board_lines:
            return original(b, data, enable, reset, token)
        assert len(data) == 9 and reset != 0
        allowed, clear = b.xor_cpu_reset_groups[reset]
        triggers = b.__dict__.setdefault('board_triggers', {})
        if enable not in triggers:
            prefix = f'timing BOARD row {len(triggers)} '
            inverse = b.raw('NOR', (enable,), prefix + 'request inverse', True, token)
            normal = b.raw('AND', (enable, inverse, allowed), prefix + 'normal pulse', False, token)
            triggers[enable] = b.raw('OR', (normal, clear), prefix + 'trigger', False, token)
        b.latch_count += 8
        result = []
        for bit in range(8):
            q = b.raw('XOR', (), f'latch Q line {token.line} bit {bit}', False, token)
            delta = b.raw('XOR', (data[bit], q), f'timing BOARD line {token.line} bit {bit} delta', False, token)
            flip = b.raw('AND', (delta, triggers[enable], data[8]), f'timing BOARD line {token.line} bit {bit} flip', False, token)
            b.gates[q]['sources'] = (q, flip)
            b.storage.add(q)
            result.append(q)
        return Bits(tuple(result) + (data[8],))
    with patch.object(Builder, 'latch', latch):
        yield


def note_board_lines(text):
    """Remember which source lines hold board cells. Later transforms keep lines on their line numbers."""
    _board_lines.clear()
    _board_lines.update(i for i, line in enumerate(text.splitlines(), 1) if line.startswith('  board_cells('))
    assert len(_board_lines) == EXTRA_CELLS, len(_board_lines)
    return text


def console_source(text):
    """Console side of the modes. In either tap or board mode:

    * Keys bypass the console's line and its clock. They are not echoed and do not move the cursor. The
      key at the head of the queue is offered straight away, bit 7 of the input byte is its ready flag, and
      each read that takes it toggles input index bit 0, which pops the queue directly, with no acknowledge
      transaction.

    A change of mode blanks the screen in one console cycle and drops any waiting line. Entering board mode fills
    the board with '.' instead, and on a screen tall enough for it draws SCORE_LINE on the bottom row.

    * A CPU store to the screen region writes one cell directly, as an ASCII character, in any mode.
    * A CPU store to the board in board mode writes one screen cell directly (row = address / 16, column =
      address & 15, columns 12..15 are not shown), with 0 shown as '.', 2 as '+', 4 as 'Q', else 'O'. Stores to
      the two SCORE_ADDRESS bytes, which are not on the board, show as digits on the bottom row, and a store of a
      value in RESULTS writes that result line in one go. KEY_ECHO shows the key at the head of the queue.
    """
    def once(old, new):
        nonlocal text
        assert text.count(old) == 1, old
        text = text.replace(old, new)

    once('port(capture,commit,reset,faulted,key_settled,key_available:in bit;', 'port(capture,commit,reset,faulted,key_settled,key_available,screen_write,screen_late,screen_high,screen_text,key_echo_late,cart_mode,board_mode:in bit;screen_address:in bit_vector(5 downto 0);screen_data:in bit_vector(7 downto 0);')
    once('busy,full,key_taken:out bit;', 'busy,full,key_taken,key_toggle:out bit;')
    count = len(re.findall(r'^  pixel_write\(\d+\) <=', text, re.M))
    board_cells = min(BOARD_ROWS * BOARD_COLUMNS, count)
    decl = f'  signal raw_seen,raw_seen_stage,raw_enter,raw_clear,blank_stage,board_blank_stage,board_seen,board_seen_stage,mirror_zero,mirror_food,mirror_head,cart_ready:bit;\n  signal mirror_code:bit_vector(5 downto 0);signal screen_full:bit_vector({BOARD_ADDRESS_BITS - 1} downto 0);signal mirror_select,mirror_write:bit_vector({count - 1} downto 0);\n'
    text = text.replace('begin\n', decl + 'begin\n', 1)
    # with single taps the console's own key path stays idle: keys never reach its line, so it never goes busy
    once('  key_event <= tap_offer and key_settled and any_key and valid_key and not ready and not ack and not faulted and not tx;', '  key_event <= tap_offer and key_settled and any_key and valid_key and not ready and not ack and not faulted and not tx and not cart_mode;')
    once('latch(tap_offer and key_settled and any_key and not ready and not ack and not faulted and not tx,cap_en,reset)', 'latch(tap_offer and key_settled and any_key and not ready and not ack and not faulted and not tx and not cart_mode,cap_en,reset)')
    once("  length_next <= bits(0,5) when ack else", "  length_next <= bits(0,5) when ack or raw_clear else")
    once("  ready_next <= '0' when ack else ready or key_enter;", "  ready_next <= '0' when ack or raw_clear else ready or key_enter;")
    once('  input_char <= resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);', '  input_char <= code when cart_mode else resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);')
    once("  keyboard_data <= '1' & input_char when ready else x\"00\";", "  keyboard_data <= '1' & input_char when ready or cart_ready else x\"00\";")
    text = text.replace(' and not ready and not faulted', ' and not ready and not faulted and not cart_mode')
    # The two clocks beat against each other, so a change can land as a capture closes. What is remembered as seen
    # comes through more gates than the blank does, so a change is either blanked for or noticed again, never lost.
    extra = '  raw_seen_stage <= latch(raw_seen xor ((cart_mode xor raw_seen) and raw_enter),cap_en,reset);raw_seen <= latch(raw_seen_stage,com_en,reset);\n  board_seen_stage <= latch(board_seen xor ((board_mode xor board_seen) and raw_enter),cap_en,reset);board_seen <= latch(board_seen_stage,com_en,reset);\n  raw_enter <= (cart_mode xor raw_seen) or (board_mode xor board_seen);\n  blank_stage <= latch(raw_enter,cap_en,reset);\n  board_blank_stage <= latch(raw_enter and board_mode,cap_en,reset);\n  raw_clear <= raw_enter;\n'
    extra += '  cart_ready <= cart_mode and key_settled and any_key and valid_key;\n  key_toggle <= gpio(24);\n  screen_full <= screen_high & screen_address;\n'
    # a character is its code's low five bits and bit six; a board value is one of four marks
    extra += "  mirror_zero <= screen_data = bits(0,8);\n  mirror_food <= screen_data = bits(2,8);\n  mirror_head <= screen_data = bits(4,8);\n"
    board_code = ['not mirror_zero', 'not mirror_head', 'not mirror_food and not mirror_head', 'not mirror_head', 'mirror_head', 'not mirror_zero and not mirror_food']
    for bit, value in enumerate(board_code):
        extra += f"  mirror_code({bit}) <= or_reduce((screen_text and screen_data({bit if bit < 5 else 6})) & (not screen_text and ({value})));\n"

    def cell_terms(i, bit_terms):
        nonlocal text
        for bit, term in bit_terms:
            pattern = re.compile(rf'^(  chars_data\({i}\)\({bit}\) <= (?:\(not )?or_reduce\()', re.M)
            text, n = pattern.subn(lambda m: f'{m[1]}{term} & ', text)
            assert n == 1, (i, bit)

    def cell_write(i, write):
        once(f'  chars_q({i}) <= latch(chars_data({i}),com_en and pixel_write({i}),reset);', f'  chars_q({i}) <= latch(chars_data({i}),(com_en and pixel_write({i})) or {write},reset);')
    for i in range(count):
        address = i // BOARD_COLUMNS * BOARD_STRIDE + i % BOARD_COLUMNS
        # the board's own stores stop at the board: its bottom row and hidden columns are walls
        guard = '' if i < board_cells else 'screen_text and '
        extra += f'  mirror_select({i}) <= {guard}screen_write and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n  mirror_write({i}) <= {guard}screen_late and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n'
        for bit in range(6):
            blank = ' & board_blank_stage' if i < board_cells and bit in (1, 2, 3) else ''
            pattern = re.compile(rf'^(  chars_data\({i}\)\({bit}\) <= (?:\(not )?or_reduce\()', re.M)
            text, n = pattern.subn(lambda m: f'{m[1]}(mirror_select({i}) and mirror_code({bit})){blank} & ', text)
            assert n == 1, (i, bit)
        pattern = re.compile(rf'^(  pixel_write\({i}\) <= [^;]*);', re.M)
        text, n = pattern.subn(lambda m: f'{m[1]} or blank_stage;', text)
        assert n == 1, i
        if i < board_cells or count < 96 or i // BOARD_COLUMNS != SCORE_LINE[0]:
            cell_write(i, f'mirror_write({i})')
    if count < 96:
        return text.rsplit('end;', 1)[0] + extra + 'end;\n'

    def glyphs(row, column, line):
        return {row * 12 + column + k: ord(ch) - 32 for k, ch in enumerate(line)}
    # entry to board mode draws the score line
    for i, code in glyphs(*SCORE_LINE).items():
        cell_terms(i, [(bit, 'board_blank_stage') for bit in range(6) if code >> bit & 1])
    tens = SCORE_LINE[0] * BOARD_COLUMNS + SCORE_LINE[1]
    bottom = {tens + k for k in range(2)}
    for k in range(2):
        address = SCORE_ADDRESS + k
        extra += f'  score_select({k}) <= board_mode and screen_write and not screen_text and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n  score_write({k}) <= board_mode and screen_late and not screen_text and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n'
        # a binary digit's glyph code is that digit plus 16, the code of '0'
        cell_terms(tens + k, [(bit, f'(score_select({k}) and screen_data({bit}))') for bit in range(4)] + [(4, f'score_select({k})')])
        cell_write(tens + k, f'mirror_write({tens + k}) or score_write({k})')
    # the key at the head of the queue: an ASCII code's glyph is its low five bits and the inverse of bit 5
    echo = KEY_ECHO[0] * BOARD_COLUMNS + KEY_ECHO[1]
    # The result line covers this cell. A key still waiting holds its write enable high, so a result store
    # drops that first, or the cell would see no rising edge. After that the cell stays with the result.
    extra += '  key_echo <= board_mode and key_settled and not game_over and not or_reduce(result_is);\n  game_over_stage <= latch(result_write or game_over,cap_en,reset);\n  game_over <= latch(game_over_stage,com_en,reset);\n'
    cell_terms(echo, [(bit, f'(key_echo and code({bit}))') for bit in range(5)] + [(5, '(key_echo and not code(5))')])
    lines = [glyphs(*where) for where in RESULTS.values()]
    for n, value in enumerate(RESULTS):
        extra += f'  result_is({n}) <= board_mode and not screen_text and (screen_data = bits({value},8)) and (screen_full = bits({RESULT_ADDRESS},{BOARD_ADDRESS_BITS}));\n  result_select({n}) <= screen_write and result_is({n});\n'
    extra += '  result_write <= screen_late and or_reduce(result_is);\n'
    for i in sorted({i for line in lines for i in line}):
        cell_terms(i, [(bit, f'result_select({n})') for n, line in enumerate(lines) for bit in range(6) if line.get(i, 0) >> bit & 1])
        cell_write(i, f'mirror_write({i}) or (board_mode and key_echo_late and not game_over and not or_reduce(result_is)) or result_write' if i == echo else f'mirror_write({i}) or result_write')
        bottom.add(i)
    for i in range(SCORE_LINE[0] * BOARD_COLUMNS, count):
        if i not in bottom:
            cell_write(i, f'mirror_write({i})')
    text = text.replace('begin\n', f'  signal score_select,score_write:bit_vector(1 downto 0);signal result_is,result_select:bit_vector({len(RESULTS) - 1} downto 0);signal result_write,game_over,game_over_stage,key_echo:bit;\n' + 'begin\n', 1)
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'


@contextmanager
def window(enabled=True):
    """Hide the cartridge rows from the baked decoder while a CPU is generated."""
    previous = compact_core.CART
    compact_core.CART = (BASE, BASE + WORDS * 4) if enabled else None
    try:
        yield
    finally:
        compact_core.CART = previous
