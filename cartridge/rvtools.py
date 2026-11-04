"""Tiny RV32E helpers for the cartridge: ELF .text extraction, disassembler and an
instruction-set simulator with the console's memory map, used to test the game on the host."""
import struct

ABI = ['zero', 'ra', 'sp', 'gp', 'tp', 't0', 't1', 't2', 's0', 's1', 'a0', 'a1', 'a2', 'a3', 'a4', 'a5']
M32 = 0xFFFFFFFF


def elf_text(path):
    """Return (load address, bytes) of the flat image of all allocated PROGBITS sections."""
    d = open(path, 'rb').read()
    assert d[:4] == b'\x7fELF' and d[4] == 1
    shoff, = struct.unpack_from('<I', d, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2E)
    secs = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    alloc = [s for s in secs if s[1] == 1 and s[2] & 2 and s[5]]
    base = min(s[3] for s in alloc)
    top = max(s[3] + s[5] for s in alloc)
    image = bytearray(top - base)
    for s in alloc:
        image[s[3] - base:s[3] - base + s[5]] = d[s[4]:s[4] + s[5]]
    return base, bytes(image)


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def decode(w):
    op = w & 127
    rd, f3, ra, rb, f7 = w >> 7 & 31, w >> 12 & 7, w >> 15 & 31, w >> 20 & 31, w >> 25
    d = dict(op=op, rd=rd, f3=f3, ra=ra, rb=rb, f7=f7, word=w)
    d['i'] = sx(w >> 20, 12)
    d['s'] = sx(w >> 25 << 5 | w >> 7 & 31, 12)
    d['b'] = sx(w >> 31 << 12 | (w >> 7 & 1) << 11 | (w >> 25 & 63) << 5 | (w >> 8 & 15) << 1, 13)
    d['u'] = w & 0xFFFFF000
    d['j'] = sx(w >> 31 << 20 | (w >> 12 & 255) << 12 | (w >> 20 & 1) << 11 | (w >> 21 & 1023) << 1, 21)
    return d


def disasm(w, pc=0):
    d = decode(w)
    op, f3, r = d['op'], d['f3'], ABI
    if op == 51:
        n = {(0, 0): 'add', (0, 32): 'sub', (1, 0): 'sll', (2, 0): 'slt', (3, 0): 'sltu', (4, 0): 'xor', (5, 0): 'srl', (5, 32): 'sra', (6, 0): 'or', (7, 0): 'and'}[(f3, d['f7'])]
        return f"{n} {r[d['rd']]},{r[d['ra']]},{r[d['rb']]}"
    if op == 19:
        if f3 in (1, 5):
            n = 'slli' if f3 == 1 else 'srai' if d['f7'] else 'srli'
            return f"{n} {r[d['rd']]},{r[d['ra']]},{d['rb']}"
        n = {0: 'addi', 2: 'slti', 3: 'sltiu', 4: 'xori', 6: 'ori', 7: 'andi'}[f3]
        return f"{n} {r[d['rd']]},{r[d['ra']]},{d['i']}"
    if op == 3:
        return f"{['lb', 'lh', 'lw', '?', 'lbu', 'lhu'][f3]} {r[d['rd']]},{d['i']}({r[d['ra']]})"
    if op == 35:
        return f"{['sb', 'sh', 'sw'][f3]} {r[d['rb']]},{d['s']}({r[d['ra']]})"
    if op == 99:
        return f"{['beq', 'bne', '?', '?', 'blt', 'bge', 'bltu', 'bgeu'][f3]} {r[d['ra']]},{r[d['rb']]},{hex(pc + d['b'])}"
    if op == 111:
        return f"jal {r[d['rd']]},{hex(pc + d['j'])}"
    if op == 103:
        return f"jalr {r[d['rd']]},{d['i']}({r[d['ra']]})"
    if op == 55:
        return f"lui {r[d['rd']]},{hex(d['u'] >> 12)}"
    if op == 23:
        return f"auipc {r[d['rd']]},{hex(d['u'] >> 12)}"
    return f'.word {w:#010x}'


COLUMNS, ROWS, STRIDE, RAM_ROWS = 12, 7, 16, 4
STEER = -234
STEPS = {'W': -16, 'S': 16, 'A': -1, 'D': 1}


