# Program cartridge and Snake

Built by `python -I build.py --beta`, which produces three blueprints: CORE 1 of 3, CONSOLE 2 of 3 and CARTRIDGE
3 of 3. `--no-cartridge` gives a two-module beta, `--cartridge FILE` builds a different program into the third module.
The pinned default build is unchanged. CPU clock changes are described in [fast-cpu.md](fast-cpu.md).

## What it is

The CPU's firmware (the BASIC interpreter) is baked into its gates. A **cartridge** is a second, separate ROM blueprint
holding a compiled program. Typing `S` then ENTER at the BASIC prompt jumps to address `0x800`, which the CPU fetches
from the cartridge instead of from its own gates. RESET goes back to BASIC.

The shipped cartridge is Snake, written in C (`cartridge/snake.c`) and compiled for RV32E.

## The cartridge memory block

`accelerated/cartridge.py` turns the flat binary into a gate netlist. The baked ROM answers a fetch with pre-decoded
fields (register selects, immediate, branch target, flags), and so does the cartridge:

| Direction | Signals | Wires |
|---|---|---|
| CORE to CARTRIDGE | `cart_address` = PC bits 11..2 | 10 |
| CARTRIDGE to CORE | `sa`, `sb`, `sw` (15 each: one-hot read A, read B and write register x1..x15), `imm` (12, sign-extended by the CPU), `target` (10), `f3`, `aluop` (3 each), `legal`, `load`, `store`, `branch`, `jal`, `jalr`, `immediate`, `compare`, `static`, `key`, `nokey` | 84 |

Bits that no row ever sets are constant, and pins that share a driver share a wire, so the seam carries **58 links** for
Snake. The cartridge is 185 logic blocks (4 KB packet); the window holds up to 512 rows.

Limits: a cartridge cannot use `lui`, `auipc`, shifts, `ecall`/`ebreak`, and must not write `x8` (the firmware's fixed base
register). `rows()` rejects those at build time. Branch targets must stay inside the window. **A cartridge must be the
image the CPU was built with**, because the CPU's register-width analysis covers it; a different program needs a rebuild.

## Cartridge mode

Cartridge mode is on while the PC is in the window. It is what makes a game fast, because the console's normal path
costs a bus transaction per character.

* **The board is the screen.** Register-addressed cartridge loads and stores reach the RAM through the low six address
  bits, so positions wrap. A store also writes the matching screen cell during its own commit phase, over a direct wire,
  without stalling: row = address / 16, column = address & 15. Value 0 shows as `.`, 2 as `+`, anything else as `O`.
* **Entry clears the screen** in one console cycle, by the same parallel page write the boot banner uses.
* **Keys are single taps.** They are not echoed and need no ENTER. Status bit 7 says a key is waiting, reading -236 takes
  it, and no acknowledge transaction is needed.
* **Folded rows.** The cartridge builder folds two instruction pairs into one single-cycle row:
  `lb t,0(x); sb r,0(x)` is a swap (the CPU reads during capture and writes during commit), and
  `lb t,-240(zero); blt/bge t,zero,target` branches on the ready flag directly.

## Firmware entry

`S` alone is detected on the direct path, before keyword dispatch. The firmware clears RAM and sets the wall bytes with
static stores, which the CPU executes in parallel bundles (about four cycles against 400 instructions for a loop), puts
the starting state in `a0..a4` in one bundle, and jumps. It does not acknowledge the input line: entering cartridge mode
drops it.

The cartridge's starting registers are real C arguments: `_start(head, tail, step, food, food_mark)`.

## Snake (`cartridge/snake.c`)

12x4 board, one byte per cell, row stride 16. Empty is 0, so testing a cell is one branch. A body cell holds the step to
the next body cell, so moving the head is one add and the tail follows by adding what it reads. Columns 12 and 15 hold
wall bytes. The left and right walls and the body end the game, the top and bottom edges wrap, and the last board stays
up. Moving into the cell the tail is just leaving counts as a collision.

A move is nine RV32E instructions in six rows: `add`, swap, `bne`, swap, `add`, key test and loop. A turn adds up to
about ten rows once, for the key comparisons.

Build the image with `cartridge/compile.py` (needs `pip install ziglang`; the committed `snake.bin` is what the build uses).
`cartridge/test_snake.py` plays the binary on an RV32E simulator with cartridge mode's memory map (`cartridge/rvtools.py`):
steering, growth, wrap, walls, body collision, reversal rules and the cost per move.

`cartridge/sim_snake.py` plays an exported blueprint in the Logic-Block-Simulator and reports the load time.

## Speed

| | First version | Now |
|---|---:|---:|
| Load, ENTER to first board | about 17 minutes | **about 22 s** |
| One move | about 287 s | **about 5 s** (6 cycles at 33 updates) |
| Cartridge size | 206 words | 43 rows |
| Steering | key plus ENTER | one tap |

Times are at the nominal 40 updates per second, from simulated runs, not from the game.

A one-letter transient glyph can show for a few updates while a screen cell changes.
