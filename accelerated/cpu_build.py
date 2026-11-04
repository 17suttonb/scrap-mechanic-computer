from copy import deepcopy
from collections import Counter
from accelerated.hdl import Build

def terminal_cpu(build, extra_ports=()):
    """Drop diagnostic-only logic by retaining ancestors of the console pins.

    This is graph reachability, including all feedback edges: any state which
    can affect execution or the console remains in the exported machine.
    """
    keep = {'capture', 'commit', 'reset', 'input_byte', 'gpio', 'halted', 'faulted', 'trap_cause'} | set(extra_ports)
    ports = {name: deepcopy(port) for name, port in build.ports.items() if name in keep}
    parents = {node: [] for node in build.circuit.nodes}
    for a, b in build.circuit.edges:
        parents[b].append(a)
    reachable = {node for port in ports.values() for node in port['node_ids']}
    pending = list(reachable)
    while pending:
        for parent in parents[pending.pop()]:
            if parent not in reachable:
                reachable.add(parent)
                pending.append(parent)
    circuit = build.circuit.clone()
    removed = set(circuit.nodes) - reachable
    circuit.remove(removed)
    circuit.validate()
    report = deepcopy(build.report)
    report.update(blocks=len(circuit.nodes), wires=len(circuit.edges), gates=dict(Counter((n['kind'] for n in circuit.nodes.values()))))
    report['cpu']['diagnostic_only_blocks_removed'] = len(removed)
    return Build(circuit, ports, report, build.source)
