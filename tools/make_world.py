"""Write a Scrap Mechanic save that holds the whole computer, already welded and wired.

Reads a folder of exported module blueprints, joins them through their link maps, and inserts one
creation into a COPY of a template save. It only reads the exports; welding and wiring the modules by
hand (ASSEMBLY.md) stays the normal, fully supported way to build the computer. Format notes, evidence
and what has not been proven in the game: docs/world-file.md.

Usage: python -I tools/make_world.py <export-folder> --template <save.db> --output out-world/computer.db

Get a template: in the game make a new flat or custom-world Creative game, leave it, close the game, and pass that .db file
from %APPDATA%/Axolot Games/Scrap Mechanic/User/User_<steamid>/Save. It is copied and never opened in place.
"""
import argparse
import base64
import json
import math
import os
import shutil
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.platform == 'win32' and sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32:
    sys.path.insert(0, str(ROOT / 'vendor'))
from accelerated.supports import occupied_cells, components  # noqa: E402

SAVE_VERSION = 29
CREATIVE_FLAGS = {1: 'flat', 4: 'custom world'}  # 7 is a Creative terrain world, whose bodies are in world 1 and whose ground isn't at z = 0
CONFIRMED_FLAGS = {4}

GATE = '9f0f56e8-2c31-4d83-996c-d00a9b296c3f'
TIMER = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'
BUTTON = '1e8d93a4-506b-470d-9ada-9c0a321e2db5'
SWITCH = '7cf717d7-d167-4f2d-a6e7-6b2c70aa3986'
SIGNS = {'4908ecfb-ec05-42c5-a02a-7675ccc3eb19', 'a96232ef-2778-4c23-8a11-7cbc5b1de8b0', '46d85e5b-353c-4f06-8fd8-b9ddd3443245'}
CONTROLLER_KIND = {GATE: 0x14, TIMER: 0x15, BUTTON: 0x0a, SWITCH: 0x0b, **{s: 0x1b for s in SIGNS}}
BLOCK_KIND, PART_KIND = 0x1f, 0x20
SIGN_SCRIPT_UID = bytes.fromhex('5130c47b0a1953fca5a1205c19264574')
TIMER_IDLE_RING = bytes.fromhex('1f000100ff18500000000000')  # LZ4 of the empty 304-byte delay line
CONFIRMED_AXES = {(1, 3), (-1, 3), (1, -2)}  # paired with rotation bytes 0x75, 0x73 and 0x25 in a real save
AXIS_TO_NIBBLE = {1: 5, 2: 6, 3: 7, -1: 3, -2: 2, -3: 1}
NIBBLE_TO_AXIS = {v: k for k, v in AXIS_TO_NIBBLE.items()}
HEADER_MAGIC = b'SQLite format 3\0'
REQUIRED_TABLES = {'Game', 'RigidBody', 'RigidBodyBounds', 'ChildShape', 'Joint', 'Controller', 'ScriptData', 'GenericData'}
PLAYER_UID = bytes.fromhex('67ce7fe2f7564898b8f076080146a358')
TEMPLATE_HELP = ('In the game make a new flat or custom-world Creative game, leave it and close the game, then pass that .db from '
                 '%APPDATA%/Axolot Games/Scrap Mechanic/User/User_<steamid>/Save. It is copied, never opened in place.')


class WorldError(Exception):
    """A problem to report to the user without a traceback."""


# ---------------------------------------------------------------- exports and the join
def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_modules(folder):
    """Read every module folder under `folder`: [(name, parts)] and the links from all their maps."""
    folder = Path(folder)
    if not folder.is_dir():
        raise WorldError(f'export folder not found: {folder}')
    modules, links = [], {}
    for sub in sorted(p for p in folder.iterdir() if (p / 'link-map.json').exists()):
        missing = [n for n in ('blueprint.json', 'description.json') if not (sub / n).exists()]
        if missing:
            raise WorldError(f'{sub.name}: missing {", ".join(missing)}')
        bp = read_json(sub / 'blueprint.json')
        if bp.get('version') != 4 or len(bp.get('bodies', [])) != 1:
            raise WorldError(f'{sub.name}: expected a version 4 blueprint with one body')
        modules.append((read_json(sub / 'description.json').get('name', sub.name), bp['bodies'][0]['childs']))
        for link in read_json(sub / 'link-map.json')['links']:
            links[link['source'], link['receiver']] = link
    if len(modules) < 2:
        raise WorldError(f'{folder}: found {len(modules)} module folder(s) with link-map.json, need at least 2')
    return modules, links


