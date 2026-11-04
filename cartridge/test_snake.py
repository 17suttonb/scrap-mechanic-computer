"""Host-side check of the compiled Snake cartridge on the RV32E instruction-set simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rvtools import COLUMNS, Machine, ROWS, STRIDE

BASE = 0x800
HOT_LOOP = 11       # instructions; the cartridge builder folds them into six rows
CELLS = COLUMNS * ROWS
image = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'snake.bin').read_bytes()


def find_poll():
    """The steering load that ends each move: a key offered here steers the next move."""
    for i in range(0, len(image), 4):
        w = int.from_bytes(image[i:i + 4], 'little')
        if w & 0xFFF0707F == 0xF1600003:
            return i - 4        # its marker: the position register holds the position to steer from
    raise AssertionError('no steering load found')


POLL = find_poll()


def play(keys=None, limit=6000, prepare=None):
    """Run the game. keys maps a move number to the keys tapped after that move. Returns one board per
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
            m.keys.extend(keys.get(len(boards), ''))
    return boards, m


def count(board, ch):
    return sum(row.count(ch) for row in board)


def head(board):
    return next((row.index('Q'), y) for y, row in enumerate(board) if 'Q' in row)


def path(boards):
    """The direction of each move, wraps included."""
    names = {(0, -1): 'W', (0, 1): 'S', (-1, 0): 'A', (1, 0): 'D'}
    out = []
    for a, b in zip(boards, boards[1:]):
        (x0, y0), (x1, y1) = head(a), head(b)
        out.append(names[((x1 - x0 + 1) % COLUMNS) - 1, ((y1 - y0 + 1) % ROWS) - 1])
    return ''.join(out)


def hidden_clean(m):
    """Nothing was stored off the board but the score and the result."""
    return all(m.ram[a] == 0 for a in range(64) if a % STRIDE >= COLUMNS and a not in (13, 14, 29))


def tour(board):
    """The direction that keeps a head on a closed path through all the cells, which never hits the body:
    down the rows between columns 1 and 11, across the side wrap at the end of the last row, up column 0."""
    x, y = head(board)
    if x == 0:
        return 'W' if y else 'D'
    if y == ROWS - 1:
        return 'D'
    if y % 2 == 0:
        return 'D' if x < COLUMNS - 1 else 'S'
    return 'A' if x > 1 else 'S'


def play_tour(limit=8000000):
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


def to_food(then=''):
    """Steer onto the first food, then tap `then`."""
    return play({1: 'S', 2: 'S', 3: 'A', 4: then}, limit=120)


# hands off: the board is seven rows, the head is one Q, the body is O, and the row wraps at both ends
boards, m = play()
assert not m.stop
assert len(boards[0]) == ROWS and all(len(r) == COLUMNS for r in boards[0]), boards[0]
assert boards[0][3] == '......OQ....', boards[0]
assert boards[0][5][6] == '+'
assert all(count(b, 'Q') == 1 and count(b, 'O') == 1 for b in boards), boards[:12]
columns = [head(b)[0] for b in boards[:14]]
assert columns == [(7 + n) % COLUMNS for n in range(14)], columns
assert boards[4][3] == '..........OQ' and boards[5][3] == 'Q..........O' and boards[6][3] == 'OQ..........', boards[4:7]
assert hidden_clean(m), m.ram.hex()

# wrapping left: the tail crosses too
boards, m = play({1: 'W', 2: 'A'})
columns = [head(b)[0] for b in boards[2:12]]
assert columns == [(6 - n) % COLUMNS for n in range(10)], columns
assert all(count(b, 'O') == 1 for b in boards[:12]), boards[:12]
assert not m.stop and hidden_clean(m)

# wrapping off the top and off the bottom: the tail crosses too, and nothing is drawn beyond the board
boards, m = play({1: 'W'})
rows = [head(b)[1] for b in boards[1:12]]
assert rows == [(2 - n) % ROWS for n in range(11)], rows
assert all(count(b, 'O') == 1 and head(b)[0] == 7 for b in boards[1:12]), boards[1:12]
assert not m.stop and hidden_clean(m)
boards, m = play({1: 'D', 2: 'S'})
rows = [head(b)[1] for b in boards[2:14]]
assert rows == [(4 + n) % ROWS for n in range(12)], rows
assert all(count(b, 'O') == 1 and head(b)[0] == 8 for b in boards[2:14]), boards[2:14]
assert not m.stop and hidden_clean(m)

# a position register stays on the board however long the game goes on, so it can never reach a firmware window
boards, m = play({1: 'W'}, limit=20000)
assert not m.stop and all(-STRIDE <= m.x[r] - (1 << 32 if m.x[r] >> 31 else 0) < 128 for r in (10, 11)), m.x[10:12]

