from collections import Counter
from accelerated.hdl import Build, compile_source
from accelerated.pixel_terminal import source as old_source, pixel_cells, taller_pages
from accelerated.cpu_components import xor_cpu_storage_prepare_source as prepare_source
from accelerated.wide_terminal import pages
from accelerated.terminal import FAULT
from accelerated.font5x7 import FONT
from accelerated.font_rom import decoder, declarations

def source(boot_text, help_text, columns=12, expose_length=True, rows=4):
    count = columns * rows
    rb = (rows - 1).bit_length()
    gw = len(FONT[' '][0])
    gh = len(FONT[' '])
    cw = gw + 1
    ch = gh + 1
    width = columns * cw
    height = rows * ch
    boot_text = pages(boot_text, columns)
    help_text = pages(help_text, columns)
    fault = pages(FAULT, columns)
    original = old_source(boot_text, help_text, columns, expose_length, font=FONT, rows=rows)
    boot_text, help_text, fault = (taller_pages(page, rows) for page in (boot_text, help_text, fault))
    s = original[:original.index('  font_code(0)')]
    s = s.replace(f'terminal_pixels:out bit_vector({columns * 4 * 24 - 1} downto 0)', f'terminal_pixels:out bit_vector({width * height - 1} downto 0)')
    s = s.replace('  signal pixel_q,pixel_value,pixel_data:pixel_cells_t;', '')
    s = s.replace('  signal font_pixels,font_raw,font_held:font_cells_t; signal font_select:font_select_t;', '')
    s = s.replace('  signal font_code:font_codes_t; signal font_decode:font_decode_t;', '')
    decl = f'  type char_cells_t is array(0 to {count - 1}) of bit_vector(5 downto 0);\n  type glyph_select_t is array(0 to {count - 1}) of bit_vector(63 downto 0);\n  signal chars_q,chars_value,chars_data:char_cells_t;\n  signal char_raw,char_held,char_data:words_t;\n  signal glyph_select:glyph_select_t;\n  {declarations(count, FONT)}\n'
    s = s.replace('begin\n', decl + 'begin\n', 1)
    for lane in range(4):
        for bit in range(6):
            if lane == 0:
                key = f'not code({bit})' if bit == 5 else f'code({bit})'
                cpu = f'not gpio({bit})' if bit == 5 else f'gpio({bit})'
                terms = [f'({key} and not gpio(16) and (code >= bits(32,7)))', f'({cpu} and gpio(16) and not gpio(17) and (gpio(7 downto 0) >= bits(32,8)))']
                for code, flag in [(30, 'gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(2,8))'), (37, 'gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(3,8))'), (13, 'gpio(16) and gpio(17) and gpio(7)')]:
                    if code >> bit & 1:
                        terms.append('(' + flag + ')')
            else:
                digit = f'bcd({(3 - lane) * 4 + bit})' if bit < 4 else "'1'" if bit == 4 else "'0'"
                terms = [f'({digit} and gpio(16) and gpio(17))']
                if lane < 3 and 50 >> bit & 1:
                    terms.append('(gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(3,8)))')
            s += f'  char_raw({lane})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
        s += f'  char_held({lane}) <= latch(char_raw({lane}),cap_en,reset);\n'
        s += f'  char_data({lane}) <= char_held({lane}) when not fault_stage else bits(0,6);\n'
    controls = [line for line in original.splitlines() if line.strip().startswith(('draw_select(', 'scroll_select(', 'pixel_write('))]
    assert len([line for line in controls if line.strip().startswith('pixel_write(')]) == count
    s += '\n'.join(controls) + '\n'
    for i in range(count):
        col = i % columns
        row = i // columns
        boot = ord(''.join(boot_text)[i]) - 32
        help_code = ord(''.join(help_text)[i]) - 32
        bad = ord(''.join(fault)[i]) - 32
        lanes = [0] + ([col] if 1 <= col <= 3 else [])
        s += f'  chars_value({i}) <= chars_q({i}) xor bits({boot},6);\n'
        for bit in range(6):
            terms = [f'(char_data({lane})({bit}) and draw_select({lane})({i}))' for lane in lanes]
            if row < rows - 1:
                terms.append(f'(scroll_select({i}) and chars_value({i + columns})({bit}))')
            if boot >> bit & 1:
                terms.append('pixel_page(0)')
            if help_code >> bit & 1:
                terms.append('pixel_page(1)')
            if bad >> bit & 1:
                terms.append('fault_stage')
            expr = 'or_reduce(' + ' & '.join(terms) + ')'
            if boot >> bit & 1:
                expr = f'(not {expr}) and pixel_running'
            s += f'  chars_data({i})({bit}) <= {expr};\n'
        s += f'  chars_q({i}) <= latch(chars_data({i}),com_en and pixel_write({i}),reset);\n'
        s += decoder(i, FONT)
    for y in range(height):
        for x in range(width):
            if x % cw == gw:
                expr = "'0'"
            elif y % ch == gh:
                expr = f'(cursor_row = bits({y // ch},{rb})) and (col_q = bits({x // cw},{(columns - 1).bit_length()})) and not ready and not faulted'
            else:
                expr = f'font_out_{y // ch * columns + x // cw}({y % ch * gw + x % cw})'
            s += f'  terminal_pixels({y * width + x}) <= {expr};\n'
    if expose_length:
        s += '  keyboard_length <= length_q;\n'
    return s + 'end;\n'

def terminal(boot_text, *, help_text, columns=12, expose_length=True, latch_style='pulse_xor', rows=4):
    boot_text = pages(boot_text, columns)
    help_text = pages(help_text, columns)
    gw = len(FONT[' '][0])
    gh = len(FONT[' '])
    cw = gw + 1
    ch = gh + 1
    width = columns * cw
    height = rows * ch
    text, stored = prepare_source(source(boot_text, help_text, columns, expose_length, rows), targets='(?:chars_q\\(\\d+\\)|line\\(i\\))')
    with pixel_cells(stored):
        b = compile_source(text, top='rv_terminal', wide_gates=True, latch_style=latch_style)
    c = b.circuit.clone()
    p = b.ports['terminal_pixels']
    blank = [i for i in range(width * height) if i % width % cw == gw]
    parents = {n: [] for n in c.nodes}
    for a, z in c.edges:
        parents[z].append(a)
    zero = p['node_ids'][blank[0]]
    remove = set()
    used = set()
    for i, out in enumerate(p['node_ids']):
        if i in blank:
            p['node_ids'][i] = zero
            if out != zero:
                remove.add(out)
        elif i // width % ch != gh:
            assert len(parents[out]) == 1
            parent = parents[out][0]
            if parent not in used:
                p['node_ids'][i] = parent
                remove.add(out)
                used.add(parent)
    assert not any((a in remove for a, z in c.edges))
    c.remove(remove)
    c.validate()
    b.report.update(terminal_boot=list(boot_text), terminal_help=list(help_text), terminal_geometry=dict(width=width, height=height, columns=columns, rows=rows, blank_pixels=blank, glyph_width=gw, glyph_height=gh, cell_width=cw, cell_height=ch, font=FONT), blocks=len(c.nodes), wires=len(c.edges), gates=dict(Counter((n['kind'] for n in c.nodes.values()))))
    return Build(c, b.ports, b.report, b.source)
