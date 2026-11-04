from collections import defaultdict
from copy import deepcopy
import re
from unittest.mock import patch
from accelerated import register_alias, pair_branch
from accelerated.async_bus import replace_once
from accelerated.compact_core import metadata
from accelerated.cpu_components import compact_text_mux_cpu as base_cpu
from accelerated.hdl import Builder

def reduction(kind, terms):
    return kind + '_reduce(' + ' & '.join(terms) + ')' if terms else "'0'" if kind == 'or' else "'1'"

def flat_alu():
    from accelerated.alu32 import alu32_source
    s = alu32_source(4, operations=(1, 2, 3, 4, 5))
    start = s.index('  gp(0) <=')
    end = s.index('  add_bits:', start)
    extra = ''
    for bit in range(1, 33):
        terms = [reduction('and', [f'p({j})' for j in range(bit - 1, k, -1)] + [f'g({k})']) for k in range(bit)]
        terms.append(reduction('and', [f'p({j})' for j in range(bit)] + ['carry_in']))
        extra += f"  c({bit}) <= {reduction('or', terms)};\n"
    return re.sub('  signal gp,gg:[^;]+;\\n', '', s[:start] + extra + s[end:])

def frontend(text, image, fast_shifts=False):
    rows = metadata(image)
    for r in range(16):
        for name in ('a', 'b'):
            old = f'select_{name}({r}) <= d_r{name} = bits({r},4);'
            value = reduction('or', [f"select_pc({row['pc'] // 4})" for row in rows if row['r' + name] == r])
            text = replace_once(text, old, f'select_{name}({r}) <= {value};')
        old = f'select_write({r}) <= (d_rd = bits({r},4)) and execute_ok;'
        value = reduction('or', [f"select_pc({row['pc'] // 4})" for row in rows if row['rd'] == r])
        text = replace_once(text, old, f'select_write({r}) <= {value} and execute_ok;')
    start = text.index('entity rv_alu32 is')
    end = text.index('entity rv_branch_compare is', start)
    text = text[:start] + flat_alu() + '\n' + text[end:]
    from accelerated.cpu_components import fixed_shifts_plan as plan
    if fast_shifts and plan(image)['applied']:
        text = replace_once(text, 'or d_shiftleft or d_shiftright;', "or '0';")
    return text

def bundles(image, maximum=4, wide=False, static_stores=False, fuse_loads=True, register_heads=False, safe_control=False):
    """Conservative, bounded extra issue lanes, selected by opcode semantics."""
    rows = metadata(image)
    existing = pair_branch.pairs(image)
    result = {}
    from accelerated.rom_specialize import metadata as specialized
    static = {r['pc']: r for r in specialized(image)}

    def ram_store(r):
        m = static[r['pc']]
        size = 1 << r['f3']
        return static_stores and r['legal'] and r['store'] and m['static_memory'] and (4096 <= m['imm'] <= 4160 - size) and (m['imm'] % size == 0)

    def arithmetic(r):
        return r['legal'] and r['rd'] and (r['word'] & 127 == 55 or (r['word'] & 127 == 19 and r['f3'] in (0, 1, 4, 5, 6, 7)))
    for index, first in enumerate(rows):
        if first['pc'] in existing or not (arithmetic(first) or (register_heads and first['legal'] and (first['word'] & 127 == 51) and first['rd']) or (fuse_loads and first['legal'] and first['load'] and first['rd']) or (wide and first['legal'] and first['store'])):
            continue
        seq = [first]
        for r in rows[index + 1:index + maximum]:
            if ram_store(r):
                r = dict(r, fusion_store_address=static[r['pc']]['imm'])
                seq.append(r)
                continue
            if r['legal'] and (r['branch'] and (wide or r['f3'] in (0, 1)) or (wide and r['jal'] and (not r['rd']))) and (r['target'] % 4 == 0) and (r['target'] < len(image)):
                if safe_control and r['branch'] and (r['f3'] not in (0, 1) or {r['ra'], r['rb']} & {v['rd'] for v in seq if v['rd']}):
                    break
                seq.append(r)
                break
            if not arithmetic(r):
                break
            imm = r['imm']
            cheap = r['f3'] != 0 or r['ra'] == 0 or imm in (0, 1, 4294967295)
            if not (cheap or wide):
                break
            seq.append(r)
        if len(seq) < 2:
            continue
        if wide or first['load'] or len(seq) >= 3 or seq[-1]['branch'] or all((r['ra'] == 0 for r in seq[1:])):
            if seq[-1]['pc'] + 4 < len(image):
                result[first['pc']] = seq
    return result

