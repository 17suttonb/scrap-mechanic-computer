from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import shutil
import uuid
from accelerated.circuit import GATE_UUID
from accelerated.io import read, save
from accelerated.packet import Encoder

def prototype(baseline, record_root, console_switches=False):
    src = Path(baseline['folder'])
    raw = (src / 'blueprint.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == baseline['blueprint_sha256']
    bp = read(src / 'blueprint.json')
    cm = read(src / 'circuit-map.json')
    labels = {n['controllerId']: n['label'] for n in cm['nodes']}
    parts = {p['controller']['id']: p for b in bp['bodies'] for p in b['childs'] if 'controller' in p and 'data' not in p['controller']}
    parents = defaultdict(set)
    children = defaultdict(set)
    for i, p in parts.items():
        for e in p['controller'].get('controllers') or []:
            parents[e['id']].add(i)
            children[i].add(e['id'])
    owner = {i: 'terminal' if labels.get(i, '').startswith(('Service ', 'Terminal ', 'timing Service ', 'timing Terminal ')) else 'cpu' for i in parts}
    svc = read(src / 'service-clock-map.json')
    clock = read(src / 'clock-map.json')
    for i in svc['names'].values():
        owner[i] = 'terminal'
    for name in ('capture', 'commit'):
        owner[svc['receivers'][name]] = 'terminal'
    for _ in range(3):
        for i, label in labels.items():
            if label.startswith('Feedback relay'):
                domains = {owner[j] for j in parents[i] | children[i]}
                if len(domains) == 1:
                    owner[i] = next(iter(domains))
    reset = clock['names']['reset']
    run = clock['names']['run']
    pure = {reset} | {i for i, p in parts.items() if p['shapeId'] == GATE_UUID and (not parents[i]) and not labels.get(i, '').startswith('cart_pin ')}
    while True:
        add = {i for i, p in parts.items() if i not in pure and parents[i] and (parents[i] <= pure) and (p['shapeId'] == GATE_UUID)}
        if not add:
            break
        pure |= add
    original_edges = {(a, b) for a, cs in children.items() for b in cs}
    edges = set()
    added = []
    links = []
    copies = {}
    next_id = max((p['controller']['id'] for b in bp['bodies'] for p in b['childs'] if 'controller' in p)) + 1

    def add_gate(domain, label, template=None):
        nonlocal next_id
        i = next_id
        next_id += 1
        p = deepcopy(template) if template else dict(shapeId=GATE_UUID, color='34465E', pos=dict(x=0, y=0, z=0), xaxis=1, zaxis=3, controller=dict(mode=1, active=False, joints=None, controllers=None))
        p['controller']['id'] = i
        p['controller']['controllers'] = None
        parts[i] = p
        owner[i] = domain
        labels[i] = label
        added.append(i)
        cm['nodes'].append(dict(controllerId=i, editorId=f'modular-{i}', label=label, kind='OR' if template is None else next((k for k, v in {'AND': 0, 'OR': 1, 'XOR': 2, 'NAND': 3, 'NOR': 4, 'XNOR': 5}.items() if v == p['controller']['mode'])), position=p['pos']))
        return i
    if console_switches:
        # The switches sit by the keyboard. A seam block must touch its partner, so each drives a relay at the seam.
        owner[reset] = owner[run] = 'terminal'
        run_tx, run_rx = add_gate('terminal', 'Module RUN send'), add_gate('cpu', 'Module RUN receive')
        links.append(dict(name='RUN', direction='terminal -> cpu', source=run_tx, receiver=run_rx, switch=run, preserves_existing_gate_delay=False))
        edges |= {(run, run_tx), (run_tx, run_rx)}
        reset_tx, reset_rx = add_gate('terminal', 'Module RESET send'), add_gate('cpu', 'Module RESET receive')
        links.append(dict(name='RESET', direction='terminal -> cpu', source=reset_tx, receiver=reset_rx, switch=reset, preserves_existing_gate_delay=False))
        edges |= {(reset, reset_tx), (reset_tx, reset_rx)}
    else:
        reset_rx = add_gate('terminal', 'Module RESET receive')
        links.append(dict(name='RESET', direction='cpu -> terminal', source=reset, receiver=reset_rx, preserves_existing_gate_delay=False))
        edges.add((reset, reset_rx))

    def local_pure(i, domain):
        if i == reset:
            return i if domain == owner[reset] else reset_rx
        if owner[i] == domain:
            return i
        key = (i, domain)
        if key not in copies:
            n = add_gate(domain, 'Module local ' + labels[i], parts[i])
            copies[key] = n
            for p in parents[i]:
                edges.add((local_pure(p, domain), n))
        return copies[key]
    received = {}

    def receive(i, domain):
        if i in pure:
            return local_pure(i, domain)
        if i == run:
            raise AssertionError('RUN uses its existing receiver')
        assert len(parents[i]) == 1 and parts[i]['controller']['mode'] == 1, (i, labels[i])
        p = next(iter(parents[i]))
        key = (p, domain)    # pins with the same driver share one link
        if key not in received:
            assert owner[p] == owner[i], (labels[i], labels[p])
            n = add_gate(domain, 'Module RX ' + labels[i], parts[i])
            received[key] = n
            edges.add((p, n))
            links.append(dict(name=labels[i], direction=owner[i] + ' -> ' + domain, source=p, receiver=n, original_output=i, preserves_existing_gate_delay=True))
        return received[key]
    for a, b in sorted(original_edges):
        if owner[a] == owner[b]:
            edges.add((a, b))
        elif a == run and console_switches:
            edges.add((run_rx, b))
        elif a == run:
            assert b == svc['names']['run']
            edges.add((a, b))
            links.append(dict(name='RUN', direction='cpu -> terminal', source=a, receiver=b, preserves_existing_gate_delay=True))
        else:
            edges.add((receive(a, owner[b]), b))
    crossings = {(a, b) for a, b in edges if owner[a] != owner[b]}
    assert crossings == {(l['source'], l['receiver']) for l in links}
    assert len(links) == 46 or console_switches, (len(links), [(l['name'], l['direction']) for l in links])
    outgoing = defaultdict(list)
    for a, b in sorted(edges):
        outgoing[a].append(dict(id=b))
    for i, p in parts.items():
        p['controller']['controllers'] = outgoing[i] or None
    bp['bodies'][0]['childs'] += [parts[i] for i in added]
    root = record_root / 'prototype' / str(uuid.uuid4())
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, root)
    o = baseline['orders']
    data = Encoder(bp).encode(o['child_order'], o['controller_order'], o['pos_order'])
    (root / 'blueprint.json').write_bytes(data)
    save(root / 'circuit-map.json', cm)
    row = dict(baseline, folder=str(root.resolve()), blueprint_sha256=hashlib.sha256(data).hexdigest())
    for key in list(row):
        if key.startswith('native_') and key not in ('native_installed', 'native_load_tested', 'native_tap_keyboard', 'native_validation'):
            row.pop(key)
    save(record_root / 'prototype.json', row)
    save(record_root / 'boundary.json', dict(baseline_blueprint_sha256=baseline['blueprint_sha256'], links=links, owners={str(k): v for k, v in owner.items()}, added_gates=len(added), module_nodes=dict(Counter(owner.values())), reset_latency_added_to_terminal_updates=1, data_and_ack_buffer_delays_preserved=True, copies={f'{i}:{domain}': n for (i, domain), n in copies.items()}))
    print('MODULAR', len(links), 'wires', len(added), 'added gates', Counter(owner.values()), flush=True)
    return row
