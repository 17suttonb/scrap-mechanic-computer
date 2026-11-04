"""Round-trip test for make_world.py; no framework, no game, synthetic fixtures unless told otherwise.

python -I tools/test_make_world.py
    Golden rows (single rows copied from a real savegame version 29 file), synthetic 2 and 3 module
    exports written into a synthetic template, read back and compared, and every refusal.
python -I tools/test_make_world.py --export <folder> [--sample <copy of a save holding that build>] [--sim <folder>]
    Also round-trips a real export. --sample compares every encoded row with a save where that build was
    welded and wired by hand. --sim writes the output's connections as a folder for bench_commands.py.
"""
import argparse
import base64
import hashlib
import json
import shutil
import sqlite3
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_world as mw  # noqa: E402

SCHEMA = """
CREATE TABLE Game(savegameversion INTEGER, flags INTEGER, seed INTEGER, gametick INTEGER, mods BLOB, uniqueIds BLOB);
CREATE TABLE RigidBody(id INTEGER PRIMARY KEY, worldId INTEGER DEFAULT 0, data BLOB);
CREATE VIRTUAL TABLE RigidBodyBounds USING rtree(id, minX, maxX, minY, maxY);
CREATE TABLE ChildShape(id INTEGER PRIMARY KEY, bodyId INTEGER, data BLOB, FOREIGN KEY(bodyId) REFERENCES RigidBody(id));
CREATE TABLE Joint(id INTEGER PRIMARY KEY, childShapeIdA INTEGER, childShapeIdB INTEGER, data BLOB, FOREIGN KEY(childShapeIdA) REFERENCES ChildShape(id), FOREIGN KEY(childShapeIdB) REFERENCES ChildShape(id));
CREATE TABLE Controller(id INTEGER PRIMARY KEY, hostId INTEGER, hostType INTEGER, data BLOB);
CREATE TABLE Container(id INTEGER PRIMARY KEY, data BLOB);
CREATE TABLE Tool(id INTEGER PRIMARY KEY, data BLOB);
CREATE TABLE ScriptData(uid BLOB, key BLOB, worldId INTEGER, flags INTEGER DEFAULT 7, data BLOB, PRIMARY KEY (uid, key));
CREATE TABLE GenericData(uid BLOB, key BLOB, worldId INTEGER, flags INTEGER DEFAULT 7, data BLOB, PRIMARY KEY (uid, key));
"""
VALID_ROTATIONS = {0x12, 0x13, 0x15, 0x16, 0x21, 0x23, 0x25, 0x27, 0x31, 0x32, 0x36, 0x37, 0x51, 0x52, 0x56, 0x57, 0x61, 0x63, 0x65, 0x67, 0x72, 0x73, 0x75, 0x76}  # every byte seen in real saves
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        sys.exit(f'FAILED: {message}')


def expect_error(words, call):
    global checks
    checks += 1
    try:
        call()
    except mw.WorldError as e:
        if words not in str(e):
            sys.exit(f'FAILED: expected an error about {words!r}, got {e}')
        return
    sys.exit(f'FAILED: expected an error about {words!r}, got none')


