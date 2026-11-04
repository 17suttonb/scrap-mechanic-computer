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


class Machine:
    """RV32E with the cartridge's view of the machine, in the state the firmware leaves on entry:
    board cleared, wall bytes in columns 12 and 15, arguments in a0..a5.  `keys` are single keys delivered one at a time."""

    def __init__(self, image, base, keys=()):
        self.image, self.base = image, base
        self.ram = bytearray(64)
        for row in range(4):
            self.ram[row * 16 + 12] = self.ram[row * 16 + 15] = 3
        self.x = [0] * 32
        self.x[10:16] = [21, 20, 1, 38, 2, 4]          # the arguments the firmware passes
        self.pc = base
        self.keys = list(keys)
        self.retired = 0
        self.stop = False

    def screen(self):
        """What the console shows: RAM cells through the display map."""
        glyph = lambda v: '.' if v == 0 else '+' if v == 2 else 'Q' if v == 4 else 'O'
        return [''.join(glyph(self.ram[r * 16 + c]) for c in range(12)) for r in range(4)]

    def load(self, a, size, signed, static=False):
        a &= M32
        if not static:
            a &= 63
        if a >= 0xFFFFFF00:
            off = a - 0x100000000
            if off == -240:
                v = 0x80 if self.keys else 0
            elif off == -236:
                v = ord(self.keys.pop(0)) if self.keys else 0
            else:
                raise AssertionError(f'load from unmapped MMIO {off}')
        else:
            assert a + size <= len(self.ram), f'RAM load out of range {a:#x} at pc {self.pc:#x}'
            v = int.from_bytes(self.ram[a:a + size], 'little')
        return sx(v, size * 8) & M32 if signed else v & ((1 << size * 8) - 1)

    def store(self, a, size, v):
        a &= 63
        self.ram[a:a + size] = (v & ((1 << size * 8) - 1)).to_bytes(size, 'little')

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
            res = self.load(a + d['i'], [1, 2, 4, 0, 1, 2][f3], f3 < 3, d['ra'] == 0)
        elif op == 35:
            self.store(a + d['s'], 1 << f3, b)
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
