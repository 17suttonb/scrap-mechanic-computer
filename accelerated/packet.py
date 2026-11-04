from collections import Counter, deque
import operator
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import shutil
import uuid
from accelerated.circuit import GATE_UUID
from accelerated.supports import occupied_cells, components
from accelerated.io import read, save
CHILD = ('pos', 'shapeId', 'xaxis', 'zaxis', 'color', 'controller')
CONTROL = ('mode', 'active', 'joints', 'controllers', 'id')

def dump(value):
    return json.dumps(value, separators=(',', ':'))

class Encoder:

    def __init__(self, bp):
        self.bp = bp
        self.cached = None
        outer = dict(bp)
        outer['bodies'] = [dict(body, childs=f'__CHILDREN_{i}__') for i, body in enumerate(bp['bodies'])]
        self.outer = dump(outer)

    def prepare(self, control_order, pos_order):
        key = (tuple(control_order), tuple(pos_order))
        if self.cached == key:
            return
        self.cached = key
        self.rows = []
        self.groups = {}
        self.keys = []
        for body in self.bp['bodies']:
            rows = []
            for child in body['childs']:
                fields = []
                for name, value in child.items():
                    if name == 'controller' and isinstance(value, dict):
                        order = [k for k in control_order if k in value] + [k for k in value if k not in control_order]
                        text = '{' + ','.join((dump(k) + ':' + dump(value[k]) for k in order)) + '}'
                    elif name == 'pos' and isinstance(value, dict):
                        order = [k for k in pos_order if k in value] + [k for k in value if k not in pos_order]
                        text = '{' + ','.join((dump(k) + ':' + dump(value[k]) for k in order)) + '}'
                    else:
                        text = dump(value)
                    fields.append(dump(name) + ':' + text)
                keys = tuple(child)
                if keys not in self.groups:
                    self.groups[keys] = len(self.keys)
                    self.keys.append(keys)
                rows.append((self.groups[keys], tuple(fields)))
            self.rows.append(rows)

    def encode(self, child_order=CHILD, control_order=CONTROL, pos_order=('x', 'y', 'z')):
        self.prepare(control_order, pos_order)
        renderers = []
        for keys in self.keys:
            order = [k for k in child_order if k in keys] + [k for k in keys if k not in child_order]
            get = operator.itemgetter(*(keys.index(k) for k in order))
            renderers.append(lambda row, get=get: '{' + ','.join(get(row)) + '}')
        result = self.outer
        for i, rows in enumerate(self.rows):
            result = result.replace(dump(f'__CHILDREN_{i}__'), '[' + ','.join((renderers[group](row) for group, row in rows)) + ']')
        return result.encode()

def reorder(bp, mode, recolor=False, wire_order='ascending', id_start=1):
    bp = deepcopy(bp)
    parts = [p for b in bp['bodies'] for p in b['childs']]
    byid = {p['controller']['id']: p for p in parts if 'controller' in p}
    order = list(byid)
    if mode == 'reverse':
        order.reverse()
    if mode.startswith('kind'):

        def key(i):
            p = byid[i]
            c = p['controller']
            extra = ()
            if 'active' in mode:
                extra += (bool(c.get('active')),)
            if 'degree' in mode:
                extra += (len(c.get('controllers') or []),)
            if 'targets' in mode:
                extra += (tuple((byid[v['id']]['controller'].get('mode', -1) for v in c.get('controllers') or [])),)
            return (p['shapeId'], c.get('mode', -1), *extra, i)
        order.sort(key=key)
    if mode.startswith('spatial'):
        axes = mode.split('-')[1]
        order.sort(key=lambda i: tuple((byid[i]['pos'][a] for a in axes)))
    if mode in ('bfs', 'undirected-bfs'):
        adjacency = {i: [] for i in byid}
        for i, p in byid.items():
            for v in p['controller'].get('controllers') or []:
                adjacency[i].append(v['id'])
                if mode == 'undirected-bfs':
                    adjacency[v['id']].append(i)
        seen = set()
        order = []
        for root in byid:
            if root in seen:
                continue
            seen.add(root)
            pending = deque([root])
            while pending:
                i = pending.popleft()
                order.append(i)
                for j in adjacency[i]:
                    if j not in seen:
                        seen.add(j)
                        pending.append(j)
    assert id_start >= 1
    mapping = {old: new for new, old in enumerate(order, id_start)}
    for i, p in byid.items():
        c = p['controller']
        c['id'] = mapping[i]
        links = c.get('controllers')
        if links:
            for v in links:
                v['id'] = mapping[v['id']]
            if wire_order == 'ascending':
                links.sort(key=lambda v: v['id'])
            elif wire_order == 'descending':
                links.sort(key=lambda v: -v['id'])
        if recolor and p['shapeId'] == GATE_UUID and (p['color'] != '222222'):
            p['color'] = '34465E'
    rank = {id(p): mapping[i] for i, p in byid.items()}
    for b in bp['bodies']:
        b['childs'].sort(key=lambda p: rank.get(id(p), len(rank) + 1))
    return (bp, mapping)