def golden_rows():
    """Each row below came from a real save, ids and all, and must encode to the same bytes."""
    p = lambda shape, x, y, z, color, axes, **kw: dict(shapeId=shape, pos=dict(x=x, y=y, z=z), color=color, xaxis=axes[0], zaxis=axes[1], **kw)  # noqa: E731
    sign = '4908ecfb-ec05-42c5-a02a-7675ccc3eb19'
    shapes = [
        (p(mw.GATE, 6, 8, 3, 'FF00D4', (-1, 3)), 543922, '01200100084cb200000b9c3f6c299b0ad06c99834d312ce8560f9f00000b9c000600080003ffd400ff7300'),
        (p('4ad97d49-c8a5-47f3-ace3-d56ba3affe50', 69, 7, 2, '34465E', (1, 3), bounds=dict(x=1, y=33, z=1)), 568405, '011f010008ac5500000b9c50feafa36bd5e3acf347a5c8497dd94a00000b9c004500070002ff5e463400010021000100'),
        (p(sign, 58, -7, 3, 'EEEEEE', (1, 3)), 568383, '0120010008ac3f00000b9c19ebc3cc75762aa0c54205ecfbec084900000b9c003afff90003ffeeeeee7500'),
        (p(mw.BUTTON, 23, -40, 3, 'DFE0E1', (1, -2)), 568431, '0120010008ac6f00000b9cb52d1e320a9cda9a0d476b50a4938d1e00000b9c0017ffd80003ffe1e0df2500'),
        (p(mw.SWITCH, 5, -3, 3, '4C6FE3', (1, -2)), 543916, '01200100084cac00000b9c8639aa702c6be7a62d4f67d1d717f77c00000b9c0005fffd0003ffe36f4c2500'),
        (p(mw.TIMER, 8, -1, 3, 'DF7F01', (1, 3)), 543918, '01200100084cae00000b9c0fb37b43e27c14a444496ec4e7d07f8f00000b9c0008ffff0003ff017fdf7500'),
    ]
    for part, sid, want in shapes:
        check(mw.encode_shape(part, sid, 2972).hex() == want, f'shape {sid}: {mw.encode_shape(part, sid, 2972).hex()}')
        back = mw.decode_shape(bytes.fromhex(want))
        check((back['id'], back['shapeId'], back['pos'], back['color'], back['axes']) == (sid, part['shapeId'], tuple(part['pos'][k] for k in 'xyz'), part['color'], (part['xaxis'], part['zaxis'])), f'decode shape {sid}')
    ctrl = lambda shape, **c: dict(shapeId=shape, controller=dict(id=1, active=False, **c))  # noqa: E731
    controllers = [
        (ctrl(mw.GATE, mode=1), 541687, 558912, [], '031401000843f700088740010008874001000001'),
        (ctrl(mw.SWITCH), 526691, 543916, [0x83941, 0x8671b], '030b010008096300084cac0100084cac0102000839410008671b0000'),
        (ctrl(mw.BUTTON), 551176, 568431, [0x88bb1, 0x88bbf, 0x88bc1, 0x88bc3, 0x88bc5, 0x88bc7, 0x88bc9], '030a01000869080008ac6f010008ac6f010700088bb100088bbf00088bc100088bc300088bc500088bc700088bc90000'),
        (ctrl(mw.TIMER, seconds=0, ticks=0), 551223, 568478, [0x86938, 0x8b2bc], '031501000869370008ac9e010008ac9e01000c1f000100ff1850000000000002000869380008b2bc000040'),
        (ctrl(sign, data='x'), 551158, 568383, [], '031b01000868f60008ac3f010008ac3f0100010000000000000000'),
    ]
    for part, cid, host, wires, want in controllers:
        check(mw.encode_controller(part, cid, host, wires).hex() == want, f'controller {cid}: {mw.encode_controller(part, cid, host, wires).hex()}')
    check(mw.decode_controller(bytes.fromhex('0315010008693a0008aca1010008aca101000e3fff0f000100ff16500000000000020008b2be0008b95e0000cc'))['ticks'] == 12, 'timer ticks')
    body = bytes.fromhex('00010100000b9c0000c2400418c29593f8c2e6820cc358fefa00003f800000000000000000000000000000c32c0000c27000000000000000ffffffff')
    max_y, min_y, max_x, min_x = struct.unpack_from('>4f', body, 9)
    check(mw.encode_body(2972, 0, (-172, -60, 0), ((min_x, max_x), (min_y, max_y))) == body, 'rigid body')
    blob = base64.b64decode('8BxMVUEAAAABBQAAAAICAAAAAgB0ZXh0BAAAAAExBAAAAAhzZWxlY3RlZAgB')
    row = mw.encode_sign_row(0x8bad9, 0, blob)
    check(row[:4] == (mw.SIGN_SCRIPT_UID, bytes.fromhex('d9ba0800'), 0, 2), 'sign row columns')
    check(row[4][:29].hex() == '5130c47b0a1953fca5a1205c192645740004d9ba08000000020000002d', 'sign row header')
    check({mw.rotation_byte(x, z) for x in mw.AXIS_TO_NIBBLE for z in mw.AXIS_TO_NIBBLE if abs(x) != abs(z)} == VALID_ROTATIONS, 'rotation formula gives the 24 bytes seen in saves')
    check(all(mw.rotation_axes(mw.rotation_byte(x, z)) == (x, z) for x in mw.AXIS_TO_NIBBLE for z in mw.AXIS_TO_NIBBLE if abs(x) != abs(z)), 'rotation round trip')
    print('golden rows match byte for byte')


