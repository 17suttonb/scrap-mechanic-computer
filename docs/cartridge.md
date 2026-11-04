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
| CARTRIDGE to CORE | `ra`, `rb`, `rd` (4 each), `imm` (12, sign-extended by the CPU), `target` (10), `f3`, `aluop` (3 each), `legal`, `load`, `store`, `branch`, `jal`, `jalr`, `immediate`, `compare`, `static` | 50 |

Bits that no instruction in the image ever sets are constant, and pins that share a driver share a wire, so the
seam actually carries **51 links** (the console seam is unchanged at 46). The cartridge itself is 371 logic blocks
(10 KB packet) for the 210-word Snake image; the window holds up to 512 words.

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

CORE packet 460,467 to 481,409 B (limit 524,288; the cartridge decode lives in the CORE as RX gates), CONSOLE
266,198, CARTRIDGE 10,139.

## Snake (`cartridge/snake.c`)

12x4 board, one byte per cell, row stride 16. The board is also the screen buffer and the body list: `.` empty,
`+` food, `O` head, and each body cell holds the letter W/A/S/D for the direction to the next cell, so a frame is a
straight copy of RAM and the tail advances by reading its own cell. Columns 12..15 are walls. Everything stays in
registers: no stack, no shifts, no word accesses.

Controls: type W, A, S or D then ENTER (a line is how the console delivers input). The snake keeps its heading
otherwise, and a 180-degree turn is ignored. Walls and the body end the game and the last board stays up.
Moving into the cell the tail is just leaving counts as a collision.

Build the image with `cartridge/compile.py` (needs `pip install ziglang`; the committed `snake.bin` is what the
build uses). `cartridge/test_snake.py` plays the binary on an RV32E simulator with the console memory map
(`cartridge/rvtools.py`): steering, growth, walls, reversal rules, and the cost per move.

## Verification

* Host RV32E simulator: all of the above pass; 125 instructions per move in steady state, 212 words of code.
* Logic-Block-Simulator on the exported joined blueprint (`cartridge/sim_snake.py`): see the results table below.
* Not done: any test in the game itself. The cartridge adds about three gate delays to the instruction decode path
  and that has only been checked in the simulator, which models gate behaviour but not the real timer ring.

## Speed

The CPU executes one instruction per 49 updates and every screen character is a console transaction, so Snake is
very slow on this machine. The simulator figures below translate at the game's 40 updates per second.
