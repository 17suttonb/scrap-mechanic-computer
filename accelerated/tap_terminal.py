from collections import Counter
from copy import deepcopy
import re
from unittest.mock import patch
from accelerated.hdl import Build
from accelerated import character_terminal
from accelerated.terminal import KEYS
from accelerated.cartridge import console_source

def source(text, debug=False):
    text = text.replace('port(capture,commit,reset,faulted:in bit;', 'port(capture,commit,reset,faulted,key_settled,key_available:in bit;', 1)
    text = text.replace('busy,full:out bit;', 'busy,full,key_taken:out bit;', 1)
    text = text.replace('begin\n', '  signal tap_consume_stage,tap_offer:bit;\nbegin\n', 1)
    start = text.index('  code_stage <= latch(raw_code,')
    end = text.index('  ack <=', start)
    text = text[:start] + "  code_stage <= bits(0,7);code <= raw_code;\n  previous_code_stage <= bits(0,7);previous_code <= bits(0,7);\n  sample_any_stage <= '0';sample_any <= any_key;\n  previous_any_stage <= '0';previous_any <= '0';\n  valid_key_stage <= '0';valid_key <= not collision(1);\n  previous_valid_stage <= '0';previous_valid <= '0';stable_press <= '0';\n" + text[end:]
    text, n = re.subn('  key_event <= [^;]*;', '  key_event <= tap_offer and key_settled and any_key and valid_key and not ready and not ack and not faulted and not tx;', text, count=1)
    assert n == 1
    text, n = re.subn('  held_next <= [^;]*;', "  held_next <= '0';", text, count=1)
    assert n == 1
    text, n = re.subn('  full <= [^;]*;', '  full <= key_available;', text, count=1)
    assert n == 1
    extra = '  tap_offer <= latch(key_settled and any_key,com_en,reset);\n  tap_consume_stage <= latch(tap_offer and key_settled and any_key and not ready and not ack and not faulted and not tx,cap_en,reset);\n  key_taken <= com_en and tap_consume_stage;\n'
    if debug:
        text = text.replace('terminal_pixels:out bit_vector', 'debug_code:out bit_vector(6 downto 0);debug_event,debug_valid,debug_any,debug_settled:out bit;terminal_pixels:out bit_vector', 1)
        extra += '  debug_code <= code;debug_event <= key_event;debug_valid <= valid_key;debug_any <= any_key;debug_settled <= key_settled;\n'
    return text.replace('  font_code(0)', extra + '  font_code(0)', 1)