def fusion(text, image, maximum=4, wide=False, static_stores=False, fuse_loads=True, register_heads=False, safe_control=False):
    text = pair_branch.source(text, image)
    selected = bundles(image, maximum, wide, static_stores, fuse_loads, register_heads, safe_control)
    decl = '  signal fusion_issue:bit;signal fusion_next_pc:bit_vector(31 downto 0);signal fusion_ret_increment:bit_vector(15 downto 0);\n'
    extra = ''
    writes = defaultdict(list)
    ram_writes = defaultdict(list)
    nexts = []
    lengths = defaultdict(list)
    load_types = {seq[0]['f3'] for seq in selected.values() if seq[0]['load']}
    for f in sorted(load_types):
        if f not in (0, 4, 5):
            continue
        decl += f'  signal fusion_load_{f}:bit_vector(31 downto 0);\n'
        expr = 'resize(byte_value,32)' if f == 4 else 'resize(half_value,32)' if f == 5 else 'bits(16777215,24) & byte_value when byte_value(7) else resize(byte_value,32)'
        extra += f'  fusion_load_{f} <= {expr};\n'

    def constant(n):
        return f'bits({n & 4294967295},32)'
    for pc, seq in selected.items():
        site = f'select_pc({pc // 4})'
        values = {0: constant(0)}
        first = seq[0]
        destinations = {}
        stores = {}
        constants = {0: 0}
        if first['word'] & 127 == 55:
            constants[first['rd']] = first['imm']
        elif first['word'] & 127 == 19 and first['ra'] == 0 and (first['f3'] == 0):
            constants[first['rd']] = first['imm']
        if first['rd']:
            values[first['rd']] = f"fusion_load_{first['f3']}" if first['load'] and first['f3'] in (0, 4, 5) else 'result'
            destinations[first['rd']] = 'result'
        target = constant(seq[-1]['pc'] + 4)
        branch_test = None
        for lane, row in enumerate(seq[1:], 1):

            def reg(r):
                return values.get(r, f'regs({r})')
            a = reg(row['ra'])
            b = reg(row['rb'])
            imm = row['imm']
            op = row['word'] & 127
            f = row['f3']
            if row['store']:
                value = constant(constants[row['rb']]) if row['rb'] in constants else b
                word = f'fusion_store_{pc}_{lane}'
                decl += f'  signal {word}:bit_vector(31 downto 0);\n'
                extra += f'  {word} <= {value};\n'
                for byte in range(1 << f):
                    stores[row['fusion_store_address'] - 4096 + byte] = f'{word}({byte * 8 + 7} downto {byte * 8})'
                continue
            if row['branch']:
                if f in (0, 1):
                    test = f"({a} {('=' if f == 0 else '/=')} {b})"
                else:
                    test = f'fusion_taken_{pc}'
                    decl += f'  signal {test}:bit;\n'
                    extra += f'  fusion_compare_{pc}:entity work.rv_branch_compare port map(a=>{a},b=>{b},funct3=>bits({f},3),taken=>{test});\n'
                target = f"{constant(row['target'])} when {test} else {target}"
                branch_test = test
                continue
            if row['jal']:
                target = constant(row['target'])
                continue
            name = f'fusion_value_{pc}_{lane}'
            decl += f'  signal {name}:bit_vector(31 downto 0);\n'
            known = None
            if op == 55:
                known = imm
            elif row['ra'] in constants:
                av = constants[row['ra']]
                n = row['word'] >> 20 & 31
                known = {0: lambda: av + imm, 1: lambda: av << n, 4: lambda: av ^ imm, 6: lambda: av | imm, 7: lambda: av & imm, 5: lambda: (av - (1 << 32) if av >> 31 and row['arithmetic'] else av) >> n}[f]() & 4294967295
            if known is not None:
                expr = constant(known)
                constants[row['rd']] = known
            elif op == 55:
                expr = constant(imm)
            elif f == 0:
                if row['ra'] == 0:
                    expr = constant(imm)
                elif imm == 0:
                    expr = a
                elif imm in (1, 4294967295):
                    lines = []
                    for bit in range(32):
                        carry = "'1'" if bit == 0 else reduction('and' if imm == 1 else 'or', [f'{a}({j})' for j in range(bit)])
                        if imm != 1 and bit:
                            carry = 'not ' + carry
                        lines.append(f'  {name}({bit}) <= {a}({bit}) xor {carry};\n')
                    extra += ''.join(lines)
                    expr = None
                else:
                    for bit in range(32):
                        low = imm & (1 << bit) - 1
                        if not low:
                            carry = "'0'"
                        else:
                            threshold = (1 << bit) - low
                            terms = []
                            for k in range(bit):
                                if not threshold >> k & 1:
                                    continue
                                terms.append(reduction('and', [f'{a}({j})' if threshold >> j & 1 else f'not {a}({j})' for j in range(bit - 1, k, -1)] + [f'not {a}({k})']))
                            carry = 'not ' + reduction('or', terms)
                        extra += f"  {name}({bit}) <= {a}({bit}) xor '{imm >> bit & 1}' xor ({carry});\n"
                    expr = None
            elif f in (4, 6, 7):
                expr = f'{a} ' + {4: 'xor', 6: 'or', 7: 'and'}[f] + ' ' + constant(imm)
            elif f == 1:
                n = row['word'] >> 20 & 31
                expr = a if not n else f'{a}({31 - n} downto 0) & bits(0,{n})'
            else:
                n = row['word'] >> 20 & 31
                expr = a if not n else (f'bits({(1 << n) - 1},{n}) & {a}(31 downto {n}) when {a}(31) else ' if row['arithmetic'] else '') + f'bits(0,{n}) & {a}(31 downto {n})'
            if expr is not None:
                extra += f'  {name} <= {expr};\n'
            if known is None:
                constants.pop(row['rd'], None)
            values[row['rd']] = name
            destinations[row['rd']] = name
        for r, value in destinations.items():
            if value != 'result':
                writes[r].append((site, value))
        for address, value in stores.items():
            ram_writes[address].append((site, value))
        nexts.append((site, seq[-1], branch_test))
        lengths[len(seq)].append(site)
    if not selected:
        return text
    issue = reduction('or', [f'select_pc({pc // 4})' for pc in selected])
    extra += f'  fusion_issue <= {issue};\n'
    decl += '  signal normal_next_pc:bit_vector(31 downto 0);\n'
    text = replace_once(text, '  next_pc <=', '  normal_next_pc <=')
    for bit in range(32):
        terms = [f'(not fusion_issue and normal_next_pc({bit}))']
        for site, last, test in nexts:
            fall = last['pc'] + 4
            dest = last['target'] if last['branch'] or last['jal'] else fall
            yes = dest >> bit & 1
            no = fall >> bit & 1
            if test is None:
                if yes:
                    terms.append(site)
            elif yes and no:
                terms.append(site)
            elif yes:
                terms.append(f'({site} and {test})')
            elif no:
                terms.append(f'({site} and not {test})')
        extra += f"  next_pc({bit}) <= {reduction('or', terms)};\n"
        extra += f"  fusion_next_pc({bit}) <= '0';\n"
    expr = ''
    for length, sites in sorted(lengths.items()):
        expr += f"bits({length},16) when {reduction('or', sites)} else "
    extra += '  fusion_ret_increment <= ' + expr + 'pair_ret_increment;\n'
    text = replace_once(text, 'ret_q + pair_ret_increment', 'ret_q + fusion_ret_increment')
    for r, choices in writes.items():
        enabled = reduction('or', [s for s, _ in choices])
        decl += f'  signal fusion_result_{r},fusion_stage_{r}:bit_vector(31 downto 0);signal fusion_write_{r}:bit;\n'
        extra += f'  fusion_write_{r} <= {enabled};\n'
        for bit in range(32):
            terms = [f'({site} and {value}({bit}))' for site, value in choices] + [f'(not fusion_write_{r} and result({bit}))']
            extra += f"  fusion_result_{r}({bit}) <= {reduction('or', terms)};\n"
        extra += f'  fusion_stage_{r} <= latch(fusion_result_{r},cap_en,reset);\n'
        pattern = f'(select_write\\({r}\\) <= )([^;]+)(;)'
        text, n = re.subn(pattern, lambda m: m[1] + f'({m[2]} or ({enabled} and execute_ok))' + m[3], text)
        assert n == 1
        lines = text.splitlines()
        n = 0
        for i, line in enumerate(lines):
            if re.match(f'\\s*(?:regs\\({r}\\)|reg_sparse_{r}|reg_equiv_{r}) <= latch', line):
                lines[i] = line.replace('result_stage', f'fusion_stage_{r}')
                n += 1
        assert n == 1, (r, n)
        text = '\n'.join(lines)
    for address, choices in ram_writes.items():
        name = f'fusion_ram_{address}'
        decl += f'  signal {name}_data,{name}_stage,{name}_value:bit_vector(7 downto 0);signal {name}_write,{name}_write_stage:bit;\n'
        extra += f'  {name}_write <= ' + reduction('or', [site for site, _ in choices]) + ';\n'
        extra += f'  {name}_write_stage <= latch({name}_write and execute_ok,cap_en,reset);\n'
        for n, (site, value) in enumerate(choices):
            decl += f'  signal {name}_choice_{n}:bit_vector(7 downto 0);\n'
            extra += f'  {name}_choice_{n} <= {value};\n'
        for bit in range(8):
            extra += f'  {name}_data({bit}) <= ' + reduction('or', [f'({site} and {name}_choice_{n}({bit}))' for n, (site, _) in enumerate(choices)]) + ';\n'
        extra += f'  {name}_stage <= latch({name}_data,cap_en,reset);\n'
        extra += f'  {name}_value <= {name}_stage when {name}_write_stage else write_bytes({address});\n'
        pattern = f'ram\\({address}\\) <= latch\\(write_bytes\\({address}\\),([^;]+),reset\\);'
        text, n = re.subn(pattern, lambda m: f'ram({address}) <= latch({name}_value,({m[1]}) or (com_en and {name}_write_stage),reset);', text)
        assert n == 1, (address, n)
    text = replace_once(text, '  signal regs:registers_t;', decl + '  signal regs:registers_t;')
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def cpu(image, ram_bytes=64, **options):
    options.pop('beta', None)
    fast_shifts = options.pop('fast_shifts', False)
    original = register_alias.source

    def provider(*a, **kw):
        return frontend(original(*a, **kw), image, fast_shifts)
    old_pair = pair_branch.source

    def paired(text, img):
        with patch.object(pair_branch, 'source', old_pair):
            return fusion(text, img, 6, True, True, False, True, True)
    original_builder = Builder.__init__

    def research_builder(b, *args, **kwargs):
        original_builder(b, *args, **kwargs)
        b.limit = 100000
    with patch.object(register_alias, 'source', provider), patch.object(pair_branch, 'source', paired), patch.object(Builder, '__init__', research_builder):
        result = base_cpu(image, ram_bytes, **options)
    result.report = deepcopy(result.report)
    result.report['cpu']['modular_acceleration'] = dict(direct_operand_predecode=True, flat_carry_lookahead=True, dedicated_address_adder=False, single_cycle_fixed_shifts=fast_shifts, extra_issue_lanes=True)
    result.report['cpu']['retirement_bundle_lengths'] = {str(pc): len(seq) for pc, seq in bundles(image, 6, True, True, False, True, True).items()}
    result.report['cpu']['modular_acceleration']['parallel_static_RAM_stores'] = True
    result.report['cpu']['modular_acceleration']['fused_loads'] = False
    result.report['cpu']['modular_acceleration']['register_ALU_bundle_heads'] = True
    result.report['cpu']['modular_acceleration']['short_control_paths_only'] = True
    if True and len(result.circuit.nodes) > 32000:
        from accelerated.optimise import compact
        result = compact(result)
    return result
