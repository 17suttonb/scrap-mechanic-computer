import copy
from decimal import Decimal, InvalidOperation
import json
from accelerated.circuit import Circuit, GATE_UUID, DEFAULT_BLUEPRINTS, encode_blueprint
from accelerated.supports import sparse_supports
from accelerated.signs import make_sign, choose_sign, sign_record
TIMER_UUID = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'

def timer_delay_ticks(phase_seconds=1, phase_ticks=None):
    """Return an exact positive 25 ms timer setting; explicit ticks take priority."""
    if phase_ticks is not None:
        if type(phase_ticks) is not int or not 1 <= phase_ticks <= 2399:
            raise ValueError('Clock phase delay must be 1..2399 ticks (25 ms per tick).')
        return phase_ticks
    if isinstance(phase_seconds, bool):
        raise ValueError('Clock phase delay must be a number of seconds.')
    try:
        ticks = Decimal(str(phase_seconds)) * 40
        if not ticks.is_finite() or ticks != ticks.to_integral_value() or (not 1 <= ticks <= 2399):
            raise ValueError('Clock phase delay must be 0.025..59.975 seconds in exact 0.025-second increments.')
        return int(ticks)
    except (InvalidOperation, TypeError):
        raise ValueError('Clock phase delay must be a number of seconds.') from None

def native_clock_period_ticks(settings):
    """Both-edge CAP period of THIS four-timer + NOR oscillator network.

    Each timer contributes configured N plus one sample/commit update; the
    inverter closes the loop with one further gate update. No settling/RUN
    startup latency or game wall-clock slowdown is included.
    """
    if len(settings) != 4:
        raise ValueError('Four timer settings required')
    for value in settings:
        timer_delay_ticks(phase_ticks=value)
    return sum(settings) + 5

def clock_network():
    c = Circuit('4004 four-phase timer clock')
    names = {}

    def node(name, kind, *parents, initial=False):
        i = c.add(kind, 0, 0, name)
        c.nodes[i]['initial'] = initial
        names[name] = i
        for p in parents:
            c.connect(p, i)
        return i
    run = node('run', 'INPUT')
    reset = node('reset', 'INPUT')
    man_cap = node('manual_capture', 'INPUT')
    man_com = node('manual_commit', 'INPUT')
    timers = [node('timer' + str(i), 'OR') for i in range(1, 5)]
    osc = node('oscillator', 'NOR', timers[3])
    for source, target in zip([osc] + timers, timers):
        c.connect(source, target)
    cap = node('capture_window', 'XOR', osc, timers[0])
    com = node('commit_window', 'XOR', timers[1], timers[2])
    idle = node('idle_window', 'XOR', timers[2], timers[3])
    manual_low = node('manual_switches_off', 'NOR', man_cap, man_com)
    request = node('run_request', 'AND', run, manual_low)
    not_request = node('not_run_request', 'NOR', request)
    not_reset = node('not_reset', 'NOR', reset)
    set_raw = node('set_raw', 'AND', request, idle)
    set_active = node('set_active', 'AND', set_raw, not_reset)
    clear_raw = node('clear_raw', 'AND', not_request, idle)
    clear = node('clear_active', 'OR', clear_raw, reset)
    active = node('active', 'NOR')
    opposite = node('not_active', 'NOR', set_active, active, initial=True)
    c.connect(clear, active)
    c.connect(opposite, active)
    auto_cap = node('auto_capture', 'AND', cap, active, not_reset)
    auto_com = node('auto_commit', 'AND', com, active, not_reset)
    manual_ok = node('manual_allowed', 'NOR', run, active, reset)
    mc = node('manual_capture_allowed', 'AND', man_cap, manual_ok)
    mm = node('manual_commit_allowed', 'AND', man_com, manual_ok)
    node('capture', 'OUTPUT', auto_cap, mc)
    node('commit', 'OUTPUT', auto_com, mm)
    node('auto_active', 'OUTPUT', active)
    node('manual_ok', 'OUTPUT', manual_ok)
    bp, manifest, packing = c._blueprint(ports={})
    ids = {m['editorId']: m['controllerId'] for m in manifest}
    names = {name: ids[i] for name, i in names.items()}
    children = [n for n in bp['bodies'][0]['childs'] if 'controller' in n]
    return (children, manifest, names, packing['wiring'])

