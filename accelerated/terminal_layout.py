import json
from pathlib import Path
from accelerated.circuit import BASE_UUID, GATE_UUID, BUTTON_UUID, encode_blueprint
from accelerated.supports import sparse_supports
from accelerated.signs import make_sign, choose_sign, sign_record
from accelerated.terminal import KEYS, KEY_ROWS, KEY_CODES

def arrange_terminal(folder, probe=False, async_diagnostics=False):
    folder = Path(folder)

    def read(name):
        return json.loads((folder / name).read_text(encoding='utf-8'))
    bp = read('blueprint.json')
    cm = read('circuit-map.json')
    packing = read('packing.json')
    ports = read('port-map.json')
    clock = read('clock-map.json')
    service_clock = read('service-clock-map.json') if (folder / 'service-clock-map.json').exists() else None
    clocks = [clock] + ([service_clock] if service_clock else [])
    design_report = read('hdl-build.json')
    geometry = design_report.get('terminal_geometry', {})
    width = geometry.get('width', 32)
    height = geometry.get('height', 24)
    blank = set(geometry.get('blank_pixels', []))
    frame_width = width + 2
    children = [c for c in bp['bodies'][0]['childs'] if c['shapeId'] == GATE_UUID or c['shapeId'] == BUTTON_UUID or ('controller' in c and 'data' not in c['controller'])]
    by_id = {c['controller']['id']: c for c in children}
    manifest = {m['controllerId']: m for m in cm['nodes']}
    pixels = [b['controllerId'] for b in ports['terminal_pixels']['bits']]
    spacers = sorted(blank)
    if spacers:
        next_pixel = max(by_id) + 1
        for offset, index in enumerate(spacers):
            controller = next_pixel + offset
            child = dict(shapeId=GATE_UUID, color='222222', pos=dict(x=0, y=0, z=0), xaxis=-1, zaxis=3, controller=dict(id=controller, mode=1, active=False, joints=None, controllers=None))
            item = dict(controllerId=controller, editorId=f'screen-spacer-{index}', label=f'Terminal spacer pixel {index}', kind='OR', position=child['pos'], physicalOnly=True)
            children.append(child)
            by_id[controller] = child
            cm['nodes'].append(item)
            manifest[controller] = item
            pixels[index] = controller
            ports['terminal_pixels']['bits'][index]['controllerId'] = controller
        geometry = dict(geometry, blank_pixels=[], spacer_pixels=spacers)
        design_report['terminal_geometry'] = geometry
        blank = set()
        packing['wiring']['physical_blocks'] += len(spacers)
    keys = [b['controllerId'] for b in ports['keys']['bits']]
    monitors = {name: ports[name]['bits'][0]['controllerId'] for name in ('busy', 'full')}
    exposed = set([p for i, p in enumerate(pixels) if i not in blank] + keys + list(monitors.values()) + [clock['names'][n] for n in ('run', 'reset')] + [t['controllerId'] for t in clock['timers']])
    if service_clock:
        exposed.update((t['controllerId'] for t in service_clock['timers']))
    diagnostics = []
    if service_clock and async_diagnostics:
        diagnostics = [(clock['names']['capture'], 'CPU CAP'), (clock['names']['commit'], 'CPU COM'), (service_clock['names']['capture'], 'SVC CAP'), (service_clock['names']['commit'], 'SVC COM'), (clock['names']['auto_active'], 'CPU RUN'), (service_clock['names']['auto_active'], 'SVC RUN')]
        for text, name in (('REQ', 'bus_request(0)'), ('ACK', 'Service bus_ack(0)')):
            matches = [m['controllerId'] for m in cm['nodes'] if m['label'] == name]
            if len(matches) != 1:
                raise ValueError('Missing diagnostic signal ' + name)
            diagnostics.append((matches[0], text))
        exposed.update((i for i, text in diagnostics))
    probe_ack = ports['gpio']['bits'][29]['controllerId'] if probe else None
    if probe_ack is not None:
        exposed.add(probe_ack)
    internal = [m for m in cm['nodes'] if m['controllerId'] not in exposed]
    memory = []
    if service_clock:
        service_ids = {m['controllerId'] for m in internal if m['label'].startswith('Service ')}
        neighbors = {i: set() for i in by_id}
        for i, part in by_id.items():
            for edge in part['controller'].get('controllers') or []:
                neighbors[i].add(edge['id'])
                neighbors[edge['id']].add(i)
        service_ids.update((m['controllerId'] for m in internal if m.get('physicalOnly') and m['label'].startswith('Feedback relay') and neighbors[m['controllerId']] & service_ids))
        memory = [m for m in internal if m['controllerId'] in service_ids]
        internal = [m for m in internal if m['controllerId'] not in service_ids]
    side = 1
    while side ** 3 < len(internal):
        side += 1
    keyboard_width = max((sum((6 if len(key) > 1 else 3 for key in row)) for row in KEY_ROWS)) + 2
    screen_x = (keyboard_width - frame_width) // 2
    cube_x = screen_x + 2
    for i, item in enumerate(internal):
        layer, slot = divmod(i, side * side)
        y, x = divmod(slot, side)
        pos = dict(x=cube_x + x, y=3 + y, z=layer + 1)
        by_id[item['controllerId']]['pos'] = pos
        item.update(position=pos, region='logic_cube')
    memory_side = 1
    while memory_side ** 3 < len(memory):
        memory_side += 1
    memory_x = cube_x + side + 4
    for i, item in enumerate(memory):
        layer, slot = divmod(i, memory_side * memory_side)
        yy, xx = divmod(slot, memory_side)
        pos = dict(x=memory_x + xx, y=3 + yy, z=layer + 1)
        by_id[item['controllerId']]['pos'] = pos
        item.update(position=pos, region='memory_module')
    pixel_map = []
    for i, controller in enumerate(pixels):
        if i in blank:
            continue
        x, y = (i % width, i // width)
        pos = dict(x=screen_x + 2 + x, y=1, z=height + 2 - y)
        by_id[controller].update(pos=pos, xaxis=-1, zaxis=3, color='222222')
        manifest[controller].update(position=pos, region='terminal_display', pixel_x=x, pixel_y=y)
        pixel_map.append(dict(x=x, y=y, controllerId=controller, position=pos))
    next_id = max(by_id) + 1
    signs = []

    def label(text, x, y, z=1, port='keyboard', upright=False):
        nonlocal next_id
        child = make_sign(text, next_id, dict(x=x, y=y, z=z), 'in')
        child['color'] = 'EEEEEE'
        if upright:
            child['zaxis'] = -2
        children.append(child)
        signs.append(sign_record(child, text, port, 'in'))
        next_id += 1
    key_map = []
    for row_index, row in enumerate(KEY_ROWS):
        row_width = sum((6 if len(key) > 1 else 3 for key in row))
        x = (keyboard_width - row_width) // 2
        y = -4 * (row_index + 1)
        for key in row:
            key_width = 6 if len(key) > 1 else 3
            center = x + key_width // 2
            controller = keys[KEYS.index(key)]
            pos = dict(x=center, y=y + 1, z=1)
            color = '44AF55' if key == 'ENTER' else 'D96852' if key == 'BKSP' else 'DFE0E1'
            by_id[controller].update(shapeId=BUTTON_UUID, pos=pos, xaxis=1, zaxis=-2, color=color)
            manifest[controller].update(position=pos, region='keyboard', kind='BUTTON', key=key)
            label(key, center - choose_sign(key).size[0] // 2, y + 1)
            key_map.append(dict(key=key, ascii=KEY_CODES[key], controllerId=controller, position=pos))
            x += key_width
    for name, text, x in (('run', 'RUN', 3), ('reset', 'RESET', 10)):
        controller = clock['names'][name]
        pos = dict(x=x, y=-21, z=1)
        by_id[controller].update(pos=pos, xaxis=1, zaxis=-2)
        manifest[controller].update(position=pos, region='terminal_controls')
        label(text, x - choose_sign(text).size[0] // 2, -21, port='clock')
    for name, text, x in (('busy', 'BUSY', 22), ('full', design_report.get('keyboard_ready_label', 'FULL'), 32)):
        controller = monitors[name]
        pos = dict(x=x, y=-21, z=1)
        by_id[controller].update(pos=pos, xaxis=1, zaxis=-2, color='D96852' if name == 'busy' else '44AF55' if design_report.get('keyboard_ready_label') else 'F5D742')
        manifest[controller].update(position=pos, region='terminal_controls')
        label(text, x - choose_sign(text).size[0] // 2, -21)
    if probe_ack is not None:
        pos = dict(x=38, y=-21, z=1)
        by_id[probe_ack].update(shapeId=BUTTON_UUID, pos=pos, xaxis=1, zaxis=-2, color='4C6FE3')
        manifest[probe_ack].update(position=pos, region='terminal_controls', kind='BUTTON')
        label('ACK', 37, -21)
    for bank, bank_clock in enumerate(clocks):
        for i, item in enumerate(bank_clock['timers']):
            controller = item['controllerId']
            pos = dict(x=cube_x + 3 + i * 6, y=side + 6 + bank * 6, z=1)
            by_id[controller]['pos'] = pos
            manifest[controller].update(position=pos, region='timer_service')
            text = (('CPU ' if bank == 0 else 'SVC ') if service_clock else '') + ('CAP', 'GAP', 'COM', 'GAP')[i]
            label(text, pos['x'] - choose_sign(text).size[0] // 2, pos['y'] + 2, port='clock')
    diagnostic_map = []
    for index, (controller, text) in enumerate(diagnostics):
        row, column = divmod(index, 4)
        x = cube_x + 3 + column * 6
        y = side + 18 + row * 6
        pos = dict(x=x, y=y + 1, z=1)
        by_id[controller].update(pos=pos, xaxis=1, zaxis=-2, color='F5D742')
        manifest[controller].update(position=pos, region='clock_diagnostics')
        label(text, x - choose_sign(text).size[0] // 2, y + 2, port='clock')
        diagnostic_map.append(dict(label=text, controllerId=controller, position=pos))

    def block(x, y, z, w, d, h):
        children.append(dict(shapeId=BASE_UUID, color='111111', pos=dict(x=x, y=y, z=z), bounds=dict(x=w, y=d, z=h), xaxis=1, zaxis=3))
    block(screen_x, 0, 2, frame_width, 1, 1)
    block(screen_x, 0, height + 3, frame_width, 1, 1)
    block(screen_x, 0, 3, 1, 1, height)
    block(screen_x + frame_width - 1, 0, 3, 1, 1, height)
    block(screen_x, 0, 1, 1, 1, 1)
    block(screen_x + frame_width - 1, 0, 1, 1, 1, 1)
    if blank:
        for x in range(3, width, 4):
            block(screen_x + 2 + x, 1, 3, 1, 1, height)
    title = design_report.get('terminal_title', 'TINY BASIC')
    label(title, screen_x + (frame_width - choose_sign(title).size[0]) // 2, 1, height + 4, port='terminal', upright=True)
    if memory:
        label('RAM + IO', memory_x + (memory_side - choose_sign('RAM + IO').size[0]) // 2, memory_side + 5, port='memory')
    bp['bodies'][0]['childs'] = children
    support = sparse_supports(bp)
    packing.update(logic_cube=dict(origin=dict(x=cube_x, y=2, z=1), width=side, depth=side, height=(len(internal) + side * side - 1) // (side * side), blocks=len(internal), spacing=1), support=support)
    if memory:
        packing['memory_module'] = dict(origin=dict(x=memory_x, y=3, z=1), width=memory_side, depth=memory_side, height=(len(memory) + memory_side * memory_side - 1) // (memory_side * memory_side), blocks=len(memory), label='RAM + IO', clock='service')
    packing['clock_panel'] = dict(style='Keyboard controls plus rear timer service bank', label_sign_count=sum((s['port'] == 'clock' for s in signs)), signs=[s for s in signs if s['port'] == 'clock'])
    packing['terminal'] = dict(width=width, height=height, columns=geometry.get('columns', width // 4), rows=geometry.get('rows', height // 6), pixel_color='222222', face_direction='-Y', orientation='upright', keyboard_width=keyboard_width, keys=key_map, pixels=pixel_map, blank_pixels=sorted(blank), spacer_pixels=geometry.get('spacer_pixels', []))
    for key in ('font', 'glyph_width', 'glyph_height', 'cell_width', 'cell_height'):
        if key in geometry:
            packing['terminal'][key] = geometry[key]
    for name, port in ports.items():
        port['hidden'] = True
        for bit in port['bits']:
            bit.update(manifest[bit['controllerId']])
    for bank_clock in clocks:
        for group in ('controls', 'indicators'):
            for item in bank_clock[group].values():
                item['position'] = dict(by_id[item['controllerId']]['pos'])
        for item in bank_clock['timers']:
            item['position'] = dict(by_id[item['controllerId']]['pos'])
    report = design_report
    report.update(packing=packing, clock=clock, label_signs=len(signs))
    if service_clock:
        report['service_clock'] = service_clock
    terminal_map = dict(packing['terminal'], busy=monitors['busy'], full=monitors['full'], probe_ack=probe_ack, native='Pending native load, keyboard and upright-screen check')
    if 'terminal_boot' in report:
        terminal_map['boot_text'] = report['terminal_boot']
    if 'terminal_help' in report:
        terminal_map['help_text'] = report['terminal_help']
    if report.get('tap_keyboard'):
        terminal_map.update(tap_keyboard=report['tap_keyboard'], key_ready=monitors['full'])
    for name, value in (('circuit-map.json', cm), ('port-map.json', ports), ('clock-map.json', clock), ('packing.json', packing), ('hdl-build.json', report), ('sign-map.json', dict(signs=signs)), ('terminal-map.json', terminal_map)):
        (folder / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    if service_clock:
        (folder / 'service-clock-map.json').write_text(json.dumps(service_clock, indent=2), encoding='utf-8')
    if diagnostic_map:
        (folder / 'clock-diagnostic-map.json').write_text(json.dumps(dict(signals=diagnostic_map), indent=2), encoding='utf-8')
    (folder / 'blueprint.json').write_bytes(encode_blueprint(bp))
    return terminal_map
