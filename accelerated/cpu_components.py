from contextlib import contextmanager as text_sram_contextmanager
from copy import deepcopy as text_sram_deepcopy
from unittest.mock import patch as text_sram_patch
from accelerated.hdl import Builder as text_sram_Builder, Bits as text_sram_Bits
from accelerated.async_bus import replace_once as text_sram_replace_once
from accelerated.compact_core import source as text_sram_core_source, compile_core as text_sram_compile_core

def text_sram_source(image, ram_bytes=32, *, text_slots=8, packed_directory=False, shared_toggle=False, **kwargs):
    assert text_slots in (8, 16)
    count = text_slots * 18
    ab = 8 if text_slots == 8 else 9
    from accelerated.rom_specialize import metadata, source as specialize
    with text_sram_patch('accelerated.compact_core.metadata', metadata):
        s = text_sram_core_source(image, ram_bytes, **kwargs)
    s = text_sram_replace_once(s, 'port(capture,commit,reset,bus_ack:in bit;', 'port(capture,commit,reset,bus_ack,text_history:in bit;')
    s = text_sram_replace_once(s, 'input_byte,bus_read_data:in bit_vector(7 downto 0);', 'input_byte,bus_read_data:in bit_vector(7 downto 0);input_length:in bit_vector(4 downto 0);')
    declaration = f'  type program_t is array(0 to {count - 1}) of bit_vector(5 downto 0);\n  signal program_chars:program_t;\n  signal program_code,program_encoded:bit_vector(5 downto 0);\n  signal program_byte:bit_vector(7 downto 0);\n  signal program_address_stage:bit_vector({ab - 1} downto 0);\n  signal region_program,region_stream,fast_address,program_write_stage,program_prev_com,program_edge,program_pulse:bit;\n  signal program_length:bit_vector(4 downto 0);signal program_length_select:bit_vector({text_slots - 1} downto 0);\n  signal program_select:bit_vector({count - 1} downto 0);\n'
    if shared_toggle:
        declaration += f'  signal program_delta:bit_vector(5 downto 0);\n  signal program_read_address:bit_vector({ab - 1} downto 0);\n  signal program_read_hold:bit;\n  signal program_column_select:bit_vector(17 downto 0);\n  signal program_slot_select:bit_vector({text_slots - 1} downto 0);\n'
    s = text_sram_replace_once(s, '  signal regs:registers_t;', declaration + '  signal regs:registers_t;')
    s = text_sram_replace_once(s, '  region_rom <=', f'  region_program <= address(31 downto {ab}) = bits({4608 >> ab},{32 - ab});\n  region_stream <= address(31 downto 8) = bits(16777201,24);\n  region_rom <=')
    s = text_sram_replace_once(s, '  address <= d_imm when d_static_memory else alu_y;', '  fast_address <= (d_load or d_store) and not or_reduce(d_imm);\n  address <= d_imm when d_static_memory else a when fast_address else alu_y;')
    s = text_sram_replace_once(s, '((d_load or d_store) and not d_static_memory) or d_shiftleft or d_shiftright;', '((d_load or d_store) and not d_static_memory and not fast_address) or d_shiftleft or d_shiftright;')
    s = text_sram_replace_once(s, ' or region_gpio;', ' or region_gpio or ((region_program or (region_stream and d_load)) and ((d_f3 = bits(0,3)) or (d_load and (d_f3 = bits(4,3)))));')
    s = text_sram_replace_once(s, "  byte_value <= '0' & input_byte(6 downto 0) when region_input and address(2) else ready_seen & bits(0,7) when region_input else", "  byte_value <= program_byte when region_program else '0' & input_byte(6 downto 0) when region_stream or (region_input and address(2)) else\n                resize(index_q,8) when region_input and (address(1 downto 0) = bits(1,2)) else ready_seen & bits(0,2) & input_length when region_input else")
    s = text_sram_replace_once(s, 'd_load and region_input and address(2) and ready_seen', 'd_load and (region_stream or (region_input and address(2))) and ready_seen')
    extra = f'\n  program_address_stage <= latch(address({ab - 1} downto 0),cap_en,reset);\n  program_write_stage <= latch(execute_ok and d_store and region_program,cap_en,reset);\n  program_prev_com <= text_history;\n  program_edge <= com_en xor program_prev_com;\n  program_pulse <= program_edge and program_prev_com;\n  program_encoded <= store_data(5 downto 0) xor bits(32,6);\n'
    read_address = 'address'
    if shared_toggle:
        extra += '  program_read_hold <= program_write_stage and not capture;\n  program_read_address <= program_address_stage when program_read_hold else address(' + str(ab - 1) + ' downto 0);\n  program_delta <= program_encoded xor program_code;\n'
        read_address = 'program_read_address'
        for col in range(18):
            extra += f'  program_column_select({col}) <= program_read_address(4 downto 0) = bits({col},5);\n'
        for slot in range(text_slots):
            extra += f'  program_slot_select({slot}) <= program_read_address({ab - 1} downto 5) = bits({slot},{ab - 5});\n'
    for i in range(count):
        address = i // 18 * 32 + i % 18
        selection = f'program_slot_select({i // 18}) and program_column_select({i % 18})' if shared_toggle else f'{read_address}({ab - 1} downto 0) = bits({address},{ab})'
        extra += f'  program_select({i}) <= {selection};\n'
        enable = f'program_select({i})' if shared_toggle else f'(program_address_stage = bits({address},{ab}))'
        extra += f"  program_chars({i}) <= latch({('program_delta' if shared_toggle else 'program_encoded')},program_pulse and program_write_stage and {enable});\n"
    for bit in range(6):
        terms = [f'(program_select({i}) and program_chars({i})({bit}))' for i in range(count)]
        groups = ['or_reduce(' + ' & '.join(terms[j:j + 72]) + ')' for j in range(0, len(terms), 72)]
        extra += f'  program_code({bit}) <= or_reduce(' + ' & '.join(groups) + ');\n'
    for i in range(text_slots):
        slot = f'ram({5 + i * 2})(7 downto {(4 if packed_directory else 5)})'
        key = f'ram({4 + i * 2})' + ('(6 downto 0)' if packed_directory else '')
        extra += f'  program_length_select({i}) <= ({slot} = {read_address}({ab - 1} downto 5)) and or_reduce({key});\n'
    for bit in range(5):

        def length(i):
            return f'ram({4 + i * 2})(7)' if packed_directory and bit == 0 else f'ram({5 + i * 2})({(bit - 1 if packed_directory else bit)})'
        extra += f'  program_length({bit}) <= or_reduce(' + ' & '.join((f'(program_length_select({i}) and {length(i)})' for i in range(text_slots))) + ');\n'
    extra += f"  program_byte <= '0' & program_code(5) & not program_code(5) & program_code(4 downto 0) when or_reduce(program_select) and ({read_address}(4 downto 0) < program_length) else bits(0,8);\n"
    result, _ = specialize(s.rsplit('end;', 1)[0] + extra + 'end;\n', image)
    return result

