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
from accelerated import compact_core
from accelerated.hdl import compile_source

BASE = 0x800                    # byte address of the first cartridge instruction
WORDS = 512                     # cartridge window: 512 words, PC bits 11..2 with bit 11 set
ADDRESS_BITS = 10
# below the board: (row, column, text) of the score, and of the result for each value stored to end the game
SCORE_LINE = (5, 2, 'SCORE 00')
SCORE_ADDRESS = 13              # tens, then ones: bytes in hidden columns, so not on the board
RESULTS = {5: (7, 2, 'YOU LOST'), 6: (7, 2, 'YOU WON')}

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
    * `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle."""
    image = combined_image(b'', cartridge)
    words = compact_core.analysis_metadata(image)[BASE // 4:BASE // 4 + len(cartridge) // 4]
    targets = {r['target'] for r in words if r['branch'] or r['jal']}
    merged, new_pc, skip = [], {}, False
    for i, r in enumerate(words):
        if skip:
            skip = False
            continue
        new_pc[r['pc']] = BASE + 4 * len(merged)
        following = words[i + 1] if i + 1 < len(words) else None
        swap = (following and r['load'] and r['f3'] in (0, 4) and r['imm'] == 0 and not r['static_memory'] and following['store'] and following['f3'] == 0
                and following['imm'] == 0 and following['ra'] == r['ra'] and following['pc'] not in targets and r['rd'] not in (r['ra'], following['rb']))
        status = (following and r['load'] and r['f3'] == 0 and r['static_memory'] and r['imm'] == (-240 & 0xFFFFFFFF) and following['branch'] and following['f3'] in (4, 5)
                  and following['ra'] == r['rd'] and following['rb'] == 0 and following['pc'] not in targets)
        advance = (following and r['store'] and r['f3'] == 0 and r['imm'] == 0 and not r['static_memory'] and following['word'] & 127 == 51 and following['f3'] == 0 and not following['arithmetic']
                   and following['rd'] == following['ra'] == r['ra'] and following['rb'] == r['rb'] and following['pc'] not in targets)
        merged.append((r, following if swap else None, following if status else None, following if advance else None))
        skip = bool(swap or status or advance)
    result = []
    for r, store, status, advance in merged:
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
        target = status['target'] if status else r['target'] if r['branch'] or r['jal'] else 0
        if target and (target & 3 or not BASE <= target < BASE + WORDS * 4):
            raise ValueError(f'branch target {target:#x} leaves the cartridge window: {where}')
        target = new_pc[target] if target else 0
        result.append(dict(sa=onehot(r['ra']), sb=onehot(store['rb'] if store else r['rb']), sw=onehot(advance['rd'] if advance else r['rd']), imm=low, target=target >> 2 & 1023, f3=r['f3'], aluop=r['aluop'], legal=1, load=r['load'], store=1 if store else r['store'], branch=r['branch'], jal=r['jal'], jalr=r['jalr'], immediate=0 if advance else r['immediate'], compare=r['compare'], static=int(bool((r['load'] or r['store']) and r['ra'] == 0)), key=int(bool(status and status['f3'] == 4)), nokey=int(bool(status and status['f3'] == 5))))
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

CPU_PORTS = 'cart_sa,cart_sb,cart_sw:in bit_vector(14 downto 0);cart_imm:in bit_vector(11 downto 0);cart_target:in bit_vector(9 downto 0);cart_f3,cart_aluop:in bit_vector(2 downto 0);cart_legal,cart_load,cart_store,cart_branch,cart_jal,cart_jalr,cart_immediate,cart_compare,cart_static,cart_key,cart_nokey:in bit;cart_address:out bit_vector(9 downto 0);cart_screen_write,cart_mode_out:out bit;'


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
    text = text.replace('  signal regs:registers_t;', '  signal region_board,screen_stage:bit;  signal regs:registers_t;', 1)
    # a store to the board also writes the matching screen cell, during this instruction's commit
    screen = '  screen_stage <= latch(execute_ok and d_store and region_board,cap_en,reset);\n  cart_screen_write <= screen_stage and com_en;\n  cart_mode_out <= latch(pc_q(11),cap_en,reset);\n'
    return text.rsplit('end;', 1)[0] + f'  cart_address <= pc_q({ADDRESS_BITS + 1} downto 2);\n' + screen + 'end;\n'


def console_source(text):
    """Console side of cartridge mode, which is on while the PC is in the cartridge window:

    * On entry the screen is filled with '.' in one console cycle and any waiting line is dropped.
    * Keys are not echoed and do not move the cursor; one tap is delivered as a complete line,
      readable at index 0, and reading it clears it, so no acknowledge transaction is needed.
    * A CPU store to the board writes one screen cell directly (row = address / 16, column =
      address & 15, columns 12..15 are not shown), with 0 shown as '.', 2 as '+', 4 as 'Q', else 'O'.
    * On a screen tall enough for it, entry also draws SCORE_LINE below the board. Stores to the two
      SCORE_ADDRESS bytes, which are not on the board, show as digits on it, and a store of a value
      in RESULTS writes that result line in one go.
    """
    def once(old, new):
        nonlocal text
        assert text.count(old) == 1, old
        text = text.replace(old, new)

    once('port(capture,commit,reset,faulted,key_settled,key_available:in bit;', 'port(capture,commit,reset,faulted,key_settled,key_available,screen_write,screen_late,cart_mode:in bit;screen_address:in bit_vector(5 downto 0);screen_data:in bit_vector(7 downto 0);')
    decl = '  signal raw_seen,raw_seen_stage,raw_enter,raw_clear,taken_seen,taken_stage,taken_old,taken_old_stage,blank_stage,key_shown,mirror_zero,mirror_food,mirror_head:bit;\n  signal mirror_code:bit_vector(5 downto 0);signal mirror_select,mirror_write:bit_vector(47 downto 0);\n'
    text = text.replace('begin\n', decl + 'begin\n', 1)
    text = text.replace('key_print or cpu_print', 'key_shown or cpu_print')
    once('  newline_event <= key_enter or (tx', '  newline_event <= (key_enter and not cart_mode) or (tx')
    once('  key_back <= key_event and (code = bits(8,7)) and (length_q /= bits(0,5));', '  key_back <= key_event and (code = bits(8,7)) and (length_q /= bits(0,5)) and not cart_mode;')
    once("  length_next <= bits(0,5) when ack else", "  length_next <= bits(0,5) when ack or raw_clear else")
    once("  ready_next <= '0' when ack else ready or key_enter;", "  ready_next <= '0' when ack or raw_clear else ready or key_enter or (key_print and cart_mode);")
    once('  input_char <= resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);', '  input_char <= resize(line(0),7) + bits(32,7) when cart_mode and (length_q /= bits(0,5)) else resize(line(to_integer(gpio(28 downto 24))),7) + bits(32,7) when (gpio(28 downto 24) < length_q) else bits(0,7);')
    text = text.replace(' and not ready and not faulted', ' and not ready and not faulted and not cart_mode')
    extra = '  key_shown <= key_print and not cart_mode;\n'
    extra += '  raw_seen_stage <= latch(cart_mode,cap_en,reset);raw_seen <= latch(raw_seen_stage,com_en,reset);\n  raw_enter <= cart_mode and not raw_seen;\n  blank_stage <= latch(raw_enter,cap_en,reset);\n'
    # The CPU's clock is unrelated to this one, so its "key taken" toggle is registered first. Compared
    # directly, a toggle arriving late in a capture phase could end before the key state saw it, and
    # the same key would be delivered again.
    extra += '  taken_stage <= latch(gpio(24),cap_en,reset);taken_seen <= latch(taken_stage,com_en,reset);\n  taken_old_stage <= latch(taken_seen,cap_en,reset);taken_old <= latch(taken_old_stage,com_en,reset);\n  raw_clear <= raw_enter or (cart_mode and (taken_seen xor taken_old));\n'
    extra += "  mirror_zero <= screen_data = bits(0,8);\n  mirror_food <= screen_data = bits(2,8);\n  mirror_head <= screen_data = bits(4,8);\n  mirror_code(0) <= not mirror_zero;mirror_code(1) <= not mirror_head;mirror_code(2) <= not mirror_food and not mirror_head;mirror_code(3) <= not mirror_head;mirror_code(4) <= mirror_head;mirror_code(5) <= not mirror_zero and not mirror_food;\n"
    for i in range(48):
        address = i // 12 * 16 + i % 12
        extra += f'  mirror_select({i}) <= screen_write and (screen_address = bits({address},6));\n  mirror_write({i}) <= screen_late and (screen_address = bits({address},6));\n'
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
    count = len(re.findall(r'^  pixel_write\(\d+\) <=', text, re.M))
    for i in range(48, count):
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
    tens = SCORE_LINE[0] * 12 + SCORE_LINE[1] + len(SCORE_LINE[2]) - 2
    for k in range(2):
        address = SCORE_ADDRESS + k
        extra += f'  score_select({k}) <= screen_write and (screen_address = bits({address},6));\n  score_write({k}) <= screen_late and (screen_address = bits({address},6));\n'
        # an ASCII digit's glyph code is its low five bits
        cell_terms(tens + k, [(bit, f'(score_select({k}) and screen_data({bit}))') for bit in range(5)])
        cell_write(tens + k, f'score_write({k})')
    lines = [glyphs(*where) for where in RESULTS.values()]
    for n, value in enumerate(RESULTS):
        extra += f'  result_is({n}) <= screen_data = bits({value},8);\n  result_select({n}) <= screen_write and result_is({n});\n'
    extra += '  result_write <= screen_late and or_reduce(result_is);\n'
    for i in sorted({i for line in lines for i in line}):
        cell_terms(i, [(bit, f'result_select({n})') for n, line in enumerate(lines) for bit in range(6) if line.get(i, 0) >> bit & 1])
        cell_write(i, 'result_write')
    text = text.replace('begin\n', f'  signal score_select,score_write:bit_vector(1 downto 0);signal result_is,result_select:bit_vector({len(RESULTS) - 1} downto 0);signal result_write:bit;\n' + 'begin\n', 1)
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