def write_template(path, version=29, flags=4, player=None):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    ids = [17, 40, 1, 700, 900, 7, 1, 1, 45, 4, 1, 1, 1, 1, 1, 25, 1, 0x40000000]
    db.execute('insert into Game values (?, ?, 5, 100, ?, ?)', (version, flags, bytes.fromhex('00000000'), struct.pack('>18I', *ids)))
    gate = dict(shapeId=mw.GATE, pos=dict(x=0, y=0, z=0), color='222222', xaxis=-1, zaxis=3, controller=dict(id=1, mode=2, active=False))
    db.execute('insert into RigidBody values (39, 0, ?)', (mw.encode_body(39, 0, (500.0, 500.0, 0.0), ((500.0, 501.0), (500.0, 501.0))),))
    db.execute('insert into RigidBodyBounds values (39, 500, 501, 500, 501)')
    db.execute('insert into ChildShape values (699, 39, ?)', (mw.encode_shape(gate, 699, 39),))
    db.execute('insert into Controller values (899, 699, 1, ?)', (mw.encode_controller(gate, 899, 699, []),))
    db.execute('insert into Container values (1, ?)', (b'chest',))
    db.execute('insert into Tool values (1, ?)', (b'tool',))
    db.execute('insert into ScriptData values (?, ?, 65534, 3, ?)', (b'u' * 16, b'k', b'world data'))
    if player:
        import lz4.block
        raw = b'\0\0' + struct.pack('>3f', player[2], player[1], player[0]) + bytes(50)
        blob = lz4.block.compress(raw, store_size=False)
        db.execute('insert into GenericData values (?, ?, 65534, 3, ?)', (mw.PLAYER_UID, b'\x01\0\0\0', mw.PLAYER_UID + b'\0\x04\x01\0\0\0\xff\xfe\x03' + struct.pack('>I', len(blob)) + blob))
    db.commit()
    db.close()


