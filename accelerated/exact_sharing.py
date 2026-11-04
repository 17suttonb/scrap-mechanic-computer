from collections import Counter
from copy import deepcopy
from accelerated.hdl import Build
from accelerated.cartridge import seam_drivers

def share(build):
    c = build.circuit.clone()
    before = c.clone()
    protected = {n for p in build.ports.values() for n in p['node_ids']} | seam_drivers(build)
    protected |= {n for n, v in c.nodes.items() if v['kind'] == 'INPUT' or 'latch ' in v['label']}
    protected |= {n for n, v in c.nodes.items() if v['label'].startswith('timing TEXT ')}
    parents = {i: set() for i in c.nodes}
    children = {i: set() for i in c.nodes}
    for a, b in c.edges:
        parents[b].add(a)
        children[a].add(b)
    projection = {i: i for i in c.nodes}
    removed = []
    changed = True
    while changed:
        changed = False
        canonical = {}
        for node in sorted(c.nodes, key=lambda i: (i not in protected, i)):
            if node not in c.nodes:
                continue
            n = c.nodes[node]
            key = (n['kind'], bool(n.get('initial')), tuple(sorted(parents[node])))
            if key not in canonical:
                canonical[key] = node
                continue
            target = canonical[key]
            if node in protected or len(children[target] | children[node]) > 128:
                continue
            if target in parents[node] or node in parents[target]:
                continue
            if any((c.nodes[ch]['kind'] in ('XOR', 'XNOR') and target in parents[ch] for ch in children[node])):
                continue
            for a in parents[node]:
                children[a].discard(node)
            for b in children[node]:
                parents[b].discard(node)
                parents[b].add(target)
            children[target].update(children[node])
            projection[node] = target
            removed.append(dict(node=node, target=target, label=n['label'], kind=n['kind']))
            del c.nodes[node]
            del parents[node]
            del children[node]
            changed = True

    def resolve(n):
        chain = []
        while projection[n] != n:
            chain.append(n)
            n = projection[n]
        for p in chain:
            projection[p] = n
        return n
    projection = {i: resolve(i) for i in projection}
    c.edges = {(a, b) for b, ps in parents.items() for a in ps}
    c.validate()
    original_parents = {i: set() for i in before.nodes}
    for a, b in before.edges:
        original_parents[b].add(a)
    for old, new in projection.items():
        assert before.nodes[old]['kind'] == c.nodes[new]['kind']
        assert bool(before.nodes[old].get('initial')) == bool(c.nodes[new].get('initial'))
        mapped = [projection[p] for p in original_parents[old]]
        assert set(mapped) == parents[new]
        if before.nodes[old]['kind'] in ('XOR', 'XNOR'):
            assert len(set(mapped)) == len(mapped)
    assert all((projection[n] == n for n in protected))
    report = deepcopy(build.report)
    report.update(blocks=len(c.nodes), wires=len(c.edges), gates=dict(Counter((n['kind'] for n in c.nodes.values()))))
    report['exact_sharing'] = dict(removed_gates=len(removed), removed_wires=len(before.edges) - len(c.edges), removed=removed, projection={str(k): v for k, v in projection.items() if k != v}, every_node_kind_initial_and_parent_set_verified=True, ports_and_storage_preserved=True, method='Equal kind, initial value and parents; no gate-stage removal; XOR multiplicity retained')
    return Build(c, deepcopy(build.ports), report, build.source)