def check_join(modules, links):
    """Check the seam-pair and overlap invariants; return controller id -> (module name, part)."""
    by_id = {}
    for name, parts in modules:
        for part in parts:
            c = part.get('controller')
            if c:
                if c['id'] in by_id:
                    raise WorldError(f'controller id {c["id"]} is in both {by_id[c["id"]][0]} and {name}: these exports are not from one build')
                by_id[c['id']] = (name, part)
    for name, part in by_id.values():
        for edge in part['controller'].get('controllers') or []:
            if by_id.get(edge['id'], (None,))[0] != name:
                raise WorldError(f'{name}: controller {part["controller"]["id"]} wires to {edge["id"]} outside its own module')
    for (source, receiver), link in links.items():
        label = link.get('name', f'{source}->{receiver}')
        for end, wanted in ((source, 'source'), (receiver, 'receiver')):
            if end not in by_id:
                raise WorldError(f'seam link {label}: {wanted} {end} is in no module; a module folder is missing or from another build')
        if by_id[source][0] == by_id[receiver][0]:
            raise WorldError(f'seam link {label}: both ends are in {by_id[source][0]}')
        a, b = by_id[source][1]['pos'], by_id[receiver][1]['pos']
        for end, pos in (('source_position', a), ('receiver_position', b)):
            if end in link and link[end] != pos:
                raise WorldError(f'seam link {label}: {end} {link[end]} but the part is at {pos}')
        if abs(a['x'] - b['x']) + abs(a['y'] - b['y']) + abs(a['z'] - b['z']) != 1:
            raise WorldError(f'seam link {label}: {a} and {b} do not touch')
        if any(e['id'] == receiver for e in by_id[source][1]['controller'].get('controllers') or []):
            raise WorldError(f'seam link {label}: already wired inside the module')
    cells = Counter()
    for name, parts in modules:
        for part in parts:
            try:
                cells.update(occupied_cells(part))
            except ValueError as e:
                raise WorldError(f'{name}: {e}: {part["shapeId"]} xaxis {part["xaxis"]} zaxis {part["zaxis"]}')
    overlaps = [c for c, n in cells.items() if n > 1]
    if overlaps:
        raise WorldError(f'modules overlap in {len(overlaps)} cells, for example {overlaps[:5]}')
    pieces = components(cells)
    if len(pieces) != 1:
        raise WorldError(f'the joined modules form {len(pieces)} separate pieces instead of one')
    return by_id


# ---------------------------------------------------------------- binary rows (big-endian, savegame version 29)
def uuid_bytes(text):
    return bytes.fromhex(text.replace('-', ''))[::-1]


def uuid_text(raw):
    h = raw[::-1].hex()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}'


def rotation_byte(xaxis, zaxis):
    return AXIS_TO_NIBBLE[zaxis] << 4 | AXIS_TO_NIBBLE[xaxis]


def rotation_axes(byte):
    return NIBBLE_TO_AXIS[byte & 15], NIBBLE_TO_AXIS[byte >> 4]


def color_bytes(text):
    return b'\xff' + bytes.fromhex(text)[::-1]