def write_exports(folder, cartridge=True):
    """A small machine: a slab per module, every part kind, seam gates touching across each module edge."""
    def part(shape, x, y, z, color, axes=(1, 3), **kw):
        return dict(shapeId=shape, pos=dict(x=x, y=y, z=z), color=color, xaxis=axes[0], zaxis=axes[1], **kw)

    def gate(cid, x, y, mode=0, wires=(), color='FF00D4'):
        return part(mw.GATE, x, y, 1, color, (-1, 3), controller=dict(id=cid, mode=mode, active=False, joints=None, controllers=[dict(id=w) for w in wires] or None))
    base = '4ad97d49-c8a5-47f3-ace3-d56ba3affe50'
    sign = base64.b64encode(bytes.fromhex('f01c4c554100000001050000000202000000020074657874040000000131040000000873656c65637465640801')).decode().rstrip('=')
    core = [part(base, -6, 0, 0, '34465E', bounds=dict(x=12, y=4, z=1)),
            gate(1, 2, 2, 0, [2, 3]), gate(2, 3, 2, 1, [3]), gate(3, 4, 2, 2), gate(4, 5, 2, 3, [1]), gate(5, -2, 1, 4), gate(6, -1, 1, 5),
            part(mw.TIMER, -5, 2, 1, 'DF7F01', controller=dict(id=7, active=False, joints=None, seconds=0, ticks=16, controllers=[dict(id=1)])),
            part('a96232ef-2778-4c23-8a11-7cbc5b1de8b0', 0, 2, 1, 'EEEEEE', controller=dict(id=8, active=False, joints=None, controllers=None, data=sign))]
    console = [part(base, -6, -4, 0, '34465E', bounds=dict(x=12, y=4, z=1)),
               part(mw.SWITCH, -6, 0, 1, '4C6FE3', (1, -2), controller=dict(id=10, active=False, joints=None, controllers=[dict(id=11)])),
               part(mw.BUTTON, -5, -1, 1, 'DFE0E1', (1, -2), controller=dict(id=12, active=False, joints=None, controllers=[dict(id=11)])),
               gate(11, -1, -2, 1), gate(13, -2, 0), gate(14, -1, 0),
               part('4908ecfb-ec05-42c5-a02a-7675ccc3eb19', 1, -3, 1, 'EEEEEE', controller=dict(id=15, active=False, joints=None, controllers=None, data=sign))]
    links = [dict(name='in(0)', source=5, receiver=13, source_position=dict(x=-2, y=1, z=1), receiver_position=dict(x=-2, y=0, z=1)),
             dict(name='out(0)', source=14, receiver=6, source_position=dict(x=-1, y=0, z=1), receiver_position=dict(x=-1, y=1, z=1))]
    modules = [('core', 'CORE 1', core), ('console', 'CONSOLE 2', console)]
    if cartridge:
        cart = [part(base, -6, 4, 0, '34465E', bounds=dict(x=12, y=2, z=1)), gate(21, 0, 5), gate(22, 1, 5, 1)]
        core.append(gate(20, 0, 4, 1))
        links.append(dict(name='cart(0)', source=20, receiver=21))
        modules.append(('cartridge', 'CARTRIDGE 3', cart))
    for sub, name, parts in modules:
        d = folder / sub
        d.mkdir(parents=True)
        (d / 'blueprint.json').write_text(json.dumps(dict(bodies=[dict(childs=parts)], version=4)), encoding='utf-8')
        (d / 'description.json').write_text(json.dumps(dict(name=name)), encoding='utf-8')
        (d / 'link-map.json').write_text(json.dumps(dict(links=links, module=sub)), encoding='utf-8')
    return folder


def table_rows(path, skip=('Game',)):
    db = sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)
    names = [r[0] for r in db.execute("select name from sqlite_master where type='table'") if r[0] not in skip and not r[0].startswith('RigidBodyBounds')]
    rows = {n: set(db.execute(f'select rowid, * from "{n}"').fetchall()) for n in names}
    rows['Bounds'] = set(db.execute('select * from RigidBodyBounds').fetchall())
    game = db.execute('select * from Game').fetchone()
    db.close()
    return rows, game


