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

Bits that no row ever sets are constant, and pins that share a driver share a wire, so the seam carries **62 links** for
Snake. The cartridge is 233 logic blocks (6 KB packet); the window holds up to 512 rows.

Limits: a cartridge cannot use `lui`, `auipc`, shifts, `ecall`/`ebreak`, and must not write `x8` (the firmware's fixed base
register). `rows()` rejects those at build time. Branch targets must stay inside the window. **A cartridge must be the
image the CPU was built with**, because the CPU's register-width analysis covers it; a different program needs a rebuild.

## Cartridge mode

Cartridge mode is on while the PC is in the window. It is what makes a game fast, because the console's normal path
costs a bus transaction per character.

* **The board is the screen.** Register-addressed cartridge loads and stores reach the RAM through the low six address
  bits, so positions wrap. A store also writes the matching screen cell during its own commit phase, over a direct wire,
  without stalling: row = address / 16, column = address & 15. Value 0 shows as `.`, 2 as `+`, 4 as `Q`, anything else as `O`.
  Only the top four screen rows are mapped, because the RAM has 64 bytes.
* **Score and result lines.** On a screen of at least eight rows, entry also draws `SCORE 00` on row 5. Stores to addresses 13 and 14, which are not on the board, write its two digits. Storing 5 or 6 (address 29 in Snake, which is not on the board either) writes `YOU LOST` or `YOU WON` on row 7, all at once, in one store.
* **Entry clears the screen** in one console cycle, by the same parallel page write the boot banner uses.
* **Keys are single taps.** They are not echoed and need no ENTER. Status bit 7 says a key is waiting, reading -236 takes
  it, and no acknowledge transaction is needed. One key waits at a time: a tap made while the last key is still waiting is ignored.
  A key seen on the screen during a move is read after the next move.
* **Folded rows.** The cartridge builder folds three instruction pairs into one single-cycle row:
  `lb t,0(x); sb r,0(x)` is a swap (the CPU reads during capture and writes during commit),
  `lb t,-240(zero); blt/bge t,zero,target` branches on the ready flag directly, and
  `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle.

## Firmware entry

`S` alone is detected on the direct path, before keyword dispatch. The firmware clears RAM and sets the wall bytes with
static stores, which the CPU executes in parallel bundles (about four cycles against 400 instructions for a loop), puts
the starting state in `a0..a4` in one bundle, and jumps. It does not acknowledge the input line: entering cartridge mode
drops it.

The cartridge's starting registers are real C arguments: `_start(head, tail, step, food, food_mark, head_mark)`.

## Snake (`cartridge/snake.c`)

12x4 board, one byte per cell, row stride 16. Empty is 0, so testing a cell is one branch. A body cell holds the step to
the next body cell, so moving the head is one add and the tail follows by adding what it reads. The head cell holds the
head mark and receives the step as the head leaves it.

All four edges wrap. Top and bottom wrap through the six-bit addressing. Columns 12 and 15 hold wall bytes that are never
shown: stepping onto one takes a slow path that moves the head to the other side and gives the cell it left a step of 11,
so the tail crosses with no extra code.

A move is ten RV32E instructions in six rows: store-and-add, swap, `bne`, swap, `add`, key test and loop. Reading a key adds
14 to 17 cycles once (`S` and `D` cost the most), about 14 s, and a side wrap adds about 13 cycles, about 11 s.

**Score.** The score is two ASCII digits in columns 13 and 14 of row 0, and those bytes are also what the screen shows. Eating adds one and carries. Those hidden columns are `'0'` in every row, set by the firmware, so the food search skips them. (They used to be 0, which put the seventh food off the board, where the player could not reach it.)

**Game over.** The swap at the new head position returns what was in that cell. Zero means free. Otherwise the slow path
checks for a wall (wrap, then look again), then for food (grow). Anything else is the snake's own body, so the program
writes `YOU LOST` and jumps to `for (;;) { }`. Moving into the cell the tail is just leaving counts, because the head is checked first.

**Winning.** The snake starts 2 long and the board has 48 cells, so food number 46 fills it. The program checks for that score before looking for a free cell, writes `YOU WON` and spins the same way.

The CPU does not halt or fault. It keeps running at full clock, re-executing that one jump for ever, with `HALTED` and
`FAULTED` off and the PC on that row, and the console stays in cartridge mode. The screen keeps the last board with the
head drawn over the cell it hit. Keys are ignored; an unread key leaves BUSY on. RESET is the only way out.

The food search tries cells in steps of 17, so on a nearly full board it can take over 200 cycles, around three minutes, with the game paused.

Build the image with `cartridge/compile.py` (needs `pip install ziglang`; the committed `snake.bin` is what the build uses).
`cartridge/test_snake.py` plays the binary on an RV32E simulator with cartridge mode's memory map (`cartridge/rvtools.py`):
steering, growth, wrap on all four edges, body collision, reversal rules and the cost per move.

`cartridge/sim_snake.py` plays an exported blueprint in the Logic-Block-Simulator and reports the load time.

## Speed

| | First version | Now |
|---|---:|---:|
| Load, ENTER to first board | about 17 minutes | **about 21 s** |
| One move | about 287 s | **about 5 s** (6 cycles at 33 updates) |
| Cartridge size | 206 words | 67 rows |
| Steering | key plus ENTER | one tap |

Times are at the nominal 40 updates per second, from simulated runs, not from the game.

A one-letter transient glyph can show for a few updates while a screen cell changes. A carry writes the tens digit first, so `19` shows for about 1.6 s between `09` and `10`.

## Physical build

In `--beta` builds every screen pixel has a block plate behind it, and the keyboard stands well back from the screen, joined to it by one-wide rails with RUN, RESET and the manual clock switches on them beyond the keys (`docs/layout.md`).

The game's part definitions say switches and buttons attach only through their back face, which points down in these orientations, and most had air there. The build now checks each module with those rules (`loose_parts` in `accelerated/supports.py`) and fails if a part is unattached. Signs are assumed to attach only through their back as well, because they are not in the game's own definitions. None of this has been loaded in the game yet.
