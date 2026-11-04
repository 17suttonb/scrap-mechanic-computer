# Program cartridge and Snake

Built by `python -I build.py --beta`, which produces three blueprints: CORE 1 of 3, CONSOLE 2 of 3 and CARTRIDGE
3 of 3. `--no-cartridge` gives a two-module beta, `--cartridge FILE` builds a different program into the third module.
The pinned default build has no cartridge. CPU clock changes are described in [fast-cpu.md](fast-cpu.md).

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

Bits that no row ever sets are constant, and pins that share a driver share a wire, so the seam carries **55 links** for
Snake (10 and 45). The cartridge is 182 logic blocks (4 KB packet); the window holds up to 512 rows.

Limits: a cartridge cannot use `lui`, `auipc`, shifts, `ecall`/`ebreak`, and must not write `x8` (the firmware's fixed base
register). `rows()` rejects those at build time. Branch targets must stay inside the window. **A cartridge must be the
image the CPU was built with**, because the CPU's register-width analysis covers it; a different program needs a rebuild.

## Cartridge mode

Cartridge mode is on while the PC is in the window. It is what makes a game fast, because the console's normal path
costs a bus transaction per character.

* **The board is the screen.** Register-addressed cartridge loads and stores use the low seven address bits. Row = address / 16, column = address & 15. Rows 0..6, columns 0..11 are the 84 cells. A store to a cell also writes the matching screen cell during its own commit phase, over a direct wire, without stalling. Value 0 shows as `.`, 2 as `+`, 4 as `Q`, anything else as `O`.
* **Everything off the board is a wall.** Register-addressed loads from hidden columns 12..15 and from row 7 return 3, and stores there do nothing. No walls are stored.
* **Where the cells are.** Rows 0..3 are the CPU's 64-byte RAM, which is why the firmware clears it. Rows 4..6 are 36 bytes of their own, reachable only by register-addressed cartridge accesses. The firmware cannot write them and RESET is the only way out of a cartridge, so they are always clear on entry.
* **Static accesses reach the RAM bytes as they are**, at addresses 0..63, hidden columns included. The hidden-column bytes are free for a program's own variables, apart from those below that the console watches (13, 14 and 29).
* **Bottom row.** On a screen of at least eight rows, entry draws `00 SCORE` on row 7. A static store to address 13 or 14 writes a binary digit to column 0 or 1. A static store of 5 or 6 to address 29 writes ` YOU LOST` or ` YOU WON` from column 3 in one store, over the word SCORE: `07  YOU LOST`.
* **Entry clears the screen** in one console cycle, by the same parallel page write the boot banner uses.
* **Keys are single taps** from a 16-key queue, and need no ENTER. They bypass the console's line and its clock. Status bit 7 (load -240) says a key is at the head of the queue, loading -236 takes it, and the queue pops as soon as the take reaches it, with no acknowledge transaction. The next queued key is ready within one move (198 updates), which is all that has been measured; a program polling faster than that has not been tested. All 43 keys arrive, including ENTER (13) and BKSP (8), as ASCII. Two keys pressed on the same update are discarded as a chord.
* **Key echo.** Column 11 of the bottom row shows the key at the head of the queue about 1.3 s after the press, before the move it steers. It needs the eight-row screen, like the rest of the bottom row.
* **The steering row.** `add zero,p,zero; lb r,-234(zero); j target` folds into one row. It takes a waiting key and picks the new direction, which is the key's if it is W, A, S or D and does not reverse the direction `r` holds. It then writes `r` with what to add to `p` to go one cell that way, which at an edge is the whole way across (11 or 96, with the sign). So a key costs nothing and the head never meets a wall. Programs that do not move something around this board can ignore it.
* **Folded rows.** The cartridge builder folds these into one single-cycle row:
  `lb t,0(x); sb r,0(x)` is a swap (the CPU reads during capture and writes during commit),
  `lb t,-240(zero); blt/bge t,zero,target` branches on the ready flag directly,
  `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle, and the steering row above.

Screens shorter than eight rows mirror only the rows they have and have no bottom row, so Snake is not playable on them.

## Firmware entry and what a program may assume

`S` alone is detected on the direct path, before keyword dispatch. The firmware clears the 64-byte RAM with 16 static stores, which the CPU executes in parallel bundles (about four cycles), and jumps to `0x800`. It does not acknowledge the input line: entering cartridge mode drops it.

That is the whole contract, and Snake needs nothing more:

1. RAM bytes 4096..4159 are zero. Rows 4..6 of the board are zero already, from RESET.
2. PC goes to `0x800`, by any path that keeps `x8` holding 4096.
3. No register needs a value. `_start` takes no arguments and sets its own six registers in six rows, which is about 5 s of the load time.

## Snake (`cartridge/snake.c`)

12x7 board, one byte per cell, row stride 16. Empty is 0, so testing a cell is one branch. A body cell holds the step to
the next body cell, so moving the head is one add and the tail follows by adding what it reads. The head cell holds the
head mark and receives the step as the head leaves it.

All four edges wrap, because of what the steering row writes to the step register. The cell the head leaves gets that step like any other, so the tail crosses with no extra code. Position registers stay on the board for the whole game.

A move is eleven RV32E instructions in six rows: store-and-add, swap, `bne`, swap, `add`, steering row. **That is the same whichever key is read, or none, and at an edge.** Only eating costs more: 12 more rows, about 10 s, plus 3 rows (2.5 s) for each address the food search rejects, and 3 rows (2.5 s) when the score carries.

**Score.** Two binary digits, at addresses 13 and 14, which are also what the screen shows. Eating adds one and carries.

**Game over.** The swap at the new head position returns what was in that cell. Zero means free. Otherwise the slow path checks for food (grow). Anything else is the snake's own body, so the program stores LOST and jumps to `for (;;) { }`. Moving into the cell the tail is just leaving counts, because the head is checked first.

**Winning.** The snake starts 2 long and the board has 84 cells, so food number 82 fills it. The program checks for that score before looking for a free cell, stores WON and spins the same way.

The CPU does not halt or fault. It keeps running at full clock, re-executing that one jump for ever, with `HALTED` and
`FAULTED` off and the PC on that row, and the console stays in cartridge mode. The screen keeps the last board with the
head drawn over the cell it hit. Keys are ignored. RESET is the only way out.

The food search steps by 53 addresses (3 rows and 5 columns), which visits all 128 addresses and skips anything nonzero, so walls need no special case. On a nearly full board it can take up to 128 tries, about five minutes, with the game paused.

Build the image with `cartridge/compile.py` (needs `pip install ziglang`; the committed `snake.bin` is what the build uses).
`cartridge/test_snake.py` plays the binary on an RV32E simulator with cartridge mode's memory map (`cartridge/rvtools.py`):
steering, growth, wrap on all four edges, body collision, reversal rules, queued and lost keys, a win at 82 and the cost per move.

`cartridge/sim_snake.py` plays an exported blueprint in the Logic-Block-Simulator and reports the load time.
`cartridge/sim_keys.py` measures key handling there; `--play N` plays a real game with checks at every move, loses it, presses RESET and starts again, and `--win` plays a perfect game.

## Key handling

Measured with `cartridge/sim_keys.py` at 16,2,8,2: 300 single taps across ten phases of the move and five press lengths from 1 to 20 updates, and pairs of taps from 3 to 800 updates apart.

| | Before | Now |
|---|---:|---:|
| Taps registered | 292 of 299 | **300 of 300** |
| Press to head turning: mean | 22.25 s | **7.28 s** |
| Fastest, slowest | 16.88 s, 35.42 s | **4.88 s, 18.93 s** |
| Slowest on a move that does not eat | about 35 s | **9.43 s** |
| Same press phase, repeated | spread of about 10 s | **within 0.1 s** |
| Two turns tapped together | 17 to 30 s apart | **4.95 s apart** |
| One move, with a key or not | 5 s, or about 19 s | **4.95 s** |

Latency now depends only on where in the move the tap lands, falling steadily from 9.4 s just after the cut-off to 4.9 s just before it, unless the move eats:

| Press, updates after the head moved | 0 | 20 | 60 | 100 | 140 | 180 |
|---:|---:|---:|---:|---:|---:|---:|
| Head turns after, s | 4.92 | 9.38 | 8.38 | 7.38 | 6.38 | 5.38 |

That breaks down as about 1.1 s from press to ready, a wait of up to one move for the steering row, and two rows for the head to appear.

Causes found, each confirmed by measurement:

1. **Keys really were lost.** The take logic tested the firmware's input stream window against the computed address and did not exclude cartridge rows. On the trace, a waiting key cleared with the CPU still in its move loop. With only that one gate fixed, the same run lost none of the taps that had lost 7. The address arithmetic says this needs the head register at -3840 to -3585, which top and bottom wrap allowed by letting it drift, so about 240 net moves upward and then about 16 moves of lost keys. That makes it rare in play: the delay was the everyday problem. Position registers no longer drift, and the gate stays for other programs.
2. **Reading a key was slow, and slower for some keys.** It took 14 to 17 extra cycles, about 14 s, in which nothing moved.
3. **Keys came through the console's clock**, taking about 2.5 s from press to ready, against about 1.1 s now.
4. **Edge moves were uneven**, adding about 11 s at a side wrap.

Same-update chords are still discarded, which is what the keyboard has always done. A tap that reverses the current direction, or is not a steering key, uses up its place in the queue, so the turn behind it waits one move.

## Speed

| | First version | 12x4 board | Now |
|---|---:|---:|---:|
| Load, ENTER to first board | about 17 minutes | 21.1 s | **24.4 s** |
| One move | about 287 s | about 5 s, 14 s more with a key | **4.95 s** (6 cycles at 33 updates) |
| Edge move | | 11 s more at the sides, nothing at top and bottom | **no extra** |
| Cartridge size | 206 words | 67 rows | 41 rows |
| Steering | key plus ENTER | one tap, one at a time | one tap, queued |

Times are at the nominal 40 updates per second, from simulated runs, not from the game.

A one-letter transient glyph can show for a few updates while a screen cell changes. A carry writes the tens digit first, so `19` shows for about 1.6 s between `09` and `10`.

## Physical build

In `--beta` builds every screen pixel has a block plate behind it, and the keyboard stands well back from the screen, joined to it by one-wide rails with RUN, RESET and the manual clock switches on them beyond the keys (`docs/layout.md`).

The game's part definitions say switches and buttons attach only through their back face, which points down in these orientations, and most had air there. The build now checks each module with those rules (`loose_parts` in `accelerated/supports.py`) and fails if a part is unattached. Signs are assumed to attach only through their back as well, because they are not in the game's own definitions. None of this has been loaded in the game yet.
