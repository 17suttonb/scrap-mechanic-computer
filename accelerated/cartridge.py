"""External program cartridge: a separate ROM module the CPU can execute from.

The CPU's firmware ROM is baked into its gates.  A cartridge is a second ROM, held in its own
blueprint, that answers the CPU's fetch address with the pre-decoded instruction fields the baked
ROM would have produced.  Execution switches to it by jumping into the cartridge address window.

    CPU -> cartridge   cart_address (10)            program counter bits 11..2
    cartridge -> CPU   cart_sa/sb/sw (15 each)      one-hot read A, read B and write register x1..x15
                       cart_imm (12)                sign-extended by the CPU
                       cart_target (10)             branch / jal target, bits 11..2
                       cart_f3 (3), cart_aluop (3)
                       cart_legal, load, store, branch, jal, jalr, immediate, compare, static (9)
                       cart_key, cart_nokey (2)     take the target if a key is / is not waiting

Everything else the baked decoder knows is zero for those rows, so a cartridge cannot use LUI/AUIPC,
shifts, ECALL or EBREAK.  The register-width and value-range analyses still run over the
cartridge image, so the CPU is specialised for the registers a given cartridge writes; the image
must be the one the CPU was built with.  With no cartridge attached the fields read zero, the
first fetch there is an illegal instruction and the CPU traps.
"""
from contextlib import contextmanager
import re
from unittest.mock import patch
from accelerated import compact_core
from accelerated.hdl import Bits, Builder, compile_source

BASE = 0x800                    # byte address of the first cartridge instruction
WORDS = 512                     # cartridge window: 512 words, PC bits 11..2 with bit 11 set
ADDRESS_BITS = 10
# The board a register-addressed cartridge access sees: row = address / STRIDE, column = address % STRIDE.
BOARD_COLUMNS = 12
BOARD_ROWS = 7
BOARD_STRIDE = 16
BOARD_ADDRESS_BITS = 7          # the rest of a position register is ignored, so positions wrap at 128
RAM_ROWS = 4                    # rows held in the CPU's 64-byte RAM; those below get bytes of their own
WALL = 3                        # what a register-addressed load returns from any address that is not a cell
EXTRA_CELLS = (BOARD_ROWS - RAM_ROWS) * BOARD_COLUMNS
# the bottom row of the screen: (row, column, text) of the score, drawn on entry, and of the result for each
# value stored to end the game, which overwrites the word SCORE
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
FIELDS = dict(sa=15, sb=15, sw=15, imm=12, target=10, f3=3, aluop=3, legal=1, load=1, store=1, branch=1, jal=1, jalr=1, immediate=1, compare=1, static=1, key=1, nokey=1)


def combined_image(firmware, cartridge):
    """Firmware padded to the cartridge window, followed by the cartridge."""
    assert len(firmware) <= BASE, f'firmware ({len(firmware)} bytes) overlaps the cartridge window at {BASE:#x}'
    assert len(cartridge) % 4 == 0 and 0 < len(cartridge) <= WORDS * 4
    return firmware + bytes(BASE - len(firmware)) + cartridge


def onehot(register):
    """Bit k set for register k+1; x0 selects nothing."""
    return 1 << register - 1 if register else 0


