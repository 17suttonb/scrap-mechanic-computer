from accelerated.async_bus import replace_once

def shorten_output(source):
    source = replace_once(source, 'phase_q,phase_stage,phase_next,phase_input:bit_vector(2 downto 0)', 'phase_q,phase_stage,phase_next,phase_input:bit_vector(1 downto 0)')
    for name in ('phase_next', 'phase_input'):
        line = next((l for l in source.splitlines() if l.strip().startswith(name + ' <=')))
        changed = line.replace('bits(6,3)', 'bits(3,2)').replace('bits(0,3)', 'bits(0,2)').replace('bits(1,3)', 'bits(1,2)')
        source = replace_once(source, line, changed)
    for name, old, new in (('send_stage', 2, 1), ('clear_stage', 4, 2), ('done_stage', 6, 3)):
        line = next((l for l in source.splitlines() if l.strip().startswith(name + ' <= latch')))
        source = replace_once(source, line, line.replace(f'bits({old},3)', f'bits({new},2)'))
    return source
