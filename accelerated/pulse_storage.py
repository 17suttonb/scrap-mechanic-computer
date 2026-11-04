def pulse_latches(builder, data, enable, reset, token):
    groups = getattr(builder, 'pulse_reset_groups', None)
    if groups is None:
        groups = {}
        builder.pulse_reset_groups = groups

    def raw(kind, parents, label, initial=False):
        return builder.raw(kind, parents, label, initial, token)
    if reset not in groups:
        number = len(groups)
        prefix = f'timing PULSE reset {number} '
        if reset == 0:
            allowed, clear = (1, 0)
        else:
            history = [reset]
            for i in range(1, 13):
                history.append(raw('OR', (history[-1],), prefix + f'history {i}'))
            busy = raw('OR', tuple(history), prefix + 'busy')
            allowed = raw('NOR', (busy,), prefix + 'writes allowed', True)
            clear = raw('XOR', (history[5], history[6]), prefix + 'clear edge')
        groups[reset] = (allowed, clear)
    allowed, clear = groups[reset]
    prefix = f'timing PULSE line {token.line} bank {builder.latch_count} '
    normal = raw('AND', (enable, allowed), prefix + 'normal strobe')
    trigger = raw('OR', (normal, clear), prefix + 'write trigger')
    result = []
    for bit, d in enumerate(data):
        q = raw('XOR', (), f'latch Q line {token.line} bit {bit}')
        masked = raw('AND', (d, allowed), prefix + f'bit {bit} masked data')
        delta = raw('XOR', (masked, q), prefix + f'bit {bit} delta')
        flip = raw('AND', (trigger, delta), prefix + f'bit {bit} flip')
        builder.gates[q]['sources'] = (q, flip)
        builder.storage.add(q)
        result.append(q)
    return result
