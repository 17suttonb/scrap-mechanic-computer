from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import re
from unittest.mock import patch
from accelerated import compact_core, register_alias, pair_branch
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

def bundles(image, maximum=4, wide=False, static_stores=False, fuse_loads=True, register_heads=False, safe_control=False, branch_runs=False):
    """Conservative, bounded extra issue lanes, selected by opcode semantics.

    With branch_runs, adjacent equality branches are evaluated together, first taken wins. A run
    has no side effects after its first branch, except a final jump."""
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
    def equality_branch(r):
        return branch_runs and r['legal'] and r['branch'] and r['f3'] in (0, 1) and r['target'] % 4 == 0 and r['target'] < len(image)
    for index, first in enumerate(rows):
        if first['pc'] in existing or not (equality_branch(first) or arithmetic(first) or (register_heads and first['legal'] and (first['word'] & 127 == 51) and first['rd']) or (fuse_loads and first['legal'] and first['load'] and first['rd']) or (wide and first['legal'] and first['store'])):
            continue
        seq = [first]
        in_run = equality_branch(first)
        for r in rows[index + 1:index + maximum]:
            if in_run and not (equality_branch(r) or (r['legal'] and r['jal'] and not r['rd'] and r['target'] % 4 == 0 and r['target'] < len(image))):
                break
            if ram_store(r):
                r = dict(r, fusion_store_address=static[r['pc']]['imm'])
                seq.append(r)
                continue
            if r['legal'] and (r['branch'] and (wide or r['f3'] in (0, 1)) or (wide and r['jal'] and (not r['rd']))) and (r['target'] % 4 == 0) and (r['target'] < len(image)):
                if safe_control and r['branch'] and (r['f3'] not in (0, 1) or {r['ra'], r['rb']} & {v['rd'] for v in seq if v['rd']}):
                    break
                seq.append(r)
                if equality_branch(r):
                    in_run = True
                    continue
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