def encode_shape(part, shape_id, body_id, offset=(0, 0, 0)):
    """A ChildShape row's data."""
    pos = tuple(part['pos'][k] + d for k, d in zip('xyz', offset))
    if not all(-32768 <= v < 32768 for v in pos):
        raise WorldError(f'part position {pos} does not fit in 16 bits')
    color = part.get('color')
    if not color or len(color) != 6:
        raise WorldError(f'part at {pos} has no 6-digit colour')
    bounds = part.get('bounds')
    kind = BLOCK_KIND if bounds else PART_KIND
    head = struct.pack('>BBBII', 1, kind, 1, shape_id, body_id) + uuid_bytes(part['shapeId']) + struct.pack('>I3h', body_id, *pos) + color_bytes(color)
    if bounds:
        if (part['xaxis'], part['zaxis']) != (1, 3):
            raise WorldError(f'block at {pos}: rotated blocks are not supported')
        return head + struct.pack('>3HB', bounds['x'], bounds['y'], bounds['z'], 0)
    return head + bytes([rotation_byte(part['xaxis'], part['zaxis']), 0])


def encode_controller(part, controller_id, host_id, wires):
    """A Controller row's data; wires are the receivers' Controller ids."""
    shape, c = part['shapeId'], part['controller']
    kind = CONTROLLER_KIND.get(shape)
    if kind is None:
        raise WorldError(f'unsupported interactive part {shape}: no sample of its save encoding')
    if c.get('joints'):
        raise WorldError(f'controller {c["id"]} has joints, which this tool does not write')
    if len(wires) > 255:
        raise WorldError(f'controller {c["id"]} has {len(wires)} outputs, more than the one-byte count holds')
    active = 0x80 if c.get('active') else 0
    data = struct.pack('>BBBIIBIB', 3, kind, 1, controller_id, host_id, 1, host_id, 1)
    if shape == TIMER:
        seconds, ticks = c.get('seconds', 0), c.get('ticks', 0)
        if seconds:
            raise WorldError(f'timer {c["id"]} has {seconds} seconds: only sub-second timers have a confirmed encoding')
        if not 0 <= ticks <= 40:
            raise WorldError(f'timer {c["id"]} has {ticks} ticks')
        data += struct.pack('>H', len(TIMER_IDLE_RING)) + TIMER_IDLE_RING
    data += bytes([len(wires)]) + b''.join(struct.pack('>I', w) for w in wires)
    if shape == GATE:
        if c.get('mode') not in range(6):
            raise WorldError(f'gate {c["id"]} has mode {c.get("mode")}')
        return data + bytes([0, active | c['mode']])
    if shape == TIMER:
        return data + bytes([0, 0, active | 0x40 | ticks])
    if shape in (BUTTON, SWITCH):
        return data + bytes([0, active])
    return data + b'\x01' + bytes(8)


def encode_body(body_id, world_id, position, bounds):
    """A RigidBody row's data: static, identity rotation, as a creation anchored to the ground saves."""
    (min_x, max_x), (min_y, max_y) = bounds
    return (struct.pack('>BBBIH', 0, 1, 1, body_id, world_id) + struct.pack('>4f', max_y, min_y, max_x, min_x)
            + struct.pack('>H', world_id) + struct.pack('>4f', 1, 0, 0, 0) + struct.pack('>3f', *position) + b'\0\xff\xff\xff\xff')


def sign_blob(part):
    text = part['controller'].get('data')
    if not text:
        raise WorldError(f'sign controller {part["controller"]["id"]} has no data')
    return base64.b64decode(text + '=' * (-len(text) % 4))


def encode_sign_row(controller_id, world_id, blob, flags=2):
    """A ScriptData row for a sign: the blueprint's LZ4 text blob behind a header that repeats the columns."""
    key = struct.pack('<I', controller_id)
    return (SIGN_SCRIPT_UID, key, world_id, flags, SIGN_SCRIPT_UID + struct.pack('>H', len(key)) + key + struct.pack('>HBI', world_id, flags, len(blob)) + blob)


