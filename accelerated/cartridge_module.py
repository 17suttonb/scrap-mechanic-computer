"""The CARTRIDGE blueprint: a cartridge file's gates, placed on the fixed seam. It needs nothing from the CORE and
CONSOLE it plugs into, so a new cartridge never means rebuilding them."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import re
import tempfile
import uuid
import lz4.block
from accelerated import cartridge as cart
from accelerated import districts as dist
from accelerated.circuit import BASE_UUID, Circuit, GATE_UUID, MODES, make_icon
from accelerated.io import read, save
from accelerated.packet import Encoder
from accelerated.signs import make_sign
from accelerated.supports import components, occupied_cells, sparse_supports

SEAM_CPU_Y = 79             # CORE-side blocks of the cartridge seam
SEAM_CART_Y = 80            # CARTRIDGE-side blocks, touching the CORE row
CUBE_Y = 83
SEAM_LEFT = -15
ROOT = Path(__file__).resolve().parent.parent


def seam_links(core_ids):
    """Every link of the seam, given the CORE's gate for each pin name."""
    links = []
    for n, (port, bit, way) in enumerate(cart.pins()):
        name, core, other = cart.pin_name(port, bit), None, cart.pin_id(n)
        core = core_ids[name]
        links.append(dict(name=name, direction='cpu -> cartridge' if way == 'out' else 'cartridge -> cpu', source=core if way == 'out' else other, receiver=other if way == 'out' else core, core=core, column=n, seam='cartridge'))
    return links


def map_groups(image, net):
    """cartridge-map.json for the groups that hold gates, in the file's order."""
    return dict(groups=[g.meta() for g in image.groups if g.key in net.report['group_blocks']])