def fusion(text, image, maximum=4, wide=False, static_stores=False, fuse_loads=True, register_heads=False, safe_control=False, branch_runs=False):
    text = pair_branch.source(text, image)
    selected = bundles(image, maximum, wide, static_stores, fuse_loads, register_heads, safe_control, branch_runs)
    shared_equal = {}

    def equal(a, b):
        nonlocal decl, extra
        if (a, b) not in shared_equal:
            shared_equal[a, b] = f'fusion_equal_{len(shared_equal)}'
            decl += f'  signal {shared_equal[a, b]}:bit;\n'
            extra += f'  {shared_equal[a, b]} <= {a} = {b};\n'
        return shared_equal[a, b]
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
        controls = []
        if first['branch']:
            test = equal(f"regs({first['ra']})", f"regs({first['rb']})")
            controls.append((test if first['f3'] == 0 else f'not {test}', first['target']))
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
                if branch_runs and f in (0, 1):
                    test = equal(a, b) if f == 0 else f'not {equal(a, b)}'
                    controls.append((test, row['target']))
                elif f in (0, 1):
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
        if first['branch'] or len(controls) > 1:
            # first taken branch wins, then the final jump or the fall-through
            fall = seq[-1]['target'] if seq[-1]['jal'] else seq[-1]['pc'] + 4
            ways, passed = [], []
            for k, (test, dest) in enumerate(controls):
                name = f'fusion_way_{pc}_{k}'
                decl += f'  signal {name}:bit;\n'
                extra += f"  {name} <= {' and '.join(passed + [test])};\n"
                ways.append((name, dest))
                passed.append(f'not {test}' if not test.startswith('not ') else test[4:])
            name = f'fusion_way_{pc}_end'
            decl += f'  signal {name}:bit;\n'
            extra += f"  {name} <= {' and '.join(passed)};\n"
            nexts.append((site, ways + [(name, fall)], None))
        else:
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
            if isinstance(last, list):
                terms += [f'({site} and {name})' for name, dest in last if dest >> bit & 1]
                continue
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
    """With `cartridge` set, `image` is the firmware padded to the cartridge window followed by the
    cartridge: the analyses see all of it but the baked decoder only the firmware."""
    from accelerated import cartridge as cart
    cartridge = options.pop('cartridge', None)
    transforms = [flat_result_path] if options.get('beta') else []
    if cartridge is None:
        with final_text_hook(transforms):
            return firmware_cpu(image, ram_bytes, **options)
    with cart.window(), cart.board_storage(), final_text_hook([cart.cpu_source, cart.note_board_lines] + transforms):
        result = firmware_cpu(image, ram_bytes, **options)
    result.report['cpu']['cartridge'] = dict(base=cart.BASE, words=len(cartridge) // 4, address_bits=cart.ADDRESS_BITS)
    return result

@contextmanager
def final_text_hook(transforms):
    """Apply text transforms to the finished CPU HDL just before it compiles. They must keep every
    existing line on its line number: storage selection is keyed by source line."""
    original = compact_core.compile_source

    def hooked(text, *args, **kwargs):
        for transform in transforms:
            text = transform(text)
        return original(text, *args, **kwargs)
    with patch.object(compact_core, 'compile_source', hooked):
        yield

def flat_result_path(text):
    """Replace the priority chains on the load and result muxes with one-hot AND-OR.

    The cases are mutually exclusive by decode, so priority is not needed, and these chains were
    the deepest part of the paths that set the CPU clock."""
    def statement(name):
        match = re.search(rf'^  {name} <=.*?;$', text, re.M | re.S)
        assert match, name
        return match

    load = statement('load_word')
    word_loads = 'bits(0,32);' not in load[0]
    text = text[:load.start()] + '\n' * load[0].count('\n') + text[load.end():]
    extra = '  load_sign_byte <= (d_f3 = bits(0,3)) and byte_value(7);\n  load_sign_half <= (d_f3 = bits(1,3)) and region_ram and ram_word(15);\n  load_upper <= (d_f3(0) or d_f3(1)) and region_ram;\n  load_whole <= d_f3(1) and region_ram;\n'
    for bit in range(32):
        if bit < 8:
            value = f'byte_value({bit})'
        elif bit < 16:
            value = f'or_reduce(load_sign_byte & (load_upper and ram_word({bit})))'
        else:
            whole = f' & (load_whole and ram_word({bit}))' if word_loads else ''
            value = f'or_reduce(load_sign_byte & load_sign_half{whole})'
        extra += f'  load_word({bit}) <= {value};\n'
    result = statement('result')
    left = re.search(r'([\w()]+) when d_shiftleft', result[0])[1]
    right = re.search(r'([\w()]+) when d_shiftright', result[0])[1]
    text = text[:result.start()] + '\n' * result[0].count('\n') + text[result.end():]
    extra += '  result_link <= d_jal or d_jalr;\n  result_alu <= not or_reduce(d_load & d_jal & d_jalr & d_shiftleft & d_shiftright & d_compare);\n  result_compare <= d_compare and ((d_f3(0) and unsigned_less) or (not d_f3(0) and signed_less));\n'
    for bit in range(32):
        terms = [f'(d_load and load_word({bit}))', f'(result_link and d_link({bit}))', f'(d_shiftleft and {left}({bit}))', f'(d_shiftright and {right}({bit}))', f'(result_alu and alu_y({bit}))']
        if bit == 0:
            terms.append('result_compare')
        extra += f'  result({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    # the trap test sits on every write enable: test the conditions directly, not the encoded cause
    assert text.count('  cause_bad <= or_reduce(cause) or') == 1
    text = text.replace('  cause_bad <= or_reduce(cause) or', '  cause_bad <= or_reduce((not pc_valid) & (not d_legal) & d_stop & ((d_load or d_store) and not (mem_aligned and mem_valid))) or')
    # next_pc can only be misaligned through jalr: ROM and cartridge targets are aligned when built
    assert text.count('((d_jal or d_jalr or (d_branch and taken)) and or_reduce(next_pc(1 downto 0)))') == 1
    text = text.replace('((d_jal or d_jalr or (d_branch and taken)) and or_reduce(next_pc(1 downto 0)))', '(d_jalr and alu_y(1))')
    taken = re.search(r'^  taken <= eq when.*?;$', text, re.M | re.S)
    flat = '  taken <= or_reduce(((funct3 = bits(0,3)) and eq) & ((funct3 = bits(1,3)) and not eq) & ((funct3 = bits(4,3)) and slt) & ((funct3 = bits(5,3)) and not slt) & ((funct3 = bits(6,3)) and ult) & ((funct3 = bits(7,3)) and not ult));'
    text = text[:taken.start()] + flat + chr(10) * taken[0].count(chr(10)) + text[taken.end():]
    # Register-addressed firmware loads (program text, input stream, variables) are the deepest
    # remaining paths: give them a second cycle. Cartridge rows do not reach text or the stream,
    # so their board reads stay single-cycle.
    slow = re.search(r'^  slow_class <= [^;]*;', text, re.M)
    fast_rows = ' and not cart_legal' if 'cart_legal' in text else ''
    text = text[:slow.end() - 1] + f' or (d_load and not d_static_memory{fast_rows})' + text[slow.end() - 1:]
    # Console registers are only ever addressed statically: decode them from the instruction alone.
    for name, value in (('region_input', 536870882), ('region_gpio', 536870880)):
        old = f'  {name} <= address(31 downto 3) = bits({value},29);'
        assert text.count(old) == 1, old
        text = text.replace(old, f'  {name} <= d_static_memory and (d_imm(31 downto 3) = bits({value},29));')
    # A bad memory access stops the CPU one instruction late, so the address decoders stay off the
    # path to every write enable.
    old = '((d_load or d_store) and not (mem_aligned and mem_valid))'
    assert text.count(old) == 1
    text = text.replace(old, 'mem_fault_q')
    extra += '  mem_fault_stage <= latch((d_load or d_store) and not (mem_aligned and mem_valid),cap_en,reset);\n  mem_fault_q <= latch(mem_fault_stage,com_en,reset);\n'
    # The next PC: same flattening, on the path from a branch comparison to the PC.
    old = "  normal_next_pc <= alu_y(31 downto 1) & '0' when d_jalr else d_target when d_jal or (d_branch and taken) or (pair_issue and pair_taken) else d_link;"
    assert text.count(old) == 1
    key = ' or (cart_key and ready_seen) or (cart_nokey and not ready_seen)' if 'cart_legal' in text else ''
    text = text.replace(old, f'  next_take <= (d_jal or (d_branch and taken) or (pair_issue and pair_taken){key}) and not d_jalr;  next_link <= not (d_jalr or d_jal or (d_branch and taken) or (pair_issue and pair_taken){key});')
    for bit in range(32):
        jump = f'(d_jalr and alu_y({bit})) & ' if bit else ''
        extra += f'  normal_next_pc({bit}) <= or_reduce({jump}(next_take and d_target({bit})) & (next_link and d_link({bit})));\n'
    text = text.replace('  signal regs:registers_t;', '  signal load_sign_byte,load_sign_half,load_upper,load_whole,result_link,result_alu,result_compare,mem_fault_stage,mem_fault_q,next_take,next_link:bit;  signal regs:registers_t;', 1)
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def firmware_cpu(image, ram_bytes=64, **options):
    branch_runs = options.pop('beta', False)
    fast_shifts = options.pop('fast_shifts', False)
    original = register_alias.source

    def provider(*a, **kw):
        return frontend(original(*a, **kw), image, fast_shifts)
    old_pair = pair_branch.source

    def paired(text, img):
        with patch.object(pair_branch, 'source', old_pair):
            return fusion(text, img, 6, True, True, False, True, True, branch_runs)
    original_builder = Builder.__init__

    def research_builder(b, *args, **kwargs):
        original_builder(b, *args, **kwargs)
        b.limit = 100000
    with patch.object(register_alias, 'source', provider), patch.object(pair_branch, 'source', paired), patch.object(Builder, '__init__', research_builder):
        result = base_cpu(image, ram_bytes, **options)
    result.report = deepcopy(result.report)
    result.report['cpu']['modular_acceleration'] = dict(direct_operand_predecode=True, flat_carry_lookahead=True, dedicated_address_adder=False, single_cycle_fixed_shifts=fast_shifts, extra_issue_lanes=True)
    result.report['cpu']['retirement_bundle_lengths'] = {str(pc): len(seq) for pc, seq in bundles(image, 6, True, True, False, True, True, branch_runs).items()}
    result.report['cpu']['modular_acceleration']['parallel_static_RAM_stores'] = True
    result.report['cpu']['modular_acceleration']['fused_loads'] = False
    result.report['cpu']['modular_acceleration']['register_ALU_bundle_heads'] = True
    result.report['cpu']['modular_acceleration']['short_control_paths_only'] = True
    if True and len(result.circuit.nodes) > 32000:
        from accelerated.optimise import compact
        result = compact(result)
    return result
