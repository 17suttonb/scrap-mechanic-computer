from __future__ import annotations
import copy
import json
from pathlib import Path
import struct
import uuid
import zlib
GATE_UUID = '9f0f56e8-2c31-4d83-996c-d00a9b296c3f'
SWITCH_UUID = '7cf717d7-d167-4f2d-a6e7-6b2c70aa3986'
BUTTON_UUID = '1e8d93a4-506b-470d-9ada-9c0a321e2db5'
BASE_UUID = '4ad97d49-c8a5-47f3-ace3-d56ba3affe50'
MODES = {'AND': 0, 'OR': 1, 'XOR': 2, 'NAND': 3, 'NOR': 4, 'XNOR': 5}
KINDS = ('INPUT', *MODES, 'OUTPUT')
COLORS = {'INPUT': '4C6FE3', 'OUTPUT': 'F5D742', 'AND': 'DF7F01', 'OR': '2CB893', 'XOR': '9263D9', 'NAND': 'D96852', 'NOR': '4FA1CF', 'XNOR': 'C669AD'}
DEFAULT_BLUEPRINTS = Path(__file__).resolve().parent.parent / 'out-blueprints'

def encode_blueprint(blueprint):
    """Game-bound JSON must be compact: whitespace contributes to load packets.

    A 19,012-gate export written with indentation exceeded the native 512 KiB
    send limit; the identical compact JSON loaded in the user's game. Keep
    readable formatting in editor projects/reports, never blueprint.json.

    Field order also affects the compressed request. Put repeated shape and
    controller metadata together; retain every value and all array orders.
    The branch CPU's two observed packets match LZ4 block bytes + 13, and this
    ordering saves about 45 KiB without changing parsed blueprint data.
    """

    def first(mapping, keys):
        return {key: mapping[key] for key in (*keys, *(key for key in mapping if key not in keys)) if key in mapping}
    ordered = dict(blueprint)
    if 'bodies' in blueprint:
        ordered['bodies'] = []
        for body in blueprint['bodies']:
            new_body = dict(body)
            if 'childs' in body:
                children = []
                for child in body['childs']:
                    new_child = first(child, ('pos', 'shapeId', 'xaxis', 'zaxis', 'color', 'controller'))
                    if isinstance(child.get('controller'), dict):
                        new_child['controller'] = first(child['controller'], ('mode', 'active', 'joints', 'controllers', 'id'))
                    children.append(new_child)
                new_body['childs'] = children
            ordered['bodies'].append(new_body)
    return json.dumps(ordered, separators=(',', ':')).encode('utf-8')

