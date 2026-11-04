"""Run the Pi program on an exported joined blueprint, or a folder of module blueprints, in the Logic-Block-Simulator.
Every proven digit it shows is checked against the true digits, and the time to each digit count is reported.

Usage: Logic-Block-Simulator\\runtime\\python.exe cartridge\\sim_pi.py <folder> [--hours 8] [--cpu-ticks 16,2,8,2] [--program 3]
Times are at the nominal 40 updates per second, from the menu key.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench_commands import Machine  # noqa: E402
sys.path.insert(0, str(ROOT / 'cartridge'))
from rvtools import true_pi  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--hours', type=float, default=8)
    p.add_argument('--cpu-ticks', default=None)
    p.add_argument('--program', default='3')
    p.add_argument('--step', type=int, default=200, help='updates between screen samples')
    a = p.parse_args()
    m = Machine(a.folder, [int(v) for v in a.cpu_ticks.split(',')] if a.cpu_ticks else None)
    assert m.menu_ready(a.program), m.text()
    start = m.start_program(a.program)
    digits = true_pi(60)
    last, last_rows, reported = '', None, set()
    ok = True
    while (m.sim.time - start) / 40 / 3600 < a.hours:
        m.sim.advance(a.step)
        rows = m.text()
        if rows == last_rows:
            continue
        last_rows = rows
        if not rows[0].startswith('PI'):
            continue
        minutes = (m.sim.time - start) / 40 / 60
        value = ''.join(rows[1:5]).replace(' ', '')
        if value != last:
            if not value.startswith(last) or (len(value) > 2 and not (value[:2] == '3.' and value[2:] == digits[:len(value) - 2])):
                print(f'{minutes:8.1f} min  WRONG: {value}, was {last}')
                ok = False
            last = value
            count = max(len(value) - 2, 0)
            if count not in reported:
                reported.add(count)
                print(f'{minutes:8.1f} min  {count:3d} digits  {value}', flush=True)
        if rows[5:] != getattr(m, 'seen', None):
            m.seen = rows[5:]
            print(f'{minutes:8.1f} min  {rows[5]:12s} {rows[6]:12s} {rows[7]}', flush=True)
        if 'DONE' in rows[7]:
            print(f'finished at {minutes / 60:.2f} hours: {rows}')
            break
    print('PASS' if ok and last else 'FAIL')
    return 0 if ok and last else 1


if __name__ == '__main__':
    sys.exit(main())
