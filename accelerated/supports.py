from collections import deque
from accelerated.circuit import BASE_UUID, GATE_UUID, SWITCH_UUID, BUTTON_UUID
from accelerated.signs import SIGN_BY_UUID
TIMER_UUID = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'

def occupied_cells(child):
    """Cell occupancy for the shapes/orientations emitted by these exporters."""
    p = child['pos']
    x, y, z = (p['x'], p['y'], p['z'])
    shape = child['shapeId']
    axes = (child['xaxis'], child['zaxis'])
    if shape in (GATE_UUID, SWITCH_UUID, BUTTON_UUID):
        if axes == (1, -2):
            yield (x, y - 1, z)
        elif axes == (1, 3):
            yield (x, y, z)
        elif axes == (-1, 3):
            yield (x - 1, y - 1, z)
        else:
            raise ValueError('Unsupported gate orientation for structural packing')
        return
    if shape in SIGN_BY_UUID and axes == (1, -2):
        width, depth, height = SIGN_BY_UUID[shape].size
        for dx in range(width):
            for dy in range(depth):
                for dz in range(height):
                    yield (x + dx, y - 1 - dz, z + dy)
        return
    if axes != (1, 3):
        raise ValueError('Unsupported part orientation for structural packing')
    if shape in SIGN_BY_UUID:
        width, depth, height = SIGN_BY_UUID[shape].size
    elif shape == TIMER_UUID:
        width, depth, height = (1, 2, 1)
    elif shape == BASE_UUID:
        width, depth, height = (child['bounds'][k] for k in 'xyz')
    else:
        raise ValueError('Unknown part in structural packing')
    for dz in range(height):
        for dy in range(depth):
            for dx in range(width):
                yield (x + dx, y + dy, z + dz)

def components(cells):
    remaining = set(cells)
    result = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        part = {start}
        pending = [start]
        while pending:
            x, y, z = pending.pop()
            for other in ((x - 1, y, z), (x + 1, y, z), (x, y - 1, z), (x, y + 1, z), (x, y, z - 1), (x, y, z + 1)):
                if other in remaining:
                    remaining.remove(other)
                    part.add(other)
                    pending.append(other)
        result.append(part)
    return result

def rail_shapes(cells, color='34465E'):
    """Cover support cells with straight runs, each with a 1x1 cross-section."""
    remaining = set(cells)
    result = []
    while remaining:
        candidates = []
        for axis in (0, 1):
            for x, y in sorted(remaining):
                previous = (x - 1, y) if axis == 0 else (x, y - 1)
                if previous in remaining:
                    continue
                length = 1
                while ((x + length, y) if axis == 0 else (x, y + length)) in remaining:
                    length += 1
                candidates.append((length, -axis, -y, -x, x, y, axis))
        length, _, _, _, x, y, axis = max(candidates)
        for offset in range(0, length, 64):
            n = min(64, length - offset)
            result.append(dict(shapeId=BASE_UUID, color=color, pos=dict(x=x + (offset if axis == 0 else 0), y=y + (offset if axis == 1 else 0), z=0), bounds=dict(x=n if axis == 0 else 1, y=n if axis == 1 else 1, z=1), xaxis=1, zaxis=3))
        for offset in range(length):
            remaining.remove((x + offset, y) if axis == 0 else (x, y + offset))
    return result

def rigid(child):
    """Blocks, logic gates and timers join on every face. Switches and buttons join only through
    their back face, and signs are treated the same way, because the game files do not say."""
    return child['shapeId'] in (BASE_UUID, GATE_UUID, TIMER_UUID)

def held(child, solid):
    """Is this non-rigid part mounted on a rigid cell? Upright parts have their back on +y unless
    they are buttons or switches, which point their back down in that orientation."""
    upright_sign = child['shapeId'] in SIGN_BY_UUID and child['zaxis'] == -2
    back = (0, 1, 0) if upright_sign else (0, 0, -1)
    return any((x + back[0], y + back[1], z + back[2]) in solid for x, y, z in occupied_cells(child))

def loose_parts(children):
    """Parts that are not attached to the largest rigid group under the game's attach rules."""
    rigid_cells = {cell for child in children if rigid(child) for cell in occupied_cells(child)}
    groups = components(rigid_cells)
    main = max(groups, key=len)
    return [child for child in children if (not (set(occupied_cells(child)) <= main)) if rigid(child) or not held(child, main)]

