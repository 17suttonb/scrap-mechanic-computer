"""Measure how Snake takes keys on an exported blueprint, in the Logic-Block-Simulator.

Usage (bundled runtime):
  Logic-Block-Simulator\\runtime\\python.exe cartridge\\sim_keys.py <joined-export-folder> [--cpu-ticks 16,2,8,2] [--holds 4,8,20]

Each trial taps keys at a chosen phase of the move cycle, phase being updates since the head last moved, then
follows the head. A tap counts as registered when the head moves in that direction. Latency runs from the press to
the first screen update showing the head on its new heading. Times are at the nominal 40 updates per second.
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench_commands import Machine  # noqa: E402

STEP = {'W': (0, -1), 'S': (0, 1), 'A': (-1, 0), 'D': (1, 0)}
OPPOSITE = {'W': 'S', 'S': 'W', 'A': 'D', 'D': 'A'}
NAMES = {v: k for k, v in STEP.items()}
BOARD_ROW = re.compile(r'^[.OQ+]{12}$')


class Game:
    def __init__(self, folder, cpu_ticks, sample=2):
        self.folder, self.cpu_ticks, self.sample = folder, cpu_ticks, sample
        self.games = 0
        self.start()

    def start(self):
        self.m = Machine(self.folder, self.cpu_ticks)
        assert self.m.menu_ready('2'), self.m.text()
        self.m.start_program('2')
        self.height = 0
        for _ in range(4000):
            self.m.sim.advance(4)
            rows = self.m.text()
            board = [r for r in rows if BOARD_ROW.match(r)]
            if ''.join(board).count('Q') == 1 and ''.join(board).count('+') == 1 and len(board) >= 4:
                self.height = len(board)
                break
        assert self.height, self.m.text()
        self.games += 1
        self.state = None
        self.head = None
        self.moved_at = self.m.sim.time
        self.moves = []             # (time, position, direction letter)
        self.heading = None
        self.ended = False
        self.wait_move()
        self.wait_move()

    def poll(self):
        """Sample the screen once; returns True if the head moved."""
        self.m.sim.advance(self.sample)
        state = self.m.screen_state()
        if state == self.state:
            return False
        self.state = state
        rows = self.m.text()
        if any('LOST' in r or 'WON' in r for r in rows):
            self.ended = True
        cells = rows[:self.height]
        heads = [(r.index('Q'), y) for y, r in enumerate(cells) if 'Q' in r]
        if len(heads) != 1 or heads[0] == self.head:
            return False
        new = heads[0]
        if self.head is not None:
            dx, dy = new[0] - self.head[0], new[1] - self.head[1]
            dx = 1 if dx == -11 else -1 if dx == 11 else dx
            dy = 1 if dy == 1 - self.height else -1 if dy == self.height - 1 else dy
            self.heading = NAMES.get((dx, dy), self.heading)
            self.moves.append((self.m.sim.time, new, self.heading))
        self.head = new
        self.moved_at = self.m.sim.time
        return True

    def wait_move(self, limit=4000):
        begin = self.m.sim.time
        while self.m.sim.time - begin < limit and not self.ended:
            if self.poll():
                return True
        return False

    def wait_phase(self, phase):
        """Wait for a move, then until `phase` updates after it."""
        self.wait_move()
        while self.m.sim.time - self.moved_at < phase and not self.ended:
            self.poll()

    def press(self, name, hold):
        """Press now and keep sampling for `hold` updates; returns the press time."""
        key = self.m.keys[name]
        at = self.m.sim.time + 1
        self.m.sim.request_input(key, True, at_tick=at)
        self.m.sim.request_input(key, False, at_tick=at + hold)
        return at

    def run_for(self, updates):
        end = self.m.sim.time + updates
        while self.m.sim.time < end and not self.ended:
            self.poll()

    def settle_moves(self, n):
        for _ in range(n):
            self.wait_move()


def perpendicular(heading, turn):
    return ('W', 'S')[turn % 2] if heading in 'AD' else ('A', 'D')[turn % 2]


def follow(g, since, expected, patience=10):
    """Moves after `since`; return the indices and times at which each expected heading began, in order."""
    results, seen, position = [], 0, 0
    for _ in range(patience * max(1, len(expected))):
        if position >= len(expected):
            break
        g.wait_move()
        if g.ended:
            break
        after = [m for m in g.moves if m[0] > since]
        while seen < len(after) and position < len(expected):
            if after[seen][2] == expected[position]:
                results.append((seen + 1, after[seen][0]))
                position += 1
            seen += 1
    return results


def single(g, phases, holds, per):
    print('\n== one tap at each phase ==')
    print('updates per move, from idle moves: ', end='')
    g.settle_moves(6)
    gaps = [b[0] - a[0] for a, b in zip(g.moves[-6:], g.moves[-5:])]
    print(gaps)
    table = defaultdict(list)
    turn = 0
    for hold in holds:
        for phase in phases:
            for _ in range(per):
                if g.ended:
                    g.start()
                g.wait_phase(phase)
                if g.ended:
                    continue
                key = perpendicular(g.heading, turn)
                turn += 1
                at = g.press(key, hold)
                heading = g.heading
                got = follow(g, at, [key], patience=6)
                if g.ended and not got:
                    continue        # the game finished under this tap: says nothing about the key
                table[hold, phase].append((got[0][0], (got[0][1] - at) / 40) if got else None)
                if not got:
                    after = ''.join(m[2] for m in g.moves if m[0] > at)
                    print(f'  LOST hold {hold} phase {phase} key {key} while heading {heading}: then moved {after!r}, {g.m.sim.time - at} updates; screen {g.m.text()}', flush=True)
                g.settle_moves(1)
    print(f'{"hold":>4} {"phase":>5} {"taps":>4} {"seen":>4} {"moves late":>12} {"latency s (min/mean/max)":>26}')
    for (hold, phase), rows in sorted(table.items()):
        ok = [r for r in rows if r]
        moves = sorted({r[0] for r in ok})
        times = [r[1] for r in ok]
        spread = f'{min(times):6.2f} {sum(times) / len(times):6.2f} {max(times):6.2f}' if ok else ''
        print(f'{hold:4d} {phase:5d} {len(rows):4d} {len(ok):4d} {str(moves):>12} {spread:>26}', flush=True)
    every = [r for rows in table.values() for r in rows]
    ok = [r for r in every if r]
    print(f'total {len(ok)}/{len(every)} registered; latency mean {sum(r[1] for r in ok) / len(ok):.2f} s, min {min(r[1] for r in ok):.2f}, max {max(r[1] for r in ok):.2f}; on move {sorted({r[0] for r in ok})}')


def double(g, gaps, phases, hold):
    print('\n== two perpendicular taps in quick succession ==')
    print(f'{"gap":>5} {"phase":>5} {"both":>5} {"first (move, s)":>16} {"second (move, s)":>17}')
    for gap in gaps:
        for phase in phases:
            if g.ended:
                g.start()
            g.wait_phase(phase)
            if g.ended:
                continue
            first = perpendicular(g.heading, gap)
            second = perpendicular(first, gap + 1)
            at = g.press(first, hold)
            g.run_for(gap)
            g.press(second, hold)
            got = follow(g, at, [first, second], patience=6)
            text = [f'{m},{(t - at) / 40:.2f}' for m, t in got] + ['game over' if g.ended else 'lost'] * (2 - len(got))
            print(f'{gap:5d} {phase:5d} {"yes" if len(got) == 2 else "over" if g.ended else "NO":>5} {text[0]:>16} {text[1]:>17}', flush=True)
            g.settle_moves(1)


def burst(g, sequence, gap, hold):
    """Tap a whole plan ahead of time and report the headings the snake then takes."""
    if g.ended:
        g.start()
    g.wait_phase(40)
    if g.ended:
        return
    start = g.heading
    at = g.m.sim.time
    for key in sequence:
        g.press(key, hold)
        g.run_for(gap)
    g.settle_moves(len(sequence) + 6)
    after = [m for m in g.moves if m[0] > at]
    path = ''.join(m[2] for m in after)
    print(f'from {start} plan {sequence} gap {gap}: moves {path}' + (' (game ended)' if g.ended else ''), flush=True)


def view(g):
    """(board rows, score, bottom row) as the screen shows them."""
    rows = g.m.text()
    return rows[:g.height], rows[g.height] if len(rows) > g.height else ''


def toward(g, cells, food):
    """A direction for the head: toward the food the short way round, not into the body, not reversing."""
    x, y = g.head
    wanted = []
    if food:
        dx, dy = (food[0] - x) % 12, (food[1] - y) % g.height
        if dx:
            wanted.append('D' if dx <= 6 else 'A')
        if dy:
            wanted.append('S' if dy <= g.height // 2 else 'W')
    order = wanted + [k for k in 'DSAW' if k not in wanted]
    for key in order:
        if key == OPPOSITE.get(g.heading):
            continue
        cx, cy = (x + STEP[key][0]) % 12, (y + STEP[key][1]) % g.height
        if cells[cy][cx] != 'O':
            return key
    return g.heading


def play(g, target, hold=8):
    """Play a real game up to `target` food, then steer into the body, checking the screen at every move:
    one head and one food, a body as long as the score says, the digits counting up, every edge crossed."""
    print(f'== a played-out game, to {target} food and then a loss ==')
    bad, last, moves, crossed, faults = 0, 0, 0, set(), []
    limit = g.m.sim.time + 600000
    while not g.ended and g.m.sim.time < limit:
        before = g.head
        if not g.wait_move():
            break
        moves += 1
        dx, dy = g.head[0] - before[0], g.head[1] - before[1]
        if abs(dx) > 1:
            crossed.add('right' if dx < 0 else 'left')
        if abs(dy) > 1:
            crossed.add('bottom' if dy < 0 else 'top')
        # a key pressed straight after a move steers the next one
        cells, _ = view(g)
        foods = [(r.index('+'), y) for y, r in enumerate(cells) if '+' in r]
        key = toward(g, cells, foods[0] if foods else None)
        if key != g.heading:
            g.press(key, hold)
        g.run_for(150)              # the tail has moved by now
        if g.ended:
            break
        cells, bottom = view(g)
        joined = ''.join(cells)
        foods = [(r.index('+'), y) for y, r in enumerate(cells) if '+' in r]
        digits = bottom[:2]
        score = int(digits) if digits.isdigit() else -1
        length = joined.count('O') + joined.count('Q')
        if score < last or score > last + 1:
            faults.append(f'move {moves}: score {last} then {digits!r}')
        last = max(score, last)
        if joined.count('Q') != 1 or len(foods) != 1 or length != score + 2:
            bad += 1
            if bad > 1:             # one sample can land inside the extra rows of a meal
                faults.append(f'move {moves}: {cells} {bottom!r}')
        else:
            bad = 0
        if faults:
            break
        if last >= target:
            break
    print(f'moves {moves}, score {last}, edges crossed {sorted(crossed)}, faults {faults[:3]}')
    if faults or g.ended and last < target:
        print('screen', g.m.text())
    # now run into the body: turning the same way every move closes a long snake on itself
    if not g.ended and not faults:
        for _ in range(16):
            g.press('DSAW'['DSAW'.index(g.heading) - 3], hold)
            g.wait_move()
            if g.ended:
                break
    g.run_for(40)
    print('final screen', g.m.text(), 'ended' if g.ended else 'still running')
    return not faults and g.ended and last >= target and len(crossed) == 4


def win(g, hold=8):
    """Follow a closed path through every cell, the host test's, until the board is full."""
    print('== a perfect game ==')
    moves, limit = 0, g.m.sim.time + 3000000
    while not g.ended and g.m.sim.time < limit:
        if not g.wait_move():
            break
        moves += 1
        x, y = g.head
        key = ('W' if y else 'D') if x == 0 else 'D' if y == g.height - 1 else ('D' if x < 11 else 'S') if y % 2 == 0 else ('A' if x > 1 else 'S')
        if key != g.heading and key != OPPOSITE.get(g.heading):
            g.press(key, hold)
    g.run_for(40)
    rows = g.m.text()
    print(f'moves {moves}, {(g.m.sim.time) / 40 / 3600:.1f} hours at 40 updates per second', rows)
    return ''.join(rows[:g.height]).count('.') == 0 and rows[g.height].startswith('82') and 'YOU WON' in rows[g.height]


