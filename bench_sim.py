"""Time a joined CPU export on the Logic-Block-Simulator: ticks/sec for a fixed boot workload.

Usage: Logic-Block-Simulator/runtime/python.exe bench_sim.py <joined-export-folder> [--ticks N]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'Logic-Block-Simulator' / 'hdl_workshop'))
from native_sim_cli import run

STIMULUS = [{'tick': 0, 'set': {'reset': 1}}, {'tick': 480, 'set': {'reset': 0}}, {'tick': 500, 'set': {'run': 1}}]
WATCHES = ['gpio', 'halted', 'faulted', 'trap_cause', 'busy']

p = argparse.ArgumentParser()
p.add_argument('folder', type=Path)
p.add_argument('--ticks', type=int, default=20000)
a = p.parse_args()
start = time.perf_counter()
sim, result = run(a.folder, ticks=a.ticks, stimuli=STIMULUS, watches=WATCHES)
wall = time.perf_counter() - start
print(f'ticks={result["tick"]} wall={wall:.2f}s ticks_per_sec={result["tick"] / wall:.1f}')
print('signals', result['signals'])