# eating grows the snake, scores, and places new food on a free cell
boards, m = to_food()
lengths = [count(b, 'O') + count(b, 'Q') for b in boards]
assert lengths[0] == 2 and max(lengths) == 3, lengths
assert all(count(b, '+') == 1 for b in boards), boards
assert m.score() == '01' and m.result() == '' and not m.stop, (m.score(), m.result())

# a key tapped after a move steers the very next move
boards, m = play({1: 'S'})
assert head(boards[1]) == (7, 4), boards[1]

# reversal and unknown keys are ignored
plain = play()[0]
assert play({2: 'A'})[0][:40] == plain[:40]
assert play({2: 'Q'})[0][:40] == plain[:40]
assert play({2: 'E'})[0][:40] == plain[:40]

# LOST AND QUEUED KEYS: every tap is taken, one per move, in order, at no cost in instructions
# several tapped together queue, and each applies on its own move
boards, m = play({1: 'WDSD'}, limit=200)
assert path(boards)[:6] == 'WDSDDD', path(boards)[:6]
assert m.taken == 4 and not m.keys, (m.taken, m.keys)

# a queued key is judged against the step it follows, not the step when it was tapped
boards, m = play({1: 'WA'}, limit=200)      # moving D: W, then A, which would have reversed D
assert path(boards)[:3] == 'WAA', path(boards)[:3]
boards, m = play({1: 'WS'}, limit=200)      # S reverses the W before it
assert path(boards)[:3] == 'WWW', path(boards)[:3]
# a key that is ignored uses up its move in the queue, and the next one still applies
boards, m = play({1: 'AW'}, limit=200)
assert path(boards)[:3] == 'DWW', path(boards)[:3]

# a key tapped on a move that wraps or eats is neither lost nor delayed
for move in range(1, 8):
    boards, m = play({move: 'W'}, limit=400)
    assert path(boards)[move - 1:move + 1] == 'WW', (move, path(boards)[:move + 3])
boards, m = to_food('W')
assert head(boards[3]) == (6, 5) and head(boards[4]) == (6, 4), boards[3:5]

# a key costs nothing: the same instructions per move with one or without, whichever key
for key in 'WSD':
    m = Machine(image, BASE)
    seen = []
    while len(seen) < 4 and not m.stop:
        m.step()
        if m.pc == BASE + POLL:
            seen.append(m.retired)
            if len(seen) == 2:
                m.keys.append(key)
    assert [b - a for a, b in zip(seen, seen[1:])] == [HOT_LOOP] * 3, (key, seen)


# running into a body cell ends the game, and the head is drawn on the cell it hit
def body_ahead(m):
    m.ram[3 * STRIDE + 9] = 1


boards, m = play(prepare=body_ahead)
assert m.stop
assert m.screen()[3][9] == 'Q', m.screen()
assert m.result() == 'YOU LOST' and m.score() == '00', (m.result(), m.score())


# the same in a row that lives outside the RAM
def body_below(m):
    m.extra[COLUMNS + 7] = 1


boards, m = play({1: 'S'}, prepare=body_below)
assert m.stop and m.result() == 'YOU LOST' and m.screen()[5][7] == 'Q', m.screen()

# a perfect game: food is always on the board, the score carries, and filling the board wins at 82
boards, m = play_tour()
assert m.stop and m.result() == 'YOU WON' and m.score() == '82', (m.stop, m.result(), m.score(), boards[-1])
assert all(count(b, '+') == 1 for b in boards), 'a board without visible food'
assert count(m.screen(), 'O') + count(m.screen(), 'Q') == CELLS, m.screen()
assert hidden_clean(m), m.ram.hex()
won_moves = len(boards)

# crossing an edge costs nothing either: a tour of every edge at the same cost per move
m = Machine(image, BASE)
seen, wraps = [], 0
keys = {14: 'W', 24: 'A', 40: 'S', 50: 'D'}
while len(seen) < 60 and not m.stop:
    m.step()
    if m.pc == BASE + POLL:
        seen.append(m.retired)
        m.keys.extend(keys.get(len(seen), ''))
        wraps += m.x[12] & 0xFFFFFFFF in (11, 0xFFFFFFF5, 96, 0xFFFFFFA0)
assert not m.stop and wraps >= 4, (m.stop, wraps)
assert m.score() == '00', m.score()     # this route does not eat, so every move must cost the same
assert {b - a for a, b in zip(seen, seen[1:])} == {HOT_LOOP}, sorted({b - a for a, b in zip(seen, seen[1:])})

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