class Machine:
    """RV32E with the cartridge's view of the machine, in the state the firmware leaves on entry: the
    64-byte RAM and the board rows below it are clear, and no registers are set. `keys` are single keys,
    delivered in order."""

    def __init__(self, image, base, keys=()):
        self.image, self.base = image, base
        self.ram = bytearray(64)            # statically addressed bytes, and board rows 0..3
        self.extra = bytearray((ROWS - RAM_ROWS) * COLUMNS)
        self.x = [0] * 32
        self.pc = base
        self.keys = list(keys)
        self.taken = 0
        self.retired = 0
        self.stop = False

    def cell(self, address):
        """Where a register-addressed access lands: ('ram', i), ('extra', i), or None for a wall."""
        address &= 127
        row, column = address // STRIDE, address % STRIDE
        if row >= ROWS or column >= COLUMNS:
            return None
        return ('ram', address) if row < RAM_ROWS else ('extra', (row - RAM_ROWS) * COLUMNS + column)

    def read_cell(self, address):
        where = self.cell(address)
        return 3 if where is None else self.ram[where[1]] if where[0] == 'ram' else self.extra[where[1]]

    def screen(self):
        """What the console shows: board cells through the display map."""
        glyph = lambda v: '.' if v == 0 else '+' if v == 2 else 'Q' if v == 4 else 'O'
        return [''.join(glyph(self.read_cell(r * STRIDE + c)) for c in range(COLUMNS)) for r in range(ROWS)]

    def score(self):
        """The two digits on the score line."""
        return f'{self.ram[13]}{self.ram[14]}'

    def result(self):
        """The result, on the bottom row."""
        return {5: 'YOU LOST', 6: 'YOU WON'}.get(self.ram[29], '')

    def steer(self, step, position):
        """The steering row: take a waiting key, and return the step for one cell in the new direction."""
        step, position = sx(step, 32), position & 127
        horizontal = bool(step & 1)
        negative = bool(step >> (1 if horizontal else 6) & 1)
        if self.keys:
            key = self.keys.pop(0)
            self.taken += 1
            if key in STEPS:
                new = STEPS[key]
                if not (bool(new & 1) == horizontal and (new < 0) != negative):
                    horizontal, negative = bool(new & 1), new < 0
        row, column = position // STRIDE, position % STRIDE
        if horizontal:
            new = (COLUMNS - 1 if column == 0 else -1) if negative else (1 - COLUMNS if column == COLUMNS - 1 else 1)
        else:
            new = ((ROWS - 1) * STRIDE if row == 0 else -STRIDE) if negative else ((1 - ROWS) * STRIDE if row == ROWS - 1 else STRIDE)
        return new & M32

    def load(self, a, size, signed, static=False):
        a &= M32
        if static and a >= 0xFFFFFF00:
            off = a - 0x100000000
            if off == -240:
                v = 0x80 if self.keys else 0
            elif off == -236:
                v = ord(self.keys.pop(0)) if self.keys else 0
            else:
                raise AssertionError(f'load from unmapped MMIO {off}')
        elif static:
            assert a + size <= len(self.ram), f'RAM load out of range {a:#x} at pc {self.pc:#x}'
            v = int.from_bytes(self.ram[a:a + size], 'little')
        else:
            assert size == 1, f'wide board load at pc {self.pc:#x}'
            v = self.read_cell(a)
        return sx(v, size * 8) & M32 if signed else v & ((1 << size * 8) - 1)

    def store(self, a, size, v, static=False):
        a &= M32
        if static:
            assert a + size <= len(self.ram), f'RAM store out of range {a:#x} at pc {self.pc:#x}'
            self.ram[a:a + size] = (v & ((1 << size * 8) - 1)).to_bytes(size, 'little')
            return
        assert size == 1, f'wide board store at pc {self.pc:#x}'
        where = self.cell(a)
        if where:
            (self.ram if where[0] == 'ram' else self.extra)[where[1]] = v & 255

    def step(self):
        i = (self.pc - self.base) // 4
        assert self.pc % 4 == 0 and 0 <= i < len(self.image) // 4, f'pc out of cartridge: {self.pc:#x}'
        w = int.from_bytes(self.image[i * 4:i * 4 + 4], 'little')
        d = decode(w)
        op, f3, x = d['op'], d['f3'], self.x
        a, b = x[d['ra']], x[d['rb']]
        npc = self.pc + 4
        res = None
        if op == 51 or op == 19:
            bb = b if op == 51 else d['i'] & M32
            sub = op == 51 and d['f7'] == 32 and f3 == 0
            sh = bb & 31
            res = {0: lambda: (a - bb if sub else a + bb), 1: lambda: a << sh, 2: lambda: int(sx(a, 32) < sx(bb, 32)), 3: lambda: int(a < bb),
                   4: lambda: a ^ bb, 5: lambda: (sx(a, 32) >> sh) if d['f7'] == 32 else a >> sh, 6: lambda: a | bb, 7: lambda: a & bb}[f3]()
        elif op == 3:
            if d['ra'] == 0 and d['i'] == STEER:
                # the marker in front of the steering load names the position register
                marker = decode(int.from_bytes(self.image[i * 4 - 4:i * 4], 'little'))
                assert marker['op'] == 51 and marker['rd'] == 0, f'steering load without its marker at {self.pc:#x}'
                res = self.steer(x[d['rd']], x[marker['ra']])
            else:
                res = self.load(a + d['i'], [1, 2, 4, 0, 1, 2][f3], f3 < 3, d['ra'] == 0)
        elif op == 35:
            self.store(a + d['s'], 1 << f3, b, d['ra'] == 0)
        elif op == 99:
            t = {0: a == b, 1: a != b, 4: sx(a, 32) < sx(b, 32), 5: sx(a, 32) >= sx(b, 32), 6: a < b, 7: a >= b}[f3]
            if t:
                npc = self.pc + d['b']
        elif op == 111:
            res, npc = self.pc + 4, self.pc + d['j']
        elif op == 103:
            res, npc = self.pc + 4, (a + d['i']) & ~1
        elif op == 55:
            res = d['u']
        elif op == 23:
            res = self.pc + d['u']
        else:
            raise AssertionError(f'illegal instruction {w:#010x} at {self.pc:#x}')
        if res is not None and d['rd']:
            x[d['rd']] = res & M32
        if npc == self.pc:
            self.stop = True
        self.pc = npc & M32
        self.retired += 1


if __name__ == '__main__':
    import sys
    base, img = elf_text(sys.argv[1])
    for i in range(0, len(img), 4):
        w = int.from_bytes(img[i:i + 4], 'little')
        print(f'{base + i:04x}: {w:08x}  {disasm(w, base + i)}')