def rows(cartridge):
    """Per-row field values of the cartridge, checked against what the interface can carry.

    Two common pairs fold into one single-cycle row. Later rows move up and branch targets follow,
    so the cartridge holds fewer rows than the image has words.

    * `lb t,0(x); sb r,0(x)` is a swap: the CPU reads during capture and writes during commit.
    * `lb t,-240(zero); blt/bge t,zero,target` loads the status and branches on the ready flag
      directly, without waiting for the load to reach a register.
    * `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle.
    * `add zero,p,zero; lb r,STEER_ADDRESS(zero); j target` is the steering row: see STEER_ADDRESS."""
    image = combined_image(b'', cartridge)
    words = compact_core.analysis_metadata(image)[BASE // 4:BASE // 4 + len(cartridge) // 4]
    targets = {r['target'] for r in words if r['branch'] or r['jal']}
    merged, new_pc, skip = [], {}, 0
    for i, r in enumerate(words):
        if skip:
            skip -= 1
            continue
        new_pc[r['pc']] = BASE + 4 * len(merged)
        steer = None
        if r['load'] and r['static_memory'] and r['imm'] == (STEER_ADDRESS & 0xFFFFFFFF):
            raise ValueError(f"a steering load must follow its marker: cartridge word at {r['pc']:#x}")
        if r['word'] & 127 == 51 and r['rd'] == 0 and i + 2 < len(words) and words[i + 1]['load'] and words[i + 1]['static_memory'] and words[i + 1]['imm'] == (STEER_ADDRESS & 0xFFFFFFFF):
            load, jump = words[i + 1], words[i + 2]
            if not (jump['jal'] and jump['rd'] == 0 and load['f3'] == 0 and not {load['pc'], jump['pc']} & targets):
                raise ValueError(f"a steering load must be `lb` followed by a jump: cartridge word at {load['pc']:#x}")
            merged.append((load, None, None, None, (jump, r['ra'])))
            skip = 2
            continue
        following = words[i + 1] if i + 1 < len(words) else None
        swap = (following and r['load'] and r['f3'] in (0, 4) and r['imm'] == 0 and not r['static_memory'] and following['store'] and following['f3'] == 0
                and following['imm'] == 0 and following['ra'] == r['ra'] and following['pc'] not in targets and r['rd'] not in (r['ra'], following['rb']))
        status = (following and r['load'] and r['f3'] == 0 and r['static_memory'] and r['imm'] == (-240 & 0xFFFFFFFF) and following['branch'] and following['f3'] in (4, 5)
                  and following['ra'] == r['rd'] and following['rb'] == 0 and following['pc'] not in targets)
        advance = (following and r['store'] and r['f3'] == 0 and r['imm'] == 0 and not r['static_memory'] and following['word'] & 127 == 51 and following['f3'] == 0 and not following['arithmetic']
                   and following['rd'] == r['ra'] and {following['ra'], following['rb']} == {r['ra'], r['rb']} and following['pc'] not in targets)
        merged.append((r, following if swap else None, following if status else None, following if advance else None, None))
        skip = int(bool(swap or status or advance))
    result = []
    for r, store, status, advance, steer in merged:
        w = r['word']
        where = f"cartridge word at {r['pc']:#x} ({w:#010x})"
        if not r['legal']:
            raise ValueError(f'illegal instruction in {where}')
        if r['lui'] or r['auipc'] or r['shiftleft'] or r['shiftright'] or r['stop']:
            raise ValueError(f'LUI, AUIPC, shifts, ECALL and EBREAK are not available to a cartridge: {where}')
        if r['rd'] == 8:
            raise ValueError(f'x8 is the firmware base register and must not be written: {where}')
        imm = r['imm']
        if r['immediate']:
            low = imm & 4095
            if (low - (low & 2048) * 2) & 0xFFFFFFFF != imm:
                raise ValueError(f'immediate does not fit 12 bits: {where}')
        else:
            low = 0
        target = steer[0]['target'] if steer else status['target'] if status else r['target'] if r['branch'] or r['jal'] else 0
        if target and (target & 3 or not BASE <= target < BASE + WORDS * 4):
            raise ValueError(f'branch target {target:#x} leaves the cartridge window: {where}')
        target = new_pc[target] if target else 0
        result.append(dict(sa=onehot(r['rd'] if steer else r['ra']), sb=onehot(steer[1] if steer else store['rb'] if store else r['rb']), sw=onehot(advance['rd'] if advance else r['rd']), imm=low, target=target >> 2 & 1023, f3=r['f3'], aluop=r['aluop'], legal=1, load=r['load'], store=1 if store else r['store'], branch=r['branch'], jal=r['jal'], jalr=r['jalr'], immediate=0 if advance else r['immediate'], compare=r['compare'], static=int(bool((r['load'] or r['store']) and r['ra'] == 0)), key=int(bool(steer or (status and status['f3'] == 4))), nokey=int(bool(steer or (status and status['f3'] == 5)))))
    return result


def source(cartridge):
    """HDL of the cartridge module."""
    data = rows(cartridge)
    ports = ';'.join(f'cart_{name}:out bit_vector({width - 1} downto 0)' if width > 1 else f'cart_{name}:out bit' for name, width in FIELDS.items())
    lines = [f'entity rv_cartridge is\n  port(cart_address:in bit_vector({ADDRESS_BITS - 1} downto 0); {ports});\nend;',
             'architecture gates of rv_cartridge is',
             f'  signal select_row:bit_vector({len(data) - 1} downto 0);',
             f'  signal address_low:bit_vector({ADDRESS_BITS - 1} downto 0);',
             'begin']
    # one flat decoder per row: the decode path is part of every instruction's timing
    for i in range(len(data)):
        literals = [f'cart_address({bit})' if (512 + i) >> bit & 1 else f'address_low({bit})' for bit in range(ADDRESS_BITS)]
        lines.append(f"  select_row({i}) <= and_reduce({' & '.join(literals)});")
    lines.append('  address_low <= not cart_address;')
    for name, width in FIELDS.items():
        for bit in range(width):
            terms = [f'select_row({i})' for i, row in enumerate(data) if row[name] >> bit & 1]
            target = f'cart_{name}({bit})' if width > 1 else f'cart_{name}'
            lines.append(f"  {target} <= or_reduce({' & '.join(terms)});" if terms else f"  {target} <= '0';")
    lines.append('end;')
    return '\n'.join(lines)


def seam_drivers(build):
    """Gates that drive a cartridge output pin. The pin must stay a single-parent buffer so the
    module split can cut the wire there, so no pass may fuse its driver into it."""
    pins = {n for name, port in build.ports.items() if name.startswith('cart_') for n in port['node_ids']}
    return {a for a, b in build.circuit.edges if b in pins} if pins else set()


def build(cartridge):
    result = compile_source(source(cartridge), top='rv_cartridge', wide_gates=True, latch_style='nand')
    result.report['cartridge'] = dict(words=len(cartridge) // 4, base=BASE)
    return result


# ---- CPU side ---------------------------------------------------------------------------------

CPU_PORTS = 'cart_sa,cart_sb,cart_sw:in bit_vector(14 downto 0);cart_imm:in bit_vector(11 downto 0);cart_target:in bit_vector(9 downto 0);cart_f3,cart_aluop:in bit_vector(2 downto 0);cart_legal,cart_load,cart_store,cart_branch,cart_jal,cart_jalr,cart_immediate,cart_compare,cart_static,cart_key,cart_nokey:in bit;cart_address:out bit_vector(9 downto 0);cart_screen_write,cart_screen_high,cart_mode_out:out bit;'


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
    old = 'port(capture,commit,reset,bus_ack,text_history:in bit;'
    assert text.count(old) == 1
    text = text.replace(old, 'port(' + CPU_PORTS + 'capture,commit,reset,bus_ack,text_history:in bit;')
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
    for name, field in flags.items():
        add('d_' + name, None, 'cart_' + field)
    for r in range(1, 16):
        for key, field in (('a', 'sa'), ('b', 'sb')):
            pattern = re.compile(rf'\b(select_{key}\({r}\) <= )([^;]*);')
            assert len(pattern.findall(text)) == 1, (key, r, len(pattern.findall(text)))
            text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or cart_{field}({r - 1});', text)
        pattern = re.compile(rf'\b(select_write\({r}\) <= )([^;]*);')
        assert len(pattern.findall(text)) == 1, ('write', r, len(pattern.findall(text)))
        text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or (cart_sw({r - 1}) and execute_ok);', text)
    old = '  region_ram <= address(31 downto 6) = bits(64,26);'
    assert text.count(old) == 1
    # the cartridge addresses the 64-byte RAM at 0..63, the firmware at 4096..4159
    # register-addressed cartridge accesses always reach the RAM, through the low six address bits
    text = text.replace(old, '  region_board <= (cart_legal and not d_static_memory) or (address(31 downto 6) = bits(0,26));  region_ram <= (address(31 downto 6) = bits(64,26)) or region_board;')
    for old, new in (('  byte_cases(0) <= region_program;', '  byte_cases(0) <= region_program and not cart_legal;'), ('  byte_cases(1) <= region_stream or (region_input and address(2));', '  byte_cases(1) <= (region_stream and not cart_legal) or (region_input and address(2));')):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    # A register-addressed cartridge load is a board read, whatever its upper address bits happen to hold.
    # Without this, reading through a position register that has drifted into the firmware's input stream
    # window takes the waiting key, and the program never sees it.
    old = 'd_load and (region_stream or (region_input and address(2))) and ready_seen'
    assert text.count(old) == 1
    text = text.replace(old, 'd_load and ((region_stream and not cart_legal) or (region_input and address(2))) and ready_seen')
    # the board's address reach: seven bits, with the hidden columns and bottom row reading as walls
    for word in range(16):
        old = f'  ram_read_word_select({word}) <= address(5 downto 2) = bits({word},4);'
        assert text.count(old) == 1, old
        hidden = ' and not board_access' if word % 4 == 3 else ''
        text = text.replace(old, f'  ram_read_word_select({word}) <= (address({BOARD_ADDRESS_BITS - 1} downto 2) = bits({word},{BOARD_ADDRESS_BITS - 2})){hidden};')
    for bit in range(8):
        wall = ' & board_wall' if WALL >> bit & 1 else ''
        pattern = re.compile(rf'^(  ram_word\({bit}\) <= or_reduce\()(.*)\);$', re.M)
        text, n = pattern.subn(lambda m: f'{m[1]}{m[2]} & board_read({bit}){wall});', text)
        assert n == 1, bit
    old = '  store_stage <= latch(execute_ok and d_store and region_ram,cap_en,reset);'
    assert text.count(old) == 1
    text = text.replace(old, '  store_stage <= latch(execute_ok and d_store and region_ram and not board_off,cap_en,reset);')
    old = "  byte_ascii_data <= '0' & input_byte(6 downto 0);"
    assert text.count(old) == 1
    text = text.replace(old, "  byte_ascii_data <= steer_byte when steer else '0' & input_byte(6 downto 0);")
    text = text.replace('  signal regs:registers_t;', f'  signal region_board,screen_stage,board_access,board_off,board_wall,board_store_stage,board_high_stage,steer,steer_take,steer_valid,steer_reverse,steer_key_horizontal,steer_key_negative,steer_now_horizontal,steer_now_negative,steer_horizontal,steer_negative:bit;signal steer_byte:bit_vector(7 downto 0);signal steer_key:bit_vector({len(STEER_KEYS) - 1} downto 0);signal steer_way,steer_edge:bit_vector(3 downto 0);signal board_read:bit_vector(7 downto 0);signal board_row_read,board_row_write:bit_vector({BOARD_ROWS - RAM_ROWS - 1} downto 0);signal board_column_read,board_column_open:bit_vector({BOARD_COLUMNS - 1} downto 0);type board_cells_t is array(0 to {EXTRA_CELLS - 1}) of bit_vector(8 downto 0);signal board_cells:board_cells_t;  signal regs:registers_t;', 1)
    # a store to the board also writes the matching screen cell, during this instruction's commit
    screen = '  screen_stage <= latch(execute_ok and d_store and region_board,cap_en,reset);\n  cart_screen_write <= screen_stage and com_en;\n  cart_mode_out <= latch(pc_q(11),cap_en,reset);\n  cart_screen_high <= board_high_stage;\n'
    return text.rsplit('end;', 1)[0] + f'  cart_address <= pc_q({ADDRESS_BITS + 1} downto 2);\n' + screen + board_source() + steer_source() + 'end;\n'


def extra_cell(row, column):
    return (row - RAM_ROWS) * BOARD_COLUMNS + column


def board_source():
    """The board rows below the RAM, reachable only by register-addressed cartridge accesses.

    The firmware cannot write them and RESET is the only way out of a cartridge, so they are always
    clear on entry. Nothing is stored for hidden columns or the bottom row, which read as walls."""
    a = BOARD_ADDRESS_BITS
    text = '  board_access <= cart_legal and not d_static_memory;\n'
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
    """Console side of cartridge mode, which is on while the PC is in the cartridge window:

    * On entry the screen is filled with '.' in one console cycle and any waiting line is dropped.
    * Keys bypass the console's line and its clock. They are not echoed and do not move the cursor. The
      key at the head of the queue is offered straight away, bit 7 of the input byte is its ready flag, and
      each read that takes it toggles input index bit 0, which pops the queue directly, with no acknowledge
      transaction.
    * A CPU store to the board writes one screen cell directly (row = address / 16, column =
      address & 15, columns 12..15 are not shown), with 0 shown as '.', 2 as '+', 4 as 'Q', else 'O'.
    * On a screen tall enough for it, entry also draws SCORE_LINE on the bottom row. Stores to the two
      SCORE_ADDRESS bytes, which are not on the board, show as digits on it, and a store of a value
      in RESULTS writes that result line in one go. KEY_ECHO shows the key at the head of the queue.
    """
    def once(old, new):
        nonlocal text
        assert text.count(old) == 1, old
        text = text.replace(old, new)

    once('port(capture,commit,reset,faulted,key_settled,key_available:in bit;', 'port(capture,commit,reset,faulted,key_settled,key_available,screen_write,screen_late,screen_high,key_echo_late,cart_mode:in bit;screen_address:in bit_vector(5 downto 0);screen_data:in bit_vector(7 downto 0);')
    once('busy,full,key_taken:out bit;', 'busy,full,key_taken,key_toggle:out bit;')
    count = len(re.findall(r'^  pixel_write\(\d+\) <=', text, re.M))
    mirror = min(BOARD_ROWS * BOARD_COLUMNS, count)
    decl = f'  signal raw_seen,raw_seen_stage,raw_enter,raw_clear,blank_stage,mirror_zero,mirror_food,mirror_head,cart_ready:bit;\n  signal mirror_code:bit_vector(5 downto 0);signal screen_full:bit_vector({BOARD_ADDRESS_BITS - 1} downto 0);signal mirror_select,mirror_write:bit_vector({mirror - 1} downto 0);\n'
    text = text.replace('begin\n', decl + 'begin\n', 1)
    # in cartridge mode the console's own key path stays idle: keys never reach its line, so it never goes busy
    once('  key_event <= tap_offer and key_settled and any_key and valid_key and not ready and not ack and not faulted and not tx;', '  key_event <= tap_offer and key_settled and any_key and valid_key and not ready and not ack and not faulted and not tx and not cart_mode;')
    once('latch(tap_offer and key_settled and any_key and not ready and not ack and not faulted and not tx,cap_en,reset)', 'latch(tap_offer and key_settled and any_key and not ready and not ack and not faulted and not tx and not cart_mode,cap_en,reset)')
    once("  length_next <= bits(0,5) when ack else", "  length_next <= bits(0,5) when ack or raw_clear else")
    once("  ready_next <= '0' when ack else ready or key_enter;", "  ready_next <= '0' when ack or raw_clear else ready or key_enter;")
    once('  input_char <= resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);', '  input_char <= code when cart_mode else resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);')
    once("  keyboard_data <= '1' & input_char when ready else x\"00\";", "  keyboard_data <= '1' & input_char when ready or cart_ready else x\"00\";")
    text = text.replace(' and not ready and not faulted', ' and not ready and not faulted and not cart_mode')
    extra = '  raw_seen_stage <= latch(cart_mode,cap_en,reset);raw_seen <= latch(raw_seen_stage,com_en,reset);\n  raw_enter <= cart_mode and not raw_seen;\n  blank_stage <= latch(raw_enter,cap_en,reset);\n  raw_clear <= raw_enter;\n'
    extra += '  cart_ready <= cart_mode and key_settled and any_key and valid_key;\n  key_toggle <= cart_mode and gpio(24);\n  screen_full <= screen_high & screen_address;\n'
    extra += "  mirror_zero <= screen_data = bits(0,8);\n  mirror_food <= screen_data = bits(2,8);\n  mirror_head <= screen_data = bits(4,8);\n  mirror_code(0) <= not mirror_zero;mirror_code(1) <= not mirror_head;mirror_code(2) <= not mirror_food and not mirror_head;mirror_code(3) <= not mirror_head;mirror_code(4) <= mirror_head;mirror_code(5) <= not mirror_zero and not mirror_food;\n"
    for i in range(mirror):
        address = i // BOARD_COLUMNS * BOARD_STRIDE + i % BOARD_COLUMNS
        extra += f'  mirror_select({i}) <= screen_write and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n  mirror_write({i}) <= screen_late and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n'
        for bit in range(6):
            pattern = re.compile(rf'^(  chars_data\({i}\)\({bit}\) <= (?:\(not )?or_reduce\()', re.M)
            blank = ' & blank_stage' if bit in (1, 2, 3) else ''
            text, n = pattern.subn(lambda m: f'{m[1]}(mirror_select({i}) and mirror_code({bit})){blank} & ', text)
            assert n == 1, (i, bit)
        pattern = re.compile(rf'^(  pixel_write\({i}\) <= [^;]*);', re.M)
        text, n = pattern.subn(lambda m: f'{m[1]} or blank_stage;', text)
        assert n == 1, i
        once(f'  chars_q({i}) <= latch(chars_data({i}),com_en and pixel_write({i}),reset);', f'  chars_q({i}) <= latch(chars_data({i}),(com_en and pixel_write({i})) or mirror_write({i}),reset);')
    # any screen rows below the board are outside the mirror: entry only blanks them, to spaces
    for i in range(mirror, count):
        pattern = re.compile(rf'^(  pixel_write\({i}\) <= [^;]*);', re.M)
        text, n = pattern.subn(lambda m: f'{m[1]} or blank_stage;', text)
        assert n == 1, i
    if count < 96:
        return text.rsplit('end;', 1)[0] + extra + 'end;\n'

    def cell_terms(i, bit_terms):
        nonlocal text
        for bit, term in bit_terms:
            pattern = re.compile(rf'^(  chars_data\({i}\)\({bit}\) <= (?:\(not )?or_reduce\()', re.M)
            text, n = pattern.subn(lambda m: f'{m[1]}{term} & ', text)
            assert n == 1, (i, bit)

    def cell_write(i, write):
        once(f'  chars_q({i}) <= latch(chars_data({i}),com_en and pixel_write({i}),reset);', f'  chars_q({i}) <= latch(chars_data({i}),(com_en and pixel_write({i})) or {write},reset);')

    def glyphs(row, column, line):
        return {row * 12 + column + k: ord(ch) - 32 for k, ch in enumerate(line)}
    # entry draws the score line
    for i, code in glyphs(*SCORE_LINE).items():
        cell_terms(i, [(bit, 'blank_stage') for bit in range(6) if code >> bit & 1])
    tens = SCORE_LINE[0] * BOARD_COLUMNS + SCORE_LINE[1]
    for k in range(2):
        address = SCORE_ADDRESS + k
        extra += f'  score_select({k}) <= screen_write and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n  score_write({k}) <= screen_late and (screen_full = bits({address},{BOARD_ADDRESS_BITS}));\n'
        # a binary digit's glyph code is that digit plus 16, the code of '0'
        cell_terms(tens + k, [(bit, f'(score_select({k}) and screen_data({bit}))') for bit in range(4)] + [(4, f'score_select({k})')])
        cell_write(tens + k, f'score_write({k})')
    # the key at the head of the queue: an ASCII code's glyph is its low five bits and the inverse of bit 5
    echo = KEY_ECHO[0] * BOARD_COLUMNS + KEY_ECHO[1]
    # The result line covers this cell. A key still waiting holds its write enable high, so a result store
    # drops that first, or the cell would see no rising edge. After that the cell stays with the result.
    extra += '  key_echo <= cart_mode and key_settled and not game_over and not or_reduce(result_is);\n  game_over_stage <= latch(result_write or game_over,cap_en,reset);\n  game_over <= latch(game_over_stage,com_en,reset);\n'
    cell_terms(echo, [(bit, f'(key_echo and code({bit}))') for bit in range(5)] + [(5, '(key_echo and not code(5))')])
    lines =[glyphs(*where) for where in RESULTS.values()]
    for n, value in enumerate(RESULTS):
        extra += f'  result_is({n}) <= (screen_data = bits({value},8)) and (screen_full = bits({RESULT_ADDRESS},{BOARD_ADDRESS_BITS}));\n  result_select({n}) <= screen_write and result_is({n});\n'
    extra += '  result_write <= screen_late and or_reduce(result_is);\n'
    for i in sorted({i for line in lines for i in line}):
        cell_terms(i, [(bit, f'result_select({n})') for n, line in enumerate(lines) for bit in range(6) if line.get(i, 0) >> bit & 1])
        cell_write(i, '(key_echo_late and not game_over and not or_reduce(result_is)) or result_write' if i == echo else 'result_write')
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
