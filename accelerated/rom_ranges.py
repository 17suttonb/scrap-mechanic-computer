from collections import deque
from functools import lru_cache
from accelerated import compact_core
from accelerated.compact_core import analysis_metadata as metadata
from accelerated.isa import signed
MASK = 4294967295
LIMIT = 64

def join(a, b):
    if a is None or b is None:
        return None
    v = a | b
    return v if len(v) <= LIMIT else None

def calculate(a, b, fn):
    if a is None or b is None or len(a) * len(b) > 16384:
        return None
    values = set()
    for x in a:
        for y in b:
            values.add(fn(x, y) & MASK)
            if len(values) > LIMIT:
                return None
    return frozenset(values)

def condition(f3, a, b):
    return {0: lambda: a == b, 1: lambda: a != b, 4: lambda: signed(a, 32) < signed(b, 32), 5: lambda: signed(a, 32) >= signed(b, 32), 6: lambda: a < b, 7: lambda: a >= b}[f3]()

def restrict(a, b, f3, taken):
    if a is not None and b is not None:
        left = set()
        right = set()
        for x in a:
            for y in b:
                if condition(f3, x, y) == taken:
                    left.add(x)
                    right.add(y)
        return (frozenset(left), frozenset(right))
    if a is None and b is not None:
        if f3 == 0 and taken or (f3 == 1 and (not taken)):
            return (b, b)
        if f3 == 6 and taken or (f3 == 7 and (not taken)):
            n = max(b)
            if n <= LIMIT:
                return (frozenset(range(n)), b)
    if b is None and a is not None:
        swap = {0: 0, 1: 1, 4: 5, 5: 4, 6: 7, 7: 6}[f3]
        if f3 in (4, 5, 6, 7):
            return (a, b)
        rb, ra = restrict(b, a, swap, taken)
        return (ra, rb)
    return (a, b)

def analyze(image):
    """With a cartridge window open any program can run, so nothing is known about any register."""
    if compact_core.CART is not None:
        return dict(passed=True, states={}, values=(frozenset({0}),) + (None,) * 15, visits=0, reachable_pcs=[], unresolved_indirect_jumps=[], out_of_rom_targets=[])
    return analyze_rom(image)

@lru_cache(maxsize=None)
def analyze_rom(image):
    rows = metadata(image)
    by_pc = {r['pc']: r for r in rows}
    zero = frozenset({0})
    states = {0: (zero,) * 16}
    queue = deque([0])
    queued = {0}
    global_values = [zero] * 16
    unresolved = set()
    steps = 0
    bad_targets = set()

    def enqueue(pc, state):
        if pc not in by_pc or pc & 3:
            bad_targets.add(pc)
            return
        if pc not in states:
            states[pc] = tuple(state)
            different = True
        else:
            old = states[pc]
            new = tuple((join(a, b) for a, b in zip(old, state)))
            different = new != old
            states[pc] = new
        if different and pc not in queued:
            queue.append(pc)
            queued.add(pc)
    while queue:
        pc = queue.popleft()
        queued.discard(pc)
        steps += 1
        assert steps < 100000, 'Analysis resource limit: no specialization authorized'
        r = by_pc[pc]
        s = list(states[pc])
        w = r['word']
        op = w & 127
        f3 = r['f3']
        f7 = w >> 25
        if not r['legal'] or r['stop']:
            continue
        a = s[r['ra']]
        b = s[r['rb']]
        imm = frozenset({r['imm']})
        value = None
        if op in (19, 51):
            v = b if op == 51 else imm
            funcs = {0: (lambda x, y: x - y) if op == 51 and f7 == 32 else lambda x, y: x + y, 1: lambda x, y: x << (y & 31), 2: lambda x, y: int(signed(x, 32) < signed(y, 32)), 3: lambda x, y: int(x < y), 4: lambda x, y: x ^ y, 5: (lambda x, y: signed(x, 32) >> (y & 31)) if f7 == 32 else lambda x, y: x >> (y & 31), 6: lambda x, y: x | y, 7: lambda x, y: x & y}
            value = calculate(a, v, funcs[f3])
            if value is None and f3 in (2, 3):
                value = frozenset({0, 1})
        elif op == 55:
            value = imm
        elif op == 23:
            value = frozenset({pc + r['imm'] & MASK})
        elif op in (111, 103):
            value = frozenset({pc + 4})
        elif op == 3:
            value = None
        if r['rd']:
            s[r['rd']] = value
        for i, v in enumerate(s):
            global_values[i] = join(global_values[i], v)
        if r['branch']:
            for taken, target in [(True, r['target']), (False, pc + 4)]:
                aa, bb = restrict(a, b, f3, taken)
                if aa == frozenset() or bb == frozenset():
                    continue
                branch = s.copy()
                if r['ra']:
                    branch[r['ra']] = aa
                if r['rb']:
                    branch[r['rb']] = bb
                enqueue(target, branch)
        elif r['jal']:
            enqueue(r['target'], s)
        elif r['jalr']:
            if a is None:
                unresolved.add(pc)
            else:
                for x in a:
                    enqueue(x + r['imm'] & MASK & ~1, s)
        else:
            enqueue(pc + 4, s)
    result = dict(passed=not unresolved and (not bad_targets), states=states, values=tuple(global_values), visits=steps, reachable_pcs=sorted(states), unresolved_indirect_jumps=sorted(unresolved), out_of_rom_targets=sorted(bad_targets))
    return result

def summary(image):
    r = analyze(image)
    return dict(passed=r['passed'], visits=r['visits'], reachable_instructions=len(r['reachable_pcs']), unresolved_indirect_jumps=r['unresolved_indirect_jumps'], out_of_rom_targets=r['out_of_rom_targets'], finite_register_value_counts={str(i): len(v) for i, v in enumerate(r['values']) if i and v is not None}, method='Monotone finite-value abstract interpretation; arbitrary ISA loads; all branch paths; no observed-trace assumptions')
