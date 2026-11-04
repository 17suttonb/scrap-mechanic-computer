"""A host model of a whole cartridge: firmware's memory map, data port, modes and direct screen writes, with its time
counted in the rows the cartridge really folds to. BASIC's console line is not modelled: only programs in tap or
board mode run here. Board mode's memory map is rvtools.Machine's."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.platform == 'win32' and sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32:
    sys.path.insert(0, str(ROOT / 'vendor'))
from accelerated import cartridge as cart  # noqa: E402
from rvtools import M32, decode, sx  # noqa: E402

UPDATES_PER_CYCLE = 33
UPDATES_PER_SECOND = 40


class Computer:
    def __init__(self, image, entry=None):
        self.folded = cart.rows(image)
        self.data = cart.relocated_data(image, self.folded.new_pc)
        self.code = image.code
        self.starts = {cart.BASE + owner * 4 for owner in self.folded.owner}
        self.pc = cart.BASE if entry is None else entry
        self.x = [0] * 32
        self.ram = bytearray(64)
        self.screen = [[' '] * 12 for _ in range(8)]
        self.cycles = 0
        self.mode = 1
        self.spin = False

    def seconds(self):
        return self.cycles * UPDATES_PER_CYCLE / UPDATES_PER_SECOND

    def text(self):
        return [''.join(row).rstrip() for row in self.screen]

    def load(self, address, size, signed, static):
        address &= M32
        if 0 <= address < 64:
            value = int.from_bytes(self.ram[address:address + size], 'little')
        elif cart.DATA_BASE <= address < cart.DATA_BASE + cart.DATA_BYTES and size == 1 and not static:
            offset = address - cart.DATA_BASE
            value = self.data[offset] if offset < len(self.data) else 0
        else:
            raise AssertionError(f'load from {address:#x} at {self.pc:#x}')
        return sx(value, size * 8) & M32 if signed else value

    def store(self, address, size, value):
        address &= M32
        if address + size <= 64:
            self.ram[address:address + size] = (value & ((1 << size * 8) - 1)).to_bytes(size, 'little')
        elif address >= M32 - 511 and size == 1:
            cell = address - (cart.SCREEN_BASE & M32)
            assert cell < 128 and cell % 16 < 12, f'screen store to {cell} at {self.pc:#x}'
            code = value & 127
            assert 32 <= code < 96, f'screen store of {code} at {self.pc:#x}'
            self.screen[cell // 16][cell % 16] = chr(code)
        else:
            raise AssertionError(f'store to {address:#x} at {self.pc:#x}')

    def step(self):
        word = int.from_bytes(self.code[self.pc - cart.BASE:self.pc - cart.BASE + 4], 'little')
        d = decode(word)
        op, f3, x = d['op'], d['f3'], self.x
        a, b = x[d['ra']], x[d['rb']]
        npc = self.pc + 4
        result = None
        if self.pc in self.starts:
            self.cycles += 1
            if (op == 3 and d['ra']) or (op == 35 and d['ra'] and d['s']):
                self.cycles += 1
        if op == 11:
            self.store(sx(word >> 20, 12) & M32, 1, word >> 12 & 255)
        elif op in (51, 19):
            other = b if op == 51 else d['i'] & M32
            assert f3 not in (1, 5), f'shift at {self.pc:#x}'
            result = {0: a - other if op == 51 and d['f7'] == 32 else a + other, 2: int(sx(a, 32) < sx(other, 32)), 3: int(a < other), 4: a ^ other, 6: a | other, 7: a & other}[f3]
        elif op == 3:
            result = self.load(a + d['i'], [1, 2, 4, 0, 1, 2][f3], f3 < 3, d['ra'] == 0)
        elif op == 35:
            self.store(a + d['s'], 1 << f3, b)
        elif op == 99:
            if {0: a == b, 1: a != b, 4: sx(a, 32) < sx(b, 32), 5: sx(a, 32) >= sx(b, 32), 6: a < b, 7: a >= b}[f3]:
                npc = self.pc + d['b']
        elif op == 111:
            result, npc = self.pc + 4, self.pc + d['j']
        elif op == 103:
            result, npc = self.pc + 4, (a + d['i']) & ~1
        else:
            raise AssertionError(f'illegal instruction {word:#010x} at {self.pc:#x}')
        if result is not None and d['rd']:
            x[d['rd']] = result & M32
        self.spin = npc == self.pc
        self.pc = npc & M32