def build(image, output, districts=True, total=3, flat=False):
    """Write one module folder under `output`. Returns its record."""
    net = cart.netlist(image)
    parts = {}
    for i, kind in net.kinds.items():
        parts[i] = dict(shapeId=GATE_UUID, color='34465E', pos=dict(x=0, y=0, z=0), xaxis=1, zaxis=3, controller=dict(mode=MODES[kind], active=kind == 'NOR', joints=None, controllers=None, id=i))
    outgoing = {}
    for a, b in sorted(net.edges):
        outgoing.setdefault(a, []).append(dict(id=b))
    for i, targets in outgoing.items():
        parts[i]['controller']['controllers'] = targets
    pin_ids = set(net.pins.values())
    ids = sorted(set(parts) - pin_ids)
    signs = []
    report = {}
    if districts:
        with tempfile.TemporaryDirectory() as folder:
            save(Path(folder) / dist.MAP_FILE, map_groups(image, net))
            plan = dist.plan_domain('cartridge', ids, net.labels, '', net.edges, Path(folder))
        for i, (x, y, z) in plan.position.items():
            parts[i].update(pos=dict(x=x, y=y, z=z), xaxis=1, zaxis=3, color=plan.colour[i])
        signs, report = plan.signs, plan.report
    elif flat:
        # One upright wall, twice as wide as tall, like the flat CORE and CONSOLE; each program's gates in its own colour.
        width = 1
        while width * (width // 2) < len(ids):
            width += 1
        tags = {}
        for i in ids:
            m = re.search(r' @cart_(\w+)', net.labels.get(i, ''))
            if m:
                tags.setdefault(m[1], len(tags))
        for n, i in enumerate(ids):
            row, col = divmod(n, width)
            parts[i].update(pos=dict(x=SEAM_LEFT + col + 1, y=CUBE_Y + 1, z=1 + row), xaxis=-1, zaxis=3)
            m = re.search(r' @cart_(\w+)', net.labels.get(i, ''))
            if m:
                parts[i]['color'] = dist.CART_PALETTE[tags[m[1]] % len(dist.CART_PALETTE)]
    else:
        side = 1
        while side ** 3 < len(ids):
            side += 1
        for n, i in enumerate(ids):
            layer, slot = divmod(n, side * side)
            y, x = divmod(slot, side)
            parts[i].update(pos=dict(x=SEAM_LEFT + x, y=CUBE_Y + y, z=1 + layer), xaxis=1, zaxis=3)
    table = cart.pins()
    for n, (port, bit, way) in enumerate(table):
        parts[cart.pin_id(n)].update(pos=dict(x=SEAM_LEFT + n, y=SEAM_CART_Y, z=1), xaxis=1, zaxis=-2, color='DFE0E1' if way == 'out' else '44AF55')
    children = [parts[i] for i in sorted(parts, key=(lambda i: (parts[i]['color'], i)) if districts else (lambda i: i))]
    next_sign = cart.ID_BASE + len(table) + len(net.kinds) + 1

    def label(text, x, y, colour='EEEEEE'):
        nonlocal next_sign
        p = make_sign(text, next_sign, dict(x=x, y=y, z=1), 'in')
        next_sign += 1
        p['color'] = colour
        children.append(p)

    def block(x, y, z, w=1, d=1, h=1):
        children.append(dict(shapeId=BASE_UUID, color='34465E', pos=dict(x=x, y=y, z=z), bounds=dict(x=w, y=d, z=h), xaxis=1, zaxis=3))
    label('CARTRIDGE', SEAM_LEFT + len(table) + 2, SEAM_CART_Y)
    label('OUTPUT BUS', SEAM_LEFT + len(table) + 2 + 6, SEAM_CART_Y)
    block(-20, SEAM_CART_Y - 1, 0, 3, 1, 1)
    block(-20, SEAM_CART_Y - 1, 1)
    block(-20, SEAM_CART_Y - 1, 2)
    label('WELD', -25, SEAM_CART_Y + 3)
    for text, x, y, colour in signs:
        label(text, x, y, colour)
    if districts:
        for x, y, w, d in dist.wall_boxes(plan.walls):
            for dx in range(0, w, 64):
                for dy in range(0, d, 64):
                    block(x + dx, y + dy, 1, min(64, w - dx), min(64, d - dy), dist.WALL_HEIGHT)
                    children[-1]['color'] = dist.WALL_COLOUR
    bp = dict(bodies=[dict(childs=children)], version=4)
    support = sparse_supports(bp, strict=True)
    occupied = Counter(c for p in bp['bodies'][0]['childs'] for c in occupied_cells(p))
    assert max(occupied.values()) == 1, [(c, n) for c, n in occupied.items() if n > 1][:5]
    assert len(components(occupied)) == 1
    assert min(c[1] for c in occupied) >= SEAM_CART_Y - 1
    folder = Path(output) / str(uuid.uuid4())
    folder.mkdir(parents=True, exist_ok=True)
    stub = Circuit('cartridge')
    stub.add('OR', 0, 0)
    (folder / 'icon.png').write_bytes(make_icon(stub))
    name = f'RV32E - accelerated CARTRIDGE 3 of {total}'
    save(folder / 'description.json', dict(description=f'Mate the plain L-shaped pads labelled WELD. The {len(table)}-block cartridge connector row touches after welding. Connect each coloured block to the grey block directly touching it across the seam. Works with any CORE and CONSOLE of this generation: RESET shows the menu of programs it holds. Native functional check pending.', localId=folder.name, name=name, type='Blueprint', version=0))
    orders = read(ROOT / 'release.json')['release_packet_orders']
    data = Encoder(bp).encode(orders['child_order'], orders['controller_order'], dist.POS_ORDER if districts else orders['pos_order'])
    (folder / 'blueprint.json').write_bytes(data)
    packet = len(lz4.block.compress(data, store_size=False)) + 13
    if packet >= 524288:
        raise ValueError(f'cartridge: {packet} byte packet, over the 524288 import limit')
    stats = dict(parts=len(children), logic_blocks=len(net.kinds), wires=len(net.edges))
    row = dict(folder=str(folder.resolve()), blueprint_name=name, blueprint_sha256=hashlib.sha256(data).hexdigest(), packet_bytes=packet, headroom_bytes=524288 - packet, stats=stats, support=support, domain='cartridge', report=net.report)
    save(folder / 'link-map.json', dict(links=[], module='cartridge'))
    if report:
        save(folder / 'district-map.json', dict(districts=report))
    save(folder / 'module-report.json', row)
    print('MODULE cartridge', packet, stats, flush=True)
    return row


def attach_joined(joined_folder, module_folder, links):
    """Add a built cartridge to a joined export, wiring its seam, for the simulator."""
    joined_folder, module_folder = Path(joined_folder), Path(module_folder)
    bp = json.loads((joined_folder / 'blueprint.json').read_text(encoding='utf-8'))
    module = json.loads((module_folder / 'blueprint.json').read_text(encoding='utf-8'))
    childs = bp['bodies'][0]['childs']
    childs += module['bodies'][0]['childs']
    by_id = {p['controller']['id']: p for p in childs if 'controller' in p}
    for l in links:
        c = by_id[l['source']]['controller']
        c['controllers'] = (c.get('controllers') or []) + [dict(id=l['receiver'])]
    orders = read(ROOT / 'release.json')['release_packet_orders']
    (joined_folder / 'blueprint.json').write_bytes(Encoder(bp).encode(orders['child_order'], orders['controller_order'], orders['pos_order']))
