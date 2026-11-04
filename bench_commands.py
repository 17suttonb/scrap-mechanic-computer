"""Time BASIC commands on any build in the Logic-Block-Simulator, by actual screen updates.

Works on a joined export (with its maps) or on a folder of module blueprints that only has link-map.json,
which are joined here and whose keys, screen and switches are recognised by shape and position.

Usage: Logic-Block-Simulator/runtime/python.exe bench_commands.py <folder> [--script 'H;?2+3;A=32'] [--quiet-after 1500]
Time is reported at the nominal 40 updates per second, from the ENTER press to the last pixel change.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'fast_sim'))
from fast_simulator import make_simulator  # noqa: E402

FONT = {}
exec((ROOT / 'accelerated' / 'font5x7.py').read_text(encoding='utf-8'), FONT)
GLYPHS = {tuple(rows): ch for ch, rows in FONT['FONT'].items()}

BUTTON = '1e8d93a4-506b-470d-9ada-9c0a321e2db5'
TIMER = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'
KEY_ROWS = [list('1234567890+-'), list('QWERTYUIOP'), list('ASDFGHJKL=?'), list('ZXCVBNM') + ['SPACE', 'BKSP', 'ENTER']]
KEY_NAMES = {' ': 'SPACE'}


def parts_of(bp):
    return [p for b in bp['bodies'] for p in b['childs'] if 'controller' in p]


def load_modules(folder):
    """Join a folder of module blueprints through their link maps; return blueprint, run, reset."""
    folders = [f for f in sorted(folder.iterdir()) if (f / 'link-map.json').exists()]
    childs, links = [], {}
    for f in folders:
        childs += json.loads((f / 'blueprint.json').read_text(encoding='utf-8'))['bodies'][0]['childs']
        for l in json.loads((f / 'link-map.json').read_text(encoding='utf-8'))['links']:
            links[l['source'], l['receiver']] = l
    by_id = {p['controller']['id']: p for p in childs if 'controller' in p}
    for source, receiver in links:
        c = by_id[source]['controller']
        c['controllers'] = (c.get('controllers') or []) + [dict(id=receiver)]
    names = {l['name']: l.get('switch', l['source']) for l in links.values()}
    return dict(bodies=[dict(childs=childs)], version=4), names['RUN'], names['RESET']


def keys_by_position(bp):
    """The 43 keys, read row by row from their positions."""
    buttons = [p for p in parts_of(bp) if p['shapeId'] == BUTTON]
    assert len(buttons) == 43, len(buttons)
    rows = {}
    for p in buttons:
        rows.setdefault(p['pos']['y'], []).append(p)
    ordered = [sorted(rows[y], key=lambda p: p['pos']['x']) for y in sorted(rows, reverse=True)]
    assert [len(r) for r in ordered] == [len(r) for r in KEY_ROWS], [len(r) for r in ordered]
    return {name: p['controller']['id'] for names, row in zip(KEY_ROWS, ordered) for name, p in zip(names, row)}


def pixels_by_position(bp):
    """Screen pixel gates, the only gates painted 222222: {(x, y): id} with y growing downward."""
    found = [p for p in parts_of(bp) if p.get('color', '').upper() == '222222']
    left, top = min(p['pos']['x'] for p in found), max(p['pos']['z'] for p in found)
    return {(p['pos']['x'] - left, top - p['pos']['z']): p['controller']['id'] for p in found}


def set_cpu_ticks(bp, values):
    """Patch the four CPU clock timers: those in the same module as the RUN switch's timers, by position order."""
    timers = [p for p in parts_of(bp) if p['shapeId'] == TIMER and p['pos']['y'] > 30]
    timers.sort(key=lambda p: p['pos']['x'])
    assert len(timers) == 4
    for p, v in zip(timers, values):
        p['controller'].update(seconds=v // 40, ticks=v % 40)


class Machine:
    def __init__(self, folder, cpu_ticks=None):
        folder = Path(folder)
        if (folder / 'blueprint.json').exists():
            bp = json.loads((folder / 'blueprint.json').read_text(encoding='utf-8'))
            names = json.loads((folder / 'clock-map.json').read_text(encoding='utf-8'))['names']
            run, reset = names['run'], names['reset']
        else:
            bp, run, reset = load_modules(folder)
        if cpu_ticks:
            set_cpu_ticks(bp, cpu_ticks)
        self.keys = keys_by_position(bp)
        self.pixel_map = pixels_by_position(bp)
        self.pixels = list(self.pixel_map.values())
        self.sim = make_simulator(bp)
        self.sim.request_input(reset, True, at_tick=self.sim.time + 1)
        self.sim.request_input(reset, False, at_tick=self.sim.time + 481)
        self.sim.request_input(run, True, at_tick=self.sim.time + 501)
        self.run_on = self.sim.time + 501

    def screen_state(self):
        return self.sim.read_ids(self.pixels)

    def text(self):
        """Decode the screen through the 5x7 font; '#' marks a cell that is not a known glyph."""
        state = self.screen_state()
        lit = {xy for n, xy in enumerate(self.pixel_map) if state >> n & 1}
        rows = max(y for _, y in self.pixel_map) // 8 + 1
        cols = max(x for x, _ in self.pixel_map) // 6 + 1
        lines = []
        for r in range(rows):
            line = ''
            for c in range(cols):
                cell = tuple(''.join('1' if (c * 6 + x, r * 8 + y) in lit else '0' for x in range(5)) for y in range(7))
                line += GLYPHS.get(cell, '#')
            lines.append(line.rstrip())
        return lines

    def settle(self, quiet, step=25, limit=40000):
        """Advance until the screen has not changed for `quiet` updates; return the last change tick."""
        last_state, last_change = self.screen_state(), self.sim.time
        start = self.sim.time
        while self.sim.time - last_change < quiet and self.sim.time - start < limit:
            self.sim.advance(step)
            state = self.screen_state()
            if state != last_state:
                last_state, last_change = state, self.sim.time
        return last_change

    def tap(self, name, gap, hold=20):
        key = self.keys[KEY_NAMES.get(name, name)]
        self.sim.request_input(key, True, at_tick=self.sim.time + 1)
        self.sim.request_input(key, False, at_tick=self.sim.time + 1 + hold)
        self.sim.advance(gap)

    def command(self, text, quiet, gap=200):
        for ch in text:
            self.tap(ch, gap)
        enter = self.sim.time
        self.tap('ENTER', 20)
        return (self.settle(quiet) - enter) / 40


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--script', default='H;?2+3;A=32;B=8;?A-B;10 ?123;LIST;RUN')
    p.add_argument('--quiet-after', type=int, default=4000)
    p.add_argument('--cpu-ticks', default=None, help='override the four CPU timer settings, e.g. 20,6,12,6')
    a = p.parse_args()
    began = time.perf_counter()
    m = Machine(a.folder, [int(v) for v in a.cpu_ticks.split(',')] if a.cpu_ticks else None)
    m.sim.advance(6000)
    print('boot', m.text(), flush=True)
    for line in a.script.split(';'):
        seconds = m.command(line, a.quiet_after)
        print(f'{line:10s} {seconds:8.2f} s  {m.text()}', flush=True)
    print(f'wall {time.perf_counter() - began:.0f}s')


if __name__ == '__main__':
    main()
