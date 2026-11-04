"""Build a cartridge file from a manifest: programs plus the directory the firmware reads. docs/cartridge.md has
the format of both.

Usage: python -I cartridge/make_cartridge.py [manifest.json] [-o file] [--blueprint folder]
The manifest (default cartridge/programs.json):

    {"title": "PROGRAMS", "programs": [
      {"name": "TBASIC", "source": "basic.s", "mode": "console", "colour": "35C759"},
      {"name": "SNAKE", "binary": "snake.bin", "symbols": "snake.groups.json", "mode": "board", "colour": "E0559B"}
    ]}

A `source` is RV32E assembly for accelerated/isa.py, at address 0: code must be position independent, which the
assembler's branches, jumps and calls are. These lines name things for the cartridge:

    ; @group key Title of this part       the code from here on is part `key`, with its own gates and sign
    ; @data label 1 2 "TEXT"              bytes in the data port, with `label` their address
    %next                                 read the next character of the line (lbu x4,0(x9); addi x9,x9,1)
    %is 65                                the character read must be 65 (addi x6,x4,-65; bne x6,x0,error)

A `binary` is a flat RV32E image, with `symbols` a JSON list of [word offset, key, title] where its parts start.
With --blueprint the cartridge is also built as a CARTRIDGE blueprint, under that folder.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if sys.platform == 'win32' and sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32:
    sys.path.insert(0, str(ROOT / 'vendor'))
from accelerated import cartridge as cart  # noqa: E402
from accelerated.districts import shade  # noqa: E402
from accelerated.isa import assemble  # noqa: E402

MACROS = {'next': ['lbu x4,0(x9)', 'addi x9,x9,1'], 'is': ['addi x6,x4,-{0}', 'bne x6,x0,error']}


def preprocess(text, data, symbols):
    """Expand macros and take out the directives: (assembly with group labels, {group label: (key, title)})."""
    out, marks = [], {}
    for line in text.splitlines():
        m = re.match(r'\s*;\s*@group\s+(\w+)\s*(.*)$', line)
        if m:
            label = f'group_mark_{len(marks)}'
            marks[label] = (m[1], m[2].strip())
            out.append(f'{label}:')
            continue
        m = re.match(r'\s*;\s*@data\s+(\w+)\s+(.*)$', line)
        if m:
            symbols[m[1]] = cart.DATA_BASE + len(data)
            for token in shlex.split(m[2], posix=False):
                data += token[1:-1].encode('ascii') if token[0] == '"' else bytes([int(token)])
            continue
        m = re.match(r'\s*%(\w+)\s*(.*)$', line)
        if m:
            out += ['    ' + row.format(*m[2].split()) for row in MACROS[m[1]]]
            continue
        out.append(line)
    return '\n'.join(out) + '\n', marks


def tone(colour, n, count):
    """A shade of the program's colour for its part number n."""
    return shade(colour, 0.14 * (n / max(count - 1, 1) - 0.5)) if colour else ''


def build(manifest, folder):
    programs = manifest['programs']
    data = bytearray(bytes(cart.ROW_STRIDE * (len(programs) + 1)))
    code = bytearray()
    entries, relocations, board, groups = [], [], [], []
    for n, program in enumerate(programs, 1):
        first_word = len(code) // 4
        first_data = len(data)
        name = program['name']
        marks = []
        if 'source' in program:
            symbols = {}
            text, found = preprocess((folder / program['source']).read_text(encoding='utf-8'), data, symbols)
            assembled = assemble(text, symbols=symbols)
            words = assembled.image
            marks = sorted((assembled.labels[label] // 4, *mark) for label, mark in found.items())
        else:
            words = (folder / program['binary']).read_bytes()
            words += bytes(-len(words) % 4)
            if 'symbols' in program:
                marks = [tuple(row) for row in json.loads((folder / program['symbols']).read_text(encoding='utf-8'))]
        code += words
        end_word = len(code) // 4
        if not marks or marks[0][0]:
            marks.insert(0, (0, name.lower(), name.upper()))
        mode = cart.MODES[program.get('mode', 'tap')]
        if mode == cart.MODE_BOARD:
            board.append([first_word, end_word])
        entries.append((name, mode, cart.BASE + first_word * 4))
        relocations.append(cart.ROW_STRIDE * n + 13)
        for k, (start, key, title) in enumerate(marks):
            end = marks[k + 1][0] if k + 1 < len(marks) else end_word - first_word
            groups.append(cart.Group(key, title or key.upper(), name, tone(program.get('colour', ''), k, len(marks)), [[first_word + start, first_word + end]], []))
        # the program's data bytes go with its first part
        if len(data) > first_data:
            first = next(g for g in groups if g.program == name)
            first.data.append([first_data, len(data)])
    data[:cart.ROW_STRIDE * (len(programs) + 1)] = cart.directory(manifest.get('title', 'PROGRAMS'), entries)
    groups.insert(0, cart.Group('directory', 'DIRECTORY', 'DIRECTORY', '9FB0C4', [], [[0, cart.ROW_STRIDE * (len(programs) + 1)]]))
    return cart.Image(bytes(data), bytes(code), relocations, board, groups)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('manifest', type=Path, nargs='?', default=ROOT / 'cartridge' / 'programs.json')
    p.add_argument('-o', '--output', type=Path, default=None)
    p.add_argument('--blueprint', type=Path, default=None)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text(encoding='utf-8'))
    image = build(manifest, a.manifest.parent)
    output = a.output or a.manifest.with_suffix('.bin')
    output.write_bytes(cart.write_image(image))
    net = cart.netlist(image)
    print(f"{output}: {net.report['words']} words, {net.report['rows']} rows, {net.report['data_bytes']} data bytes, {net.report['logic_blocks']} logic blocks, {net.report['wires']} wires")
    folded = cart.rows(image)
    for g in image.groups:
        count = sum(cart.in_ranges(g.code, owner) for owner in folded.owner)
        print(f"  {g.program:10s} {g.key:20s} {count:4d} rows {net.report['group_blocks'].get(g.key, 0):5d} logic blocks")
    if a.blueprint:
        from accelerated.cartridge_module import build as build_module
        row = build_module(image, a.blueprint)
        print('EXPORTED', row['blueprint_name'], row['folder'], row['packet_bytes'], 'bytes')


if __name__ == '__main__':
    main()