def sparse_supports(blueprint, strict=False, plates=()):
    """Replace z=0 platforms with 1x1 runs joining structural sections.

    Native logic blocks, signs, timers and any thin display frame count as
    structural cells. Closest sections are joined by face-connected 1x1 runs
    just below them, so adjacent links coalesce into long blueprint pieces.
    Controller shapes, positions, IDs, saved states and wiring are untouched.

    With strict, a run may only join through faces the game really attaches: every non-rigid part
    is its own section unless it is already mounted on a rigid cell. `plates` are z=0 blocks to keep;
    runs do not repeat their cells.
    """
    if len(blueprint['bodies']) != 1:
        raise ValueError('Sparse packing requires one body')
    original = blueprint['bodies'][0]['childs']
    kept = {id(c) for c in plates}
    removed = [c for c in original if c['shapeId'] == BASE_UUID and c['pos']['z'] == 0 and id(c) not in kept]
    children = [c for c in original if not (c['shapeId'] == BASE_UUID and c['pos']['z'] == 0 and id(c) not in kept)]
    occupied = set()
    for child in children:
        for cell in occupied_cells(child):
            if cell in occupied:
                raise ValueError(f'Overlapping physical parts at {cell}: {[(c["shapeId"][:6], c["pos"], c["bounds"] if "bounds" in c else None) for c in children if cell in occupied_cells(c)]}')
            occupied.add(cell)
    plate_cells = {xy[:2] for c in plates for xy in occupied_cells(c)}
    joins = set()
    if strict:
        parts = components({cell for child in children if rigid(child) for cell in occupied_cells(child)})
        solid = set().union(*parts) if parts else set()
        for child in children:
            if not rigid(child) and not held(child, solid):
                joins.add(len(parts))
                parts.append(set(occupied_cells(child)))
    else:
        parts = components(occupied)
    if not parts:
        raise ValueError('No parts to support')
    ground = [{(x, y) for x, y, z in part if z == 1} for part in parts]
    if any((not cells for cells in ground)):
        raise ValueError('A section does not reach the support plane')
    owners = {xy: index for index, cells in enumerate(ground) for xy in cells}
    root = max(range(len(parts)), key=lambda i: len(parts[i]))
    attached = {root}
    network = set(ground[root])
    added = set()
    min_x = min((x for x, y in owners))
    max_x = max((x for x, y in owners))
    min_y = min((y for x, y in owners))
    max_y = max((y for x, y in owners))
    while len(attached) < len(parts):
        pending = deque(sorted(network))
        previous = {xy: None for xy in network}
        target = None
        while pending and target is None:
            x, y = pending.popleft()
            for xy in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                xx, yy = xy
                if xy in previous or not (min_x <= xx <= max_x and min_y <= yy <= max_y):
                    continue
                previous[xy] = (x, y)
                if xy in owners and owners[xy] not in attached:
                    target = xy
                    break
                pending.append(xy)
        if target is None:
            raise ValueError('Unable to connect structural sections')
        owner = owners[target]
        at = target
        while at is not None:
            added.add(at)
            network.add(at)
            at = previous[at]
        attached.add(owner)
        if owner not in joins:
            network.update(ground[owner])
    rails = rail_shapes(added - plate_cells)
    blueprint['bodies'][0]['childs'] = children + rails
    if strict:
        assert not loose_parts(children + rails), [(c['shapeId'][:8], c['pos']) for c in loose_parts(children + rails)][:8]
    structural = sum((c['bounds']['x'] * c['bounds']['y'] * c['bounds']['z'] for c in children + rails if c['shapeId'] == BASE_UUID))
    return dict(style='one-cell-wide runs', plane_z=0, all_sections_connected=True, structural_blocks=structural, new_rail_blocks=len(added), new_rail_shapes=len(rails), replaced_support_blocks=sum((c['bounds']['x'] * c['bounds']['y'] * c['bounds']['z'] for c in removed)), removed_platform_blocks=sum((c['bounds']['x'] * c['bounds']['y'] * c['bounds']['z'] for c in removed if c['bounds']['x'] > 1 and c['bounds']['y'] > 1)), note='Logic, signs and timers provide structure; no platform under the cube or panels.')
