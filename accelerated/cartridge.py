"""External program cartridge: a separate ROM module the CPU can execute from.

The CPU's firmware ROM is baked into its gates.  A cartridge is a second ROM, held in its own
blueprint, that answers the CPU's fetch address with the pre-decoded instruction fields the baked
ROM would have produced.  Execution switches to it by jumping into the cartridge address window.

    CPU -> cartridge   cart_address (10)            program counter bits 11..2
    cartridge -> CPU   cart_ra/rb/rd (4 each)       register numbers
                       cart_imm (12)                sign-extended by the CPU
                       cart_target (10)             branch / jal target, bits 11..2
                       cart_f3 (3), cart_aluop (3)
                       cart_legal, load, store, branch, jal, jalr, immediate, compare, static (8)

Everything else the baked decoder knows is zero for those rows, so a cartridge cannot use LUI/AUIPC,
shifts, ECALL or EBREAK.  The register-width and value-range analyses still run over the
cartridge image, so the CPU is specialised for the registers a given cartridge writes; the image
must be the one the CPU was built with.  With no cartridge attached the fields read zero, the
first fetch there is an illegal instruction and the CPU traps.
"""
from contextlib import contextmanager
from unittest.mock import patch
import re
from accelerated import compact_core
from accelerated.hdl import compile_source

BASE = 0x800                    # byte address of the first cartridge instruction
WORDS = 512                     # cartridge window: 512 words, PC bits 11..2 with bit 11 set
ADDRESS_BITS = 10

# field name -> width, in the order the interface is documented and laid out
FIELDS = dict(ra=4, rb=4, rd=4, imm=12, target=10, f3=3, aluop=3, legal=1, load=1, store=1, branch=1, jal=1, jalr=1, immediate=1, compare=1, static=1)


def combined_image(firmware, cartridge):
    """Firmware padded to the cartridge window, followed by the cartridge."""
    assert len(firmware) <= BASE, f'firmware ({len(firmware)} bytes) overlaps the cartridge window at {BASE:#x}'
    assert len(cartridge) % 4 == 0 and 0 < len(cartridge) <= WORDS * 4
    return firmware + bytes(BASE - len(firmware)) + cartridge


def rows(cartridge):
    """Per-word field values of the cartridge, checked against what the interface can carry."""
    image = combined_image(b'', cartridge)
    result = []
    for r in compact_core.analysis_metadata(image)[BASE // 4:BASE // 4 + len(cartridge) // 4]:
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
        target = r['target'] if r['branch'] or r['jal'] else 0
        if target and not BASE <= target < BASE + WORDS * 4:
            raise ValueError(f'branch target {target:#x} leaves the cartridge window: {where}')
        result.append(dict(ra=r['ra'], rb=r['rb'], rd=r['rd'], imm=low, target=target >> 2 & 1023, f3=r['f3'], aluop=r['aluop'], legal=1, load=r['load'], store=r['store'], branch=r['branch'], jal=r['jal'], jalr=r['jalr'], immediate=r['immediate'], compare=r['compare'], static=int(bool((r['load'] or r['store']) and r['ra'] == 0))))
    return result


def source(cartridge):
    """HDL of the cartridge module."""
    data = rows(cartridge)
    ports = ';'.join(f'cart_{name}:out bit_vector({width - 1} downto 0)' if width > 1 else f'cart_{name}:out bit' for name, width in FIELDS.items())
    lines = [f'entity rv_cartridge is\n  port(cart_address:in bit_vector({ADDRESS_BITS - 1} downto 0); {ports});\nend;',
             'architecture gates of rv_cartridge is',
             f'  signal select_row:bit_vector({len(data) - 1} downto 0);',
             f'  signal word_hi:bit_vector({(len(data) - 1) // 32} downto 0);signal word_lo:bit_vector(31 downto 0);',
             'begin']
    for i in range(32):
        lines.append(f'  word_lo({i}) <= cart_address(4 downto 0) = bits({i},5);')
    for i in range((len(data) - 1) // 32 + 1):
        lines.append(f'  word_hi({i}) <= cart_address(9 downto 5) = bits({16 + i},5);')
    for i in range(len(data)):
        lines.append(f'  select_row({i}) <= word_hi({i // 32}) and word_lo({i % 32});')
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

CPU_PORTS = 'cart_ra,cart_rb,cart_rd:in bit_vector(3 downto 0);cart_imm:in bit_vector(11 downto 0);cart_target:in bit_vector(9 downto 0);cart_f3,cart_aluop:in bit_vector(2 downto 0);cart_legal,cart_load,cart_store,cart_branch,cart_jal,cart_jalr,cart_immediate,cart_compare,cart_static:in bit;cart_address:out bit_vector(9 downto 0);'


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
    for name, width in (('ra', 4), ('rb', 4), ('rd', 4), ('f3', 3), ('aluop', 3)):
        for bit in range(width):
            add('d_' + name, bit, f'cart_{name}({bit})')
    for bit in range(32):
        add('d_imm', bit, f'cart_imm({min(bit, 11)})')
    for bit in range(2, 12):
        add('d_target', bit, f'cart_target({bit - 2})')
    for name, field in flags.items():
        add('d_' + name, None, 'cart_' + field)
    for r in range(1, 16):
        for key, field in (('a', 'ra'), ('b', 'rb')):
            pattern = re.compile(rf'\b(select_{key}\({r}\) <= )([^;]*);')
            assert len(pattern.findall(text)) == 1, (key, r, len(pattern.findall(text)))
            text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or (cart_{field} = bits({r},4));', text)
        pattern = re.compile(rf'\b(select_write\({r}\) <= )([^;]*);')
        assert len(pattern.findall(text)) == 1, ('write', r, len(pattern.findall(text)))
        text = pattern.sub(lambda m: f'{m[1]}({m[2]}) or ((cart_rd = bits({r},4)) and execute_ok);', text)
    old = '  region_ram <= address(31 downto 6) = bits(64,26);'
    assert text.count(old) == 1
    # the cartridge addresses the 64-byte RAM at 0..63, the firmware at 4096..4159
    text = text.replace(old, '  region_ram <= (address(31 downto 6) = bits(64,26)) or (address(31 downto 6) = bits(0,26));')
    return text.rsplit('end;', 1)[0] + f'  cart_address <= pc_q({ADDRESS_BITS + 1} downto 2);\nend;\n'


@contextmanager
def cpu_text_hook():
    """Apply cpu_source to the HDL the CPU generator is about to compile."""
    original = compact_core.compile_source

    def hooked(text, *args, **kwargs):
        return original(cpu_source(text), *args, **kwargs)
    with patch.object(compact_core, 'compile_source', hooked):
        yield


@contextmanager
def window(enabled=True):
    """Hide the cartridge rows from the baked decoder while a CPU is generated."""
    previous = compact_core.CART
    compact_core.CART = (BASE, BASE + WORDS * 4) if enabled else None
    try:
        yield
    finally:
        compact_core.CART = previous