def round_trip(export, template, output, label):
    """Write, then independently decode and compare with the exports' own data."""
    before_hash = hashlib.sha256(template.read_bytes()).hexdigest()
    before, game_before = table_rows(template)
    r = mw.make_world(export, template, output, force=True)
    check(hashlib.sha256(template.read_bytes()).hexdigest() == before_hash, 'template file unchanged')
    check(not list(output.parent.glob(output.name + '.tmp*')), 'no temp file left')
    after, game_after = table_rows(output)
    for name in set(before) - {'RigidBody', 'ChildShape', 'Controller', 'ScriptData', 'Bounds'}:
        check(before[name] == after[name], f'{name} unchanged')
    for name in ('RigidBody', 'ChildShape', 'Controller', 'ScriptData', 'Bounds'):
        check(before[name] <= after[name], f'existing {name} rows unchanged')
    check(game_before[:5] == game_after[:5], 'Game columns other than uniqueIds unchanged')
    modules, links = mw.load_modules(export)
    parts = [p for _, ps in modules for p in ps]
    db = sqlite3.connect(f'file:{Path(output).as_posix()}?mode=ro', uri=True)
    body = r['body']
    (world, blob) = db.execute('select worldId, data from RigidBody where id=?', (body,)).fetchone()
    check(world == 0 and bytes(blob)[:3] == b'\0\1\1' and len(blob) == 60, 'static body row')
    check(db.execute('select count(*) from RigidBody').fetchone()[0] == len(before['RigidBody']) + 1, 'exactly one new body')
    check(db.execute('select count(*) from RigidBodyBounds where id=?', (body,)).fetchone()[0] == 1, 'bounds row')
    ids = struct.unpack(f'>{len(game_after[5]) // 4}I', game_after[5])
    for table, slot in (('RigidBody', 1), ('ChildShape', 3), ('Controller', 4)):
        check(ids[slot] > db.execute(f'select max(id) from {table}').fetchone()[0], f'next id above every {table} id')
    check(ids[0] == 17 and ids[2] == struct.unpack(f'>{len(game_before[5]) // 4}I', game_before[5])[2] and ids[5:] == struct.unpack(f'>{len(game_before[5]) // 4}I', game_before[5])[5:], 'other counters unchanged')
    shapes, controllers, signs = mw.read_back(db, body)
    check(len(shapes) == len(parts), f'{len(shapes)} shapes for {len(parts)} parts')
    key = lambda s: (s['shapeId'], s['pos'], s['color'], s['axes'], s.get('bounds'))  # noqa: E731
    want = Counter((p['shapeId'], (p['pos']['x'], p['pos']['y'], p['pos']['z']), p['color'].upper(), (p['xaxis'], p['zaxis']), tuple(p['bounds'][k] for k in 'xyz') if p.get('bounds') else None) for p in parts)
    check(Counter(key(s) for s in shapes.values()) == want, 'every part once with type, position, rotation, colour')
    by_pos = {key(s): s['id'] for s in shapes.values()}
    new = {}
    wired = []
    for p in parts:
        c = p.get('controller')
        if not c:
            continue
        sid = by_pos[(p['shapeId'], (p['pos']['x'], p['pos']['y'], p['pos']['z']), p['color'].upper(), (p['xaxis'], p['zaxis']), None)]
        got = controllers[sid]
        new[c['id']] = got['id']
        wired.append((c, got))
        check(got['kind'] == mw.CONTROLLER_KIND[p['shapeId']], 'controller kind')
        check(got.get('active', False) == (bool(c.get('active')) and p['shapeId'] not in mw.SIGNS), f'saved state of controller {c["id"]}')
        if p['shapeId'] == mw.GATE:
            check(got['mode'] == c['mode'], 'gate mode')
        if p['shapeId'] == mw.TIMER:
            check((got['seconds'], got['ticks'], got['ring']) == (c['seconds'], c['ticks'], mw.TIMER_IDLE_RING), 'timer settings')
        if p['shapeId'] in mw.SIGNS:
            check(signs[got['id']] == mw.sign_blob(p), 'sign text')
    check(len(controllers) == len(new), 'one controller per interactive part')
    edges = {(c['id'], e['id']) for c, _ in wired for e in c.get('controllers') or []} | set(links)
    got_edges = {(c['id'], w) for c, g in wired for w in g['wires']}
    back = {v: k for k, v in new.items()}
    check(sum(len(g['wires']) for _, g in wired) == len(edges), 'no duplicate wires')
    check({(a, back[w]) for a, w in got_edges} == edges, 'wire graph is the module wires plus the seam links')
    db.close()
    print(f'{label}: {len(parts)} parts, {len(new)} controllers, {len(edges)} wires ({len(links)} seam) read back equal; template and other rows unchanged')
    return r


