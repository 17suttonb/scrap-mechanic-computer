from accelerated.async_bus import service_source, replace_once

def fast_service_source(ram_bytes=32):
    source = service_source(ram_bytes)
    source = replace_once(source, 'begin\n', '  signal phase_q,phase_stage,phase_next,phase_input:bit_vector(2 downto 0);\n  signal busy,extended,command_start,ack_start,ack_operation,numeric_operation,send_stage,clear_stage,done_stage,advance_input:bit;\n  signal read_stage:bit_vector(7 downto 0); signal index_stage:bit_vector(4 downto 0);\n  signal gpio_input:bytes_t;\nbegin\n  busy <= or_reduce(phase_q);\n  extended <= fire_stage and write_stage and (kind_stage = bits(1,2)) and address_stage(2) and\n              (address_stage(1 downto 0) /= bits(3,2));\n  command_start <= extended and not address_stage(1);\n  ack_start <= extended and address_stage(1);\n  phase_next <= bits(0,3) when phase_q = bits(6,3) else phase_q + bits(1,3) when busy else bits(0,3);\n  phase_stage <= latch(phase_next,cap_en,reset);\n  phase_input <= bits(1,3) when extended else phase_stage;\n  phase_q <= latch(phase_input,com_en,reset);\n  send_stage <= latch(phase_q = bits(2,3),cap_en,reset);\n  clear_stage <= latch(phase_q = bits(4,3),cap_en,reset);\n  done_stage <= latch(phase_q = bits(6,3),cap_en,reset);\n  ack_operation <= latch(ack_start,com_en and extended,reset);\n  numeric_operation <= latch(address_stage(0),com_en and extended,reset);\n  advance_input <= fire_stage and not write_stage and (kind_stage = bits(2,2)) and\n                   address_stage(2) and (address_stage(1 downto 0) = bits(0,2)) and keyboard_data(7);\n  index_stage <= latch(gpio_bytes(3)(4 downto 0) + bits(1,5),cap_en,reset);\n  read_stage <= latch(read_mux,cap_en,reset);\n')
    source = replace_once(source, '(request_one xnor request_two) and (request_two xor ack_q),cap_en,reset)', '(request_one xnor request_two) and (request_two xor ack_q) and not busy,cap_en,reset)')
    start = source.index('  gpio_cells:for i in 0 to 3 generate')
    end = source.index('  read_mux <=', start)
    gpio = []
    for i in range(4):
        legacy = f'(fire_stage and write_stage and (kind_stage = bits(1,2)) and not address_stage(2) and (address_stage(1 downto 0) = bits({i},2)))'
        if i == 0:
            value, enable = ('data_stage', legacy + ' or command_start')
        elif i == 1:
            value, enable = ('data_stage', legacy)
        elif i == 2:
            value = 'bits(0,8) when clear_stage and not ack_operation else\n                  bits(3,8) when send_stage and not ack_operation and numeric_operation else\n                  bits(1,8) when send_stage and not ack_operation else data_stage'
            enable = legacy + ' or (send_stage and not ack_operation) or (clear_stage and not ack_operation)'
        else:
            value = 'bits(0,8) when clear_stage and ack_operation else bits(32,8) when send_stage and ack_operation else\n                  gpio_bytes(3)(7 downto 5) & index_stage when advance_input else data_stage'
            enable = legacy + ' or advance_input or (send_stage and ack_operation) or (clear_stage and ack_operation)'
        gpio += [f'  gpio_input({i}) <= {value};', f'  gpio_bytes({i}) <= latch(gpio_input({i}),com_en and ({enable}),reset);']
    source = source[:start] + '\n'.join(gpio) + '\n' + source[end:]
    source = replace_once(source, 'gpio_bytes(to_integer(address_stage(1 downto 0))) when kind_stage = bits(1,2) else', 'gpio_bytes(to_integer(address_stage(1 downto 0))) when (kind_stage = bits(1,2)) and not address_stage(2) else')
    source = replace_once(source, 'response <= latch(read_mux,com_en and fire_stage,reset);', 'response <= latch(read_stage,com_en and fire_stage,reset);')
    source = replace_once(source, 'ack_q <= latch(token_stage,com_en and fire_stage,reset);', 'ack_q <= latch(token_stage,com_en and ((fire_stage and not extended) or done_stage),reset);')
    source = replace_once(source, 'begin\n', '  signal posted_barrier:bit;\nbegin\n  posted_barrier <= write_stage and (kind_stage = bits(1,2)) and\n                    address_stage(2) and address_stage(1) and not address_stage(0);\n')
    source = replace_once(source, 'done_stage <= latch(phase_q = bits(6,3),cap_en,reset);', 'done_stage <= latch((phase_q = bits(6,3)) and ack_operation,cap_en,reset);')
    source = replace_once(source, 'com_en and ((fire_stage and not extended) or done_stage)', 'com_en and ((fire_stage and not posted_barrier) or done_stage)')
    source = replace_once(source, 'begin\n', "  signal input_response:bit_vector(7 downto 0);\nbegin\n  input_response <= '0' & keyboard_data(6 downto 0) when address_stage(2) else keyboard_data;\n")
    source = replace_once(source, 'keyboard_data when (kind_stage = bits(2,2))', 'input_response when (kind_stage = bits(2,2))')
    from accelerated.short_console import shorten_output
    source = shorten_output(source)
    from accelerated.local_word_ram import service_without_ram
    source = service_without_ram(source, ram_bytes)
    return source
