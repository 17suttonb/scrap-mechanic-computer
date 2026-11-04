"""Host-side check of the compiled Snake cartridge on the RV32E instruction-set simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rvtools import Machine

BASE = 0x800
HOT_LOOP = 9
image = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'snake.bin').read_bytes()


def play(keys_at_board=None, limit=200000):
    """Run to game over. keys_at_board maps a move number to a key tapped during that move.
    Returns the boards seen, one per move, and the machine."""
    keys_at_board = keys_at_board or {}
    m = Machine(image, BASE)
    boards, last = [], None
    while not m.stop and m.retired < limit:
        m.step()
        if m.pc == BASE + POLL:
            board = m.screen()
            boards.append(board)
            if len(boards) in keys_at_board:
                m.keys.append(keys_at_board[len(boards)])
    return boards, m


def find_poll():
    """The status read that ends each move: a key offered here steers the next move."""
    for i in range(0, len(image), 4):
        w = int.from_bytes(image[i:i + 4], 'little')
        if w & 0xFFF0707F == 0xF1000003:
            return i
    raise AssertionError('no status read found')


POLL = find_poll()
count = lambda board, ch: sum(row.count(ch) for row in board)

# hands off: runs right along row 1 into the wall at column 12
boards, m = play()
assert m.stop
assert boards[0][1] == '.....OO.....', boards[0]
assert boards[0][2][6] == '+'
assert boards[-1][1] == '..........OO', boards[-1]
assert all(count(b, 'O') == 2 for b in boards), boards

# a turn takes effect on the next move: down from column 6 reaches row 2
boards, m = play({1: 'S'})
assert boards[1][2][6] == 'O', boards[1]

# eating the food at (6, 2) grows the snake and places new food on a free cell
boards, m = play({1: 'S'})
lengths = [count(b, 'O') for b in boards]
assert max(lengths) == 3 and lengths[0] == 2, lengths
assert all(count(b, '+') == 1 for b in boards), boards

# reversal and unknown keys are ignored
plain = play()[0]
assert play({2: 'A'})[0] == plain
assert play({2: 'Q'})[0] == plain

# the top and bottom edges wrap: up from row 1 comes back on row 3, still running
boards, m = play({1: 'W'}, limit=2000)
assert boards[2][0][6] == 'O' and boards[3][3][6] == 'O' and not m.stop, boards[3]

# the left wall ends the game
boards, m = play({1: 'W', 2: 'A'})
assert m.stop and boards[-1][0][0] == 'O', boards[-1]

# running into a body cell ends the game
m = Machine(image, BASE)
m.ram[16 + 9] = 1
while not m.stop and m.retired < 1000:
    m.step()
assert m.stop and m.x[10] == 16 + 9, m.x[10]

# cost of one move in steady state
m = Machine(image, BASE)
marks = []
while len(marks) < 4 and not m.stop:
    m.step()
    if m.pc == BASE + POLL:
        marks.append(m.retired)
costs = [b - a for a, b in zip(marks, marks[1:])]
assert costs == [HOT_LOOP] * 3, costs
print(f'cartridge words: {len(image) // 4}, instructions per move: {costs[0]}, instructions to first move: {marks[0]}')
print('OK')
