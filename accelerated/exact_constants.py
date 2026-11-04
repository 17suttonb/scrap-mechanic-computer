from collections import Counter
from copy import deepcopy
from accelerated.hdl import Build

def function(kind, parents, known):
    if kind == 'INPUT':
        return ('input',)
    if not parents:
        return ('constant', False)
    fixed = [known[p] for p in parents if p in known]
    other = tuple(sorted((p for p in parents if p not in known)))
    kind = 'OR' if kind == 'OUTPUT' else kind
    if kind in ('AND', 'NAND'):
        if False in fixed:
            return ('constant', kind == 'NAND')
        if not other:
            return ('constant', kind == 'AND')
        return (kind, other)
    if kind in ('OR', 'NOR'):
        if True in fixed:
            return ('constant', kind == 'OR')
        if not other:
            return ('constant', kind == 'NOR')
        return (kind, other)
    parity = sum(fixed) % 2
    if not other:
        return ('constant', bool(parity) if kind == 'XOR' else not bool(parity))
    return ('XOR' if (kind == 'XOR') != bool(parity) else 'XNOR', other)

def fold(build, *, inductive_cycles=False):
    old = build.circuit
    c = old.clone()
    parents = {n: set() for n in old.nodes}
    for a, b in old.edges:
        parents[b].add(a)
    if inductive_cycles:
        known = {n: bool(v.get('initial')) for n, v in old.nodes.items() if v['kind'] != 'INPUT'}
        while True:
            rejected = [n for n, value in known.items() if function(old.nodes[n]['kind'], parents[n], known) != ('constant', value)]
            if not rejected:
                break
            for n in rejected:
                del known[n]
    else:
        known = {}
        changed = True
        while changed:
            changed = False
            for n, v in old.nodes.items():
                if n in known or v['kind'] == 'INPUT':
                    continue
                f = function(v['kind'], parents[n], known)
                if f[0] == 'constant' and f[1] == bool(v.get('initial')):
                    known[n] = f[1]
                    changed = True
    protected = {n for p in build.ports.values() for n in p['node_ids']}
    protected |= {n for n, v in old.nodes.items() if 'latch ' in v['label'] or v['label'].startswith('timing TEXT ')}
    removed = set(known) - protected
    for n in removed:
        del c.nodes[n]
    edges = set()
    requests = {False: [], True: []}
    symbolic = {}
    for n, v in c.nodes.items():
        ps = parents[n]
        unknown = {p for p in ps if p not in known}
        fixed = [known[p] for p in ps if p in known]
        kind = 'OR' if v['kind'] == 'OUTPUT' else v['kind']
        use = []
        if kind == 'INPUT':
            assert not ps
        elif kind in ('AND', 'NAND'):
            if False in fixed:
                unknown = set()
                use = [False]
            elif not unknown and ps and (kind == 'AND'):
                use = [True]
        elif kind in ('OR', 'NOR'):
            if True in fixed:
                unknown = set()
                use = [True]
            elif not unknown and ps and (kind == 'NOR'):
                use = [False]
        elif kind in ('XOR', 'XNOR'):
            if sum(fixed) % 2:
                use = [True]
            elif not unknown and ps and (kind == 'XNOR'):
                use = [False]
        symbolic[n] = unknown | {-2 if x else -1 for x in use}
        assert function(v['kind'], ps, known) == function(v['kind'], symbolic[n], {-1: False, -2: True}), (n, v, ps, symbolic[n])
        edges.update(((p, n) for p in unknown))
        for value in use:
            requests[value].append(n)
    constants = {}
    for value, targets in requests.items():
        for start in range(0, len(targets), 128):
            zero = c.add('OR', 0, 0, 'size invariant zero')
            constants[zero] = False
            driver = zero
            if value:
                driver = c.add('NOR', 0, 0, 'size invariant one')
                c.nodes[driver]['initial'] = True
                constants[driver] = True
                edges.add((zero, driver))
            edges.update(((driver, target) for target in targets[start:start + 128]))
    c.edges = edges
    incoming = {n: set() for n in c.nodes}
    for a, b in edges:
        incoming[b].add(a)
    live = {n for p in build.ports.values() for n in p['node_ids']}
    pending = list(live)
    while pending:
        for p in incoming[pending.pop()]:
            if p not in live:
                live.add(p)
                pending.append(p)
    dead = set(c.nodes) - live
    c.remove(dead)
    c.validate()
    actual_parents = {n: set() for n in c.nodes}
    for a, b in c.edges:
        actual_parents[b].add(a)
    final_known = {**{n: v for n, v in known.items() if n in c.nodes}, **{n: v for n, v in constants.items() if n in c.nodes}}
    for n, v in c.nodes.items():
        if n in old.nodes:
            assert bool(v.get('initial')) == bool(old.nodes[n].get('initial'))
            assert function(v['kind'], actual_parents[n], final_known) == function(old.nodes[n]['kind'], parents[n], known)
        else:
            assert function(v['kind'], actual_parents[n], final_known) == ('constant', bool(v.get('initial')))
    report = deepcopy(build.report)
    report.update(blocks=len(c.nodes), wires=len(c.edges), gates=dict(Counter((n['kind'] for n in c.nodes.values()))))
    report['exact_constants'] = dict(invariant_nodes=len(known), folded_nodes=len(removed), unobservable_nodes=len(dead), removed_gates=len(old.nodes) - len(c.nodes), removed_wires=len(old.edges) - len(c.edges), original_invariants={str(n): v for n, v in known.items()}, initial_state_and_every_retained_gate_function_verified=True, native_empty_gate_semantics_verified=True, method='Initial-state induction; no transient constant folding or variable gate-stage removal')
    if inductive_cycles:
        report['exact_constants'].update(inductive_cycles=True, method='Simultaneous initial-state induction: surviving invariant conjunction preserved for arbitrary inputs at every native update')
    return Build(c, deepcopy(build.ports), report, build.source)
