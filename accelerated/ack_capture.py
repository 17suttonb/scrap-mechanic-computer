from copy import deepcopy
from collections import Counter
import re
from accelerated.hdl import Build

def capture_network(add, connect, data, enable, reset, *, window=6, stretch=6, label_prefix='timing ACK '):
    if type(window) is not int or type(stretch) is not int or window < 1 or (stretch < 1):
        raise ValueError('Positive integer qualification/stretch lengths required')
    nodes = {}

    def gate(name, kind, *parents, initial=False):
        i = add(kind, label_prefix + name, initial)
        nodes[name] = i
        for p in parents:
            connect(p, i)
        return i
    inv = gate('not data', 'NOR', data)
    rails = []
    for value, source in ((0, inv), (1, data)):
        sample = gate(f'{value} sample', 'AND', source, enable)
        history = [sample]
        for i in range(1, window):
            history.append(gate(f'{value} history {i}', 'OR', history[-1]))
        qualified = gate(f'{value} qualified', 'AND', *history)
        pulse = [qualified]
        for i in range(1, stretch):
            pulse.append(gate(f'{value} pulse {i}', 'OR', pulse[-1]))
        rails.append(gate(f'{value} stretched', 'OR', *pulse))
    running = gate('not reset', 'NOR', reset)
    set_high = gate('set', 'AND', rails[1], running)
    clear = gate('clear', 'OR', rails[0], reset)
    q = gate('Q', 'NOR', clear)
    qb = gate('/Q', 'NOR', set_high, q, initial=True)
    relay = gate('feedback', 'OR', qb, initial=True)
    connect(relay, q)
    return (q, nodes)

def stabilize_ack(build, *, window=6, stretch=6):
    """Replace only the first CPU ACK sample in a compiled CPU netlist.

    Timing-marked primitives must survive compact_build without fusion or
    deduplication. Source HDL remains the input to this explicit netlist pass.
    """
    circuit = build.circuit.clone()
    parents = {i: [] for i in circuit.nodes}
    for a, b in circuit.edges:
        parents[b].append(a)
    lines = build.source.splitlines()
    matches = [i for i, line in enumerate(lines, 1) if re.search('ack_one_stage\\s*<=\\s*latch\\(bus_ack,cap_en,reset\\)', line)]
    if len(matches) != 1:
        raise ValueError('Expected one original CPU ACK capture assignment')
    line = matches[0]
    candidates = sorted((i for i, n in circuit.nodes.items() if n['label'] == f'latch Q line {line} bit 0'))
    if len(candidates) != 2:
        raise ValueError('Expected the CAP and COM ACK latches on the same source line')
    old = candidates[0]
    data = build.ports['bus_ack']['node_ids'][0]
    reset = build.ports['reset']['node_ids'][0]
    opposite = next((p for p in parents[old] if circuit.nodes[p]['label'] == f'latch /Q line {line} bit 0'))
    reset_bar = next((p for p in parents[opposite] if circuit.nodes[p]['kind'] == 'OR' and data in parents[p]))
    disabled = next((p for p in parents[reset_bar] if p != data))
    while circuit.nodes[disabled]['kind'] == 'OR' and len(parents[disabled]) == 1:
        disabled = parents[disabled][0]
    if circuit.nodes[disabled]['kind'] != 'NOR' or len(parents[disabled]) != 1:
        raise ValueError('Expected NAND ACK enable inverter')
    enable = parents[disabled][0]

    def add(kind, label, initial):
        i = circuit.add(kind, 0, 0, label)
        circuit.nodes[i]['initial'] = initial
        return i
    q, nodes = capture_network(add, circuit.connect, data, enable, reset, window=window, stretch=stretch)
    circuit.edges = {(q if a == old else a, b) for a, b in circuit.edges}
    parents = {i: [] for i in circuit.nodes}
    for a, b in circuit.edges:
        parents[b].append(a)
    reachable = {i for port in build.ports.values() for i in port['node_ids']}
    pending = list(reachable)
    while pending:
        for p in parents[pending.pop()]:
            if p not in reachable:
                reachable.add(p)
                pending.append(p)
    circuit.remove(set(circuit.nodes) - reachable)
    circuit.validate()
    report = deepcopy(build.report)
    report.update(blocks=len(circuit.nodes), wires=len(circuit.edges), gates=dict(Counter((n['kind'] for n in circuit.nodes.values()))))
    report['cpu']['ack_capture'] = dict(version=1, kind='Six-update rail qualification and stretched NOR SR capture', qualification_updates=window, minimum_write_pulse_updates=stretch, nodes=nodes, minimum_cap_width_updates=window, maximum_cap_width_updates=2 * window - 1, supported_cpu_timer_settings=[7, 8], netlist_pass=True, hdl_source_is_pre_transform=True)
    return Build(circuit, deepcopy(build.ports), report, build.source)
