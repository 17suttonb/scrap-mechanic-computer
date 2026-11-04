from accelerated.async_bus import replace_once

def service_without_ram(source, ram_bytes=32):
    start = source.index('  memory_cells:')
    end = source.index('  gpio_input(0)', start)
    source = source[:start] + f'  unused_ram:for i in 0 to {ram_bytes - 1} generate ram(i) <= x"00"; end generate;\n' + source[end:]
    source = replace_once(source, 'ram(to_integer(address_stage)) when kind_stage = bits(0,2)', 'x"00" when kind_stage = bits(0,2)')
    source = replace_once(source, 'ram_zero <= ram(3) & ram(2) & ram(1) & ram(0);', "ram_zero <= (others => '0');")
    return source