def refusals(root, export, template):
    out = root / 'refuse' / 'out.db'
    good = mw.make_world(export, template, out)
    expect_error('--force', lambda: mw.make_world(export, template, out))
    check(good['shapes'] > 0, 'made')
    expect_error('same file', lambda: mw.make_world(export, template, template, force=True))
    expect_error('game folders', lambda: mw.make_world(export, template, root / 'Axolot Games' / 'x.db'))
    expect_error('game folders', lambda: mw.make_world(export, template, root / 'Steam' / 'steamapps' / 'common' / 'Scrap Mechanic' / 'x.db'))
    check(not (root / 'Axolot Games').exists(), 'nothing made in a refused folder')
    expect_error('template not found', lambda: mw.make_world(export, root / 'none.db', root / 'o.db'))

    side = root / 'side.db'
    shutil.copyfile(template, side)
    Path(str(side) + '-journal').write_bytes(b'x')
    expect_error('in use', lambda: mw.make_world(export, side, root / 'o.db'))
    plain = root / 'plain.db'
    plain.write_bytes(b'not a database at all')
    expect_error('not an SQLite', lambda: mw.make_world(export, plain, root / 'o.db'))
    for name, version, flags, words in (('old.db', 28, 4, 'savegame version'), ('terrain.db', 29, 7, 'not a flat or custom')):
        t = root / name
        write_template(t, version=version, flags=flags)
        expect_error(words, lambda: mw.make_world(export, t, root / 'o.db'))
        check(not (root / 'o.db').exists() and not list(root.glob('o.db.tmp*')), 'failed write leaves nothing')

    def mutated(name, change, drop=None):
        d = root / name
        shutil.copytree(export, d)
        if drop:
            shutil.rmtree(d / drop)
        for sub in d.iterdir():
            bp, links = json.loads((sub / 'blueprint.json').read_text()), json.loads((sub / 'link-map.json').read_text())
            change(sub.name, bp['bodies'][0]['childs'], links['links'])
            (sub / 'blueprint.json').write_text(json.dumps(bp))
            (sub / 'link-map.json').write_text(json.dumps(links))
        return d

    def overlap(name, parts, links):
        if name == 'console':
            parts[0]['pos']['y'] += 1

    def far(name, parts, links):
        links[0]['receiver_position'] = dict(x=9, y=9, z=9)

    def apart(name, parts, links):
        for link in links:
            link.pop('source_position', None)
            link.pop('receiver_position', None)
        if name == 'core':
            for p in parts:
                p['pos']['z'] += 9

    def same_id(name, parts, links):
        if name == 'console':
            for p in parts:
                if p.get('controller', {}).get('id') == 11:
                    p['controller']['id'] = 2

    def slow_timer(name, parts, links):
        for p in parts:
            if p['shapeId'] == mw.TIMER:
                p['controller']['seconds'] = 1

    def unknown(name, parts, links):
        if name == 'console':
            for p in parts:
                if p['shapeId'] == mw.SWITCH:
                    p['shapeId'] = '00000000-0000-0000-0000-000000000000'
    o = root / 'o.db'
    expect_error('overlap', lambda: mw.make_world(mutated('overlap', overlap), template, o))
    expect_error('receiver_position', lambda: mw.make_world(mutated('far', far), template, o))
    expect_error('do not touch', lambda: mw.make_world(mutated('apart', apart), template, o))
    expect_error('is in both', lambda: mw.make_world(mutated('dup', same_id), template, o))
    expect_error('seconds', lambda: mw.make_world(mutated('slow', slow_timer), template, o))
    expect_error('Unsupported', lambda: mw.make_world(mutated('unknown', unknown), template, o))
    expect_error('in no module', lambda: mw.make_world(mutated('nocart', lambda *a: None, drop='cartridge'), template, o))
    expect_error('at least 2', lambda: mw.make_world(mutated('one', lambda *a: None, drop='console') / 'core', template, o))
    print('refusals: overwrite, game folders, templates in use / wrong version / wrong world type, overlap, broken seams, duplicate ids, unknown parts, missing modules')