def attach(build, queue_depth=0):
    c = build.circuit.clone()
    ports = deepcopy(build.ports)
    keys = ports['keys']['node_ids']
    reset = ports['reset']['node_ids'][0]
    busy = ports['busy']['node_ids'][0]
    taken = ports.pop('key_taken')['node_ids'][0]
    settled_pin = ports.pop('key_settled')['node_ids'][0]
    available_pin = ports.pop('key_available')['node_ids'][0]
    old_edges = set(c.edges)

    def gate(kind, parents, label, initial=False):
        i = c.add(kind, 0, 0, 'timing TAP ' + label)
        c.nodes[i]['initial'] = initial
        for p in parents:
            c.connect(p, i)
        return i
    if 'key_toggle' in ports:
        # A cartridge takes a key by toggling this line: pop the queue directly, without the console's clock.
        toggle = ports.pop('key_toggle')['node_ids'][0]
        delayed = toggle
        for i in range(3):
            delayed = gate('OR', [delayed], f'cartridge take delay {i}')
        taken = gate('OR', [taken, gate('XOR', [toggle, delayed], 'cartridge take')], 'console or cartridge take')
    clearing = gate('OR', [], 'clear hold')
    raw = gate('OR', keys, 'any physical key')
    raw_history = [raw]
    for i in range(6):
        raw_history.append(gate('OR', [raw_history[-1]], f'physical release history {i}'))
    raw_hold = gate('OR', raw_history, 'physical release guard')
    pops = [taken]
    for i in range(4):
        pops.append(gate('OR', [pops[-1]], f'consume history {i}'))
    pop_hold = gate('OR', pops, 'consume stretch')
    clear_feedback = gate('AND', [clearing, raw_hold], 'clear while held')
    for i in (reset, busy, pop_hold, clear_feedback):
        c.connect(i, clearing)
    available = gate('NOR', [], 'key ready', True)
    pending = []
    if queue_depth:
        from accelerated.key_queue import build_queue
        events, lamp = build_queue(c, gate, keys, reset, taken, available, queue_depth)
    for n, (key, physical) in enumerate(zip(KEYS, keys)):
        if queue_depth:
            event = events[n]
        else:
            h1 = gate('OR', [physical], f'{key} press history 1')
            h2 = gate('OR', [h1], f'{key} press history 2')
            stretch = gate('OR', [physical, h1, h2], f'{key} press stretch')
            event = gate('AND', [stretch, available], f'{key} admitted press')
        q = gate('NOR', [], f'latch Q {key}')
        inverse = gate('NOR', [], f'latch /Q {key}', True)
        history = gate('OR', [q], f'{key} feedback history')
        c.connect(clearing, q)
        c.connect(inverse, q)
        c.connect(event, inverse)
        c.connect(history, inverse)
        pending.append(q)
    any_pending = gate('OR', pending, 'any pending key')
    history = [any_pending]
    for i in range(16):
        history.append(gate('OR', [history[-1]], f'payload settle history {i}'))
    settled = gate('AND', [any_pending, history[-1]], 'settled payload')
    recent = gate('OR', history, 'pending release guard')
    for i in (clearing, recent, reset, busy):
        c.connect(i, available)
    replacement = {**dict(zip(keys, pending)), settled_pin: settled, available_pin: lamp if queue_depth else available}
    removed = {settled_pin, available_pin}
    if 'screen_late' in ports:
        # A board write's data settles through the cell's input mux before its write pulse arrives.
        late = ports.pop('screen_late')['node_ids'][0]
        delayed = ports['screen_write']['node_ids'][0]
        for i in range(6):
            delayed = gate('OR', [delayed], f'screen write delay {i}')
        replacement[late] = delayed
        removed.add(late)
        # The key echo's data settles the same way, off the settled key rather than a CPU store.
        late = ports.pop('key_echo_late')['node_ids'][0]
        delayed = settled
        for i in range(6):
            delayed = gate('OR', [delayed], f'key echo delay {i}')
        replacement[late] = delayed
        removed.add(late)
    c.edges = {(replacement.get(a, a), b) for a, b in old_edges} | c.edges - old_edges
    c.remove(removed)
    c.validate()
    report = deepcopy(build.report)
    report.update(keyboard_ready_label='KEY READY', tap_keyboard=dict(capacity=queue_depth or 1, physical_keys=len(keys), minimum_tested_press_updates=None, settle_updates=16, policy=(f'FIFO of {queue_depth} keystrokes: tap keys in order; KEY READY is ON while the queue has room. Chords and presses while the queue is full are discarded.' if queue_depth else 'Tap once when KEY READY is ON; release; next key when KEY READY returns. Chords and presses while unavailable are discarded.'), retained_until_console_capture=True, held_key_repeat=False), blocks=len(c.nodes), wires=len(c.edges), gates=dict(Counter((n['kind'] for n in c.nodes.values()))))
    return Build(c, ports, report, build.source)

def terminal(boot_text, **options):
    debug = options.pop('debug', False)
    queue_depth = options.pop('key_queue', 0)
    screen = options.pop('screen', False)
    provider = character_terminal.old_source
    cells = character_terminal.source
    with patch.object(character_terminal, 'old_source', lambda *a, **kw: source(provider(*a, **kw), debug)), patch.object(character_terminal, 'source', (lambda *a, **kw: console_source(cells(*a, **kw))) if screen else cells):
        build = character_terminal.terminal(boot_text, **options)
    return attach(build, queue_depth)
