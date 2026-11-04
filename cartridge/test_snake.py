"""Host-side check of the compiled Snake cartridge on the RV32E instruction-set simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rvtools import Machine, elf_text

elf = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent / 'snake.elf')
base, image = elf_text(elf)


def play(script, limit=400000):
    """script: {frame number: line typed once that frame has been drawn}."""
    m = Machine(image, base)
    seen = 0
    while not m.stop and m.retired < limit:
        m.step()
        if len(m.frames) != seen:
            seen = len(m.frames)
            if seen in script and m.line is None and not m.lines:
                m.lines.append(script[seen])
    return m, [f.decode() for f in m.frames] + [m.cur and bytes(m.cur).decode()]


def rows(frame):
    return frame.split('\n')


def head(frame):
    r = rows(frame)
    for y, row in enumerate(r):
        if 'O' in row:
            return row.index('O'), y


# 1. hands off: the snake runs right along row 1 and hits the wall
m, f = play({})
assert m.stop
assert rows(f[0])[1] == '....DO......', rows(f[0])
assert rows(f[-1])[1] == '..........DO', rows(f[-1])
assert sum(len(x) for x in f) and all(len(x) == 12 * 4 + 3 for x in f if x), [len(x) for x in f]

# 2. steering: down, then left, then up, then right = 2x2 loop; reversing is ignored
m, f = play({1: 'S', 2: 'A', 3: 'W', 4: 'D'})
path = [head(x) for x in f if x]
print('loop path', path)
assert path[:5] == [(5, 1), (6, 1), (6, 2), (5, 2), (5, 1)], path

# 3. eating: food starts at index 30 = (6, 2); go there and check the tail stays (growth) and food moves
m, f = play({1: 'S'})
assert head(f[2]) == (6, 2)
body = lambda fr: sum(fr.count(c) for c in 'WASDO')
print('body lengths', [body(x) for x in f if x])
assert body(f[2]) == body(f[1]) + 1 and '+' in f[2] and rows(f[2])[3][11] == '+', f[2]

# 4. a ten-step run for the instruction budget
m = Machine(image, base)
counts, last, seen = [], 0, 0
while not m.stop and m.retired < 100000:
    m.step()
    if len(m.frames) != seen:
        seen = len(m.frames)
        counts.append(m.retired - last)
        last = m.retired
print('cartridge words:', len(image) // 4, ' instructions per game step:', counts)
print('OK')
