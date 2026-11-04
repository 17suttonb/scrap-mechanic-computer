import ast
from dataclasses import dataclass
import re
MASK = 4294967295
ALIASES = {'zero': 0, 'ra': 1, 'sp': 2, 'gp': 3, 'tp': 4, 't0': 5, 't1': 6, 't2': 7, 's0': 8, 'fp': 8, 's1': 9, 'a0': 10, 'a1': 11, 'a2': 12, 'a3': 13, 'a4': 14, 'a5': 15}
REG = {'add': (0, 0), 'sub': (0, 32), 'sll': (1, 0), 'slt': (2, 0), 'sltu': (3, 0), 'xor': (4, 0), 'srl': (5, 0), 'sra': (5, 32), 'or': (6, 0), 'and': (7, 0)}
IMM = {'addi': 0, 'slti': 2, 'sltiu': 3, 'xori': 4, 'ori': 6, 'andi': 7}
BRANCH = {'beq': 0, 'bne': 1, 'blt': 4, 'bge': 5, 'bltu': 6, 'bgeu': 7}
LOAD = {'lb': 0, 'lh': 1, 'lw': 2, 'lbu': 4, 'lhu': 5}
STORE = {'sb': 0, 'sh': 1, 'sw': 2}

def signed(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & 1 << bits - 1 else value

def reg(text):
    text = text.strip().lower()
    value = ALIASES.get(text)
    if value is None and re.fullmatch('x\\d+', text):
        value = int(text[1:])
    if value is None or not 0 <= value < 16:
        raise ValueError(f'RV32E register must be x0..x15: {text}')
    return value

def expression(text, labels):

    def walk(n):
        if isinstance(n, ast.Constant) and type(n.value) is int:
            return n.value
        if isinstance(n, ast.Name):
            return labels[n.id.lower()]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return walk(n.operand) * (-1 if isinstance(n.op, ast.USub) else 1)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
            return walk(n.left) + walk(n.right) * (-1 if isinstance(n.op, ast.Sub) else 1)
        raise ValueError('Expressions allow integers, labels, + and - only')
    return walk(ast.parse(text, mode='eval').body)

def immediate(value, bits, alignment=1):
    if value % alignment or not -(1 << bits - 1) <= value < 1 << bits - 1:
        raise ValueError(f'Immediate {value} must fit signed {bits} bits, aligned to {alignment}')
    return value & (1 << bits) - 1

def rword(op, rd, rs1, rs2):
    f3, f7 = REG[op]
    return f7 << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | rd << 7 | 51

def iword(opcode, rd, rs1, imm, f3=0):
    return (imm & 4095) << 20 | rs1 << 15 | f3 << 12 | rd << 7 | opcode

def bword(f3, rs1, rs2, offset):
    u = immediate(offset, 13, 2)
    return (u >> 12 & 1) << 31 | (u >> 5 & 63) << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | (u >> 1 & 15) << 8 | (u >> 11 & 1) << 7 | 99

def jword(rd, offset):
    u = immediate(offset, 21, 2)
    return (u >> 20 & 1) << 31 | (u >> 1 & 1023) << 21 | (u >> 11 & 1) << 20 | (u >> 12 & 255) << 12 | rd << 7 | 111

@dataclass
class Program:
    image: bytes
    labels: dict
    listing: list
    source: str

def assemble(source, compact_li=False, symbols=None):
    """symbols: names already defined, such as data addresses, for use in expressions."""
    labels = dict(symbols or {})
    records = []
    pc = 0
    extent = 0
    for number, raw in enumerate(source.splitlines(), 1):
        line = re.split('[;#]', raw, maxsplit=1)[0].strip()
        if not line:
            continue
        if ':' in line:
            name, line = line.split(':', 1)
            name = name.strip().lower()
            line = line.strip()
            if not re.fullmatch('[a-z_][a-z_0-9]*', name) or name in labels:
                raise ValueError(f'Line {number}: invalid/duplicate label')
            labels[name] = pc
            if not line:
                continue
        parts = line.split(None, 1)
        op = parts[0].lower()
        args = [s.strip() for s in parts[1].split(',')] if len(parts) > 1 else []
        if op == '.org':
            pc = expression(args[0], labels)
            if pc < 0 or pc % 4:
                raise ValueError('.org must be a nonnegative word-aligned address')
            continue
        count = 2 if op in ('li', 'call') else len(args) if op == '.word' else 1
        if compact_li and op == 'li' and len(args) == 2 and re.fullmatch(r'\d+', args[1].strip()) and int(args[1]) & 4095 == 0:
            count = 1
        records.append((pc, number, op, args, line))
        pc += 4 * count
        extent = max(extent, pc)
        if pc > 65536:
            raise ValueError('Assembly image exceeds 64 KiB')
    if not records or not extent:
        raise ValueError('Empty program')
    image = bytearray(b'\x13\x00\x00\x00' * (extent // 4))
    used = set()
    listing = []
    for pc, number, op, args, line in records:
        try:
            ev = lambda x: expression(x, labels)
            if op == '.word':
                words = [ev(x) & MASK for x in args]
            elif op == 'li':
                rd = reg(args[0])
                value = ev(args[1])
                if not -(1 << 31) <= value <= MASK:
                    raise ValueError('LI value must fit 32 bits')
                value &= MASK
                high = value + 2048 >> 12 & 1048575
                low = signed(value, 12)
                words = [high << 12 | rd << 7 | 55, iword(19, rd, rd, low)]
                if compact_li and low == 0 and len(args) == 2 and re.fullmatch(r'\d+', args[1].strip()):
                    words = words[:1]
            elif op == 'call':
                delta = ev(args[0]) - pc
                high = delta + 2048 >> 12
                low = delta - (high << 12)
                words = [(high & 1048575) << 12 | 1 << 7 | 23, iword(103, 1, 1, low)]
            elif op == 'nop':
                words = [iword(19, 0, 0, 0)]
            elif op == 'mv':
                words = [iword(19, reg(args[0]), reg(args[1]), 0)]
            elif op == 'ret':
                words = [iword(103, 0, 1, 0)]
            elif op == 'j':
                words = [jword(0, ev(args[0]) - pc)]
            elif op in REG:
                words = [rword(op, reg(args[0]), reg(args[1]), reg(args[2]))]
            elif op in IMM:
                words = [iword(19, reg(args[0]), reg(args[1]), immediate(ev(args[2]), 12), IMM[op])]
            elif op in ('slli', 'srli', 'srai'):
                shift = ev(args[2])
                if not 0 <= shift < 32:
                    raise ValueError('Shift must be 0..31')
                words = [iword(19, reg(args[0]), reg(args[1]), shift | (1024 if op == 'srai' else 0), 1 if op == 'slli' else 5)]
            elif op in ('lui', 'auipc'):
                value = ev(args[1])
                if not -(1 << 19) <= value < 1 << 20:
                    raise ValueError('Upper immediate must fit 20 bits')
                words = [(value & 1048575) << 12 | reg(args[0]) << 7 | (55 if op == 'lui' else 23)]
            elif op in BRANCH:
                words = [bword(BRANCH[op], reg(args[0]), reg(args[1]), ev(args[2]) - pc)]
            elif op == 'jal':
                rd, target = (1, args[0]) if len(args) == 1 else (reg(args[0]), args[1])
                words = [jword(rd, ev(target) - pc)]
            elif op in LOAD or op in STORE or op == 'jalr':
                match = re.fullmatch('(.+)\\(([^()]+)\\)', args[1])
                if not match:
                    raise ValueError('Expected offset(register)')
                imm = immediate(ev(match[1]), 12)
                base = reg(match[2])
                r = reg(args[0])
                if op in LOAD:
                    words = [iword(3, r, base, imm, LOAD[op])]
                elif op == 'jalr':
                    words = [iword(103, r, base, imm)]
                else:
                    words = [imm >> 5 << 25 | r << 20 | base << 15 | STORE[op] << 12 | (imm & 31) << 7 | 35]
            elif op == 'sbi':
                # A cartridge's store of a constant byte: sbi value, offset. Custom-0 opcode, one row, no register.
                value, offset = ev(args[0]), ev(args[1])
                if not 0 <= value < 256:
                    raise ValueError('SBI value must fit a byte')
                words = [immediate(offset, 12) << 20 | value << 12 | 11]
            elif op in ('ecall', 'ebreak', 'fence'):
                words = [{'ecall': 115, 'ebreak': 1048691, 'fence': 267386895}[op]]
            else:
                raise ValueError(f'Unknown instruction {op}')
            for i, word in enumerate(words):
                at = pc + 4 * i
                if at in used:
                    raise ValueError('Overlapping assembly addresses')
                used.add(at)
                image[at:at + 4] = (word & MASK).to_bytes(4, 'little')
                listing.append(f"{at:08X}  {word & MASK:08X}  {(line if i == 0 else '')}")
        except (ValueError, KeyError, IndexError, SyntaxError) as error:
            raise ValueError(f'ASM line {number}: {error}') from None
    return Program(bytes(image), labels, listing, source)
