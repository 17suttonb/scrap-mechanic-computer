"""Drive an exported joined blueprint in the Logic-Block-Simulator: boot, type S + ENTER, play, print the screen.

Usage (bundled runtime):
  Logic-Block-Simulator\\runtime\\python.exe cartridge\\sim_snake.py <joined-export-folder> [--ticks N] [--keys W@30000,D@60000]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'Logic-Block-Simulator' / 'hdl_workshop'))
from native_simulator import NativeLogicSimulator  # noqa: E402


def screen(sim, terminal):
    """Decode the 72x32 pixel screen into 4 rows of 12 characters."""
    pixels = terminal['pixels']
    bits = sim.read_ids([p['controllerId'] for p in pixels])
    lit = {(p['x'], p['y']) for i, p in enumerate(pixels) if bits >> i & 1}
    glyphs = {tuple(rows): ch for ch, rows in terminal['font'].items()}
    cw, ch, gw, gh = terminal['cell_width'], terminal['cell_height'], terminal['glyph_width'], terminal['glyph_height']
    lines = []
    for r in range(terminal['rows']):
        line = ''
        for c in range(terminal['columns']):
            cell = tuple(''.join('1' if (c * cw + x, r * ch + y) in lit else '0' for x in range(gw)) for y in range(gh))
            line += glyphs.get(cell, '?')
        lines.append(line)
    return lines


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--ticks', type=int, default=60000, help='ticks to simulate after the S command is sent')
    p.add_argument('--start', default='S', help='line typed at the BASIC prompt')
    p.add_argument('--keys', default='', help='extra lines to type, e.g. S@20000,D@40000 (key@ticks after the start command)')
    p.add_argument('--every', type=int, default=5000)
    a = p.parse_args()
    bp = json.loads((a.folder / 'blueprint.json').read_text(encoding='utf-8'))
    clock = json.loads((a.folder / 'clock-map.json').read_text(encoding='utf-8'))['names']
    terminal = json.loads((a.folder / 'terminal-map.json').read_text(encoding='utf-8'))
    keys = {k['key']: k['controllerId'] for k in terminal['keys']}
    sim = NativeLogicSimulator(bp, input_mode='controller')

    def press(key, at, hold=60):
        sim.request_input(keys[key], True, at_tick=at)
        sim.request_input(keys[key], False, at_tick=at + hold)
    sim.request_input(clock['reset'], True, at_tick=1)
    sim.request_input(clock['reset'], False, at_tick=481)
    sim.request_input(clock['run'], True, at_tick=501)
    t0 = 12000
    at = t0
    for ch in a.start:
        press(ch, at)
        at += 400
    press('ENTER', at)
    start = at + 400
    for item in filter(None, a.keys.split(',')):
        line, when = item.split('@')
        at = start + int(when)
        for ch in line:
            press(ch, at)
            at += 400
        press('ENTER', at)
    began = time.perf_counter()
    sim.advance(start)
    last = None
    done = 0
    while done < a.ticks:
        step = min(a.every, a.ticks - done)
        sim.advance(step)
        done += step
        rows = screen(sim, terminal)
        flags = {n: sim.read_ids([terminal[n]]) for n in ('busy',)}
        if rows != last:
            print(f'--- tick {sim.time} (+{done}) busy={flags["busy"]} wall={time.perf_counter() - began:.0f}s')
            print('\n'.join(rows), flush=True)
            last = rows
    print('DONE', sim.time, flush=True)


if __name__ == '__main__':
    main()