@text_sram_contextmanager
def text_sram_cells(text):
    rows = {i for i, line in enumerate(text.splitlines(), 1) if line.strip().startswith('program_chars(')}
    assert len(rows) in (144, 288)
    shared = '  signal program_delta:' in text
    original = text_sram_Builder.latch
    count = 0

    def latch(b, data, enable, reset, token):
        nonlocal count
        if token.line not in rows:
            return original(b, data, enable, reset, token)
        assert reset == 0 and len(data) == 6
        count += len(data)
        b.latch_count += len(data)
        result = []
        for bit, d in enumerate(data):
            prefix = f'timing TEXT line {token.line} bit {bit} '
            q = b.raw('XOR', (), f'latch Q line {token.line} bit {bit}', False, token)
            delta = d if shared else b.raw('XOR', (d, q), prefix + 'delta', False, token)
            flip = b.raw('AND', (delta, enable), prefix + 'flip', False, token)
            b.gates[q]['sources'] = (q, flip)
            b.storage.add(q)
            result.append(q)
        return text_sram_Bits(tuple(result))
    with text_sram_patch.object(text_sram_Builder, 'latch', latch):
        yield
    assert count == len(rows) * 6

def text_sram_cpu(image, ram_bytes=32, **options):
    text = text_sram_source(image, ram_bytes, data_rom=options.get('data_rom', False))
    with text_sram_patch('accelerated.compact_core.source', lambda *a, **kw: text), text_sram_cells(text):
        build = text_sram_compile_core(image, ram_bytes, **options)
    c = build.circuit.clone()
    ports = text_sram_deepcopy(build.ports)
    old = ports.pop('text_history')['node_ids'][0]
    parents = {i: [] for i in c.nodes}
    for a, b in c.edges:
        parents[b].append(a)
    edge = next((i for i in c.nodes if c.nodes[i]['kind'] == 'XOR' and old in parents[i]), None)
    history = None
    if edge is None:
        assert not any((a == old for a, b in c.edges))
        c.remove({old})
    else:
        assert len(parents[edge]) == 2
        enable = next((i for i in parents[edge] if i != old))
        history = c.add('OR', 0, 0, 'timing TEXT COM history')
        c.connect(enable, history)
        c.edges = {(history if a == old else a, b) for a, b in c.edges}
        c.remove({old})
        c.nodes[edge]['label'] = 'timing TEXT COM edge'
        for a, b in c.edges:
            if a == edge:
                c.nodes[b]['label'] = 'timing TEXT COM single update'
    c.validate()
    report = text_sram_deepcopy(build.report)
    count = sum((line.strip().startswith('program_chars(') for line in text.splitlines()))
    report['cpu']['text_sram'] = dict(characters=count, bits=count * 6, encoding='ASCII 32..95, six bits; lengths in software', address=4608, slots=count // 18, characters_per_slot=18, history_node=history, readable_byte_only=True, shared_delta='  signal program_delta:' in text)
    from accelerated.rom_specialize import zero_bits
    report['cpu']['register_zero_masks'] = zero_bits(image)
    from accelerated.hdl import Build
    return clock_boost_fold_shared_inverters(Build(c, ports, report, build.source), scope='storage')
from collections import Counter as clock_boost_Counter
from copy import deepcopy as clock_boost_deepcopy
from accelerated.hdl import Build as clock_boost_Build

def clock_boost_storage_disable_inverters(build):
    """Locate !enable in /R=(D OR !enable) of the existing NAND cells."""
    c = build.circuit
    parents = {i: set() for i in c.nodes}
    for a, b in c.edges:
        parents[b].add(a)

    def unbuffer(i):
        seen = set()
        while c.nodes[i]['kind'] == 'OR' and len(parents[i]) == 1 and (i not in seen):
            seen.add(i)
            i = next(iter(parents[i]))
        return i
    found = set()
    for q, n in c.nodes.items():
        if n['kind'] != 'NAND' or not n['label'].startswith('latch Q '):
            continue
        qs = {unbuffer(i) for i in parents[q]}
        opposite = next((i for i in qs if c.nodes[i]['label'].startswith('latch /Q ')), None)
        if opposite is None:
            continue
        setbar = next((i for i in qs if i != opposite), None)
        if setbar is None:
            continue
        if c.nodes[setbar]['kind'] == 'NOR' and len(parents[setbar]) == 1:
            product = unbuffer(next(iter(parents[setbar])))
            inputs = parents[product] if c.nodes[product]['kind'] == 'AND' else set()
        elif c.nodes[setbar]['kind'] == 'NAND':
            inputs = parents[setbar]
        else:
            continue
        terms = {unbuffer(i) for i in inputs}
        for r in parents[opposite]:
            r = unbuffer(r)
            if c.nodes[r]['kind'] != 'OR':
                continue
            for i in parents[r]:
                i = unbuffer(i)
                if c.nodes[i]['kind'] == 'NOR' and len(parents[i]) == 1 and (unbuffer(next(iter(parents[i]))) in terms):
                    found.add(i)
    return found

def clock_boost_fold_shared_inverters(build, scope='all'):
    """Spend input wires to compute shared complements one gate stage earlier."""
    circuit = build.circuit.clone()
    parents = {i: set() for i in circuit.nodes}
    children = {i: set() for i in circuit.nodes}
    for a, b in circuit.edges:
        parents[b].add(a)
        children[a].add(b)
    inverse = {'AND': 'NAND', 'NAND': 'AND', 'OR': 'NOR', 'NOR': 'OR', 'XOR': 'XNOR', 'XNOR': 'XOR'}
    exposed = {i for p in build.ports.values() for i in p['node_ids']}
    changes = []
    selected = clock_boost_storage_disable_inverters(build) if scope == 'storage' else set(circuit.nodes)
    for target in sorted(circuit.nodes):
        n = circuit.nodes[target]
        if target not in selected or n['kind'] != 'NOR' or len(parents[target]) != 1 or (target in exposed) or n['label'].startswith(('timing ', 'latch ')):
            continue
        source = next(iter(parents[target]))
        driver = circuit.nodes[source]
        up = parents[source]
        if driver['kind'] not in inverse or not 2 <= len(up) <= 128 or driver['label'].startswith(('timing ', 'latch ')) or (target in up):
            continue
        if any((len(children[a] | {target}) > 128 for a in up)):
            continue
        circuit.edges.remove((source, target))
        circuit.edges.update(((a, target) for a in up))
        children[source].discard(target)
        for a in up:
            children[a].add(target)
        parents[target] = set(up)
        n['kind'] = inverse[driver['kind']]
        changes.append(dict(target=target, source=source, input_count=len(up)))
    circuit.validate()
    report = clock_boost_deepcopy(build.report)
    report.update(blocks=len(circuit.nodes), wires=len(circuit.edges), gates=dict(clock_boost_Counter((n['kind'] for n in circuit.nodes.values()))), early_shared_complements=dict(changed_gates=len(changes), scope=scope, identified_storage_disable_gates=len(selected) if scope == 'storage' else None, changes=changes))
    return clock_boost_Build(circuit, clock_boost_deepcopy(build.ports), report, build.source)
from contextlib import contextmanager as xor_cpu_storage_contextmanager
from copy import deepcopy as xor_cpu_storage_deepcopy
import re as xor_cpu_storage_re
from unittest.mock import patch as xor_cpu_storage_patch
from accelerated.hdl import Bits as xor_cpu_storage_Bits, Builder as xor_cpu_storage_Builder
from accelerated import register_alias as xor_cpu_storage_register_alias
from accelerated.rom_ranges import analyze as xor_cpu_storage_analyze
xor_cpu_storage_TARGET = '(?:regs\\(\\d+\\)|reg_sparse_\\d+|reg_equiv_\\d+|ram\\(\\d+\\)|pc_q|pc_compressed_q|ret_q)'

def xor_cpu_storage_prepare_source(text, targets=xor_cpu_storage_TARGET):
    pattern = f'\\b({targets})\\s*<=\\s*latch\\([^;]*;'
    text = xor_cpu_storage_re.sub(pattern, lambda match: '\n  ' + match[0] + '\n', text)
    rows = {i: line.strip().split('<=')[0].strip() for i, line in enumerate(text.splitlines(), 1) if xor_cpu_storage_re.match(f'\\s*{targets}\\s*<=\\s*latch\\(', line)}
    assert rows, 'No selected COM payload banks'
    return (text, rows)

@xor_cpu_storage_contextmanager
def xor_cpu_storage_cells(rows, *, clear_delay=13, guard_updates=20):
    assert 1 < clear_delay < guard_updates
    original = xor_cpu_storage_Builder.latch
    stats = dict(converted_bits=0, banks=[], reset_policy='Preserved NAND staging supplies zero; delayed shared reset pulse', minimum_reset_updates=max(32, guard_updates + 12), style='three-gate XOR, local rising write pulse')

    def latch(b, data, enable, reset, token):
        if b.latch_style != 'nand' or token.line not in rows:
            return original(b, data, enable, reset, token)
        assert reset != 0, 'Selected payloads require the documented staged reset contract'
        b.latch_count += len(data)
        stats['converted_bits'] += len(data)
        stats['banks'].append(dict(name=rows[token.line], bits=len(data), line=token.line))

        def raw(kind, parents, label, initial=False):
            return b.raw(kind, parents, label, initial, token)
        groups = getattr(b, 'xor_cpu_reset_groups', None)
        if groups is None:
            groups = {}
            b.xor_cpu_reset_groups = groups
        if reset not in groups:
            prefix = f'timing XOR CPU reset {len(groups)} '
            history = [reset]
            for n in range(1, guard_updates + 1):
                history.append(raw('OR', (history[-1],), prefix + f'history {n}'))
            busy = raw('OR', tuple(history), prefix + 'busy')
            allowed = raw('NOR', (busy,), prefix + 'allowed', True)
            inverse = raw('NOR', (history[clear_delay - 1],), prefix + 'clear inverse', True)
            clear = raw('AND', (history[clear_delay - 1], inverse), prefix + 'clear pulse')
            groups[reset] = (allowed, clear)
        allowed, clear = groups[reset]
        prefix = f'timing XOR CPU line {token.line} '
        inverse = raw('NOR', (enable,), prefix + 'request inverse', True)
        normal = raw('AND', (enable, inverse, allowed), prefix + 'normal pulse')
        trigger = raw('OR', (normal, clear), prefix + 'trigger')
        result = []
        for bit, d in enumerate(data):
            q = raw('XOR', (), f'latch Q line {token.line} bit {bit}')
            delta = raw('XOR', (d, q), prefix + f'bit {bit} delta')
            flip = raw('AND', (delta, trigger), prefix + f'bit {bit} flip')
            b.gates[q]['sources'] = (q, flip)
            b.storage.add(q)
            result.append(q)
        return xor_cpu_storage_Bits(tuple(result))
    with xor_cpu_storage_patch.object(xor_cpu_storage_Builder, 'latch', latch):
        yield stats
    assert stats['converted_bits'], 'CPU hook did not convert any storage'

def xor_cpu_storage_cpu(image, ram_bytes=32, **options):
    try:
        flow = xor_cpu_storage_analyze(image)['passed']
    except AssertionError:
        flow = False
    text, rows = xor_cpu_storage_prepare_source(xor_cpu_storage_register_alias.source(image, ram_bytes, flow=flow, data_rom=options.get('data_rom', False)))
    with xor_cpu_storage_patch.object(xor_cpu_storage_register_alias, 'source', lambda *a, **kw: text), xor_cpu_storage_cells(rows) as stats:
        result = xor_cpu_storage_register_alias.cpu(image, ram_bytes, flow=flow, **options)
    result.report = xor_cpu_storage_deepcopy(result.report)
    result.report['cpu']['xor_commit_storage'] = stats
    return result
from copy import deepcopy as basic_variables_deepcopy
import re as basic_variables_re
from unittest.mock import patch as basic_variables_patch
from accelerated import register_alias as basic_variables_register_alias
from accelerated.async_bus import replace_once as basic_variables_replace_once
from accelerated.pair_branch import cpu as basic_variables_pair_cpu
from accelerated.compact_core import analysis_metadata as basic_variables_metadata
basic_variables_VARIABLES = 26
basic_variables_RAM_BYTES = 64
basic_variables_DIRECTORY = 32
basic_variables_LINE_COUNT = 49
basic_variables_BUDGET = 63

def basic_variables_memory_source(text, image):
    text = basic_variables_replace_once(text, '  signal regs:registers_t;', '  signal address_fast_select,address_alu_select:bit;\n  signal regs:registers_t;')
    address = '  address <= d_imm when d_static_memory else a when fast_address else alu_y;'
    replacement = '  address_fast_select <= fast_address and not d_static_memory;\n  address_alu_select <= not (fast_address or d_static_memory);\n' + ''.join((f'  address({i}) <= or_reduce((d_static_memory and d_imm({i})) & (address_fast_select and a({i})) & (address_alu_select and alu_y({i})));\n' for i in range(32)))
    text = basic_variables_replace_once(text, address, replacement.rstrip())
    declarations = '  signal byte_cases:bit_vector(5 downto 0);\n  signal byte_select:bit_vector(6 downto 0);\n  signal byte_ascii_data,byte_index_data,byte_status_data:bit_vector(7 downto 0);\n'
    text = basic_variables_replace_once(text, '  signal regs:registers_t;', declarations + '  signal regs:registers_t;')
    cases = ['region_program', 'region_stream or (region_input and address(2))', 'region_input and (address(1 downto 0) = bits(1,2))', 'region_input', 'region_gpio', 'region_ram']
    body = ''.join((f'  byte_cases({i}) <= {expr};\n' for i, expr in enumerate(cases)))
    body += '  byte_select(0) <= byte_cases(0);\n'
    for i in range(1, 6):
        body += f'  byte_select({i}) <= byte_cases({i}) and not or_reduce(byte_cases({i - 1} downto 0));\n'
    body += "  byte_select(6) <= not or_reduce(byte_cases);\n  byte_ascii_data <= '0' & input_byte(6 downto 0);\n  byte_index_data <= resize(index_q,8);\n  byte_status_data <= ready_seen & bits(0,2) & input_length;\n"
    sources = ['program_byte', 'byte_ascii_data', 'byte_index_data', 'byte_status_data', 'bus_read_data', 'ram_word', 'rom_word']
    for bit in range(8):
        body += f'  byte_value({bit}) <= or_reduce(' + ' & '.join((f'(byte_select({i}) and {name}({bit}))' for i, name in enumerate(sources))) + ');\n'
    text, count = basic_variables_re.subn('^  byte_value <=.*?;', lambda _: body.rstrip(), text, count=1, flags=basic_variables_re.M | basic_variables_re.S)
    assert count == 1
    lines = text.splitlines()
    changed = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(('program_length_select(', 'program_length(')):
            lines[i] = basic_variables_re.sub('ram\\((\\d+)\\)', lambda m: f'ram({int(m[1]) + 28})', line)
            changed += 1
    assert changed in (13, 21)
    text = '\n'.join(lines)
    no_lw = not any((r['load'] and r['legal'] and (r['f3'] == 2) for r in basic_variables_metadata(image)))
    if no_lw:
        text = basic_variables_replace_once(text, 'ram_word when region_ram else rom_word when region_rom else resize(byte_value,32);', 'bits(0,32);')
    return (text, no_lw)

def basic_variables_cpu(image, ram_bytes=basic_variables_RAM_BYTES, **options):
    assert ram_bytes == basic_variables_RAM_BYTES
    original = basic_variables_register_alias.base_source
    proof = {}

    def provider(*args, **kwargs):
        text, proof['no_word_loads'] = basic_variables_memory_source(original(*args, **kwargs), image)
        return text
    with basic_variables_patch.object(basic_variables_register_alias, 'base_source', provider):
        result = basic_variables_pair_cpu(image, ram_bytes, **options)
    result.report = basic_variables_deepcopy(result.report)
    result.report['cpu']['basic_variables'] = dict(count=basic_variables_VARIABLES, ram_bytes=basic_variables_RAM_BYTES, directory_offset=basic_variables_DIRECTORY, line_count_offset=basic_variables_LINE_COUNT, step_budget_offset=basic_variables_BUDGET, word_load_arm_pruned=proof['no_word_loads'])
    return result
from copy import deepcopy as basic_sixteen_deepcopy
from unittest.mock import patch as basic_sixteen_patch
from accelerated import register_alias as basic_sixteen_register_alias

def basic_sixteen_cpu(image, ram_bytes=64, **options):
    original = basic_sixteen_register_alias.base_source

    def provider(*args, **kwargs):
        return original(*args, **kwargs, text_slots=16, packed_directory=True, shared_toggle=True)
    with basic_sixteen_patch.object(basic_sixteen_register_alias, 'base_source', provider):
        result = basic_variables_cpu(image, ram_bytes, **options)
    result.report = basic_sixteen_deepcopy(result.report)
    result.report['cpu']['basic_variables'].update(line_count_offset=28, step_budget_offset=29, program_lines=16)
    result.report['cpu']['program_storage'] = dict(lines=16, characters_per_line=18, physical_bits=1728, directory_format='key7-length5-slot4', shared_delta=True, virtual_range=[4608, 5119])
    return result
from copy import deepcopy as fixed_shifts_deepcopy
import re as fixed_shifts_re
from unittest.mock import patch as fixed_shifts_patch
from accelerated.compact_core import metadata as fixed_shifts_metadata
from accelerated import register_alias as fixed_shifts_register_alias

def fixed_shifts_plan(image):
    shifts = [r for r in fixed_shifts_metadata(image) if r['legal'] and (r['shiftleft'] or r['shiftright'])]
    left = sorted({r['word'] >> 20 & 31 for r in shifts if r['shiftleft']})
    right = sorted({r['word'] >> 20 & 31 for r in shifts if r['shiftright']})
    fallback = any((r['word'] & 127 != 19 or (r['shiftright'] and r['arithmetic']) for r in shifts))
    return dict(applied=bool(shifts) and (not fallback) and (max(len(left), len(right)) <= 4), left=left, right=right, fallback_general_shifter=fallback or max(len(left), len(right)) > 4, shifts=shifts)

def fixed_shifts_source(text, image):
    p = fixed_shifts_plan(image)
    if not p['applied']:
        return text
    assert '  signal shift_left,shift_right:shift_t;' in text
    decl = '  signal fixed_shift_left,fixed_shift_right:bit_vector(31 downto 0);\n'
    for name in ('left', 'right'):
        decl += ''.join((f'  signal shift_{name}_{count}:bit;\n' for count in p[name]))
    text = text.replace('  signal shift_left,shift_right:shift_t;', decl)
    text = fixed_shifts_re.sub('\\s*shift_left\\(0\\) <= a;shift_right\\(0\\) <= a;', '', text, count=1)
    text, n = fixed_shifts_re.subn('^  shift_(?:left|right)\\([1-5]\\) <=[^;]*;\\n', '', text, flags=fixed_shifts_re.M)
    assert n == 10, n
    text = text.replace('shift_left(5)', 'fixed_shift_left').replace('shift_right(5)', 'fixed_shift_right')
    extra = ''
    for name in ('left', 'right'):
        for count in p[name]:
            pcs = [r['pc'] // 4 for r in p['shifts'] if r['shift' + name] and r['word'] >> 20 & 31 == count]
            extra += f'  shift_{name}_{count} <= or_reduce(' + ' & '.join((f'select_pc({pc})' for pc in pcs)) + ');\n'
        for bit in range(32):
            terms = []
            for count in p[name]:
                index = bit - count if name == 'left' else bit + count
                if 0 <= index < 32:
                    terms.append(f'(shift_{name}_{count} and a({index}))')
            value = 'or_reduce(' + ' & '.join(terms) + ')' if terms else "'0'"
            extra += f'  fixed_shift_{name}({bit}) <= {value};\n'
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def fixed_shifts_cpu(image, ram_bytes=64, **options):
    original = fixed_shifts_register_alias.base_source

    def provider(*args, **kwargs):
        return fixed_shifts_source(original(*args, **kwargs), image)
    with fixed_shifts_patch.object(fixed_shifts_register_alias, 'base_source', provider):
        result = basic_sixteen_cpu(image, ram_bytes, **options)
    result.report = fixed_shifts_deepcopy(result.report)
    p = fixed_shifts_plan(image)
    p.pop('shifts')
    result.report['cpu']['fixed_ROM_shifts'] = p
    return result
from copy import deepcopy as compact_ram_mask_deepcopy
import re as compact_ram_mask_re
from unittest.mock import patch as compact_ram_mask_patch
from accelerated import register_alias as compact_ram_mask_register_alias

def compact_ram_mask_source(text):
    old = 'signal mask_stage:bit_vector(63 downto 0);'
    assert old in text
    text = text.replace(old, 'signal ram_store_word:bit_vector(15 downto 0);signal ram_store_lane:bit_vector(3 downto 0);')
    text, n = compact_ram_mask_re.subn('^  mask_stage\\(\\d+\\) <= latch\\([^;]*;\\n', '', text, flags=compact_ram_mask_re.M)
    assert n == 64, n
    text, n = compact_ram_mask_re.subn('com_en and store_stage and mask_stage\\((\\d+)\\)', lambda m: f'and_reduce(com_en & store_stage & ram_store_word({int(m[1]) // 4}) & ram_store_lane({int(m[1]) % 4}))', text)
    assert n == 64, n
    extra = ''
    for i in range(16):
        extra += f'  ram_store_word({i}) <= addr_stage(5 downto 2) = bits({i},4);\n'
    for lane in range(4):
        extra += f"  ram_store_lane({lane}) <= (store_size = bits(2,2)) or\n    ((store_size = bits(1,2)) and (addr_stage(1) = '{lane // 2}')) or\n    ((store_size = bits(0,2)) and (addr_stage(1 downto 0) = bits({lane},2)));\n"
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def compact_ram_mask_cpu(image, ram_bytes=64, **options):
    assert ram_bytes == 64
    original = compact_ram_mask_register_alias.base_source

    def provider(*args, **kwargs):
        return compact_ram_mask_source(original(*args, **kwargs))
    with compact_ram_mask_patch.object(compact_ram_mask_register_alias, 'base_source', provider):
        result = fixed_shifts_cpu(image, ram_bytes, **options)
    result.report = compact_ram_mask_deepcopy(result.report)
    result.report['cpu']['compact_ram_write_masks'] = dict(captured_mask_bits_removed=64, source='Existing captured address and store size; 16 word selects and four byte-lane selects', alignment_guard='Unaligned or invalid stores have execute_ok=0 and store_stage=0')
    return result
from copy import deepcopy as compact_ram_read_deepcopy
import re as compact_ram_read_re
from unittest.mock import patch as compact_ram_read_patch
from accelerated import register_alias as compact_ram_read_register_alias

def compact_ram_read_source(text):
    decl = '  type ram_read_lanes_t is array(0 to 3) of bit_vector(7 downto 0);\n  signal ram_read_lanes:ram_read_lanes_t;\n  signal ram_read_word_select:bit_vector(15 downto 0);\n  signal ram_read_byte_select:bit_vector(3 downto 0);\n'
    assert '  signal regs:registers_t;' in text
    text = text.replace('  signal regs:registers_t;', decl + '  signal regs:registers_t;', 1)
    text, n = compact_ram_read_re.subn('^  ram_word\\(\\d+\\) <= [^;]*;\\n', '', text, flags=compact_ram_read_re.M)
    assert n == 32, n
    extra = ''
    for word in range(16):
        extra += f'  ram_read_word_select({word}) <= address(5 downto 2) = bits({word},4);\n'
    for lane in range(4):
        extra += f'  ram_read_byte_select({lane}) <= address(1 downto 0) = bits({lane},2);\n'
        for bit in range(8):
            terms = [f'(ram_read_word_select({word}) and ram({word * 4 + lane})({bit}))' for word in range(16)]
            extra += f'  ram_read_lanes({lane})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    for bit in range(8):
        terms = [f'(ram_read_byte_select({lane}) and ram_read_lanes({lane})({bit}))' for lane in range(4)]
        extra += f'  ram_word({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
        extra += f'  ram_word({bit + 8}) <= ram_read_lanes(3)({bit}) when address(1) else ram_read_lanes(1)({bit});\n'
        extra += f'  ram_word({bit + 16}) <= ram_read_lanes(2)({bit});\n'
        extra += f'  ram_word({bit + 24}) <= ram_read_lanes(3)({bit});\n'
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def compact_ram_read_cpu(image, ram_bytes=64, **options):
    assert ram_bytes == 64
    original = compact_ram_read_register_alias.base_source

    def provider(*args, **kwargs):
        return compact_ram_read_source(original(*args, **kwargs))
    with compact_ram_read_patch.object(compact_ram_read_register_alias, 'base_source', provider):
        result = compact_ram_mask_cpu(image, ram_bytes, **options)
    result.report = compact_ram_read_deepcopy(result.report)
    result.report['cpu']['compact_ram_read'] = dict(word_selectors=16, byte_lane_selectors=4, contract='Byte loads select any lane; aligned halfword selects low/high pair; aligned word returns all lanes. Unaligned halfword/word loads trap.')
    return result
from itertools import combinations as rom_wire_sharing_combinations
import re as rom_wire_sharing_re

def rom_wire_sharing_source(text):
    pattern = '^  ([\\w()\\d]+) <= or_reduce\\((select_pc\\(\\d+\\)(?: & select_pc\\(\\d+\\))*)\\);$'
    matches = [m for m in rom_wire_sharing_re.finditer(pattern, text, rom_wire_sharing_re.M) if not m[1].startswith('d_word(')]
    if not matches:
        return text
    original = [set(map(int, rom_wire_sharing_re.findall('select_pc\\((\\d+)\\)', m[2]))) for m in matches]
    rows = [set(r) for r in original]
    base = max(set.union(*rows)) + 1
    groups = []
    while True:
        leaves = [{v for v in r if v < base} for r in rows]
        candidates = {frozenset(a & b) for a, b in rom_wire_sharing_combinations(leaves, 2)}
        best = None
        for group in candidates:
            if not 3 <= len(group) <= 64:
                continue
            use = [i for i, r in enumerate(leaves) if group <= r]
            key = (len(group) * (len(use) - 1) - len(use) - 10, len(group), tuple(sorted(group)))
            if best is None or key > best[0]:
                best = (key, group, use)
        if best is None or best[0][0] <= 0:
            break
        _, group, use = best
        n = base + len(groups)
        groups.append(tuple(sorted(group)))
        for i in use:
            rows[i].difference_update(group)
            rows[i].add(n)
    if not groups:
        return text
    for old, row in zip(original, rows):
        expanded = set()
        for n in row:
            expanded.update(groups[n - base] if n >= base else [n])
        assert expanded == old

    def expr(n):
        return f'rom_union({n - base})' if n >= base else f'select_pc({n})'
    replacements = {m[0]: f'  {m[1]} <= or_reduce(' + ' & '.join((expr(n) for n in sorted(row))) + ');' for m, row in zip(matches, rows)}
    text = rom_wire_sharing_re.sub(pattern, lambda m: replacements.get(m[0], m[0]), text, flags=rom_wire_sharing_re.M)
    decl = f'  signal rom_union:bit_vector({len(groups) - 1} downto 0);\n'
    text = text.replace('  signal regs:registers_t;', decl + '  signal regs:registers_t;', 1)
    extra = ''
    for i, g in enumerate(groups):
        extra += f'  rom_union({i}) <= or_reduce(' + ' & '.join((f'select_pc({n})' for n in g)) + ');\n'
    print('ROM SHARE', len(groups), 'wires', sum(map(len, original)), '->', sum(map(len, rows)) + sum(map(len, groups)), flush=True)
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'
from copy import deepcopy as compact_rom_decode_deepcopy
import re as compact_rom_decode_re
from unittest.mock import patch as compact_rom_decode_patch
from accelerated import register_alias as compact_rom_decode_register_alias

def compact_rom_decode_source(text):
    share = rom_wire_sharing_source
    text = share(text)
    pattern = '^  select_pc\\((\\d+)\\) <= pc_q\\((\\d+) downto 2\\) = bits\\((\\d+),(\\d+)\\);$'
    matches = list(compact_rom_decode_re.finditer(pattern, text, compact_rom_decode_re.M))
    assert matches
    width = int(matches[0][4])
    split = width // 2
    assert all((int(m[1]) == int(m[3]) and int(m[4]) == width for m in matches))
    lo = 1 << split
    hi = 1 << width - split
    decl = f'  signal rom_pc_lo:bit_vector({lo - 1} downto 0);signal rom_pc_hi:bit_vector({hi - 1} downto 0);\n'
    text = text.replace('  signal regs:registers_t;', decl + '  signal regs:registers_t;', 1)
    text = compact_rom_decode_re.sub(pattern, lambda m: f'  select_pc({m[1]}) <= rom_pc_hi({int(m[1]) // lo}) and rom_pc_lo({int(m[1]) % lo});', text, flags=compact_rom_decode_re.M)
    extra = ''
    for i in range(lo):
        extra += f'  rom_pc_lo({i}) <= pc_q({split + 1} downto 2) = bits({i},{split});\n'
    for i in range(hi):
        extra += f'  rom_pc_hi({i}) <= pc_q({width + 1} downto {split + 2}) = bits({i},{width - split});\n'
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def compact_rom_decode_cpu(image, ram_bytes=64, **options):
    original = compact_rom_decode_register_alias.base_source

    def provider(*args, **kwargs):
        return compact_rom_decode_source(original(*args, **kwargs))
    with compact_rom_decode_patch.object(compact_rom_decode_register_alias, 'base_source', provider):
        result = compact_ram_read_cpu(image, ram_bytes, **options)
    result.report = compact_rom_decode_deepcopy(result.report)
    result.report['cpu']['shared_instruction_predecode'] = True
    result.report['cpu']['shared_ROM_output_groups'] = True
    return result
from copy import deepcopy as compact_text_mux_deepcopy
import re as compact_text_mux_re
from unittest.mock import patch as compact_text_mux_patch
from accelerated import register_alias as compact_text_mux_register_alias

def compact_text_mux_source(text):
    assert '  signal program_delta:' in text
    decl = '  type text_slots_mux_t is array(0 to 15) of bit_vector(5 downto 0);\n  type text_columns_delta_t is array(0 to 17) of bit_vector(5 downto 0);\n  signal text_slots_mux:text_slots_mux_t;signal text_columns_delta:text_columns_delta_t;\n'
    text = text.replace('  signal regs:registers_t;', decl + '  signal regs:registers_t;', 1)
    pattern = '^  program_chars\\((\\d+)\\) <= latch\\(program_delta,program_pulse and program_write_stage and program_select\\((\\d+)\\)\\);$'

    def bank(m):
        i = int(m[1])
        assert i == int(m[2])
        return f'  program_chars({i}) <= latch(text_columns_delta({i % 18}),program_slot_select({i // 18}));'
    text, n = compact_text_mux_re.subn(pattern, bank, text, flags=compact_text_mux_re.M)
    assert n == 288, n
    text, n = compact_text_mux_re.subn('^  program_code\\(\\d+\\) <= [^;]*;\\n', '', text, flags=compact_text_mux_re.M)
    assert n == 6, n
    text = text.replace('or_reduce(program_select)', '(program_read_address(4 downto 0) < bits(18,5))')
    extra = ''
    for col in range(18):
        for bit in range(6):
            extra += f'  text_columns_delta({col})({bit}) <= and_reduce(program_delta({bit}) & program_column_select({col}) & program_pulse & program_write_stage);\n'
    for slot in range(16):
        for bit in range(6):
            terms = [f'(program_column_select({col}) and program_chars({slot * 18 + col})({bit}))' for col in range(18)]
            extra += f'  text_slots_mux({slot})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    for bit in range(6):
        terms = [f'(program_slot_select({slot}) and text_slots_mux({slot})({bit}))' for slot in range(16)]
        extra += f'  program_code({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    return text.rsplit('end;', 1)[0] + extra + 'end;\n'

def compact_text_mux_cpu(image, ram_bytes=64, **options):
    original = compact_text_mux_register_alias.base_source

    def provider(*args, **kwargs):
        return compact_text_mux_source(original(*args, **kwargs))
    with compact_text_mux_patch.object(compact_text_mux_register_alias, 'base_source', provider):
        result = compact_rom_decode_cpu(image, ram_bytes, **options)
    result.report = compact_text_mux_deepcopy(result.report)
    result.report['cpu']['factored_text_read_write'] = True
    return result