def decode_shape(data):
    kind = data[1]
    if data[0] != 1 or data[2] != 1 or kind not in (BLOCK_KIND, PART_KIND):
        raise WorldError(f'unknown ChildShape layout {data[:3].hex()}')
    out = dict(id=struct.unpack_from('>I', data, 3)[0], body=struct.unpack_from('>I', data, 7)[0], shapeId=uuid_text(data[11:27]),
               pos=struct.unpack_from('>3h', data, 31), color=data[38:41][::-1].hex().upper())
    if struct.unpack_from('>I', data, 27)[0] != out['body'] or data[37] != 0xff or data[-1] != 0:
        raise WorldError(f'unexpected ChildShape bytes {data.hex()}')
    if kind == BLOCK_KIND:
        out.update(bounds=struct.unpack_from('>3H', data, 41), axes=(1, 3))
    else:
        out.update(axes=rotation_axes(data[41]))
    return out


def decode_controller(data):
    kind = data[1]
    cid, host, one, host2, two = struct.unpack_from('>IIBIB', data, 3)
    if data[0] != 3 or data[2] != 1 or one != 1 or two != 1 or host != host2:
        raise WorldError(f'unknown Controller layout {data[:17].hex()}')
    out = dict(id=cid, host=host, kind=kind)
    o = 17
    if kind == 0x15:
        n = struct.unpack_from('>H', data, o)[0]
        out['ring'] = data[o + 2:o + 2 + n]
        o += 2 + n
    count = data[o]
    out['wires'] = list(struct.unpack_from(f'>{count}I', data, o + 1))
    tail = data[o + 1 + 4 * count:]
    out['tail'] = tail
    if kind == 0x14:
        out.update(active=bool(tail[1] & 0x80), mode=tail[1] & 0x7f)
    elif kind == 0x15:
        out.update(active=bool(tail[2] & 0x80), seconds=tail[1], ticks=tail[2] & 0x3f)
    elif kind in (0x0a, 0x0b):
        out.update(active=bool(tail[1] & 0x80))
    return out


# ---------------------------------------------------------------- the save copy
def world_rows(modules, links, body_id, first_shape, first_controller, world_id):
    """Build shape, controller and script rows; ids in the blueprints map onto fresh ids."""
    new_controller, next_controller = {}, first_controller
    for name, parts in modules:
        for part in parts:
            if part.get('controller'):
                new_controller[part['controller']['id']] = next_controller
                next_controller += 1
    extra = {}
    for source, receiver in links:
        extra.setdefault(source, []).append(receiver)
    shapes, controllers, scripts, next_shape, warned = [], [], [], first_shape, Counter()
    for name, parts in modules:
        for part in parts:
            shape_id = next_shape
            next_shape += 1
            if not part.get('bounds') and (part['xaxis'], part['zaxis']) not in CONFIRMED_AXES:
                warned[part['xaxis'], part['zaxis']] += 1
            shapes.append((shape_id, body_id, encode_shape(part, shape_id, body_id)))
            c = part.get('controller')
            if not c:
                if not part.get('bounds'):
                    raise WorldError(f'{name}: part {part["shapeId"]} has neither bounds nor a controller')
                continue
            wires = [e['id'] for e in c.get('controllers') or []] + extra.get(c['id'], [])
            controller_id = new_controller[c['id']]
            controllers.append((controller_id, shape_id, 1, encode_controller(part, controller_id, shape_id, [new_controller[w] for w in wires])))
            if part['shapeId'] in SIGNS:
                scripts.append(encode_sign_row(controller_id, world_id, sign_blob(part)))
    for (x, z), n in warned.items():
        print(f'WARNING: {n} parts use rotation xaxis {x} zaxis {z}, computed by the formula in docs/world-file.md but not confirmed by a sample.', flush=True)
    return shapes, controllers, scripts, new_controller


def bounds_of(modules, position):
    """World XY bounds as saves store them: cell edges in metres, 0.004 inside; also the local cell span."""
    cells = [c for _, parts in modules for p in parts for c in occupied_cells(p)]
    span = [(min(c[i] for c in cells), max(c[i] for c in cells) + 1) for i in range(3)]
    xy = [(position[i] + lo / 4 + 0.004, position[i] + hi / 4 - 0.004) for i, (lo, hi) in enumerate(span[:2])]
    return xy, span