def restart(g):
    """RESET after a game: the menu must come back, BASIC must work, and a second game must start on a clean board."""
    sim = g.m.sim

    def reset():
        sim.request_input(g.m.run, False, at_tick=sim.time + 1)
        sim.request_input(g.m.reset, True, at_tick=sim.time + 20)
        sim.request_input(g.m.reset, False, at_tick=sim.time + 500)
        sim.request_input(g.m.run, True, at_tick=sim.time + 520)
        sim.advance(530)
    reset()
    assert g.m.menu_ready('1'), g.m.text()
    menu = g.m.text()
    g.m.boot('1')
    seconds = g.m.command('?2+3', 4000)
    answer = g.m.text()
    reset()
    assert g.m.menu_ready('2'), g.m.text()
    g.m.start_program('2')
    sim.advance(2400)
    # a cell shows a transient glyph as it changes
    while '#' in ''.join(g.m.text()):
        sim.advance(2)
    board = g.m.text()
    print('after RESET', menu)
    print(f'?2+3 in {seconds:.2f} s', answer)
    print('second game', board)
    cells = ''.join(board[:7])
    return menu[0] == 'MENU' and '5' in ''.join(answer) and cells.count('Q') == 1 and cells.count('+') == 1 and cells.count('O') <= 2 and board[7].startswith('00 SCORE')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--cpu-ticks', default=None)
    p.add_argument('--holds', default='4,8,20')
    p.add_argument('--phases', default='0,30,60,90,120,150,180')
    p.add_argument('--per', type=int, default=4, help='taps per phase and hold')
    p.add_argument('--gaps', default='0,6,12,25,50,100,200,300,400')
    p.add_argument('--play', type=int, default=0, help='only play one game to this much food, lose it, then RESET and start again')
    p.add_argument('--win', action='store_true', help='only play one perfect game')
    a = p.parse_args()
    ticks = [int(v) for v in a.cpu_ticks.split(',')] if a.cpu_ticks else None
    g = Game(a.folder, ticks)
    print(f'board height {g.height}')
    if a.win:
        ok = win(g)
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    if a.play:
        ok = play(g, a.play)
        ok = restart(g) and ok
        print('PASS' if ok else 'FAIL')
        return 0 if ok else 1
    phases = [int(v) for v in a.phases.split(',')]
    single(g, phases, [int(v) for v in a.holds.split(',')], a.per)
    double(g, [int(v) for v in a.gaps.split(',')], phases[::3], 8)
    print('\n== a plan tapped ahead ==')
    for plan, gap in (('SAWD', 30), ('SAWD', 10), ('WDSA', 60), ('SDSD', 30), ('ASW', 30)):
        burst(g, plan, gap, 8)
    print(f'games played: {g.games}')


if __name__ == '__main__':
    sys.exit(main())
