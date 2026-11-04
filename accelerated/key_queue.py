"""Self-timed keystroke FIFO placed between the physical keys and the tap-latch stage.

A press is encoded to a 6-bit key index, rejected if two keys collide, and written
into a ring of depth slots. The head slot is decoded one-hot into the existing
per-key pending latches whenever the console can accept a key; the console's
`key_taken` pulse pops it. Every latch is the 3-gate hist/inv/Q cell used by the
tap keyboard, so a set or reset needs a 3-update pulse. Pulses are stretched
privately by each cell. Gates are built with the caller's `gate(kind, parents,
label, initial)` helper so they keep the protected `timing TAP` prefix.
"""
INDEX_BITS = 6


def build_queue(c, gate, keys, reset, taken, available, depth=8):
    """Return (event_per_key, lamp). `available` is the downstream-free level."""
    n = len(keys)
    assert n <= 1 << INDEX_BITS

    def delay(src, count, label):
        for i in range(count):
            src = gate('OR', [src], f'{label} d{i}')
        return src

    def inv(src, label):
        return gate('NOR', [src], label)

    def stretch(src, label, extra=2):
        parts = [src]
        for i in range(extra):
            parts.append(gate('OR', [parts[-1]], f'{label} s{i}'))
        return gate('OR', parts, f'{label} stretch') if len(parts) > 1 else src

    def private_stretch(src, label):
        d1 = gate('OR', [src], f'{label} p1')
        d2 = gate('OR', [d1], f'{label} p2')
        return [src, d1, d2]

    # Encode the pressed key as index and complement index; two keys disagree in some bit.
    any_raw = gate('OR', list(keys), 'Q any key')
    idx = []
    cidx = []
    for b in range(INDEX_BITS):
        idx.append(gate('OR', [k for i, k in enumerate(keys) if i >> b & 1], f'Q index {b}'))
        cidx.append(gate('OR', [k for i, k in enumerate(keys) if not i >> b & 1], f'Q complement {b}'))
    collide = gate('OR', [gate('AND', [idx[b], cidx[b]], f'Q disagree {b}') for b in range(INDEX_BITS)], 'Q collision')
    ok = gate('NOR', [collide], 'Q single key', True)
    valid = gate('AND', [delay(any_raw, 3, 'Q any'), ok], 'Q valid press')
    edge_prev = gate('OR', [valid], 'Q edge prev')
    edge = gate('AND', [edge_prev, gate('NOR', [edge_prev], 'Q edge idle', True)], 'Q press edge')
    # edge fires 7 updates after the raw key and the slot write gate 2 more; line the index up with it.
    idx_late = [delay(i, 8, f'Q index {b}') for b, i in enumerate(idx)]

    nreset = inv(reset, 'Q not reset')

    # Cells need each other's outputs, so create cell shells first and wire afterwards.
    def shell(label, init=False):
        hist = gate('OR', [], f'{label} hist', init)
        invert = gate('NOR', [], f'{label} inv', not init)
        q = gate('NOR', [], f'{label} Q', init)
        c.connect(q, hist)
        c.connect(hist, invert)
        c.connect(invert, q)
        return dict(q=q, inv=invert)

    def attach_inputs(sh, sets, resets):
        for s in sets:
            c.connect(s, sh['inv'])
        for r in resets:
            c.connect(r, sh['q'])

    wp_c = [shell(f'Q wp{i}', i == 0) for i in range(depth)]
    rp_c = [shell(f'Q rp{i}', i == 0) for i in range(depth)]
    valid_c = [shell(f'Q slot{i} valid') for i in range(depth)]
    data_c = [[shell(f'Q slot{i} bit{b}') for b in range(INDEX_BITS)] for i in range(depth)]
    wp = [s['q'] for s in wp_c]
    rp = [s['q'] for s in rp_c]
    valid_q = [s['q'] for s in valid_c]

    full = gate('OR', [gate('AND', [wp[i], valid_q[i]], f'Q full{i}') for i in range(depth)], 'Q full')
    lamp = gate('NOR', [full, reset], 'Q key ready', True)
    nfull = inv(full, 'Q not full')
    # A press right behind another would overlap the write pipeline; drop it rather than corrupt a slot.
    lockout = gate('OR', [], 'Q write lockout')
    nlock = gate('NOR', [lockout], 'Q write unlocked', True)
    accept = gate('AND', [edge, nfull, nreset, nlock], 'Q accepted press')
    lock_chain = [accept]
    for i in range(2):
        lock_chain.append(gate('OR', [lock_chain[-1]], f'Q lock d{i}'))
    for source in lock_chain:
        c.connect(source, lockout)
    advance_write = gate('OR', [accept], 'Q write advance')

    head_valid = gate('OR', [gate('AND', [rp[i], valid_q[i]], f'Q head{i}') for i in range(depth)], 'Q head valid')
    head_idx = [gate('OR', [gate('AND', [rp[i], data_c[i][b]['q']], f'Q head{i} bit{b}') for i in range(depth)], f'Q head index {b}') for b in range(INDEX_BITS)]

    # Pop on the rising edge of the console's take pulse.
    taken_prev = gate('OR', [taken], 'Q taken prev')
    taken_edge = gate('AND', [taken_prev, gate('NOR', [taken_prev], 'Q taken idle', True)], 'Q taken edge')
    pop = gate('AND', [taken_edge, delay(head_valid, 2, 'Q head hold')], 'Q pop')

    for i in range(depth):
        nxt = (i + 1) % depth
        w = gate('AND', [accept, wp[i]], f'Q write{i}')
        write_wide = stretch(w, f'Q write{i}')
        step_w = stretch(gate('AND', [advance_write, wp[i]], f'Q wp step{i}'), f'Q wp step{i}')
        step_r = stretch(gate('AND', [pop, rp[i]], f'Q slot{i} pop'), f'Q slot{i} pop')
        attach_inputs(valid_c[i], [write_wide], [step_r, reset])
        for b in range(INDEX_BITS):
            s = gate('AND', [w, idx_late[b]], f'Q slot{i} set{b}')
            attach_inputs(data_c[i][b], private_stretch(s, f'Q slot{i} set{b}'), [step_r, reset])
        attach_inputs(wp_c[nxt], [step_w] + ([reset] if nxt == 0 else []), [] if nxt == 0 else [reset])
        attach_inputs(wp_c[i], [], [step_w])
        attach_inputs(rp_c[nxt], [step_r] + ([reset] if nxt == 0 else []), [] if nxt == 0 else [reset])
        attach_inputs(rp_c[i], [], [step_r])

    # Load the head into the per-key latches once it has been stable and the console is free.
    ready = gate('AND', [head_valid, available], 'Q head ready')
    stable = gate('AND', [ready, delay(ready, 5, 'Q ready hold')], 'Q head stable')
    stable_prev = gate('OR', [stable], 'Q stable prev')
    load_edge = gate('AND', [stable_prev, gate('NOR', [stable_prev], 'Q stable idle', True)], 'Q load edge')
    load = stretch(load_edge, 'Q load', 2)
    nhead = [inv(b, f'Q head not {j}') for j, b in enumerate(head_idx)]
    events = []
    for k in range(n):
        literals = [head_idx[b] if k >> b & 1 else nhead[b] for b in range(INDEX_BITS)]
        events.append(gate('AND', [*literals, load], f'Q load key {k}'))
    return events, lamp
