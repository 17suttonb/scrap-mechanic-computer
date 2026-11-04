from collections import defaultdict
from accelerated.compact_core import metadata as original_metadata, analysis_metadata
from accelerated.async_bus import replace_once

def metadata(image):
    rows = original_metadata(image)
    initial = rows[0] if rows else {}
    base_register = initial.get('rd', 0) if initial.get('lui') else 0
    base = initial.get('imm', 0)
    fixed_base = base_register and all((r['rd'] != base_register for r in rows[1:]))
    for r in rows:
        if fixed_base and r['ra'] == base_register and (r['load'] or r['store']):
            r['static_memory'] = 1
            r['imm'] = base + r['imm'] & 4294967295
        if not (r['branch'] or r['jal']):
            r['target'] = 0
        if not r['immediate']:
            r['imm'] = 0
    return rows

def zero_bits(image):
    writes = defaultdict(list)
    rows = analysis_metadata(image)
    initial = rows[0] if rows else {}
    fixed = initial.get('rd', 0) if initial.get('lui') else 0
    if any((r['rd'] == fixed for r in rows[1:])):
        fixed = 0
    for r in rows:
        if not r['rd'] or not r['legal']:
            continue
        w = r['word']
        op = w & 127
        ra = w >> 15 & 31
        f3 = w >> 12 & 7
        mask = 4294967295
        if op == 55:
            mask = w & 4294963200
        elif op in (111, 103):
            mask = r['pc'] + 4
        elif op == 19 and ra == 0:
            imm = w >> 20 & 4095
            imm = imm - 4096 if imm & 2048 else imm
            if f3 == 0:
                mask = imm & 4294967295
            elif f3 in (4, 6):
                mask = imm & 4294967295
            elif f3 in (1, 5, 7):
                mask = 0
            elif f3 == 2:
                mask = int(0 < imm)
            elif f3 == 3:
                mask = int(0 < imm & 4294967295)
        elif fixed and op == 19 and (ra == fixed) and (f3 == 0):
            mask = initial['imm'] + r['imm'] & 4294967295
        elif op == 3 and f3 == 4:
            mask = 255
        elif op == 3 and f3 == 5:
            mask = 65535
        elif op in (19, 51) and f3 in (2, 3):
            mask = 1
        elif op == 19 and f3 == 7:
            mask = r['imm']
        writes[r['rd']].append(mask)
    return {r: sum((1 << b for b in range(32) if any((v >> b & 1 for v in writes[r])))) for r in range(1, 16)}

def source(text, image):
    lines = text.splitlines()
    for bit in range(32):
        prefix = f'  d_link({bit}) <='
        i = next((i for i, s in enumerate(lines) if s.startswith(prefix)))
        expr = f'pc_q({bit})' if bit < 2 else 'not pc_q(2)' if bit == 2 else f'pc_q({bit}) xor and_reduce(pc_q({bit - 1} downto 2))'
        lines[i] = prefix + ' ' + expr + ';'
    text = '\n'.join(lines)
    masks = zero_bits(image)
    for r, mask in masks.items():
        if mask == 4294967295:
            continue
        old = f'  regs({r}) <= latch(result_stage,com_en and write_stage({r}),reset);'
        bits = [b for b in range(32) if mask >> b & 1]
        if not bits:
            new = f'  regs({r}) <= bits(0,32);'
        else:
            name = f'reg_sparse_{r}'
            width = len(bits)
            text = replace_once(text, '  signal regs:registers_t;', f'  signal {name}:bit_vector({width - 1} downto 0);\n  signal regs:registers_t;')
            mapping = {bit: j for j, bit in enumerate(bits)}
            data = ' & '.join((f'result_stage({b})' for b in reversed(bits)))
            new = f'  {name} <= latch({data},com_en and write_stage({r}),reset);\n'
            new += f'  regs({r}) <= ' + ' & '.join((f'{name}({mapping[b]})' if b in mapping else "'0'" for b in range(31, -1, -1))) + ';'
        text = replace_once(text, old, new)
    return (text, masks)
