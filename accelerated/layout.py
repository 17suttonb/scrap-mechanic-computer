from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import shutil
import uuid
from accelerated.circuit import BASE_UUID, GATE_UUID
from accelerated.supports import occupied_cells, components, sparse_supports, TIMER_UUID
from accelerated.signs import make_sign, sign_record, SIGN_BY_UUID
from accelerated import sections as sec
from accelerated import districts as dist
from accelerated.io import read, save
from accelerated.packet import Encoder
import lz4.block

FLAT_CONSOLE_RIGHT = -19     # last column of the console's logic wall, just left of the screen frame
FLAT_CONSOLE_Y = 20
FLAT_CORE_Y = 44
FLAT_CORE_TIMER_Y = 36
CART_SEAM_CPU_Y = 79       # CORE-side blocks of the cartridge seam
CART_SEAM_CART_Y = 80      # CARTRIDGE-side blocks, touching the CORE row
CART_CUBE_Y = 83           # logic cube of the cartridge module
CONSOLE_SWITCH_X = -6      # RUN, then RESET two along, then the manual clock switches
CONSOLE_SWITCH_Y = -22
BLOCK_LIMIT = 64

def build(root, flat=False, solid=False, districts=False):
    """solid (beta): RUN and RESET on the console, the screen backed by blocks, the keyboard further from
    the screen on thin rails. districts: the logic as a map of the machine, see docs/layout.md."""
    touching = True
    title = 'FAST BASIC'
    name_prefix = 'accelerated '
    bridge_range = (46, 46)
    r = read(root / 'prototype.json')
    src = Path(r['folder'])
    bp = read(src / 'blueprint.json')
    boundary = read(root / 'boundary.json')
    owner = {int(i): v for i, v in boundary['owners'].items()}
    parts = {p['controller']['id']: deepcopy(p) for b in bp['bodies'] for p in b['childs'] if 'controller' in p and 'data' not in p['controller']}
    original_edges = {(i, e['id']) for i, p in parts.items() for e in p['controller'].get('controllers') or []}
    links = [l for l in boundary['links'] if 'cartridge' not in l['direction']]
    cart_links = [l for l in boundary['links'] if 'cartridge' in l['direction']]
    modules = ('cpu', 'terminal') + (('cartridge',) if cart_links else ())
    outgoing = [l for l in links if l['direction'] == 'cpu -> terminal']
    incoming = [l for l in links if l['direction'] == 'terminal -> cpu']

    def key(l):
        name = l['name']
        order = ['bus_write_data', 'bus_address', 'bus_kind', 'bus_write(', 'bus_request', 'input_index', 'faulted', 'RUN', 'RESET', 'Terminal keyboard_data', 'Terminal keyboard_length', 'Service bus_read_data', 'Service bus_ack', 'Service gpio']
        rank = next((n for n, prefix in enumerate(order) if name.startswith(prefix)), 99)
        import re
        bit = re.search('\\((\\d+)\\)', name)
        return (rank, int(bit[1]) if bit else 0)
    outgoing.sort(key=key)
    incoming.sort(key=key)
    links = outgoing + incoming
    assert len(outgoing) == 22 and len(incoming) == 23 or (True and (46, 46)[0] <= len(links) <= (46, 46)[1]) or cart_links
    link_count = len(links)
    assert len({l['source'] for l in links}) == link_count
    assert not {l['source'] for l in links} & {l['receiver'] for l in links}
    term = read(src / 'terminal-map.json')
    pixel_ids = {p['controllerId'] for p in term['pixels']}
    key_ids = {p['controllerId'] for p in term['keys']}
    assert not pixel_ids & {l['source'] for l in links}
    monitors = {term['busy'], term['key_ready']}
    clock = read(src / 'clock-map.json')
    svc = read(src / 'service-clock-map.json')
    timer_ids = {t['controllerId'] for c in (clock, svc) for t in c['timers']}
    flat_width = {}
    exposed = pixel_ids | key_ids | monitors | timer_ids | {l[k] for l in links + cart_links for k in ('source', 'receiver')}
    # switches join only through their back, so they cannot be part of a wall of gates
    switches = {l['name']: l['switch'] for l in links if 'switch' in l}
    manual = {'cpu': [clock['names'][n] for n in ('manual_capture', 'manual_commit')], 'terminal': [svc['names'][n] for n in ('manual_capture', 'manual_commit')]} if solid else {}
    exposed |= set(switches.values()) | {i for ids in manual.values() for i in ids}
    # far enough back to take in the whole screen at once, with the eye in a key row
    keyboard_back = term['height'] * 3 // 4 if solid else 0
    switch_y = CONSOLE_SWITCH_Y - keyboard_back
    for i in key_ids | monitors:
        if keyboard_back:
            parts[i]['pos']['y'] -= keyboard_back
    plans = {}
    if districts:
        labels = {n['controllerId']: n['label'] for n in read(src / 'circuit-map.json')['nodes']}
        source = (src / 'source.vhd').read_text(encoding='utf-8')
        for domain in modules:
            ids = [i for i in parts if owner[i] == domain and i not in exposed]
            plans[domain] = dist.plan_domain(domain, ids, labels, source, original_edges, src)
            for i, (x, y, z) in plans[domain].position.items():
                parts[i].update(pos=dict(x=x, y=y, z=z), xaxis=1, zaxis=3, color=plans[domain].colour[i])
    section_of, section_colour, section_count = {}, {}, {}
    if flat:
        # Paint by functional section instead of by gate type. Colours are spread by maximin distance
        # and kept away from the fixed colours already on the board.
        labels = {n['controllerId']: n['label'] for n in read(src / 'circuit-map.json')['nodes']}
        allowed_top = dict(cpu='rv32e_cpu', terminal='rv_terminal')
        fixed = ['34465E', 'EEEEEE', 'DFE0E1', '4C6FE3', 'D96852', 'F5D742']
        for domain in ('cpu', 'terminal'):
            ids = [i for i in parts if owner[i] == domain and i not in exposed]
            found = sec.assign_sections(labels, original_edges, ids)
            allowed = sec.DOMAIN_SECTIONS[domain]
            section_of.update({i: v if v in allowed else sec.TOP_DEFAULT[allowed_top[domain]] for i, v in found.items()})
            counts = Counter(section_of[i] for i in ids if section_of[i])
            section_count[domain] = counts
            section_colour[domain] = sec.section_colors(sorted(counts, key=lambda k: (-counts[k], k)), fixed)
    for domain, origin in [('terminal', (-15, 3, 1)), ('cpu', (-15, 40, 1))] + ([('cartridge', (-15, CART_CUBE_Y, 1))] if cart_links else []):
        ids = sorted((i for i in parts if owner[i] == domain and i not in exposed))
        if districts:
            continue
        if flat:
            # One upright wall (x-z plane, bottom row on the ground plane), twice as wide as tall.
            # Each logic block is turned 180 degrees about Z; the origin shifts by (1, 1) so the
            # occupied cell stays the grid cell.
            width = 1
            while width * (width // 2) < len(ids):
                width += 1
            for n, i in enumerate(ids):
                row, col = divmod(n, width)
                x0, y0 = {'terminal': (FLAT_CONSOLE_RIGHT - width, FLAT_CONSOLE_Y), 'cartridge': (origin[0], CART_CUBE_Y)}.get(domain, (origin[0], FLAT_CORE_Y))
                parts[i].update(pos=dict(x=x0 + col + 1, y=y0 + 1, z=1 + row), xaxis=-1, zaxis=3)
                if section_of.get(i):
                    parts[i]['color'] = section_colour[domain][section_of[i]]
            flat_width[domain] = width
            continue
        side = 1
        while side ** 3 < len(ids):
            side += 1
        for n, i in enumerate(ids):
            layer, slot = divmod(n, side * side)
            y, x = divmod(slot, side)
            parts[i].update(pos=dict(x=origin[0] + x, y=origin[1] + y, z=origin[2] + layer), xaxis=1, zaxis=3)
    for domain, c, y in [('cpu', clock, 72), ('terminal', svc, 7)]:
        for n, timer in enumerate(c['timers']):
            if districts:
                x, y = (-12 + 6 * n, dist.CORE_TIMER_Y) if domain == 'cpu' else (dist.CONSOLE_ANNEX_X + 6 * n, dist.CONSOLE_KEY_Y)
            elif flat and domain == 'cpu':
                x, y = -12 + 6 * n, FLAT_CORE_TIMER_Y
            else:
                x = 15 + 6 * n if domain == 'terminal' else -12 + 6 * n
            parts[timer['controllerId']].update(pos=dict(x=x, y=y, z=1), xaxis=1, zaxis=3)
    for index, l in enumerate(links, 1):
        row = 0 if l['direction'] == 'cpu -> terminal' else 1
        col = index - 1
        x = -15 + col * 1
        cpu_y = 31
        terminal_y = 30
        for role in ('source', 'receiver'):
            i = l[role]
            y = cpu_y if owner[i] == 'cpu' else terminal_y
            color = '44AF55' if role == 'source' else 'DFE0E1'
            if not switches and l['name'] == 'RUN' and (role == 'source'):
                color = '4C6FE3'
            if not switches and l['name'] == 'RESET' and (role == 'source'):
                color = 'D96852'
            parts[i].update(pos=dict(x=x, y=y, z=1), xaxis=1, zaxis=-2, color=color)
        l.update(number=index, row=0, column=col, source_position=parts[l['source']]['pos'], receiver_position=parts[l['receiver']]['pos'])
    # above the keyboard, level with its lamps
    for name, x, color in (('RUN', CONSOLE_SWITCH_X, '4C6FE3'), ('RESET', CONSOLE_SWITCH_X + 2, 'D96852')):
        if name in switches:
            parts[switches[name]].update(pos=dict(x=x, y=switch_y + 1, z=1), xaxis=1, zaxis=-2, color=color)
    for domain, ids in manual.items():
        for n, i in enumerate(ids):
            x, y = (CONSOLE_SWITCH_X + 5 + n * 2, switch_y) if domain == 'terminal' else (-15 + n * 2, 33)
            parts[i].update(pos=dict(x=x, y=y + 1, z=1), xaxis=1, zaxis=-2)
    cart_links.sort(key=lambda l: (l['direction'] != 'cpu -> cartridge', l['name']))
    for index, l in enumerate(cart_links, 1):
        for role in ('source', 'receiver'):
            i = l[role]
            parts[i].update(pos=dict(x=-15 + index - 1, y=CART_SEAM_CPU_Y if owner[i] == 'cpu' else CART_SEAM_CART_Y, z=1), xaxis=1, zaxis=-2, color='44AF55' if role == 'source' else 'DFE0E1')
        l.update(number=index, row=0, column=index - 1, seam='cartridge', source_position=parts[l['source']]['pos'], receiver_position=parts[l['receiver']]['pos'])
    actual = {}
    for i, p in parts.items():
        for cell in occupied_cells(p):
            actual[cell] = i
    across = set()
    for cell, i in actual.items():
        x, y, z = cell
        for other in ((x + 1, y, z), (x, y + 1, z), (x, y, z + 1)):
            j = actual.get(other)
            if j is not None and owner[i] != owner[j]:
                across.add(frozenset((i, j)))
    wanted = {frozenset((l['source'], l['receiver'])) for l in links + cart_links}
    assert across == wanted, ('extra', [(sorted(p), [(parts[i]['pos'], owner[i]) for i in p]) for p in list(across - wanted)[:4]], 'missing', [(l['name'], l['direction'], l['source'], l['receiver'], parts[l['source']]['pos'], parts[l['receiver']]['pos']) for l in links + cart_links if frozenset((l['source'], l['receiver'])) not in across][:6])
    for l in links + cart_links:
        a = list(occupied_cells(parts[l['source']]))
        b = list(occupied_cells(parts[l['receiver']]))
        assert len(a) == len(b) == 1 and sum((abs(x - y) for x, y in zip(a[0], b[0]))) == 1
    old_signs = {p['controller']['id']: p for b in bp['bodies'] for p in b['childs'] if 'controller' in p and 'data' in p['controller']}
    keep_signs = [deepcopy(old_signs[s['controllerId']]) for s in read(src / 'sign-map.json')['signs'] if s['port'] == 'keyboard']
    for p in keep_signs:
        p['pos']['y'] -= keyboard_back
    next_sign = max([*parts, *old_signs]) + 1
    module_records = {}
    all_modules = []
    all_signs = []
    for domain in modules:
        # In a districts file a shade's gates stay together, in id order: position runs are what compresses.
        order = (lambda i: (parts[i]['color'], i)) if domain in plans else (lambda i: i)
        children = [deepcopy(parts[i]) for i in sorted(parts, key=order) if owner[i] == domain]
        signs = []
        for p in children:
            p['controller']['controllers'] = [e for e in p['controller'].get('controllers') or [] if owner[e['id']] == domain] or None
        if domain == 'terminal':
            children += deepcopy(keep_signs)

        def block(x, y, z, w=1, d=1, h=1, color='34465E'):
            color = '34465E'
            children.append(dict(shapeId=BASE_UUID, color=color, pos=dict(x=x, y=y, z=z), bounds=dict(x=w, y=d, z=h), xaxis=1, zaxis=3))

        def box(x, y, z, w, d, h):
            """A block of any size, in pieces the game's block size limit allows. Returns them."""
            pieces = []
            for dx in range(0, w, BLOCK_LIMIT):
                for dy in range(0, d, BLOCK_LIMIT):
                    for dz in range(0, h, BLOCK_LIMIT):
                        block(x + dx, y + dy, z + dz, min(BLOCK_LIMIT, w - dx), min(BLOCK_LIMIT, d - dy), min(BLOCK_LIMIT, h - dz))
                        pieces.append(children[-1])
            return pieces

        def label(text, x, y, z=1, upright=False):
            nonlocal next_sign
            p = make_sign(text, next_sign, dict(x=x, y=y, z=z), 'in')
            next_sign += 1
            p['color'] = 'EEEEEE'
            if upright:
                p['zaxis'] = -2
            children.append(p)
            signs.append(sign_record(p, text, 'module', 'in'))
        label({'cpu': 'CORE', 'terminal': 'CONSOLE', 'cartridge': 'CARTRIDGE'}[domain], -15 + (len(cart_links) if domain == 'cartridge' else link_count) + 2, {'cpu': 30, 'terminal': 28, 'cartridge': CART_SEAM_CART_Y}[domain])
        if domain == 'cpu' and cart_links:
            label('CARTRIDGE SEAM', -15 + len(cart_links) + 2, CART_SEAM_CPU_Y - 3)
        if domain == 'cpu' and not switches:
            label('RUN RESET', 3, 32)
        if domain == 'terminal' and switches:
            label('RUN RESET', CONSOLE_SWITCH_X - 1, switch_y + 1)
        if domain == 'terminal':
            block(-17, 0, 2, 74)
            block(-17, 0, term['height'] + 3, 74)
            block(-17, 0, 3, 1, 1, term['height'])
            block(56, 0, 3, 1, 1, term['height'])
            block(-17, 0, 1)
            block(56, 0, 1)
            label('FAST BASIC', 16, 1, term['height'] + 4, upright=True)
            if solid:
                # the foot of the screen, and a plate behind every pixel and the title sign. The supports
                # below join the keyboard with one-wide runs: its keys only attach through their backs.
                box(-16, 0, 1, 72, 1, 1)
                box(-17, 1, 1, 74, 1, term['height'] + 5)
            for n, t in enumerate(svc['timers']):
                label('IO ' + str(n + 1), parts[t['controllerId']]['pos']['x'] - 1, dist.CONSOLE_KEY_Y + 2 if districts else 10)
        elif domain == 'cpu':
            label('CORE MODULE', 14 if districts else 0, dist.CORE_TIMER_Y if districts else FLAT_CORE_TIMER_Y - 2 if flat else 70)
            for n, t in enumerate(clock['timers']):
                label('CPU ' + str(n + 1), parts[t['controllerId']]['pos']['x'] - 1, dist.CORE_TIMER_Y + 2 if districts else FLAT_CORE_TIMER_Y + 3 if flat else 75)
        if flat and domain != 'cartridge':
            # Colour key along the foot of the wall, each sign painted in its section's colour.
            x = -15 if domain == 'cpu' else FLAT_CONSOLE_RIGHT - flat_width[domain]
            for section, count in section_count[domain].most_common():
                label(sec.TITLES.get(section, section.upper()), x, FLAT_CORE_Y - 2 if domain == 'cpu' else FLAT_CONSOLE_Y - 3)
                children[-1]['color'] = section_colour[domain][section]
                x += SIGN_BY_UUID[children[-1]['shapeId']].size[0] + 1
        for pad_y, weld_y in ([(30, 33), (CART_SEAM_CPU_Y - 1, CART_SEAM_CPU_Y - 4)] if domain == 'cpu' and cart_links else [(30, 33)] if domain == 'cpu' else [(CART_SEAM_CART_Y - 1, CART_SEAM_CART_Y + 3)] if domain == 'cartridge' else [(29, 26)]):
            block(-20, pad_y, 0, 3, 1, 1, color='F5D742')
            block(-20, pad_y, 1, color='F5D742')
            block(-20, pad_y, 2, color='D96852')
            label('WELD', -25, weld_y)
        if domain in plans:
            for text, x, y, colour in plans[domain].signs:
                label(text, x, y)
                children[-1]['color'] = colour
        module = dict(bp, bodies=[dict(childs=children)])
        support = sparse_supports(module, strict=solid)
        occupied = Counter((c for p in module['bodies'][0]['childs'] for c in occupied_cells(p)))
        assert max(occupied.values()) == 1, [(c, n) for c, n in occupied.items() if n > 1][:5]
        assert len(components(occupied)) == 1
        if domain == 'cartridge':
            assert min((c[1] for c in occupied)) >= CART_SEAM_CART_Y - 1
        elif domain == 'cpu':
            assert 30 <= min((c[1] for c in occupied)) and max((c[1] for c in occupied)) <= CART_SEAM_CPU_Y - 1
        else:
            assert max((c[1] for c in occupied)) <= 29
        ids = {p['controller']['id'] for p in children if 'controller' in p}
        assert all((e['id'] in ids for p in children for e in p.get('controller', {}).get('controllers') or []))
        folder = root / 'modules' / str(uuid.uuid4())
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / 'icon.png', folder / 'icon.png')
        description = read(src / 'description.json')
        description.update(localId=folder.name, name='RV32E - ' + 'accelerated ' + {'cpu': 'CORE 1 of ', 'terminal': 'CONSOLE 2 of ', 'cartridge': 'CARTRIDGE 3 of '}[domain] + str(len(modules)), description=(f'Mate the plain L-shaped pads labelled WELD. The {len(cart_links)}-block cartridge connector row touches after welding. Connect each coloured block to the grey block directly touching it across the seam. Needs the matching CORE and CONSOLE; type S then ENTER at the prompt to run the program in the cartridge, RESET to return. Native functional check pending.' if domain == 'cartridge' else '') or f'Mate the plain L-shaped pads labelled WELD. The {link_count}-block connector rows touch after welding. Connect each coloured block to the grey block directly touching it across the seam. Blue switch RUN; red switch RESET. RUN OFF; RESET ON 6 seconds, OFF; RUN ON. Tap H then ENTER. No passive test bank. Native functional check pending.')
        save(folder / 'description.json', description)
        o = r['orders']
        data = Encoder(module).encode(o['child_order'], o['controller_order'], dist.POS_ORDER if domain in plans else o['pos_order'])
        (folder / 'blueprint.json').write_bytes(data)
        packet = len(lz4.block.compress(data, store_size=False)) + 13
        if domain in plans and packet >= 524288:
            raise ValueError(f'{domain}: the districts layout makes a {packet} byte packet, over the 524288 import limit; --layout cube is about 25 kB smaller (docs/layout.md)')
        assert packet < 524288
        stats = dict(parts=len(children), logic_blocks=sum((p['shapeId'] == GATE_UUID for p in children)), timers=sum((p['shapeId'] == TIMER_UUID for p in children)), wires=sum((len(p.get('controller', {}).get('controllers') or []) for p in children)))
        row = dict(folder=str(folder.resolve()), blueprint_name=description['name'], blueprint_sha256=hashlib.sha256(data).hexdigest(), packet_bytes=packet, headroom_bytes=524288 - packet, stats=stats, support=support, domain=domain)
        save(folder / 'link-map.json', dict(links=links + cart_links, module=domain))
        if domain in plans:
            save(folder / 'district-map.json', dict(districts=plans[domain].report))
        if flat and domain != 'cartridge':
            save(folder / 'section-map.json', dict(sections={k: dict(title=sec.TITLES.get(k, k), colour=section_colour[domain][k], logic_blocks=n) for k, n in section_count[domain].most_common()}))
        save(folder / 'module-report.json', row)
        module_records[domain] = row
        all_modules += deepcopy(module['bodies'][0]['childs'])
        all_signs += signs
        print('MODULE', domain, packet, stats, flush=True)
    joined = dict(bp, bodies=[dict(childs=all_modules)])
    byid = {p['controller']['id']: p for p in all_modules if 'controller' in p}
    for l in links + cart_links:
        c = byid[l['source']]['controller']
        c['controllers'] = (c.get('controllers') or []) + [dict(id=l['receiver'])]
    joined_edges = {(i, e['id']) for i, p in byid.items() for e in p['controller'].get('controllers') or []}
    assert joined_edges == original_edges
    for i, p in parts.items():
        assert byid[i]['shapeId'] == p['shapeId'] and byid[i]['controller']['active'] == p['controller']['active'] and (byid[i]['controller'].get('mode') == p['controller'].get('mode'))
    cells = Counter((c for p in all_modules for c in occupied_cells(p)))
    assert max(cells.values()) == 1 and len(components(cells)) == 1
    folder = root / 'joined' / str(uuid.uuid4())
    folder.parent.mkdir(exist_ok=True)
    shutil.copytree(src, folder)
    o = r['orders']
    data = Encoder(joined).encode(o['child_order'], o['controller_order'], o['pos_order'])
    (folder / 'blueprint.json').write_bytes(data)
    cm = read(folder / 'circuit-map.json')
    for n in cm['nodes']:
        n['position'] = parts[n['controllerId']]['pos']
    save(folder / 'circuit-map.json', cm)
    for name in ('port-map.json', 'clock-map.json', 'service-clock-map.json', 'terminal-map.json'):
        d = read(folder / name)

        def update(v):
            if isinstance(v, list):
                return [update(x) for x in v]
            if not isinstance(v, dict):
                return v
            v = {k: update(x) for k, x in v.items()}
            if 'controllerId' in v and v['controllerId'] in parts and ('position' in v):
                v['position'] = parts[v['controllerId']]['pos']
            return v
        save(folder / name, update(d))
    row = dict(r, folder=str(folder.resolve()), blueprint_sha256=hashlib.sha256(data).hexdigest(), benchmark_root=str((root / 'joined-validation').resolve()), native_validation='Private assembly simulation; separately import modules', joined_packet_estimate=len(lz4.block.compress(data, store_size=False)) + 13)
    save(root / 'joined.json', row)
    save(root / 'modules.json', dict(modules=module_records, links=links, joined=row, total_headroom=sum((m['headroom_bytes'] for m in module_records.values())), electrical_graph_identical_to_prototype=True, touching_rows=True, each_bridge_is_unique_touching_pair=True, structural_paint_cues=False))
    print('JOINED', row['joined_packet_estimate'], link_count + len(cart_links), 'bridges; collision-free supported assembly', flush=True)
    return row