def compare_sample(export, sample):
    """Compare every row we would write with a save where this build was assembled by hand."""
    temp = Path(tempfile.mkdtemp(prefix='make_world_sample'))
    try:
        copy = temp / 'sample.db'
        shutil.copyfile(sample, copy)
        db = sqlite3.connect(copy)
        (body, _), = db.execute('select bodyId, count(*) n from ChildShape group by bodyId order by n desc limit 1').fetchall()
        shapes, controllers, signs = mw.read_back(db, body)
        raw_shape = {i: bytes(d) for i, d in db.execute('select id, data from ChildShape where bodyId=?', (body,))}
        raw_ctrl = {h: bytes(d) for h, d in db.execute('select c.hostId, c.data from Controller c join ChildShape s on c.hostId=s.id where s.bodyId=?', (body,))}
        modules, links = mw.load_modules(export)
        parts = [p for _, ps in modules for p in ps]
        gates_bp = [p['pos'] for p in parts if p['shapeId'] == mw.GATE]
        gates_sv = [s['pos'] for s in shapes.values() if s['shapeId'] == mw.GATE]
        offset = tuple(min(g[i] for g in gates_sv) - min(g[k] for g in gates_bp) for i, k in enumerate('xyz'))
        print(f'sample body {body}: {len(shapes)} shapes; export {len(parts)} parts; blueprint + {offset} = local save position')
        by_cell = {(s['shapeId'], s['pos']): s for s in shapes.values() if 'bounds' not in s}
        ctrl_new, identical, wrong = {}, 0, []
        for p in parts:
            if p.get('bounds'):
                continue
            pos = tuple(p['pos'][k] + d for k, d in zip('xyz', offset))
            s = by_cell.get((p['shapeId'], pos))
            if s is None or mw.encode_shape(p, s['id'], body, offset) != raw_shape[s['id']]:
                wrong.append((p['shapeId'], pos))
            else:
                identical += 1
                ctrl_new[p['controller']['id']] = (controllers[s['id']], s['id'])
        print(f'parts: {identical} ChildShape rows byte-identical, {len(wrong)} wrong {wrong[:3]}')
        check(not wrong, 'all non-block ChildShape rows byte-identical to the hand-built save')
        same, runtime, bad, bad_wires = 0, Counter(), [], 0
        extra = {}
        for s, r in links:
            extra.setdefault(s, []).append(r)
        for p in parts:
            c = p.get('controller')
            if not c:
                continue
            got, host = ctrl_new[c['id']]
            wires = {ctrl_new[e['id']][0]['id'] for e in c.get('controllers') or []} | {ctrl_new[r][0]['id'] for r in extra.get(c['id'], [])}
            if wires != set(got['wires']):
                bad_wires += 1
            ours = mw.encode_controller(p, got['id'], host, got['wires'])
            if ours == raw_ctrl[host]:
                same += 1
                continue
            mine = mw.decode_controller(ours)
            a, b = dict(mine), dict(got)
            for d in (a, b):
                d.pop('active', None)
                d.pop('ring', None)
                d['tail'] = bytes(x & 0x7f for x in d['tail'])
            if a == b:
                runtime[got['kind']] += 1
            else:
                bad.append((a, b))
        print(f'controllers: {same} byte-identical, {sum(runtime.values())} differ only in saved run state (output on / timer contents) {dict(runtime)}, {len(bad)} wrong {bad[:2]}')
        print(f'wires: {sum(len(c["wires"]) for c in controllers.values())} in the save, {bad_wires} controllers with a different set of receivers')
        check(not bad and not bad_wires, 'controllers and wires match the hand-built save')
        by_ctrl = {c['id']: c for c in controllers.values()}
        sign_parts = [p for p in parts if p['shapeId'] in mw.SIGNS]
        check(all(signs.get(ctrl_new[p['controller']['id']][0]['id']) == mw.sign_blob(p) for p in sign_parts), 'sign blobs')
        full = {bytes(k): bytes(d) for k, d in db.execute('select key, data from ScriptData where uid=?', (mw.SIGN_SCRIPT_UID,))}
        rows = sum(1 for p in sign_parts if mw.encode_sign_row(ctrl_new[p['controller']['id']][0]['id'], 0, mw.sign_blob(p))[4] == full[struct.pack('<I', ctrl_new[p['controller']['id']][0]['id'])])
        print(f'signs: {rows} of {len(sign_parts)} ScriptData rows byte-identical')
        check(rows == len(sign_parts), 'sign rows')
        check(len(by_ctrl) == len(controllers), 'unique controllers')
        cells = set()
        for s in shapes.values():
            if 'bounds' in s:
                bx, by, bz = s['bounds']
                cells |= {(s['pos'][0] + x, s['pos'][1] + y, s['pos'][2] + z) for x in range(bx) for y in range(by) for z in range(bz)}
        want = {(p['pos']['x'] + offset[0] + x, p['pos']['y'] + offset[1] + y, p['pos']['z'] + offset[2] + z) for p in parts if p.get('bounds') for x in range(p['bounds']['x']) for y in range(p['bounds']['y']) for z in range(p['bounds']['z'])}
        n_blocks = sum(1 for p in parts if p.get('bounds'))
        n_saved = sum(1 for s in shapes.values() if 'bounds' in s)
        print(f'blocks: export has {n_blocks} boxes, save has {n_saved} after the game merged them; {len(cells & want)} of {len(want)} cells covered, {len(cells - want)} extra cells in the save {sorted(cells - want)[:8]}')
        check(want <= cells, 'every structural cell is in the hand-built save')
        (raw,) = db.execute('select data from RigidBody where id=?', (body,)).fetchone()
        raw = bytes(raw)
        max_y, min_y, max_x, min_x = struct.unpack_from('>4f', raw, 9)
        pos = struct.unpack_from('>3f', raw, 43)
        check(mw.encode_body(body, 0, pos, ((min_x, max_x), (min_y, max_y))) == raw, 'body row layout')
        ours, _ = mw.bounds_of(modules, tuple(pos[i] + offset[i] / 4 for i in range(3)))
        print(f'body: row layout byte-identical; bounds ours {ours[0][0]:.3f}..{ours[0][1]:.3f} x {ours[1][0]:.3f}..{ours[1][1]:.3f}, save {min_x:.3f}..{max_x:.3f} x {min_y:.3f}..{max_y:.3f}')
        db.close()
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def write_sim_folder(output, summary, export, folder):
    """Rebuild a joined blueprint from the output's rows alone, for bench_commands.py."""
    db = sqlite3.connect(f'file:{Path(output).as_posix()}?mode=ro', uri=True)
    shapes, controllers, _ = mw.read_back(db, summary['body'])
    db.close()
    childs = []
    for sid, s in shapes.items():
        p = dict(shapeId=s['shapeId'], pos=dict(x=s['pos'][0], y=s['pos'][1], z=s['pos'][2]), color=s['color'], xaxis=s['axes'][0], zaxis=s['axes'][1])
        if 'bounds' in s:
            p['bounds'] = dict(zip('xyz', s['bounds']))
        c = controllers.get(sid)
        if c:
            p['controller'] = dict(id=c['id'], active=False, joints=None, controllers=[dict(id=w) for w in c['wires']] or None)
            for field in ('mode', 'seconds', 'ticks'):
                if field in c:
                    p['controller'][field] = c[field]
        childs.append(p)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    _, links = mw.load_modules(export)
    names = {link['name']: link.get('switch', link['source']) for link in links.values()}
    new = summary['new_controller']
    (folder / 'blueprint.json').write_text(json.dumps(dict(bodies=[dict(childs=childs)], version=4)), encoding='utf-8')
    (folder / 'clock-map.json').write_text(json.dumps(dict(names=dict(run=new[names['RUN']], reset=new[names['RESET']]))), encoding='utf-8')
    print(f'simulator folder (connections from the output only): {folder}')


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--export', type=Path)
    a.add_argument('--sample', type=Path, help='a COPY of a save holding that build welded by hand')
    a.add_argument('--sim', type=Path)
    args = a.parse_args()
    golden_rows()
    root = Path(tempfile.mkdtemp(prefix='make_world_test'))
    try:
        template = root / 'template.db'
        write_template(template)
        three = write_exports(root / 'three')
        round_trip(three, template, root / 'three.db', 'synthetic 3 modules')
        two = write_exports(root / 'two', cartridge=False)
        round_trip(two, template, root / 'two.db', 'synthetic 2 modules')
        refusals(root, three, template)
        try:
            import lz4.block  # noqa: F401
            standing = root / 'standing.db'
            write_template(standing, player=(10.0, 20.0, 0.72))
            r = mw.make_world(three, standing, root / 'clear.db')
            check(r['position'][2] == 0 and r['position'][0] == 10 and r['bounds'][1][0] >= 20, f'default position clear of the player: {r["position"]}')
            print('default position stands clear of the saved player')
        except ImportError:
            print('no LZ4 here: skipped the saved player check')
        if args.export:
            big = root / 'real.db'
            r = round_trip(args.export, template, big, 'real export')
            if args.sim:
                write_sim_folder(big, r, args.export, args.sim)
            if args.sample:
                compare_sample(args.export, args.sample)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f'ALL PASSED: {checks} checks')


if __name__ == '__main__':
    main()
