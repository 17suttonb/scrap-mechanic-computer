import json
from accelerated.circuit import GATE_UUID, encode_blueprint
from accelerated.clock import attach_clock
from accelerated.export_cpu import export_cpu
from accelerated.terminal_layout import arrange_terminal
from accelerated.tune_terminal_clock import configure_clock

def export_async(build, program, destination, cpu_ticks=6, service_ticks=8, diagnostics=False):
    settings = [cpu_ticks] * 4 if isinstance(cpu_ticks, int) else list(cpu_ticks)
    folder = export_cpu(build, program, destination, settings[0], minimal_panel=True)
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    bp = read('blueprint.json')
    cm = read('circuit-map.json')
    packing = read('packing.json')
    ports = read('port-map.json')
    primary = read('clock-map.json')
    timer_settings = {t['controllerId']: value for t, value in zip(primary['timers'], settings)}
    for part in bp['bodies'][0]['childs']:
        if part.get('controller', {}).get('id') in timer_settings:
            value = timer_settings[part['controller']['id']]
            part['controller'].update(seconds=value // 40, ticks=value % 40)
    primary = configure_clock(primary, settings)
    service_ports = {'capture': ports['service_capture'], 'commit': ports['service_commit'], 'reset': ports['reset']}
    clock = attach_clock(bp, cm['nodes'], packing, service_ports, phase_ticks=service_ticks, minimal_panel=True, panel_origin_x=-90)
    by_id = {p['controller']['id']: p for b in bp['bodies'] for p in b['childs'] if 'controller' in p}
    manifest = {n['controllerId']: n for n in cm['nodes']}
    for name in ('run', 'reset'):
        receiver = by_id[clock['names'][name]]
        receiver.update(shapeId=GATE_UUID)
        receiver['controller'].update(mode=1, active=False)
        source = by_id[primary['names'][name]]['controller']
        source['controllers'] = (source['controllers'] or []) + [dict(id=clock['names'][name])]
        manifest[clock['names'][name]].update(kind='OR', region='logic_cube')
    clock['cycle_unit'] = 'Memory/console service cycle'
    clock['shared_controls'] = dict(run=primary['names']['run'], reset=primary['names']['reset'])
    clock['nominal_service_seconds'] = clock.pop('nominal_instruction_seconds')
    clock['nominal_service_ticks'] = clock.pop('nominal_instruction_ticks')
    clock['visible_controls'] = []
    clock['visible_indicators'] = []
    packing['wiring']['physical_wires'] += 2
    report = read('hdl-build.json')
    report.update(packing=packing, clock=primary, service_clock=clock, timer_blocks=8, physical_blocks=packing['wiring']['physical_blocks'], physical_wires=packing['wiring']['physical_wires'], feedback_relays=packing['wiring']['feedback_relays'])
    for name, value in (('circuit-map.json', cm), ('packing.json', packing), ('port-map.json', ports), ('clock-map.json', primary), ('service-clock-map.json', clock), ('hdl-build.json', report)):
        (folder / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    (folder / 'blueprint.json').write_bytes(encode_blueprint(bp))
    arrange_terminal(folder, async_diagnostics=diagnostics)
    return folder
