"""Run the Pi program on the host model and check every state of the screen against the true digits of pi, and the
value held in RAM at the end of each pass.

Usage: python -I cartridge/test_pi.py [--image cartridge/programs.bin] [--name PI]
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host import Computer, cart  # noqa: E402
from rvtools import true_pi  # noqa: E402

DIGITS = 60


def entry_of(image, name):
    data = cart.relocated_data(image, cart.rows(image).new_pc)
    for n in range(1, data[13] + 1):
        row = data[n * cart.ROW_STRIDE:(n + 1) * cart.ROW_STRIDE]
        if row[:12].rstrip(b'\0').decode().split(' ', 1)[1] == name:
            return row[12], row[13] | row[14] << 8
    raise SystemExit(f'no program called {name}')


def proven(screen):
    """The digits on rows 1..4, as text."""
    return ''.join(screen[1:5]).replace(' ', '')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--image', type=Path, default=Path(__file__).resolve().parent / 'programs.bin')
    p.add_argument('--name', default='PI')
    a = p.parse_args()
    image = cart.read_image(a.image.read_bytes())
    mode, entry = entry_of(image, a.name)
    assert mode == cart.MODE_TAP
    m = Computer(image, _row_to_word(image, entry))
    digits = true_pi(DIGITS)
    last, last_now, shown, printed = '', '', 0, {}
    steps = 0
    while not m.spin and steps < 400_000_000:
        m.step()
        steps += 1
        if steps % 64 == 0 or m.spin:
            screen = m.text()
            value = proven(screen)
            if value != last:
                # never a wrong digit, and never a digit taken back
                assert value.startswith(last), (value, last)
                if len(value) > 2:
                    assert value[:2] == '3.' and value[2:] == digits[:len(value) - 2], (value, digits)
                last = value
                count = max(0, len(value) - 2)
                if count not in printed:
                    printed[count] = m.seconds()
                    print(f'{m.seconds() / 60:8.1f} min  {count:3d} digits proven  {value}', flush=True)
            now = screen[5]
            if now != last_now:
                last_now = now
                print(f'{m.seconds() / 60:8.1f} min  {now}  {screen[6]}  {screen[7]}', flush=True)
    assert m.spin, 'did not finish'
    assert 'DONE' in m.text()[7]
    print(f'finished at {m.seconds() / 3600:.2f} hours: {m.text()}')
    print('OK')


def _row_to_word(image, entry):
    """Host programs run on the unfolded words: back from the folded entry address to its word."""
    folded = cart.rows(image)
    return next(old for old, new in folded.new_pc.items() if new == entry)


if __name__ == '__main__':
    main()
