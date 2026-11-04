from accelerated.hdl import compile_source
from accelerated.async_bus import replace_once
from accelerated.fast_console import fast_service_source

def complete_service(ram_bytes=32):
    s = fast_service_source(ram_bytes)
    s = replace_once(s, 'phase_q,phase_stage,phase_next,phase_input:bit_vector(1 downto 0)', 'phase_q,phase_stage,phase_next,phase_input:bit_vector(2 downto 0)')
    for name in ('phase_next', 'phase_input', 'send_stage', 'clear_stage', 'done_stage'):
        line = next((l for l in s.splitlines() if l.strip().startswith(name + ' <=')))
        s = replace_once(s, line, line.replace('bits(0,2)', 'bits(0,3)').replace('bits(1,2)', 'bits(1,3)').replace('bits(2,2)', 'bits(2,3)').replace('bits(3,2)', 'bits(3,3)'))
    s = replace_once(s, 'begin\n', '  signal complete_q,prompt_stage,release_stage,release_clear_stage:bit;\nbegin\n  complete_q <= latch(address_stage(1),com_en and command_start,reset);\n  prompt_stage <= latch(complete_q and (phase_q = bits(3,3)),cap_en,reset);\n  release_stage <= latch(complete_q and (phase_q = bits(5,3)),cap_en,reset);\n  release_clear_stage <= latch(complete_q and (phase_q = bits(6,3)),cap_en,reset);\n')
    s = replace_once(s, 'address_stage(2) and\n              (address_stage(1 downto 0) /= bits(3,2));', 'address_stage(2);')
    s = replace_once(s, 'command_start <= extended and not address_stage(1);', 'command_start <= extended and (not address_stage(1) or address_stage(0));')
    s = replace_once(s, 'ack_start <= extended and address_stage(1);', 'ack_start <= extended and address_stage(1) and not address_stage(0);')
    s = replace_once(s, 'address_stage(2) and address_stage(1) and not address_stage(0);', 'address_stage(2) and address_stage(1);')
    s = replace_once(s, 'phase_q = bits(3,3) else phase_q +', '(phase_q = bits(7,3)) or ((phase_q = bits(3,3)) and not complete_q) else phase_q +')
    s = replace_once(s, 'clear_stage <= latch(phase_q = bits(2,3),cap_en,reset);', 'clear_stage <= latch((phase_q = bits(2,3)) or (complete_q and (phase_q = bits(4,3))),cap_en,reset);')
    s = replace_once(s, 'done_stage <= latch((phase_q = bits(3,3)) and ack_operation,cap_en,reset);', 'done_stage <= latch(((phase_q = bits(3,3)) and ack_operation) or ((phase_q = bits(7,3)) and complete_q),cap_en,reset);')
    s = replace_once(s, 'gpio_input(0) <= data_stage;', 'gpio_input(0) <= bits(2,8) when prompt_stage else data_stage;')
    line = next((l for l in s.splitlines() if l.strip().startswith('gpio_bytes(0) <= latch')))
    s = replace_once(s, line, line.replace('or command_start)', 'or command_start or prompt_stage)'))
    s = replace_once(s, 'bits(1,8) when send_stage and not ack_operation else data_stage', 'bits(1,8) when (send_stage and not ack_operation) or prompt_stage else data_stage')
    line = next((l for l in s.splitlines() if l.strip().startswith('gpio_bytes(2) <= latch')))
    s = replace_once(s, line, line.replace('or (clear_stage and not ack_operation))', 'or (clear_stage and not ack_operation) or prompt_stage)'))
    s = replace_once(s, 'gpio_input(3) <= bits(0,8) when clear_stage and ack_operation else bits(32,8) when send_stage and ack_operation else', 'gpio_input(3) <= bits(0,8) when (clear_stage and ack_operation) or release_clear_stage else bits(32,8) when (send_stage and ack_operation) or release_stage else')
    line = next((l for l in s.splitlines() if l.strip().startswith('gpio_bytes(3) <= latch')))
    s = replace_once(s, line, line.replace('or (clear_stage and ack_operation))', 'or (clear_stage and ack_operation) or release_stage or release_clear_stage)'))
    result = compile_source(s, top='rv_memory_service', wide_gates=True, latch_style='pulse_xor')
    result.report['complete_console'] = True
    return result
