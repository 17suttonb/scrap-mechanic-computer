import re
from accelerated.terminal import terminal_source, FONT, FAULT

def pages(rows, columns):
    if len(rows) != 4 or any((len(row) > columns for row in rows)):
        raise ValueError('Page does not fit the terminal')
    return tuple((row.ljust(columns) for row in rows))

def source(boot_text, help_text, columns=16, expose_length=True):
    assert 8 <= columns <= 32
    cb = (columns - 1).bit_length()
    ab = cb + 2
    count = columns * 4
    width = columns * 4
    boot_text = pages(boot_text, columns)
    help_text = pages(help_text, columns)
    fault = pages(FAULT, columns)
    s = terminal_source(tuple((r[:8] for r in boot_text)), tuple((r[:8] for r in help_text)), expose_length=expose_length)
    s = s[:s.index('  bcd_rom(0)')]
    s = s.replace('terminal_pixels:out bit_vector(767 downto 0)', f'terminal_pixels:out bit_vector({width * 24 - 1} downto 0)')
    s = s.replace('array(0 to 31)', f'array(0 to {count - 1})')
    s = s.replace('col_store:bit_vector(2 downto 0)', f'col_store:bit_vector({cb - 1} downto 0)')
    s = s.replace('emit_addr,emit_addr_stage,input_addr_stage:bit_vector(4 downto 0)', f'emit_addr,emit_addr_stage:bit_vector({ab - 1} downto 0); signal input_addr_stage:bit_vector(4 downto 0)')
    s = re.sub('(col_q\\s*(?:/=|=|\\+|-)\\s*)bits\\((\\d+),3\\)', lambda m: m[1] + f'bits({(columns - 1 if int(m[2]) == 7 else m[2])},{cb})', s)
    a = s.index('  col_next <=')
    b = s.index(';', a)
    s = s[:a] + s[a:b].replace(',3)', f',{cb})') + s[b:]
    s = s.replace('col_next xor bits(1,3)', f'col_next xor bits(1,{cb})').replace('col_store xor bits(1,3)', f'col_store xor bits(1,{cb})')
    s = s.replace('row_q - bits(1,2)) & bits(7,3)', f'row_q - bits(1,2)) & bits({columns - 1},{cb})')
    s = s.replace('target_row & bits(0,3)', f'target_row & bits(0,{cb})')
    s = s.replace('type bcd_t is array(0 to 255)', 'type bcd_t is array(0 to 127)')
    s = s.replace('bcd <= bcd_rom(to_integer(number_abs));', 'bcd <= x"128" when number_abs(7) else bcd_rom(to_integer(number_abs(6 downto 0)));')
    for i in range(128):
        s += f'  bcd_rom({i}) <= x"{i // 100:X}{i // 10 % 10:X}{i % 10:X}";\n'
    for row in range(4):
        s += f'  row_select({row}) <= (base_q = bits({row},2)) and not faulted;\n'
    for i in range(count):
        boot = ord(''.join(boot_text)[i]) - 32
        help_code = ord(''.join(help_text)[i]) - 32
        col = i % columns
        lanes = [0] + ([col] if 1 <= col <= 3 else [])
        writes = {j: f'((emit_count_stage > bits({j},3)) and (emit_addr_stage = bits({(i // columns << cb) + col - j},{ab})))' for j in lanes}
        enable = ' or '.join([*writes.values(), f'(clear_stage and clear_row_stage = bits({i // columns},2))', 'or_reduce(page_stage)'])
        s += f'  cells({i}) <= storage({i}) xor bits({boot},6);\n'
        for bit in range(6):
            terms = [f'(emit_stage({j})({bit}) and {cond})' for j, cond in writes.items()]
            if boot >> bit & 1:
                terms.append('(page_stage = bits(1,2))')
            if help_code >> bit & 1:
                terms.append('(page_stage = bits(2,2))')
            s += f'  screen_input({i})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
        s += f'  storage({i}) <= latch(screen_input({i}) xor bits({boot},6),com_en and ({enable}),reset);\n'
        for bit in range(6):
            terms = [f'(cells({physical * columns + col})({bit}) and row_select({(physical - i // columns) % 4}))' for physical in range(4)]
            if ord(''.join(fault)[i]) - 32 >> bit & 1:
                terms.append('faulted')
            s += f'  visible({i})({bit}) <= or_reduce(' + ' & '.join(terms) + ');\n'
        for code in range(64):
            char = chr(code + 32)
            value = f'(visible({i}) = bits({code},6))' if char in FONT and char != ' ' else "'0'"
            s += f'  glyph_select({i})({code}) <= {value};\n'
    for y in range(24):
        for x in range(width):
            if x % 4 == 3:
                value = "'0'"
            elif y % 6 == 5:
                value = f'(cursor_row = bits({y // 6},2)) and (col_q = bits({x // 4},{cb})) and not ready and not faulted'
            else:
                terms = [f'glyph_select({y // 6 * columns + x // 4})({ord(char) - 32})' for char, glyph in FONT.items() if glyph[y % 6][x % 4] == '1']
                value = 'or_reduce(' + ' & '.join(terms) + ')' if terms else "'0'"
            s += f'  terminal_pixels({y * width + x}) <= {value};\n'
    if expose_length:
        s += '  keyboard_length <= length_q;\n'
    return s + 'end;\n'