def metadata(value, mapping, parent=''):
    if isinstance(value, list):
        return [metadata(x, mapping, parent) for x in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for k, v in value.items():
        if type(v) is int and (k == 'controllerId' or k.endswith('ControllerId') or parent in ('names', 'receivers', 'receiver_sources') or (k in ('busy', 'full', 'key_ready'))):
            assert v in mapping, (parent, k, v)
            result[k] = mapping[v]
        else:
            result[k] = metadata(v, mapping, k)
    return result

def canonical(bp):
    """Ignore only body-child and outgoing-wire order, preserving all values."""
    bp = deepcopy(bp)
    for b in bp['bodies']:
        for p in b['childs']:
            c = p.get('controller', {})
            if c.get('controllers'):
                c['controllers'].sort(key=lambda v: v['id'])
        b['childs'].sort(key=lambda p: json.dumps(p, sort_keys=True))
    return bp

def prepare(record, root, orders):
    original = Path(record['folder'])
    raw = (original / 'blueprint.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record['blueprint_sha256']
    bp = json.loads(raw)
    ordered, mapping = reorder(bp, 'kind-active', wire_order='ascending', id_start=1)
    inverse = {new: old for old, new in mapping.items()}
    restored = deepcopy(ordered)
    for body in restored['bodies']:
        for part in body['childs']:
            if 'controller' not in part:
                continue
            controller = part['controller']
            controller['id'] = inverse[controller['id']]
            for edge in controller.get('controllers') or []:
                edge['id'] = inverse[edge['id']]
    assert canonical(restored) == canonical(bp), 'Renumbering changed circuit data'
    folder = Path(root) / 'packet-ready' / str(uuid.uuid4())
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original, folder)
    for name in ('port-map.json', 'circuit-map.json', 'clock-map.json', 'service-clock-map.json', 'terminal-map.json', 'program-timers.json', 'packing.json', 'hdl-build.json', 'sign-map.json'):
        if (folder / name).exists():
            save(folder / name, metadata(read(folder / name), mapping))
    data = Encoder(ordered).encode(orders['child_order'], orders['controller_order'], orders['pos_order'])
    (folder / 'blueprint.json').write_bytes(data)
    parts = [part for body in ordered['bodies'] for part in body['childs']]
    cells = Counter((cell for part in parts for cell in occupied_cells(part)))
    assert max(cells.values()) == 1 and len(components(cells)) == 1
    ports = read(folder / 'port-map.json')
    byid = {part['controller']['id']: part for part in parts if 'controller' in part}
    for port in ports.values():
        for bit in port['bits']:
            assert bit['controllerId'] in byid
            bit['position'] = byid[bit['controllerId']]['pos']
    save(folder / 'port-map.json', ports)
    cmap = read(folder / 'circuit-map.json')
    for node in cmap['nodes']:
        if node['controllerId'] in byid:
            node['position'] = byid[node['controllerId']]['pos']
    save(folder / 'circuit-map.json', cmap)
    save(folder / 'controller-remap.json', {str(k): v for k, v in mapping.items()})
    return dict(record, folder=str(folder.resolve()), blueprint_sha256=hashlib.sha256(data).hexdigest())
