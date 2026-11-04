"""Time a joined CPU export on the Logic-Block-Simulator: ticks/sec for a fixed boot workload.

Usage: Logic-Block-Simulator/runtime/python.exe bench_sim.py <joined-export-folder> [--ticks N]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'Logic-Block-Simulator' / 'hdl_workshop'))
from native_sim_cli import run, resolve

STIMULUS = [{'tick': 0, 'set': {'reset': 1}}, {'tick': 480, 'set': {'reset': 0}}, {'tick': 500, 'set': {'run': 1}}]
WATCHES = ['gpio', 'halted', 'faulted', 'trap_cause', 'busy']

p = argparse.ArgumentParser()
p.add_argument('folder', type=Path)
p.add_argument('--ticks', type=int, default=20000)
p.add_argument('--fast', action='store_true', help='use the Rust simulator in fast_sim/ (it must be built)')
a = p.parse_args()
if a.fast:
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'fast_sim'))
    from fast_simulator import FastLogicSimulator
    sim = FastLogicSimulator(json.loads((a.folder / 'blueprint.json').read_text(encoding='utf-8')))
    for row in STIMULUS:
        for name, value in row['set'].items():
            for i in resolve(a.folder, name):
                sim.request_input(i, bool(value), at_tick=row['tick'] + 1)
    start = time.perf_counter()
    sim.advance(a.ticks)
    wall = time.perf_counter() - start
    print(f'ticks={sim.time} wall={wall:.3f}s ticks_per_sec={sim.time / wall:.1f}')
    print('signals', {name: hex(sim.read_ids(resolve(a.folder, name))) for name in WATCHES})
    sys.exit()
start = time.perf_counter()
sim, result = run(a.folder, ticks=a.ticks, stimuli=STIMULUS, watches=WATCHES)
wall = time.perf_counter() - start
print(f'ticks={result["tick"]} wall={wall:.2f}s ticks_per_sec={result["tick"] / wall:.1f}')
print('signals', result['signals'])
