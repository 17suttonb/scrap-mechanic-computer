import json
from pathlib import Path
from accelerated.clock import export_clocked

def export_cpu(build, program, destination, phase_ticks=10, simulation=None, *, minimal_panel=False, bind_ports=None):
    folder = export_clocked(build, destination, phase_ticks=phase_ticks, minimal_panel=minimal_panel, bind_ports=bind_ports)
    clock = json.loads((folder / 'clock-map.json').read_text())
    clock['cycle_unit'] = 'CPU microstep'
    clock['nominal_microstep_seconds'] = clock.pop('nominal_instruction_seconds')
    clock['nominal_microstep_ticks'] = clock.pop('nominal_instruction_ticks')
    report = json.loads((folder / 'hdl-build.json').read_text())
    report['clock'] = clock
    report['cpu']['expected_demo'] = simulation['final'] if simulation else None
    description = json.loads((folder / 'description.json').read_text())
    description['description'] = 'RV32E integer CPU with program ROM, RAM and GPIO. Override enable OFF. RUN OFF; RESET ON/OFF; RUN ON. ECALL/EBREAK halt.'
    for name, value in (('clock-map.json', clock), ('hdl-build.json', report), ('description.json', description)):
        (folder / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    (folder / 'program.bin').write_bytes(program.image)
    (folder / 'program.s').write_text(program.source, encoding='utf-8')
    (folder / 'program.lst').write_text('\n'.join(program.listing), encoding='utf-8')
    if simulation:
        (folder / 'cpu-simulation.json').write_text(json.dumps(simulation, indent=2), encoding='utf-8')
    text = f'RV32E CPU - IN-GAME RUN\n\nThis executes real RV32E integer instructions from its ROM.\n1. Leave OVERRIDE ENABLE OFF; instruction override switches are diagnostic inputs.\n2. RUN, MAN CAP, MAN COM OFF. RESET ON, allow state to clear, then RESET OFF.\n3. RUN ON. RETIRED counts completed instructions; MICROSTEP shows controller state.\n4. EBREAK/ECALL halt: HALTED ON, FAULTED OFF, TRAP CAUSE 3/11.\n   Other trap causes indicate an illegal instruction, alignment or access fault.\nTimers are {phase_ticks} ticks per phase, nominally {phase_ticks / 10:g}s per CPU microstep.\nAn instruction takes multiple microsteps. This is not the instruction frequency.\n\nRAM: 0x1000 upward. GPIO word: 0xFFFFFF00. Input byte: 0xFFFFFF10.\nDebug address = register*4 + little-endian byte; DEBUG DATA reads that byte.\nTo stop, RUN OFF and wait for MANUAL OK. MAN CAP/COM can then step microstates.\n'
    if simulation:
        text += f"\nExpected halt after {simulation['microsteps']} microsteps; timer-only estimate {simulation['microsteps'] * phase_ticks / 10:g}s.\n"
        text += 'Expected final outputs:\n' + ''.join((f'  {name}: {value} (0x{value:X})\n' for name, value in simulation['final'].items()))
    (folder / 'CLOCK.txt').write_text(text, encoding='utf-8')
    (folder / 'TEST.txt').write_text(text, encoding='utf-8')
    guide = Path(__file__).parent / 'CPU.md'
    if guide.exists():
        (folder / 'CPU-README.md').write_text(guide.read_text(), encoding='utf-8')
    return folder