def unique_ids(raw):
    if len(raw) % 4 or len(raw) < 24:
        raise WorldError('template: Game.uniqueIds has an unknown layout')
    values = list(struct.unpack(f'>{len(raw) // 4}I', raw))
    if values[0] != len(values) - 1:
        raise WorldError('template: Game.uniqueIds has an unknown layout')
    return values


def player_positions(db):
    """Saved player (x, y, z) in metres, read from the LZ4 player rows; empty if they can't be read."""
    try:
        import lz4.block
    except ImportError:
        return []
    found = []
    for (data,) in db.execute('select data from GenericData where uid=?', (PLAYER_UID,)):
        data = bytes(data)
        try:
            n = struct.unpack_from('>I', data, 25)[0]
            raw = lz4.block.decompress(data[29:29 + n], uncompressed_size=4096)
            z, y, x = struct.unpack_from('>3f', raw, 2)
        except (struct.error, lz4.block.LZ4BlockError):
            continue
        if all(math.isfinite(v) and abs(v) < 1e6 for v in (x, y, z)):
            found.append((x, y, z))
    return found


def check_template(db):
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    missing = REQUIRED_TABLES - tables
    if missing:
        raise WorldError(f'template: not a Scrap Mechanic save, missing tables {sorted(missing)}')
    version, flags = db.execute('select savegameversion, flags from Game').fetchone()
    if version != SAVE_VERSION:
        raise WorldError(f'template: savegame version {version}, this tool only knows version {SAVE_VERSION}. {TEMPLATE_HELP}')
    if flags not in CREATIVE_FLAGS:
        raise WorldError(f'template: game mode flags {flags} are not a flat or custom Creative world. {TEMPLATE_HELP}')
    if flags not in CONFIRMED_FLAGS:
        print(f'WARNING: template is a {CREATIVE_FLAGS[flags]} Creative world; samples of version {SAVE_VERSION} are all custom worlds (flags 4).', flush=True)
    if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
        raise WorldError('template: SQLite integrity check failed')
    worlds = {r[0] for r in db.execute('select distinct worldId from RigidBody')}
    if worlds - {0}:
        raise WorldError(f'template: bodies in worlds {sorted(worlds)}; only world 0 is expected in a Creative save')


def forbidden_output(path):
    """Inside the game's user data or install folder?"""
    parts = [p.lower() for p in path.resolve().parts]
    if 'axolot games' in parts:
        return True
    return any(a == 'common' and b == 'scrap mechanic' for a, b in zip(parts, parts[1:]))


def choose_position(db, modules):
    """Stand the computer on z = 0, clear of saved players; a player would otherwise load inside it."""
    players = player_positions(db)
    _, span = bounds_of(modules, (0, 0, 0))
    x = round(sum(p[0] for p in players) / len(players)) if players else 0
    y = math.ceil(max((p[1] for p in players), default=0) - span[1][0] / 4) + 4
    return float(x), float(y), 0.0


def read_back(db, body_id):
    """Decode one body from a save: shapes by id, controllers by host, signs by controller id."""
    shapes = {}
    for sid, bid, data in db.execute('select id, bodyId, data from ChildShape where bodyId=?', (body_id,)):
        shape = decode_shape(bytes(data))
        if shape['id'] != sid or shape['body'] != bid:
            raise WorldError(f'ChildShape {sid}: columns and data disagree')
        shapes[sid] = shape
    controllers = {}
    for cid, host, data in db.execute('select c.id, c.hostId, c.data from Controller c join ChildShape s on c.hostId = s.id where s.bodyId=?', (body_id,)):
        ctrl = decode_controller(bytes(data))
        if ctrl['id'] != cid or ctrl['host'] != host:
            raise WorldError(f'Controller {cid}: columns and data disagree')
        controllers[host] = ctrl
    signs = {struct.unpack('<I', bytes(k))[0]: bytes(d)[29:] for k, d in db.execute('select key, data from ScriptData where uid=?', (SIGN_SCRIPT_UID,))}
    return shapes, controllers, signs


