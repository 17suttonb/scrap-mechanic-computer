# Program cartridge and Snake

Branch: `snake-rom` (from `input-queue`). Built by `python -I build.py --beta`, which now produces three
blueprints: CORE 1 of 3, CONSOLE 2 of 3 and CARTRIDGE 3 of 3. `--no-cartridge` gives the previous two-module
beta, `--cartridge FILE` builds a different program into the third module. The pinned default build is unchanged.

## What it is

The CPU's firmware (the BASIC interpreter) is baked into its gates. A **cartridge** is a second, separate ROM
blueprint holding a compiled program. Typing `S` then ENTER at the BASIC prompt acknowledges the line and jumps
to address `0x800`, which the CPU fetches from the cartridge instead of from its own gates. RESET goes back to BASIC.

The shipped cartridge is Snake, written in C (`cartridge/snake.c`) and compiled for RV32E.

## A) The cartridge memory block

`accelerated/cartridge.py` turns the flat binary into a gate netlist. The baked ROM answers a fetch with
pre-decoded fields (register numbers, immediate, branch target, flags), and so does the cartridge:

| Direction | Signals | Wires |
|---|---|---|
| CORE to CARTRIDGE | `cart_address` = PC bits 11..2 | 10 |
| CARTRIDGE to CORE | `sa`, `sb`, `sw` (15 each: one-hot read A, read B and write register x1..x15), `imm` (12, sign-extended by the CPU), `target` (10), `f3`, `aluop` (3 each), `legal`, `load`, `store`, `branch`, `jal`, `jalr`, `immediate`, `compare`, `static` | 82 |

Bits that no instruction in the image ever sets are constant, and pins that share a driver share a wire, so the
seam actually carries **62 links** for Snake (the console seam is unchanged at 46). The cartridge itself is 407 logic
blocks (10 KB packet) for the 206-word Snake image; the window holds up to 512 words.

The register selects are one-hot rather than 4-bit numbers on purpose. With encoded numbers the CPU had to decode
them after the seam, which added enough delay that a `bltu` loop in the cartridge latched a wrong next PC at the stock
20/6/12/6 clock (it worked with a 22-tick capture phase). One-hot selects remove that decode and the stock clock passes.

Limits: a cartridge cannot use `lui`, `auipc`, shifts, `ecall`/`ebreak`, and must not write `x8` (the firmware's fixed
base register). `rows()` rejects those at build time. Branch targets must stay inside the window.

## B) The CPU changes

* **Fetch.** The generator is given the firmware padded to `0x800` followed by the cartridge image. The baked decoder
  is built from the firmware rows only (`compact_core.metadata` blanks the cartridge window), while the register-width
  and value-range analyses (`analysis_metadata`: `zero_bits`, `layouts`, `rom_ranges.analyze`) still see the real
  cartridge code. The registers the cartridge writes are therefore kept wide enough, and the firmware netlist is
  otherwise the same. **A cartridge must be the image the CPU was built with**; a different one needs a rebuild.
* **Pins.** `cartridge.cpu_source` adds the `cart_*` ports to the finished CPU HDL and ORs them into every decoder
  output (`d_*`, `select_a/b/write`). Without a cartridge the fields read 0, so the first fetch is an illegal
  instruction and the CPU traps with the CPU ERR screen.
* **RAM at address 0.** `region_ram` also matches `0..63`, so the cartridge sees the 64-byte RAM at 0 (the firmware
  keeps using 4096). That lets the C code address RAM with a plain `lbu rd, n(zero)`, with no `lui`.
* **Firmware.** `S` on its own: `bne x4,x0,error; sb x0,-250(x0); j 2048`.
* **Split and layout.** `split.py` owns `Cartridge ...` nodes in a third domain; pins that share a driver share one
  link. `layout.py` puts the cartridge module beyond the CORE (seam row of blocks at `y=79/80`, own WELD pads) and
  keeps the console seam as it was. Compaction and sharing never fuse a gate into a cartridge output pin
  (`seam_drivers`), because the split needs each pin to stay a single-parent buffer.

Packets: CORE 460,467 to 478,638 B (limit 524,288; the cartridge's receiving gates and the select merges live in the
CORE), CONSOLE 266,258 B, CARTRIDGE 9,884 B. The CPU clock is unchanged (20/6/12/6, 49 updates per instruction).

## Snake (`cartridge/snake.c`)

11x4 playfield (the 12th column is not used, see below), one byte per cell, row stride 16. The board is also the screen buffer and the body list: `.` empty,
`+` food, `O` head, and each body cell holds the letter W/A/S/D for the direction to the next cell, so a frame is a
straight copy of RAM and the tail advances by reading its own cell. Columns 11..15 are walls. Everything stays in
registers: no stack, no shifts, no word accesses (the compiler is told to avoid x1, x8 and x9).

The console has no clear-screen code (`12` reprints the boot banner) and moves the cursor to the next row as soon as
a 12th character is written. A frame is therefore a newline and four 11-character rows separated by newlines: the
terminal scrolls and the new rows replace the old ones.

Controls: type W, A, S or D then ENTER (a line is how the console delivers input). The snake keeps its heading
otherwise, and a 180-degree turn is ignored. Walls and the body end the game and the last board stays up.
Moving into the cell the tail is just leaving counts as a collision.

Build the image with `cartridge/compile.py` (needs `pip install ziglang`; the committed `snake.bin` is what the
build uses). `cartridge/test_snake.py` plays the binary on an RV32E simulator with the console memory map
(`cartridge/rvtools.py`): steering, growth, walls, reversal rules, and the cost per move.

## Verification

* Host RV32E simulator: all of the above pass; 118 instructions per move in steady state, 206 words of code.
* Logic-Block-Simulator on the exported joined three-module blueprint (`cartridge/sim_snake.py`, stock clock):
  * `?2+3` ENTER still prints `005` with the cartridge attached.
  * `S` ENTER enters the cartridge; the first board is drawn about 40,000 updates after the key (the 64-byte board is
    initialised first), `O` at column 5 of row 1, food at row 2, and the board is rewritten every ~11,500 updates with
    the head one cell further right.
  * Typing `S` ENTER during play turned the snake down at the wall; the next move into the bottom wall stopped the game
    and the last board stayed on screen, as in the host run.
  * No halt or fault flag at any point. A run of the initialisation loop with the cartridge decoding through 4-bit
    register numbers failed at stock timing, which is what led to the one-hot selects (see above).
* Not done: any test in the game itself. The cartridge adds about three gate delays to the instruction decode path
  and that has only been checked in the simulator, which models gate behaviour but not the real timer ring.

## Speed

The CPU executes one instruction per 49 updates and every screen character is a console transaction, so Snake is
very slow on this machine: about 11,500 updates per move in the simulator (118 instructions plus 47 screen writes),
which is roughly **4.8 minutes per move** at the game's 40 updates per second, and about 17 minutes from `S` ENTER to
the first board. About half of each move is the screen redraw, which cannot be reduced because the console can only
append characters. It is a working demonstration of the cartridge, not a playable game at this clock.

Simulator throughput is in BENCHMARKS.md (the three-module beta simulates about 12% slower than the previous beta
because it has about 1.3k more logic blocks).
