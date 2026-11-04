import re
from contextlib import contextmanager
from unittest.mock import patch
from accelerated.hdl import Builder
from accelerated.wide_terminal import source as character_source, pages
from accelerated.terminal import FONT, FAULT
from accelerated.cpu_components import xor_cpu_storage_cells as cells

def bitmap(text, cell, font=FONT, basis=None):
    char = ''.join(text)[cell]
    glyph = font.get(char, font[' '])
    basis = basis or [(x, y) for y in range(len(glyph)) for x in range(len(glyph[0]))]
    return sum((int(glyph[y][x]) << i for i, (x, y) in enumerate(basis)))

def source(boot_text, help_text, columns=16, expose_length=True, *, font=FONT, basis=None, glyph_mapping=None):
    gw = len(font[' '][0])
    gh = len(font[' '])
    cw = gw + 1
    ch = gh + 1
    basis = basis or [(x, y) for y in range(gh) for x in range(gw)]
    bits = len(basis)
    glyph_mapping = glyph_mapping or [1 << i for i in range(gw * gh)]
    cb = (columns - 1).bit_length()
    ab = cb + 2
    count = columns * 4
    width = columns * cw
    height = 4 * ch
    boot_text = pages(boot_text, columns)
    help_text = pages(help_text, columns)
    fault = pages(FAULT, columns)
    s = character_source(boot_text, help_text, columns, expose_length).split('  row_select(0)')[0]
    s = s.replace(f'terminal_pixels:out bit_vector({columns * 4 * 24 - 1} downto 0)', f'terminal_pixels:out bit_vector({width * height - 1} downto 0)')
    s = s.replace('signal cells,storage,visible,screen_input:cells_t; ', '').replace(' signal glyph_select:select_t;', '')
    s = s.replace('  signal row_select:bit_vector(3 downto 0);', '')
    decl = f'  type pixel_cells_t is array(0 to {count - 1}) of bit_vector({bits - 1} downto 0);\n  type font_cells_t is array(0 to 3) of bit_vector({bits - 1} downto 0);\n  type font_select_t is array(0 to 4) of bit_vector(63 downto 0);\n  type font_codes_t is array(0 to 4) of bit_vector(5 downto 0);\n  type font_decode_t is array(0 to 4) of bit_vector({bits - 1} downto 0);\n  type draws_t is array(0 to 3) of bit_vector({count - 1} downto 0);\n  signal pixel_q,pixel_value,pixel_data:pixel_cells_t;\n  signal font_pixels,font_raw,font_held:font_cells_t; signal font_select:font_select_t;\n  signal font_code:font_codes_t; signal font_decode:font_decode_t;\n  signal draw_select:draws_t;signal clear_flags:bit_vector(3 downto 0);\n  signal print_row,command_row,back_row:bit_vector(1 downto 0);\n  signal back_col:bit_vector({cb - 1} downto 0);\n  signal col_plus,col_minus:bit_vector({cb - 1} downto 0);\n  signal col_cases:bit_vector(5 downto 0);signal col_select:bit_vector(6 downto 0);\n  signal row_cases:bit_vector(3 downto 0);signal row_masks:bit_vector(4 downto 0);\n  signal row_plus,row_minus:bit_vector(1 downto 0);\n  signal scroll_stage,fault_seen,fault_pending,fault_stage,pixel_running:bit;\n  signal pixel_page:bit_vector(1 downto 0);\n  signal pixel_write,scroll_select:bit_vector({count - 1} downto 0);\n  signal draw_row:bit_vector(1 downto 0);\n'
    s = s.replace('begin\n', decl + 'begin\n', 1)
    s = re.sub('  col_next <=.*?;', '', s, count=1, flags=re.S)
    s += '  col_cases(0) <= boot_event or help_event or prompt_event;\n  col_cases(1) <= number_event;col_cases(2) <= error_event;\n  col_cases(3) <= key_back;col_cases(4) <= advance;col_cases(5) <= key_print or cpu_print;\n  col_select(0) <= col_cases(0);\n  col_select(6) <= not or_reduce(col_cases);\n'
    for i in range(1, 6):
        s += f'  col_select({i}) <= col_cases({i}) and not or_reduce(col_cases({i - 1} downto 0));\n'
    for bit in range(cb):
        inc = 'not col_q(0)' if bit == 0 else f'col_q({bit}) xor and_reduce(col_q({bit - 1} downto 0))'
        dec = 'not col_q(0)' if bit == 0 else f'col_q({bit}) xor not or_reduce(col_q({bit - 1} downto 0))'
        s += f'  col_plus({bit}) <= ({inc}) and (col_q /= bits({columns - 1},{cb}));\n'
        if not columns - 1 >> bit & 1:
            dec = f'({dec}) and (col_q /= bits(0,{cb}))'
        s += f'  col_minus({bit}) <= {dec};\n'
        terms = [f'(col_select(3) and col_minus({bit}))', f'(col_select(5) and col_plus({bit}))', f'(col_select(6) and col_q({bit}))']
        for i, value in [(0, 1), (1, 4), (2, 3)]:
            if value >> bit & 1:
                terms.append(f'col_select({i})')
        s += f'  col_next({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    s = s.replace('row_q + bits(1,2) when advance else row_q;', 'row_q when rotate else row_q + bits(1,2) when advance else row_q;')
    s = s.replace('target_row <= row_q + bits(1,2) when before_line or newline_event else row_q;', 'target_row <= row_q when rotate else row_q + bits(1,2) when before_line or newline_event else row_q;')
    s = s.replace('clear_row <= row_q + bits(1,2);', 'clear_row <= row_q when rotate else row_q + bits(1,2);')
    s = s.replace('base_next <= bits(0,2) when boot_event or help_event else base_q + bits(1,2) when rotate else base_q;', 'base_next <= bits(0,2);')
    s = re.sub('  row_next <=.*?;', '', s, count=1, flags=re.S)
    s += '  row_cases(0) <= boot_event;row_cases(1) <= help_event;\n  row_cases(2) <= key_back and (col_q = bits(0,' + str(cb) + '));\n  row_cases(3) <= advance and (row_q /= bits(3,2));\n  row_masks(0) <= row_cases(0);row_masks(4) <= not or_reduce(row_cases);\n  row_plus(0) <= not row_q(0);row_plus(1) <= row_q(1) xor row_q(0);\n  row_minus(0) <= not row_q(0);row_minus(1) <= row_q(1) xor not row_q(0);\n'
    for i in range(1, 4):
        s += f'  row_masks({i}) <= row_cases({i}) and not or_reduce(row_cases({i - 1} downto 0));\n'
    for bit in range(2):
        terms = [f'(row_masks(2) and row_minus({bit}))', f'(row_masks(3) and row_plus({bit}))', f'(row_masks(4) and row_q({bit}))', 'row_masks(1)']
        if bit == 1:
            terms.append('row_masks(0)')
        s += f'  row_next({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    s = s.replace('else row_q & col_q;', 'else draw_row & col_q;')
    s += '  draw_row <= row_q - bits(1,2) when rotate and not before_line and not newline_event else row_q;\n  scroll_stage <= latch(rotate,cap_en,reset);\n  fault_pending <= faulted and not fault_seen;\n  fault_stage <= latch(fault_pending,cap_en,reset);\n  fault_seen <= latch(faulted,com_en,reset);\n  pixel_running <= not reset;\n  pixel_page <= page_stage when not fault_stage else bits(0,2);\n'
    s += f'  print_row <= row_q - bits(1,2) when (row_q = bits(3,2)) and (col_q = bits({columns - 1},{cb})) else row_q;\n  command_row <= row_q + bits(1,2) when (row_q /= bits(3,2)) and (col_q /= bits(0,{cb})) else row_q;\n  back_row <= row_q - bits(1,2) when col_q = bits(0,{cb}) else row_q;\n  back_col <= bits({columns - 1},{cb}) when col_q = bits(0,{cb}) else col_q - bits(1,{cb});\n'
    for row in range(4):
        s += f'  clear_flags({row}) <= latch(advance and (clear_row = bits({row},2)),cap_en,reset);\n'
    s += '  font_code(0) <= code(5 downto 0) xor bits(32,6);\n  font_code(1) <= gpio(5 downto 0) xor bits(32,6);\n  font_code(2) <= "01" & bcd(11 downto 8);\n  font_code(3) <= "01" & bcd(7 downto 4);\n  font_code(4) <= "01" & bcd(3 downto 0);\n'
    for lane in range(5):
        for code in range(64):
            char = chr(code + 32)
            expr = f'font_code({lane}) = bits({code},6)' if char in font and char != ' ' else "'0'"
            s += f'  font_select({lane})({code}) <= {expr};\n'
        for bit, (x, y) in enumerate(basis):
            terms = [f'font_select({lane})({ord(char) - 32})' for char, glyph in font.items() if glyph[y][x] == '1']
            s += f'  font_decode({lane})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
    for lane in range(4):
        for bit, (x, y) in enumerate(basis):
            if lane == 0:
                terms = [f'(font_decode(0)({bit}) and not gpio(16) and (code >= bits(32,7)))', f'(font_decode(1)({bit}) and gpio(16) and not gpio(17) and (gpio(7 downto 0) >= bits(32,8)))']
                for char, flag in [('>', 'gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(2,8))'), ('E', 'gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(3,8))'), ('-', 'gpio(16) and gpio(17) and gpio(7)')]:
                    if font[char][y][x] == '1':
                        terms.append('(' + flag + ')')
            else:
                terms = [f'(font_decode({lane + 1})({bit}) and gpio(16) and gpio(17))']
                if lane < 3 and font['R'][y][x] == '1':
                    terms.append('(gpio(16) and not gpio(17) and (gpio(7 downto 0) = bits(3,8)))')
            s += f'  font_raw({lane})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
        s += f'  font_held({lane}) <= latch(font_raw({lane}),cap_en,reset);\n'
        s += f'  font_pixels({lane}) <= font_held({lane}) when not fault_stage else bits(0,{bits});\n'
    for i in range(count):
        col = i % columns
        row = i // columns
        boot = bitmap(boot_text, i, font, basis)
        help_bits = bitmap(help_text, i, font, basis)
        bad = bitmap(fault, i, font, basis)
        lanes = [0] + ([col] if 1 <= col <= 3 else [])
        for j in range(4):
            if j not in lanes:
                s += f"  draw_select({j})({i}) <= '0';\n"
                continue
            if j:
                expr = f'(number_event' + (' or error_event' if j < 3 else '') + f') and (command_row = bits({row},2))'
            else:
                terms = [f'((key_print or cpu_print) and (print_row = bits({row},2)) and (col_q = bits({col},{cb})))', f'(key_back and (back_row = bits({row},2)) and (back_col = bits({col},{cb})))']
                if col == 0:
                    terms.append(f'((number_event or error_event or prompt_event) and (command_row = bits({row},2)))')
                expr = 'or_reduce(' + ' & '.join(terms) + ')'
            s += f'  draw_select({j})({i}) <= latch({expr},cap_en,reset);\n'
        writes = {j: f'draw_select({j})({i})' for j in lanes}
        selected = 'or_reduce(' + ' & '.join(writes.values()) + ')'
        s += f'  scroll_select({i}) <= scroll_stage and not {selected} and not fault_stage;\n'
        s += f'  pixel_write({i}) <= {selected} or scroll_stage or clear_flags({row}) or or_reduce(page_stage) or fault_stage;\n'
        s += f'  pixel_value({i}) <= pixel_q({i}) xor bits({boot},{bits});\n'
        for bit in range(bits):
            terms = [f'(font_pixels({j})({bit}) and {cond})' for j, cond in writes.items()]
            if row < 3:
                terms.append(f'(scroll_select({i}) and pixel_value({i + columns})({bit}))')
            if boot >> bit & 1:
                terms.append('pixel_page(0)')
            if help_bits >> bit & 1:
                terms.append('pixel_page(1)')
            if bad >> bit & 1:
                terms.append('fault_stage')
            expr = 'or_reduce(' + ' & '.join(terms) + ')'
            if boot >> bit & 1:
                expr = f'(not {expr}) and pixel_running'
            s += f'  pixel_data({i})({bit}) <= {expr};\n'
        s += f'  pixel_q({i}) <= latch(pixel_data({i}),com_en and pixel_write({i}),reset);\n'
    for y in range(height):
        for x in range(width):
            if x % cw == gw:
                expr = "'0'"
            elif y % ch == gh:
                expr = f'(cursor_row = bits({y // ch},2)) and (col_q = bits({x // cw},{cb})) and not ready and not faulted'
            else:
                mask = glyph_mapping[y % ch * gw + x % cw]
                cell = y // ch * columns + x // cw
                terms = [f'pixel_value({cell})({i})' for i in range(bits) if mask >> i & 1]
                expr = terms[0] if len(terms) == 1 else 'xor_reduce(' + ' & '.join(terms) + ')' if terms else "'0'"
            s += f'  terminal_pixels({y * width + x}) <= {expr};\n'
    if expose_length:
        s += '  keyboard_length <= length_q;\n'
    return s + 'end;\n'

@contextmanager
def pixel_cells(rows):
    with cells(rows, clear_delay=28, guard_updates=40) as stats:
        converted = Builder.latch

        def selected(b, data, enable, reset, token):
            style = b.latch_style
            if token.line in rows:
                b.latch_style = 'nand'
            try:
                return converted(b, data, enable, reset, token)
            finally:
                b.latch_style = style
        with patch.object(Builder, 'latch', selected):
            yield stats
