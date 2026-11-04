"""Shared compact packing for every blueprint exporter; wiring is preserved."""
from circuit_core import BASE_UUID
from sign_labels import make_sign, plan_signs, sign_record
from blueprint_supports import sparse_supports


def plate(x, y, width, depth, color='222222'):
    return [dict(bounds=dict(x=min(64, width-dx), y=min(64, depth-dy), z=1),
                 color=color, pos=dict(x=x+dx, y=y+dy, z=0),
                 shapeId=BASE_UUID, xaxis=1, zaxis=3)
            for dy in range(0, depth, 64) for dx in range(0, width, 64)]


def pack_cube(blueprint, manifest, ordered, ports):
    """Unit-spaced logic cube with every exposed port on a front apron.

    Gate axes 1,-2 occupy the cell (pos.x, pos.y-1, pos.z). The apron and cube
    are connected by one-cell-wide structural runs. Dense logic gates touch
    face-to-face; no platform beneath the cube or panels is needed.
    """
    children = [n for b in blueprint['bodies'] for n in b['childs'] if 'controller' in n]
    by_editor = {n['id']: (child, item) for n, child, item in zip(ordered, children, manifest)}
    all_port_ids = {i for port in ports.values() for i in port['node_ids']}
    io_ids = {i for port in ports.values() if not port.get('hidden') for i in port['node_ids']}
    if not all_port_ids <= by_editor.keys():
        raise ValueError('A port references a missing circuit node.')
    internal = [n for n in ordered if n['id'] not in io_ids]
    side = 1
    while side ** 3 < len(internal):
        side += 1
    layers = max(1, (len(internal) + side*side-1) // (side*side))
    for index, node in enumerate(internal):
        layer, slot = divmod(index, side*side)
        y, x = divmod(slot, side)
        position = dict(x=x, y=y+1, z=layer+1)
        child, item = by_editor[node['id']]
        child['pos'] = position
        item.update(position=position, region='logic_cube')

    # One labeled group per bus slice. Shelf packing keeps scalar controls
    # together and reserves two cells of depth for all three native sign sizes.
    entries, groups = [], []
    for name, port in sorted(ports.items(), key=lambda item: item[1]['mode'] == 'out'):
        if port.get('hidden'):continue
        count = len(port['node_ids'])
        if not count:
            continue
        group = dict(name=name, mode=port['mode'], width=count, segments=[])
        groups.append(group)
        bits = list(zip(port['indices'], port['node_ids']))
        for start in range(0,count,16):
            chunk = bits[start:start+16]
            title = port.get('display_name', name)
            text = ('IN ' if port['mode'] == 'in' else 'OUT ') + title
            if count > 1:
                text += f' {chunk[0][0]}-{chunk[-1][0]}'
            text = ' '.join(text.split())
            pieces = plan_signs(text)
            entries.append(dict(group=group, mode=port['mode'], bits=chunk, text=text,
                                pieces=pieces, width=max(sum(p.spec.size[0] for p in pieces),2*len(chunk)-1)))
    target_width = max(side, 67, max((e['width']+2 for e in entries),default=0))
    row, x, last_mode, used_width = 0, 1, None, 0
    for entry in entries:
        if last_mode is not None and last_mode != entry['mode']:
            row += 2 if x > 1 else 1
            x = 1
        last_mode = entry['mode']
        if x+entry['width'] > target_width-1:
            row += 1
            x = 1
        entry.update(row=row,x=x)
        used_width = max(used_width,x+entry['width']+1)
        x += entry['width']+2
    used_rows = row+1 if entries else 0
    panel_width = max(side,used_width) if entries else 0
    panel_depth = used_rows*4+1 if entries else 0
    placements, signs, sign_map = {}, [], []
    next_id = max((n['controller']['id'] for n in children),default=0)+1
    for entry in entries:
        group, width = entry['group'], entry['width']
        y = -panel_depth+1+entry['row']*4
        sx = entry['x']+(width-sum(p.spec.size[0] for p in entry['pieces']))//2
        ids = []
        for piece in entry['pieces']:
            position = dict(x=sx,y=y,z=1)
            child=make_sign(piece.text,next_id,position,entry['mode'],piece.spec.name)
            signs.append(child)
            sign_map.append(sign_record(child,piece.text,group['name'],entry['mode']))
            sx+=piece.spec.size[0]
            ids.append(next_id)
            next_id += 1
        segment = dict(row=entry['row'],text=entry['text'],bit_indices=[bit for bit,node in entry['bits']],sign_controller_ids=ids)
        group['segments'].append(segment)
        px = entry['x']+(width-(2*len(entry['bits'])-1))//2
        for column,(bit,node_id) in enumerate(entry['bits']):
            if node_id in placements:
                raise ValueError('An I/O node is shared by multiple exported port bits.')
            placements[node_id] = True
            position = dict(x=px+column*2,y=y+3,z=1)
            child,item = by_editor[node_id]
            child['pos'] = position
            item.update(position=position,region='io_panel',panel_row=entry['row'],panel_column=column,
                        label_text=entry['text'],label_sign_ids=ids)
    for group in groups:
        group.update(start_row=group['segments'][0]['row'],end_row=group['segments'][-1]['row'])
    blueprint['bodies'][0]['childs'] = children + signs
    support=sparse_supports(blueprint)
    return dict(
        logic_cube=dict(origin=dict(x=0,y=0,z=1), width=side, depth=side,
                        height=layers, blocks=len(internal), spacing=1),
        io_panel=dict(origin=dict(x=0,y=-panel_depth,z=0), width=panel_width,
                      depth=panel_depth, blocks=len(placements), pin_spacing=2,
                      rows=used_rows, groups=groups,label_sign_count=len(signs),signs=sign_map),
        support=support,
        note='Unit-spaced logic; exposed I/O connected by 1x1 structural runs. No full backing platforms. Controller IDs and wiring preserved.')
