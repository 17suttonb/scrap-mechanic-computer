"""Host-side check of the compiled Snake cartridge on the RV32E instruction-set simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rvtools import Machine

BASE = 0x800
HOT_LOOP = 10
image = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'snake.bin').read_bytes()


def find_poll():
    """The status read that ends each move: a key offered here steers the next move."""
    for i in range(0, len(image), 4):
        w = int.from_bytes(image[i:i + 4], 'little')
        if w & 0xFFF0707F == 0xF1000003:
            return i
    raise AssertionError('no status read found')


POLL = find_poll()


def play(keys=None, limit=3000, prepare=None):
    """Run the game. keys maps a move number to a key tapped after that move. Returns one board per
    move, and the machine, which has stopped if the game ended."""
    keys = keys or {}
    m = Machine(image, BASE)
    if prepare:
        prepare(m)
    boards = []
    while not m.stop and m.retired < limit:
        m.step()
        if m.pc == BASE + POLL:
            boards.append(m.screen())
            if len(boards) in keys:
                m.keys.append(keys[len(boards)])
    return boards, m


def count(board, ch):
    return sum(row.count(ch) for row in board)


def head(board):
    return next((row.index('Q'), y) for y, row in enumerate(board) if 'Q' in row)


def walls_intact(m):
    return all(m.ram[r * 16 + 12] == 3 and m.ram[r * 16 + 15] == 3 for r in range(4))


def tour(board):
    """The direction that keeps a head on a closed path through all 48 cells, which never hits the body."""
    x, y = head(board)
    if x == 0:
        return 'D' if y == 0 else 'W'
    if y == 3:
        return 'A'
    if y % 2 == 0:
        return 'D' if x < 11 else 'S'
    return 'A' if x > 1 else 'S'


def play_tour(limit=2000000):
    """Follow the closed path until the game ends. Returns every board and the machine."""
    m = Machine(image, BASE)
    boards, going = [], 'D'
    while not m.stop and m.retired < limit:
        m.step()
        if m.pc == BASE + POLL:
            boards.append(m.screen())
            key = tour(boards[-1])
            if key != going:
                m.keys.append(key)
                going = key
    return boards, m


# hands off: the head is one Q, the body is O, and the row wraps at both ends of the screen
boards, m = play()
assert not m.stop
assert boards[0][1] == '.....OQ.....', boards[0]
assert boards[0][2][6] == '+'
assert all(count(b, 'Q') == 1 and count(b, 'O') == 1 for b in boards), boards[:12]
columns = [head(b)[0] for b in boards[:14]]
assert columns == [(6 + n) % 12 for n in range(14)], columns
assert boards[5][1] == '..........OQ' and boards[6][1] == 'Q..........O' and boards[7][1] == 'OQ..........', boards[5:8]
assert walls_intact(m), m.ram.hex()

# wrapping left
boards, m = play({1: 'W', 2: 'A'})
columns = [head(b)[0] for b in boards[2:12]]
assert columns == [(5 - n) % 12 for n in range(10)], columns
assert not m.stop and walls_intact(m)

# a turn takes effect on the next move: down from column 6 reaches the food in row 2
boards, m = play({1: 'S'})
assert boards[1][2][6] == 'Q', boards[1]

# eating grows the snake and places new food on a free cell
lengths = [count(b, 'O') + count(b, 'Q') for b in boards]
assert lengths[0] == 2 and max(lengths) == 3, lengths
assert all(count(b, '+') == 1 for b in boards), boards

# top and bottom wrap
boards, m = play({1: 'W'})
assert head(boards[1]) == (6, 0) and head(boards[2]) == (6, 3) and not m.stop, boards[2]

# reversal and unknown keys are ignored
plain = play()[0]
assert play({2: 'A'})[0][:40] == plain[:40]
assert play({2: 'Q'})[0][:40] == plain[:40]


# running into a body cell ends the game, and the head is drawn on the cell it hit
def body_ahead(m):
    m.ram[16 + 9] = 1


boards, m = play(prepare=body_ahead)
assert m.stop
assert m.screen()[1][9] == 'Q', m.screen()
assert m.result() == 'YOU LOST' and m.score() == '00', (m.result(), m.score())

# the score counts food, and no result shows while the game runs
boards, m = play({1: 'S'}, limit=60)
assert m.score() == '01' and m.result() == '' and not m.stop, (m.score(), m.result())

# a perfect game: food is always on the board, the score carries, and filling the board wins
boards, m = play_tour()
assert m.stop and m.result() == 'YOU WON' and m.score() == '46', (m.stop, m.result(), m.score(), boards[-1])
assert all(count(b, '+') == 1 for b in boards), 'a board without visible food'
assert count(m.screen(), 'O') + count(m.screen(), 'Q') == 48, m.screen()
assert walls_intact(m), m.ram.hex()
won_moves = len(boards)

# cost of one move in steady state
m = Machine(image, BASE)
marks = []
while len(marks) < 4 and not m.stop:
    m.step()
    if m.pc == BASE + POLL:
        marks.append(m.retired)
costs = [b - a for a, b in zip(marks, marks[1:])]
assert costs == [HOT_LOOP] * 3, costs
print(f'cartridge words: {len(image) // 4}, instructions per move: {costs[0]}, instructions to first move: {marks[0]}, moves in a winning game: {won_moves}')
print('OK')
