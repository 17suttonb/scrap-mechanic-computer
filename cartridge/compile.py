"""Compile snake.c to the flat RV32E cartridge image (snake.bin, snake.lst).

Needs a C compiler that targets RISC-V: `pip install ziglang` (used here as `python -m ziglang cc`).
The committed snake.bin is what the build consumes, so the compiler is only needed to change the game.
"""
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rvtools import disasm, elf_text

HERE = Path(__file__).resolve().parent
CART_BASE = 0x800
ARGS = ['-target', 'riscv32-freestanding-none', '-mcpu=generic_rv32+e+reserve_x1+reserve_x8+reserve_x9', '-mabi=ilp32e', '-Os', '-fomit-frame-pointer', '-ffreestanding', '-nostdlib',
        '-fno-delete-null-pointer-checks', '-fno-pic']


def main(source=HERE / 'snake.c'):
    elf = HERE / (Path(source).stem + '.elf')
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *ARGS, '-Wl,-T,' + str(HERE / 'link.ld'), '-o', str(elf), str(source)], check=True)
    base, image = elf_text(elf)
    assert base == CART_BASE, hex(base)
    image = image.rstrip(bytes(1))
    image += bytes(-len(image) % 4)
    (HERE / (elf.stem + '.bin')).write_bytes(image)
    (HERE / (elf.stem + '.lst')).write_text(''.join(f'{base + i:04x}: {int.from_bytes(image[i:i + 4], "little"):08x}  {disasm(int.from_bytes(image[i:i + 4], "little"), base + i)}\n' for i in range(0, len(image), 4)))
    print(f'{elf.stem}: {len(image) // 4} words at {base:#x}')


if __name__ == '__main__':
    main(*sys.argv[1:2])