def verify_written(db, body_id, modules, links, by_id, new_controller):
    """Every part exactly once with the right fields; the wires are the module wires plus the seam links."""
    shapes, controllers, signs = read_back(db, body_id)
    wanted = Counter()
    for _, parts in modules:
        for p in parts:
            c = p.get('controller') or {}
            wanted[p['shapeId'], (p['pos']['x'], p['pos']['y'], p['pos']['z']), p['color'].upper(), (p['xaxis'], p['zaxis']),
                   tuple(p['bounds'][k] for k in 'xyz') if p.get('bounds') else None, new_controller.get(c.get('id'))] += 1
    got = Counter()
    for sid, s in shapes.items():
        got[s['shapeId'], s['pos'], s['color'], s['axes'], s.get('bounds'), controllers[sid]['id'] if sid in controllers else None] += 1
    if got != wanted:
        raise WorldError(f'read-back: {sum((got - wanted).values())} shapes differ from the blueprints')
    by_new = {new_controller[i]: part for i, (_, part) in by_id.items()}
    edges = {(new_controller[i], new_controller[e['id']]) for i, (_, part) in by_id.items() for e in part['controller'].get('controllers') or []}
    edges |= {(new_controller[s], new_controller[r]) for s, r in links}
    found = set()
    for ctrl in controllers.values():
        part = by_new[ctrl['id']]['controller']
        for field in ('mode', 'seconds', 'ticks'):
            if field in ctrl and part.get(field, 0) != ctrl[field]:
                raise WorldError(f'read-back: controller {ctrl["id"]} {field} is {ctrl[field]}, expected {part.get(field, 0)}')
        found |= {(ctrl['id'], w) for w in ctrl['wires']}
        if ctrl['kind'] == 0x1b and signs.get(ctrl['id']) != sign_blob(by_new[ctrl['id']]):
            raise WorldError(f'read-back: sign {ctrl["id"]} text differs')
    if found != edges:
        raise WorldError(f'read-back: {len(found ^ edges)} wires differ from the join')
    return len(shapes), len(controllers), len(edges)


def fill_copy(db, modules, links, by_id, position):
    check_template(db)
    world_id = 0
    if position is None:
        position = choose_position(db, modules)
    (raw_ids,) = db.execute('select uniqueIds from Game').fetchone()
    ids = unique_ids(bytes(raw_ids))

    def high(table):
        return db.execute(f'select coalesce(max(id), 0) from {table}').fetchone()[0] + 1
    body_id, first_shape, first_controller = max(ids[1], high('RigidBody')), max(ids[3], high('ChildShape')), max(ids[4], high('Controller'))
    shapes, controllers, scripts, new_controller = world_rows(modules, links, body_id, first_shape, first_controller, world_id)
    (min_x, max_x), (min_y, max_y) = xy = bounds_of(modules, position)[0]
    clash = db.execute('select id from RigidBodyBounds where maxX >= ? and minX <= ? and maxY >= ? and minY <= ?', (min_x, max_x, min_y, max_y)).fetchall()
    if clash:
        print(f'WARNING: the computer overlaps {len(clash)} existing creation(s) at this position; use a template without them or --position.', flush=True)
    db.execute('insert into RigidBody(id, worldId, data) values (?, ?, ?)', (body_id, world_id, encode_body(body_id, world_id, position, xy)))
    db.execute('insert into RigidBodyBounds(id, minX, maxX, minY, maxY) values (?, ?, ?, ?, ?)', (body_id, min_x, max_x, min_y, max_y))
    db.executemany('insert into ChildShape(id, bodyId, data) values (?, ?, ?)', shapes)
    db.executemany('insert into Controller(id, hostId, hostType, data) values (?, ?, ?, ?)', controllers)
    db.executemany('insert into ScriptData(uid, key, worldId, flags, data) values (?, ?, ?, ?, ?)', scripts)
    ids[1], ids[3], ids[4] = body_id + 1, shapes[-1][0] + 1, controllers[-1][0] + 1
    db.execute('update Game set uniqueIds=?', (struct.pack(f'>{len(ids)}I', *ids),))
    n_shapes, n_controllers, n_wires = verify_written(db, body_id, modules, links, by_id, new_controller)
    return dict(body=body_id, shapes=n_shapes, controllers=n_controllers, wires=n_wires, signs=len(scripts), position=position, bounds=xy, new_controller=new_controller)