class Circuit:

    def __init__(self, name='Untitled circuit'):
        self.name = name
        self.nodes = {}
        self.edges = set()
        self.next_id = 1
        self.tick_count = 0

    def add(self, kind, x, y, label=None):
        if kind not in KINDS:
            raise ValueError('Unknown gate type')
        i = self.next_id
        self.next_id += 1
        self.nodes[i] = dict(id=i, kind=kind, x=float(x), y=float(y), label=label or f'{kind} {i}', initial=False, state=False)
        return i

    def connect(self, source, target):
        if source not in self.nodes or target not in self.nodes:
            raise ValueError('Wire references a missing gate')
        if self.nodes[target]['kind'] == 'INPUT':
            raise ValueError('Switch inputs cannot receive wires')
        self.edges.add((source, target))

    def remove(self, ids):
        ids = set(ids)
        self.nodes = {i: n for i, n in self.nodes.items() if i not in ids}
        self.edges = {(a, b) for a, b in self.edges if a not in ids and b not in ids}

    def reset(self):
        self.tick_count = 0
        for n in self.nodes.values():
            n['state'] = n['initial']

    def to_dict(self):
        nodes = [{k: v for k, v in n.items() if k != 'state'} for n in self.nodes.values()]
        return dict(format='scrap-circuit', version=1, name=self.name, nodes=nodes, edges=[list(e) for e in sorted(self.edges)])

    def clone(self):
        return copy.deepcopy(self)

    def validate(self):
        if not self.nodes:
            raise ValueError('Add at least one gate before exporting')
        if len(self.nodes) > 120000:  # uncompacted netlists run larger; the export check below limits what ships
            raise ValueError('Netlist is limited to 120,000 gates before compaction')
        incoming, outgoing = ({}, {})
        for a, b in self.edges:
            if a not in self.nodes or b not in self.nodes:
                raise ValueError('A wire references a missing gate')
            if self.nodes[b]['kind'] == 'INPUT':
                raise ValueError('Switch inputs cannot receive wires')
            incoming[b] = incoming.get(b, 0) + 1
            outgoing[a] = outgoing.get(a, 0) + 1
        if max([*incoming.values(), *outgoing.values(), 0]) > 255:
            raise ValueError('A gate exceeds the vanilla 255 connection limit')

    def blueprint(self, ports=None):
        blueprint, manifest, _ = self._blueprint(ports)
        return (blueprint, manifest)

    def _blueprint(self, ports=None):
        self.validate()
        ordered = sorted(self.nodes.values(), key=lambda n: (round(n['y'] / 100), n['x'], n['id']))
        edges, relays = (set(), [])
        relay_id = max(self.nodes) + 1
        for a, b in sorted(self.edges):
            if a > b and (b, a) in self.edges:
                node = dict(id=relay_id, kind='OR', label=f'Feedback relay {a} to {b}', initial=bool(self.nodes[a]['initial']), x=0, y=0)
                ordered.append(node)
                relays.append((a, b, relay_id))
                edges.update(((a, relay_id), (relay_id, b)))
                relay_id += 1
            else:
                edges.add((a, b))
        if len(ordered) > 50000:
            raise ValueError('Circuit exceeds 50,000 gates after adding feedback wiring relays')
        children = []
        ids = {n['id']: i + 1 for i, n in enumerate(ordered)}
        outgoing = {n['id']: [] for n in ordered}
        for a, b in sorted(edges):
            outgoing[a].append(dict(id=ids[b]))
        manifest = []
        for n in ordered:
            controller = dict(active=bool(n['initial']), controllers=outgoing[n['id']] or None, id=ids[n['id']], joints=None)
            if n['kind'] != 'INPUT':
                controller['mode'] = MODES.get(n['kind'], 1)
            children.append(dict(color=COLORS[n['kind']], controller=controller, pos=dict(x=0, y=0, z=1), shapeId=SWITCH_UUID if n['kind'] == 'INPUT' else GATE_UUID, xaxis=1, zaxis=-2))
            manifest.append(dict(controllerId=ids[n['id']], editorId=n['id'], label=n['label'], kind=n['kind'], physicalOnly=n['id'] not in self.nodes, position=dict(x=0, y=0, z=1)))
        if ports is None:
            ports = {f"{n['label']} [{n['id']}]": dict(mode='in' if n['kind'] == 'INPUT' else 'out', indices=[0], node_ids=[n['id']], display_name=n['label']) for n in ordered if n['kind'] in ('INPUT', 'OUTPUT')}
        from accelerated.packing import pack_cube
        blueprint = dict(bodies=[dict(childs=children)], version=4)
        packing = pack_cube(blueprint, manifest, ordered, ports)
        packing['wiring'] = dict(source_blocks=len(self.nodes), source_wires=len(self.edges), physical_blocks=len(ordered), physical_wires=len(edges), feedback_relays=len(relays), relays=[dict(sourceControllerId=ids[a], targetControllerId=ids[b], relayControllerId=ids[r]) for a, b, r in relays], note='Reciprocal links are routed through OR relays to form three-part loops.')
        return (blueprint, manifest, packing)

    def export(self, root, ports=None):
        bp, manifest, packing = self._blueprint(ports)
        root = Path(root).expanduser()
        if not root.is_dir():
            raise ValueError('Choose an existing blueprint directory')
        local_id = str(uuid.uuid4())
        folder = root / local_id
        payload = {'blueprint.json': bp, 'description.json': dict(description=f"Built with Circuit Workshop. {packing['wiring']['physical_blocks']} gates, {packing['wiring']['physical_wires']} wires. Inputs: blue. Outputs: yellow.", localId=local_id, name=self.name.strip() or 'Circuit', type='Blueprint', version=0), 'circuit.project.json': self.to_dict(), 'circuit-map.json': dict(note='Exposed I/O is labeled by native signs; internal labels remain editor metadata. Coordinates and label sign IDs are recorded here.', nodes=manifest), 'packing.json': dict(layout='cube', **packing), 'sign-map.json': dict(signs=packing['io_panel']['signs'])}
        encoded = {name: encode_blueprint(value) if name == 'blueprint.json' else json.dumps(value, indent=2).encode('utf-8') for name, value in payload.items()}
        encoded['icon.png'] = make_icon(self)
        folder.mkdir(exist_ok=False)
        try:
            for name, contents in encoded.items():
                (folder / name).write_bytes(contents)
        except Exception:
            for name in encoded:
                (folder / name).unlink(missing_ok=True)
            folder.rmdir()
            raise
        return folder

def make_icon(circuit):
    """Small generated PNG board thumbnail, using only the Python standard library."""
    size = 256
    pixels = bytearray(bytes.fromhex('111b29') * size * size)

    def rect(x, y, w, h, color):
        color = bytes.fromhex(color)
        for yy in range(max(0, y), min(size, y + h)):
            for xx in range(max(0, x), min(size, x + w)):
                pos = (yy * size + xx) * 3
                pixels[pos:pos + 3] = color
    rect(16, 16, 224, 6, '54d6b0')
    for idx, n in enumerate(list(circuit.nodes.values())[:64]):
        rect(24 + idx % 8 * 27, 43 + idx // 8 * 25, 17, 15, COLORS[n['kind']])
    raw = b''.join((b'\x00' + pixels[y * size * 3:(y + 1) * size * 3] for y in range(size)))

    def chunk(tag, payload):
        return struct.pack('!I', len(payload)) + tag + payload + struct.pack('!I', zlib.crc32(tag + payload) & 4294967295)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
