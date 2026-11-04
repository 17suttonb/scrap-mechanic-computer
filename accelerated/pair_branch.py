from copy import deepcopy
import re
from unittest.mock import patch
from accelerated import register_alias
from accelerated.async_bus import replace_once
from accelerated.compact_core import metadata as architectural_metadata
from accelerated.rom_specialize import metadata
from accelerated.rom_ranges import analyze
from accelerated.cpu_components import xor_cpu_storage_cpu as xor_cpu

def pairs(image):
    rows = architectural_metadata(image)
    try:
        proof = analyze(image)
    except AssertionError:
        proof = dict(passed=False, states={})
    result = {}
    for first, second in zip(rows, rows[1:]):
        if not first['legal'] or not second['legal']:
            continue
        if first['word'] & 127 != 19 or first['f3'] != 0 or (not first['rd']):
            continue
        if not second['branch'] or second['rb'] == first['rd']:
            continue
        forwarded = second['ra'] == first['rd']
        same_source = second['ra'] == first['ra'] and first['imm'] == 0
        if not (forwarded or same_source):
            continue
        if second['target'] & 3 or not 0 <= second['target'] < len(image):
            continue
        if first['pc'] + 8 >= len(image):
            continue
        if second['rb'] == 0:
            rhs = 0
        else:
            state = proof['states'].get(first['pc']) if proof.get('passed') else None
            values = state[second['rb']] if state is not None else None
            if values is None or len(values) != 1:
                continue
            rhs = next(iter(values))
        result[first['pc']] = dict(first=first, second=second, forwarded=forwarded, rhs=rhs, comparison=range_control(first['imm'], rhs, second['f3']))
    return result

def range_control(immediate, rhs, funct3):
    """Translate a wrapped ADDI-result comparison into a range on original A."""
    mask = 4294967295
    if funct3 in (0, 1):
        return dict(low=rhs - immediate & mask, high=0, mode=0, invert=funct3 == 1)
    signed = funct3 in (4, 5)
    low = (2147483648 if signed else 0) - immediate & mask
    length = rhs ^ 2147483648 if signed else rhs
    return dict(low=low if length else 0, high=low + length & mask if length else 0, mode=(2 if low + length > mask else 1) if length else 3, invert=funct3 in (5, 7))

def literal_less(value):
    """Shallow unsigned comparison with a literal; wide gates replace ripple."""
    terms = []
    for bit in range(32):
        if not value >> bit & 1:
            continue
        prefix = [f'a({j})' if value >> j & 1 else f'not a({j})' for j in range(31, bit, -1)]
        terms.append('and_reduce(' + ' & '.join([*prefix, f'not a({bit})']) + ')')
    return 'or_reduce(' + ' & '.join(terms) + ')' if terms else "'0'"

def source(text, image):
    selected = pairs(image)
    rows = deepcopy(metadata(image))
    for row in rows:
        row.update(pair_low=0, pair_high=0, pair_mode=0, pair_invert=0)
    for row in rows:
        pair = selected.get(row['pc'])
        if pair:
            second = pair['second']
            row.update(target=second['target'], link=row['pc'] + 8, **{'pair_' + key: int(value) for key, value in pair['comparison'].items()})

    def reduction(terms):
        return 'or_reduce(' + ' & '.join(terms) + ')' if terms else "'0'"
    for name, width in [('target', 32)]:
        for bit in range(width):
            terms = [f'select_pc({i})' for i, row in enumerate(rows) if row[name] >> bit & 1]
            line = f'  d_{name}({bit}) <= {reduction(terms)};'
            text, count = re.subn(f'^  d_{name}\\({bit}\\) <=[^\\n]*;', lambda _: line, text, flags=re.M)
            assert count == 1, (name, bit, count)
    for bit in range(32):
        expr = f'pc_q({bit})' if bit < 2 else 'pc_q(2) xnor pair_issue' if bit == 2 else 'pc_q(3) xor pair_link_carry' if bit == 3 else f'pc_q({bit}) xor and_reduce(pc_q({bit - 1} downto 3) & pair_link_carry)'
        text, count = re.subn(f'^  d_link\\({bit}\\) <=[^\\n]*;', lambda _: f'  d_link({bit}) <= {expr};', text, flags=re.M)
        assert count == 1
    groups = {}
    for pc, pair in selected.items():
        groups.setdefault(tuple(pair['comparison'].items()), []).append(pc)
    boundaries = sorted({v for control in groups for k, v in control if k in ('low', 'high')})
    declarations = '  signal pair_issue,pair_taken,pair_link_carry:bit;\n'
    declarations += '  signal pair_ret_increment:bit_vector(15 downto 0);\n'
    declarations += ''.join((f'  signal pair_less_{i}:bit;\n' for i in range(len(boundaries))))
    declarations += ''.join((f'  signal pair_site_{i},pair_check_{i}:bit;\n' for i in range(len(groups))))
    text = replace_once(text, '  signal regs:registers_t;', declarations + '  signal regs:registers_t;')
    extra = '\n  pair_issue <= ' + reduction([f'select_pc({pc // 4})' for pc in selected]) + ';\n'
    extra += '  pair_link_carry <= pair_issue or pc_q(2);\n'
    names = {v: f'pair_less_{i}' for i, v in enumerate(boundaries)}
    for value, name in names.items():
        extra += f'  {name} <= {literal_less(value)};\n'
    for i, (items, pcs) in enumerate(groups.items()):
        c = dict(items)
        lo, hi = (names[c['low']], names[c['high']])
        expr = f"(a = bits({c['low']},32))" if c['mode'] == 0 else f'(not {lo} and {hi})' if c['mode'] == 1 else f'(not {lo} or {hi})' if c['mode'] == 2 else "'0'"
        if c['invert']:
            expr = 'not ' + expr
        extra += f'  pair_check_{i} <= {expr};\n'
        extra += f"  pair_site_{i} <= {reduction([f'select_pc({pc // 4})' for pc in pcs])};\n"
    extra += '  pair_taken <= ' + reduction([f'(pair_site_{i} and pair_check_{i})' for i in range(len(groups))]) + ';\n'
    extra += '  pair_ret_increment <= bits(2,16) when pair_issue else bits(1,16);\n'
    text = text.rsplit('end;', 1)[0] + extra + 'end;\n'
    text = replace_once(text, 'd_target when d_jal or (d_branch and taken) else d_link;', 'd_target when d_jal or (d_branch and taken) or (pair_issue and pair_taken) else d_link;')
    text = replace_once(text, 'ret_q + bits(1,16)', 'ret_q + pair_ret_increment')
    return text

def cpu(image, ram_bytes=32, **options):
    original = register_alias.source

    def provider(*args, **kwargs):
        return source(original(*args, **kwargs), image)
    with patch.object(register_alias, 'source', provider):
        result = xor_cpu(image, ram_bytes, **options)
    result.report = deepcopy(result.report)
    result.report['cpu']['pair_branch'] = dict(kind='ADDI/move followed by branch; grouped shallow literal-range comparators parallel with ALU', sites=list(pairs(image)), maximum_retired_per_cycle=2, firmware_unchanged=True, second_instruction_can_be_entered_independently=True, comparisons={str(pc): p['comparison'] for pc, p in pairs(image).items()})
    return result