def attach_clock(blueprint, manifest, packing, port_map, phase_seconds=1, *, phase_ticks=None, minimal_panel=False, bind_ports=None, panel_origin_x=None):
    """Modify a freshly exported blueprint and maps, preserving CPU part IDs."""
    total_ticks = timer_delay_ticks(phase_seconds, phase_ticks)
    seconds, ticks = divmod(total_ticks, 40)
    phase_seconds = total_ticks / 40
    if any((name not in port_map or port_map[name]['mode'] != 'in' or port_map[name]['width'] != 1 for name in ('capture', 'commit', 'reset'))):
        raise ValueError('Timer clock requires scalar capture, commit and reset inputs.')
    bindings = {'capture': 'capture', 'commit': 'commit', 'reset': 'reset'}
    bindings.update(bind_ports or {})
    if any((name not in port_map or port_map[name]['mode'] != 'in' or port_map[name]['width'] != 1 for name in bindings)):
        raise ValueError('Clock bindings require scalar input ports')
    children = blueprint['bodies'][0]['childs']
    by_id = {n['controller']['id']: n for n in children if 'controller' in n}
    offset = max(by_id)
    added, items, names, wiring = clock_network()
    added, items = (copy.deepcopy(added), copy.deepcopy(items))
    names = {name: i + offset for name, i in names.items()}
    timer_ids = {names['timer' + str(i)] for i in range(1, 5)}
    controls = ('run', 'reset', 'manual_capture', 'manual_commit')
    monitors = ('auto_active', 'manual_ok', 'capture', 'commit')
    if any((source not in names for source in bindings.values())):
        raise ValueError('Unknown clock binding source')
    visible_controls = controls[:2] if minimal_panel else controls
    visible_monitors = () if minimal_panel else monitors
    exposed = {names[k] for k in visible_controls + visible_monitors} | timer_ids
    panel_y = packing['io_panel']['origin']['y']
    panel_x, panel_depth = (-40 if panel_origin_x is None else panel_origin_x, 11 if minimal_panel else 17)
    positions = {}
    for index, name in enumerate(controls):
        positions[names[name]] = dict(x=panel_x + 3 + index * 10, y=panel_y + 4, z=1)
    for index in range(4):
        positions[names['timer' + str(index + 1)]] = dict(x=panel_x + 3 + index * 10, y=panel_y + 8, z=1)
    for index, name in enumerate(monitors):
        positions[names[name]] = dict(x=panel_x + 3 + index * 10, y=panel_y + 15, z=1)
    cube = packing['logic_cube']
    side, internal_count = (cube['width'], cube['blocks'])
    for child, item in zip(added, items):
        ctrl = child['controller']
        ctrl['id'] += offset
        for edge in ctrl['controllers'] or []:
            edge['id'] += offset
        i = ctrl['id']
        if i not in exposed:
            layer, slot = divmod(internal_count, side * side)
            yy, xx = divmod(slot, side)
            positions[i] = dict(x=xx, y=yy + 1, z=layer + 1)
            internal_count += 1
        child['pos'] = positions[i]
        if i in timer_ids:
            child.update(shapeId=TIMER_UUID, xaxis=1, zaxis=3, color='DF7F01')
            ctrl.pop('mode', None)
            ctrl.update(seconds=seconds, ticks=ticks)
        item.update(controllerId=i, editorId=None, position=positions[i], physicalOnly=True, module='timer_clock', region='clock_panel' if i in exposed else 'logic_cube')
        if i in timer_ids:
            item['kind'] = 'TIMER'
    cube.update(blocks=internal_count, height=(internal_count + side * side - 1) // (side * side))
    added_by_id = {n['controller']['id']: n for n in added}
    receivers = {}
    for name, source in bindings.items():
        target = port_map[name]['bits'][0]['controllerId']
        receiver = by_id[target]
        receiver['shapeId'] = GATE_UUID
        receiver['controller']['mode'] = 1
        receiver['controller']['active'] = False
        added_by_id[names[source]]['controller'].setdefault('controllers', None)
        edges = added_by_id[names[source]]['controller']['controllers'] or []
        added_by_id[names[source]]['controller']['controllers'] = edges + [dict(id=target)]
        for item in manifest:
            if item['controllerId'] == target:
                item.update(kind='OR', clockReceiver=True)
        port_map[name]['bits'][0].update(kind='OR', clockReceiver=True)
        receivers[name] = target
    labels = []
    label_rows = [('RUN', 'RESET', 'MAN CAP', 'MAN COM'), ('CAP TIMER', 'GAP TIMER', 'COM TIMER', 'GAP TIMER'), ('AUTO ACTIVE', 'MANUAL OK', 'CAPTURE', 'COMMIT')]
    if minimal_panel:
        label_rows = [label_rows[0][:2], label_rows[1]]
    next_id = max(added_by_id) + 1
    for row, texts in enumerate(label_rows):
        for column, text in enumerate(texts):
            spec = choose_sign(text)
            pos = dict(x=panel_x + 3 + column * 10 - spec.size[0] // 2, y=panel_y + 1 + row * 5 + (1 if row == 2 else 0), z=1)
            child = make_sign(text, next_id, pos, 'out' if row == 2 else 'in')
            added.append(child)
            labels.append(sign_record(child, text, 'clock', 'out' if row == 2 else 'in'))
            next_id += 1
    children.extend(added)
    packing['support'] = sparse_supports(blueprint)
    manifest.extend(items)
    old_wiring = packing['wiring']
    old_wiring['physical_blocks'] += len(items) - 4
    old_wiring['physical_wires'] += wiring['physical_wires'] + len(bindings)
    old_wiring['feedback_relays'] += wiring['feedback_relays']
    old_wiring['clock_timers'] = 4
    old_wiring['clock_logic_blocks'] = len(items) - 4
    old_wiring['relays'].extend(({k: v + offset for k, v in relay.items()} for relay in wiring['relays']))
    if old_wiring['physical_blocks'] + 4 > 50000:
        raise ValueError('Circuit exceeds 50,000 components after attaching timer clock.')
    packing['clock_panel'] = dict(origin=dict(x=panel_x, y=panel_y, z=0), width=40, depth=panel_depth, label_sign_count=len(labels), blocks=len(exposed), signs=labels)
    period = native_clock_period_ticks([total_ticks] * 4)
    return dict(version=2, phase_seconds=phase_seconds, phase_ticks=total_ticks, panel_profile='minimal' if minimal_panel else 'diagnostic', visible_controls=list(visible_controls), visible_indicators=list(visible_monitors), nominal_instruction_seconds=period / 40, nominal_instruction_ticks=period, timer_only_sum_ticks=total_ticks * 4, period_model='Four configured delays + four native sampling updates + one NOR update', sequence=['capture', 'gap', 'commit', 'gap'], names=names, receivers=receivers, receiver_sources={port: names[source] for port, source in bindings.items()}, controls={name: dict(controllerId=names[name], position=positions[names[name]]) for name in controls}, indicators={name: dict(controllerId=names[name], position=positions[names[name]]) for name in monitors}, timers=[dict(controllerId=names['timer' + str(i)], position=positions[names['timer' + str(i)]], seconds=seconds, ticks=ticks) for i in range(1, 5)], note='Timer ring runs continuously. RUN is sampled in the final idle phase. Reset overrides modes. Manual controls require RUN off and MANUAL OK on.')

def export_clocked(build, destination=DEFAULT_BLUEPRINTS, phase_seconds=1, *, phase_ticks=None, minimal_panel=False, bind_ports=None):
    total_ticks = timer_delay_ticks(phase_seconds, phase_ticks)
    phase_seconds = total_ticks / 40
    if any((name not in build.ports or build.ports[name]['mode'] != 'in' or len(build.ports[name]['node_ids']) != 1 for name in ('capture', 'commit', 'reset'))):
        raise ValueError('Timer clock requires scalar capture, commit and reset inputs.')
    relays = sum((a > b and (b, a) in build.circuit.edges for a, b in build.circuit.edges))
    if len(build.circuit.nodes) + relays + len(clock_network()[0]) > 50000:
        raise ValueError('Circuit exceeds 50,000 components after attaching timer clock.')
    folder = build.export(destination)

    def read(name):
        return json.loads((folder / name).read_text(encoding='utf-8'))
    bp, cm, packing, ports = (read('blueprint.json'), read('circuit-map.json'), read('packing.json'), read('port-map.json'))
    clock = attach_clock(bp, cm['nodes'], packing, ports, phase_ticks=total_ticks, minimal_panel=minimal_panel, bind_ports=bind_ports)
    report = read('hdl-build.json')
    report.update(clock=clock, packing=packing, physical_blocks=packing['wiring']['physical_blocks'], physical_wires=packing['wiring']['physical_wires'], timer_blocks=4, feedback_relays=packing['wiring']['feedback_relays'], label_signs=packing['io_panel']['label_sign_count'] + packing['clock_panel']['label_sign_count'])
    sm = read('sign-map.json')
    sm['signs'].extend(packing['clock_panel']['signs'])
    desc = read('description.json')
    desc['description'] = '4004 with four-timer clock. RUN OFF; RESET ON/OFF; RUN ON. MAN CAP/COM must be OFF for automatic mode.'
    payload = {'circuit-map.json': cm, 'packing.json': packing, 'port-map.json': ports, 'hdl-build.json': report, 'sign-map.json': sm, 'clock-map.json': clock, 'description.json': desc}
    encoded = {name: json.dumps(value, indent=2).encode('utf-8') for name, value in payload.items()}
    encoded['blueprint.json'] = encode_blueprint(bp)
    encoded['CLOCK.txt'] = f"INTEL 4004 - AUTOMATIC TIMER CLOCK\n\nUse the new panel with RUN, RESET, MAN CAP, MAN COM and four timers.\nThe old IN capture/commit/reset positions are now driven signal receivers.\n\n1. Leave RUN, MAN CAP and MAN COM OFF.\n2. Turn RESET ON, let the CPU clear, then turn RESET OFF. PC should stay 000.\n3. Turn RUN ON. AUTO ACTIVE lights at the next idle interval.\n4. CAPTURE and COMMIT alternate with an OFF interval between them.\nEach timer starts at {phase_seconds:g}s ({total_ticks} ticks). Native-model period is {clock['nominal_instruction_ticks']} updates ({clock['nominal_instruction_seconds']:g}s at 40 updates/sec).\nThe game scheduler and propagation add to this; this is not a guaranteed frequency.\n\nSTOP / MANUAL: Turn RUN OFF and wait for AUTO ACTIVE to go out and MANUAL OK to light.\nThen use MAN CAP ON/OFF followed by MAN COM ON/OFF, pausing after each change.\nLeave both manual switches OFF before turning RUN ON again.\nRUN is sampled between instructions, so stopping allows the current instruction to finish.\nRESET overrides both modes. The internal timer ring keeps cycling while RUN is OFF.\n\nSPEED: Stop and wait for MANUAL OK before adjusting the timers.\nCAP TIMER and COM TIMER set active durations; GAP TIMERs set the two idle intervals.\nAfter changing settings, allow a complete clock cycle before turning RUN ON.\nUse positive delays and start with all four equal. Shorter settings require in-game testing.\n\nDEMO: After 43 instructions PC=012, accumulator=F, carry=0, and ram_zero,\nram_port_zero, rom_port_zero are all F (each nibble has all four bits ON).\nIt then remains in the done loop. Timer-only estimate for the demo: {43 * phase_seconds * 4:g}s, plus startup and propagation.\n".encode('utf-8')
    for name, data in encoded.items():
        (folder / name).write_bytes(data)
    return folder