def make_world(export, template, output, position=None, force=False):
    """Returns a summary dict. The template file is only ever read through a byte copy."""
    export, template, output = Path(export), Path(template), Path(output)
    if forbidden_output(output):
        raise WorldError(f'refusing to write inside the game folders: {output}. Write elsewhere and copy it in yourself (docs/world-file.md).')
    if not template.is_file():
        raise WorldError(f'template not found: {template}. {TEMPLATE_HELP}')
    if output.resolve() == template.resolve():
        raise WorldError('output and template are the same file')
    for side in ('-journal', '-wal', '-shm'):
        if Path(str(template) + side).exists():
            raise WorldError(f'{template.name}{side} exists: the save is in use. Close the game first.')
    if output.exists() and not force:
        raise WorldError(f'{output} exists; pass --force to replace it')
    with template.open('rb') as f:
        if f.read(16) != HEADER_MAGIC:
            raise WorldError(f'template: {template.name} is not an SQLite file')
    modules, links = load_modules(export)
    by_id = check_join(modules, links)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + f'.tmp{os.getpid()}')
    shutil.copyfile(template, temp)
    try:
        db = sqlite3.connect(temp)
        try:
            summary = fill_copy(db, modules, links, by_id, position)
            db.commit()
            for row in db.execute('pragma foreign_key_check'):
                raise WorldError(f'foreign key check failed: {row}')
            if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
                raise WorldError('integrity check failed on the output')
        finally:
            db.close()
        os.replace(temp, output)
    finally:
        if temp.exists():
            temp.unlink()
    summary.update(output=str(output.resolve()), modules=[name for name, _ in modules], links=len(links))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('export', type=Path, help='folder holding the CORE, CONSOLE and optional CARTRIDGE module folders')
    p.add_argument('--template', type=Path, required=True, help='a Creative save to copy. ' + TEMPLATE_HELP)
    p.add_argument('--output', type=Path, required=True, help='where to write the new .db; refused inside the game folders')
    p.add_argument('--position', type=float, nargs=3, metavar=('X', 'Y', 'Z'), help='world position of blueprint cell 0,0,0 in metres; default z 0, clear of saved players')
    p.add_argument('--force', action='store_true', help='replace an existing output file')
    a = p.parse_args()
    try:
        r = make_world(a.export, a.template, a.output, tuple(a.position) if a.position else None, a.force)
    except WorldError as e:
        sys.exit(f'make_world: {e}')
    print(f'Joined {len(r["modules"])} modules: {", ".join(r["modules"])}')
    print(f'One body, id {r["body"]}: {r["shapes"]} shapes, {r["controllers"]} controllers, {r["wires"]} wires ({r["links"]} seam links), {r["signs"]} signs.')
    print(f'Position {r["position"]}, x {r["bounds"][0][0]:.2f}..{r["bounds"][0][1]:.2f}, y {r["bounds"][1][0]:.2f}..{r["bounds"][1][1]:.2f} metres.')
    print(f'Read back and checked. Written: {r["output"]}')
    print('NOT YET PROVEN IN THE GAME: try it on a throwaway world first (docs/world-file.md).')
    print('To install: close the game, then copy that file yourself to')
    print(r'  %APPDATA%\Axolot Games\Scrap Mechanic\User\User_<steamid>\Save\<new name>.db')
    print('using a name no existing save has, and load it from the game menu. This tool never writes there.')


if __name__ == '__main__':
    main()
