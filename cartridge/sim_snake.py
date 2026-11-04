"""Play Snake on an exported joined blueprint, or a folder of module blueprints, in the Logic-Block-Simulator and time it.

Usage (bundled runtime):
  Logic-Block-Simulator\\runtime\\python.exe cartridge\\sim_snake.py <joined-export-folder>
        [--cpu-ticks 20,6,12,6] [--moves 12] [--keys 3:S,6:D] [--program 2]

--keys taps a key once the given number of moves have been drawn. Times are at the nominal 40 updates
per second. Load time runs from the menu key to the first complete board.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench_commands import Machine  # noqa: E402

BOARD_ROWS = 7


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--cpu-ticks', default=None)
    p.add_argument('--moves', type=int, default=12)
    p.add_argument('--keys', default='')
    p.add_argument('--limit', type=int, default=60000, help='give up after this many updates from the menu key')
    p.add_argument('--step', type=int, default=4, help='updates between screen samples')
    p.add_argument('--program', default='2', help='the menu key that starts Snake')
    a = p.parse_args()
    keys = {int(k.split(':')[0]): k.split(':')[1] for k in filter(None, a.keys.split(','))}
    m = Machine(a.folder, [int(v) for v in a.cpu_ticks.split(',')] if a.cpu_ticks else None)
    assert m.menu_ready(a.program), m.text()
    enter = m.start_program(a.program)
    last_state, boards, times, previous = None, 0, [], None
    while m.sim.time - enter < a.limit and boards < a.moves:
        m.sim.advance(a.step)
        state = m.screen_state()
        if state == last_state:
            continue
        last_state = state
        rows = m.text()
        cells = ''.join(rows)
        complete = cells.count('+') == 1 and cells.count('Q') == 1 and all(len(r) == 12 for r in rows[:BOARD_ROWS]) and '#' not in cells
        if not complete or rows == previous:
            continue
        previous = rows
        boards += 1
        times.append(m.sim.time)
        print(f'+{(m.sim.time - enter) / 40:7.2f}s board {boards}: ' + ' | '.join(rows), flush=True)
        if boards in keys:
            m.tap(keys[boards], 4)
    if not times:
        print('no board appeared; screen:', m.text())
        return 1
    print(f'load time from the menu key: {(times[0] - enter) / 40:.2f} s')
    if len(times) > 2:
        print(f'screen changes: {len(times)}, last at +{(times[-1] - enter) / 40:.2f} s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
