from collections import Counter, deque
from copy import deepcopy
from accelerated.hdl import Build

def compact_build(build, max_consumers=1):
    if max_consumers not in (1, 2):
        raise ValueError('Compaction supports one or two consumers')
    circuit = build.circuit.clone()
    before = (len(circuit.nodes), len(circuit.edges))
    timing = {i for i, n in circuit.nodes.items() if n['label'].startswith('timing ')}
    protected = {node for port in build.ports.values() for node in port['node_ids']} | timing
    parents = {i: set() for i in circuit.nodes}
    children = {i: set() for i in circuit.nodes}
    for a, b in circuit.edges:
        parents[b].add(a)
        children[a].add(b)
    compatible = {'AND': {'AND', 'NAND'}, 'OR': {'OR', 'NOR', 'OUTPUT'}}
    inverted = {'AND': 'NAND', 'OR': 'NOR', 'NAND': 'AND', 'NOR': 'OR', 'XOR': 'XNOR', 'XNOR': 'XOR'}
    pending = deque(sorted(circuit.nodes))
    removed = inversions = 0
    while pending:
        node = pending.popleft()
        if node not in circuit.nodes or node in protected or (not 1 <= len(children[node]) <= max_consumers) or (not parents[node]):
            continue
        kind = circuit.nodes[node]['kind']
        targets = children[node].copy()
        if targets & timing:
            continue
        if len(targets) == 1 and kind in inverted and ('latch ' not in circuit.nodes[node]['label']):
            target = next(iter(targets))
            if circuit.nodes[target]['kind'] == 'NOR' and len(parents[target]) == 1 and (target not in parents[node]) and (len(parents[node]) <= 128):
                upstream = parents[node].copy()
                circuit.nodes[target]['kind'] = inverted[kind]
                parents[target] = upstream
                for source in upstream:
                    children[source].discard(node)
                    children[source].add(target)
                del circuit.nodes[node]
                del parents[node]
                del children[node]
                removed += 1
                inversions += 1
                pending.extend(sorted(upstream))
                pending.append(target)
                continue
        if kind not in compatible or any((circuit.nodes[target]['kind'] not in compatible[kind] for target in targets)):
            continue
        if targets & parents[node]:
            continue
        sources = {target: parents[target] - {node} | parents[node] for target in targets}
        if any((len(inputs) > 128 for inputs in sources.values())):
            continue
        if len(targets) > 1:
            old_wires = len(parents[node]) + len(targets)
            added_wires = sum((len(inputs) - len(parents[target]) + 1 for target, inputs in sources.items()))
            if added_wires > old_wires:
                continue
            if any((len(children[source] - {node} | targets) > 128 for source in parents[node])):
                continue
        upstream = parents[node].copy()
        for target, inputs in sources.items():
            parents[target] = inputs
        for source in upstream:
            children[source].discard(node)
            children[source].update(targets)
        del circuit.nodes[node]
        del parents[node]
        del children[node]
        removed += 1
        pending.extend(sorted(upstream))
        pending.extend(sorted(targets))
    deduplicated = 0
    changed = True
    while changed:
        changed = False
        canonical = {}
        for node in sorted(circuit.nodes, key=lambda i: (i not in protected, i)):
            if node not in circuit.nodes:
                continue
            kind = circuit.nodes[node]['kind']
            if kind == 'INPUT' or 'latch ' in circuit.nodes[node]['label']:
                continue
            key = ('OR' if kind == 'OUTPUT' else kind, tuple(sorted(parents[node])))
            if key not in canonical:
                canonical[key] = node
                continue
            target = canonical[key]
            if node in protected or len(children[target] | children[node]) > 128:
                continue
            if target in parents[node] or node in parents[target]:
                continue
            if any((circuit.nodes[child]['kind'] in ('XOR', 'XNOR') and target in parents[child] for child in children[node])):
                continue
            for source in parents[node]:
                children[source].discard(node)
            for child in children[node]:
                parents[child].discard(node)
                parents[child].add(target)
            children[target].update(children[node])
            del circuit.nodes[node]
            del parents[node]
            del children[node]
            deduplicated += 1
            removed += 1
            changed = True
    circuit.edges = {(a, b) for b, inputs in parents.items() for a in inputs}
    circuit.validate()
    report = deepcopy(build.report)
    report.update(blocks=len(circuit.nodes), wires=len(circuit.edges), gates=dict(Counter((n['kind'] for n in circuit.nodes.values()))), compaction=dict(source_blocks_before=before[0], source_wires_before=before[1], source_blocks_after=len(circuit.nodes), source_wires_after=len(circuit.edges), fused_gates=removed, inversion_folds=inversions, deduplicated_gates=deduplicated, max_consumers=max_consumers, method='Associative/inversion folding and identical-function sharing; no extra wires; exposed ports and storage retained'))
    return Build(circuit, deepcopy(build.ports), report, build.source)
