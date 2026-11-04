from copy import deepcopy
from unittest.mock import patch
from accelerated.async_bus import replace_once
from accelerated.compact_core import analysis_metadata as metadata
from accelerated.rom_specialize import zero_bits
from accelerated.cpu_components import text_sram_source as base_source, text_sram_cpu as base_cpu

def layouts(image, flow=False):
    rows = metadata(image)
    masks = zero_bits(image)
    result = {}
    analysis = None
    if flow:
        from accelerated.rom_ranges import analyze
        analysis = analyze(image)
        assert analysis['passed'], 'ROM address proof failed'
    for r in range(1, 16):
        finite = analysis['values'][r] if analysis is not None else None
        if masks[r] != 4294967295 and finite is None:
            continue
        writes = [x for x in rows if x['rd'] == r and x['legal']]
        signatures = []
        for bit in range(32):
            if finite is not None:
                signatures.append(tuple((v >> bit & 1 for v in sorted(finite))))
                continue
            signature = []
            for row in writes:
                w = row['word']
                op = w & 127
                f3 = w >> 12 & 7
                ra = w >> 15 & 31
                if op in (111, 103):
                    v = row['pc'] + 4 >> bit & 1
                elif op == 55:
                    v = (w & 4294963200) >> bit & 1
                elif op == 3:
                    width = {0: 8, 1: 16, 2: 32, 4: 8, 5: 16}[f3]
                    v = 0 if bit >= width and f3 in (4, 5) else ('load', min(bit, width - 1))
                elif op == 19 and ra == 0:
                    imm = w >> 20 & 4095
                    imm -= 4096 if imm & 2048 else 0
                    value = imm & 4294967295 if f3 in (0, 4, 6) else int(0 < imm) if f3 == 2 else int(0 < imm & 4294967295) if f3 == 3 else 0
                    v = value >> bit & 1
                elif op in (19, 51) and f3 in (2, 3):
                    v = ('compare', 0) if bit == 0 else 0
                elif op == 19 and f3 == 7 and (not row['imm'] >> bit & 1):
                    v = 0
                else:
                    v = ('independent', bit)
                signature.append(v)
            signatures.append(tuple(signature))
        groups = {}
        mapping = []
        representatives = []
        for bit, sig in enumerate(signatures):
            if all((v == 0 for v in sig)):
                mapping.append(None)
                continue
            if sig not in groups:
                groups[sig] = len(groups)
                representatives.append(bit)
            mapping.append(groups[sig])
        if len(groups) < masks[r].bit_count():
            result[r] = dict(mapping=mapping, representatives=representatives, width=len(groups))
    return result

def source(image, ram_bytes=32, *, flow=False, **options):
    text = base_source(image, ram_bytes, **options)
    masks = zero_bits(image)
    for r, layout in layouts(image, flow).items():
        reps = layout['representatives']
        name = f'reg_equiv_{r}'
        text = replace_once(text, '  signal regs:registers_t;', f'  signal {name}:bit_vector({len(reps) - 1} downto 0);\n  signal regs:registers_t;')
        data = ' & '.join((f'result_stage({b})' for b in reversed(reps)))
        new = f'  {name} <= latch({data},com_en and write_stage({r}),reset);\n'
        new += f'  regs({r}) <= ' + ' & '.join(("'0'" if index is None else f'{name}({index})' for index in reversed(layout['mapping']))) + ';'
        if masks[r] != 4294967295:
            lines = text.splitlines()
            old = []
            kept = []
            for line in lines:
                if line.strip().startswith(f'signal reg_sparse_{r}:'):
                    continue
                if line.strip().startswith(f'reg_sparse_{r} <= latch'):
                    continue
                if line.strip().startswith(f'regs({r}) <='):
                    kept.append(new)
                else:
                    kept.append(line)
            text = '\n'.join(kept)
        else:
            text = replace_once(text, f'  regs({r}) <= latch(result_stage,com_en and write_stage({r}),reset);', new)
    if flow:
        width = len(image).bit_length()
        n = width - 2
        text = replace_once(text, '  signal regs:registers_t;', f'  signal pc_compressed_stage,pc_compressed_q:bit_vector({n - 1} downto 0);\n  signal regs:registers_t;')
        text = replace_once(text, 'pc_stage <= latch(next_pc,cap_en,reset);', f'pc_compressed_stage <= latch(next_pc({width - 1} downto 2),cap_en,reset);\n  pc_stage <= bits(0,{32 - width}) & pc_compressed_stage & bits(0,2);')
        text = replace_once(text, '  pc_q <= latch(pc_stage,com_en and execute_stage,reset);', f'  pc_compressed_q <= latch(pc_compressed_stage,com_en and execute_stage,reset);\n  pc_q <= bits(0,{32 - width}) & pc_compressed_q & bits(0,2);')
    return text

def cpu(image, ram_bytes=32, *, flow=False, **options):
    text = source(image, ram_bytes, flow=flow, data_rom=options.get('data_rom', False))
    with patch('accelerated.cpu_components.text_sram_source', lambda *a, **kw: text):
        result = base_cpu(image, ram_bytes, **options)
    result.report = deepcopy(result.report)
    result.report['cpu']['register_bit_aliases'] = layouts(image, flow)
    if flow:
        from accelerated.rom_ranges import summary
        result.report['cpu']['rom_range_proof'] = summary(image)
        result.report['cpu']['pc_storage_indices'] = list(range(2, len(image).bit_length()))
    return result
