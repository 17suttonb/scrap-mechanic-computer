"""Check the Rust simulator against the authoritative Python one, comparing every controller's state.

Usage: Logic-Block-Simulator/runtime/python.exe fast_sim/verify.py [joined-export-folder] [--ticks N]

Without a folder it only runs the randomised circuits. Exits nonzero on the first difference.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fast_simulator import FastLogicSimulator, NativeLogicSimulator  # noqa: E402

GATE = '9f0f56e8-2c31-4d83-996c-d00a9b296c3f'
SWITCH = '7cf717d7-d167-4f2d-a6e7-6b2c70aa3986'
BUTTON = '1e8d93a4-506b-470d-9ada-9c0a321e2db5'
TIMER = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'


def compare(python, fast, where):
    left, right = python.values, fast.values()
    if left != right:
        wrong = [i for i in left if left[i] != right[i]]
        raise SystemExit(f'DIFFERENT at {where}, tick {python.time}: {len(wrong)} controllers, first {wrong[:5]}')
    if python.time != fast.time:
        raise SystemExit(f'time differs at {where}: {python.time} {fast.time}')


def random_circuit(rng):
    n = rng.randint(4, 120)
    parts = []
    for i in range(n):
        roll = rng.random()
        if roll < 0.08:
            parts.append(dict(shapeId=rng.choice((SWITCH, BUTTON)), controller=dict(id=i, active=rng.random() < 0.5, controllers=[])))
        elif roll < 0.2:
            parts.append(dict(shapeId=TIMER, controller=dict(id=i, active=rng.random() < 0.3, seconds=rng.choice((0, 0, 0, 1)), ticks=rng.randint(0, 39), controllers=[])))
        else:
            parts.append(dict(shapeId=GATE, controller=dict(id=i, active=rng.random() < 0.3, mode=rng.randint(0, 5), controllers=[])))
    edges, timer_parent = set(), set()
    for _ in range(rng.randint(n, n * 4)):
        a, b = rng.randrange(n), rng.randrange(n)
        if parts[b]['shapeId'] in (SWITCH, BUTTON) or (a, b) in edges or (b, a) in edges or (a == b and parts[a]['shapeId'] == TIMER):
            continue
        if parts[b]['shapeId'] == TIMER:
            if b in timer_parent:
                continue
            timer_parent.add(b)
        edges.add((a, b))
        parts[a]['controller']['controllers'].append(dict(id=b))
    return dict(bodies=[dict(childs=parts)])


def fuzz(circuits):
    rng = random.Random(4004)
    started = time.perf_counter()
    for number in range(circuits):
        bp = random_circuit(rng)
        python, fast = NativeLogicSimulator(bp, input_mode='controller'), FastLogicSimulator(bp)
        inputs = [i for i, k in python.kinds.items() if k == 'INPUT']
        compare(python, fast, f'circuit {number} start')
        for _ in range(40):
            for _ in range(rng.randint(0, 2) if inputs else 0):
                node, value, at = rng.choice(inputs), rng.random() < 0.5, python.time + rng.randint(1, 8)
                python.request_input(node, value, at_tick=at)
                fast.request_input(node, value, at_tick=at)
            steps = rng.choice((1, 1, 1, 2, 3, 7, 50))
            python.advance(steps)
            fast.advance(steps)
            compare(python, fast, f'circuit {number}')
    print(f'random circuits: {circuits} identical ({time.perf_counter() - started:.1f} s)')


def real_build(folder, ticks):
    bp = json.loads((Path(folder) / 'blueprint.json').read_text(encoding='utf-8'))
    names = json.loads((Path(folder) / 'clock-map.json').read_text(encoding='utf-8'))['names']
    keys = {k['key']: k['controllerId'] for k in json.loads((Path(folder) / 'terminal-map.json').read_text(encoding='utf-8'))['keys']}
    python, fast = NativeLogicSimulator(bp, input_mode='controller'), FastLogicSimulator(bp)
    script = [(1, names['reset'], True), (481, names['reset'], False), (501, names['run'], True)]
    at = 6000
    for key in ('?', '2', '+', '3', 'ENTER', 'S', 'ENTER', 'S', 'A', 'W', 'D'):
        script += [(at, keys[key], True), (at + 20, keys[key], False)]
        at += 1500 if key == 'ENTER' else 300
    for tick, node, value in script:
        python.request_input(node, value, at_tick=tick)
        fast.request_input(node, value, at_tick=tick)
    python_time = fast_time = 0
    for block in range(ticks // 250):
        began = time.perf_counter()
        python.advance(250)
        python_time += time.perf_counter() - began
        began = time.perf_counter()
        fast.advance(250)
        fast_time += time.perf_counter() - began
        compare(python, fast, 'real build')
    print(f'real build: {len(python.values)} controllers identical at all {ticks // 250} checks over {ticks} ticks')
    print(f'python {ticks / python_time:.0f} ticks/s, rust {ticks / fast_time:.0f} ticks/s, {python_time / fast_time:.0f} times faster')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', nargs='?')
    p.add_argument('--ticks', type=int, default=30000)
    p.add_argument('--circuits', type=int, default=400)
    a = p.parse_args()
    fuzz(a.circuits)
    if a.folder:
        real_build(a.folder, a.ticks)


if __name__ == '__main__':
    main()
