# Program cartridge: the menu, BASIC, Snake and Pi

Built by `python -I build.py --beta`, which produces three blueprints: CORE 1 of 3, CONSOLE 2 of 3 and CARTRIDGE 3 of 3. `--no-cartridge` gives a two-module beta whose menu says `NO CARTRIDGE`, and `--cartridge FILE` builds a different cartridge into the third module. The pinned default build has no cartridge, and its blueprints are the same as before this work. CPU clock changes are described in [fast-cpu.md](fast-cpu.md). Nothing here has been loaded in the game: all times are simulator runs at a nominal 40 updates per second.

## What it is

The CPU's firmware (`firmware/menu.s`) is baked into its gates and is generic. At RESET it reads the directory on the cartridge, lists the programs and starts the one whose number is tapped. It contains no program names, counts or addresses. RESET goes back to the menu.

The standard cartridge (`cartridge/programs.json`, built into `cartridge/programs.bin`) holds BASIC (`cartridge/basic.s`), Snake (`cartridge/snake.c`) and Pi (`cartridge/pi.s`). `cartridge/demo/` is a different cartridge with different programs, which runs on the same CORE and CONSOLE, byte for byte: `cartridge/sim_demo.py` checks that.

A menu of three short names is complete 126 s after RUN, at about 4 s a character and 11.5 s between rows. A key can be tapped before that: the queue holds it, and the menu checks between rows.

## The interface

The baked ROM answers a fetch with pre-decoded fields (register selects, immediate, branch target, flags), and so does the cartridge. The seam has **112 links**, always all of them, each pin driven by a gate of its own at a fixed column of the seam (`cartridge.pins()`), so a cartridge fits any CORE of this generation. A pin nothing drives reads zero.

| Direction | Signals | Wires |
|---|---|---|
| CORE to CARTRIDGE | `cart_address` = PC bits 12..2; bit 12 is the window | 11 |
| CORE to CARTRIDGE | `cart_data_address` = computed address bits 10..0; bit 10 is the data port | 11 |
| CARTRIDGE to CORE | `sa`, `sb`, `sw` (14 each: one-hot read A, read B and write register x1..x15, no x8) | 42 |
| CARTRIDGE to CORE | `imm` (12, sign-extended by the CPU), `target` (10), `f3`, `aluop` (3 each) | 28 |
| CARTRIDGE to CORE | `legal`, `load`, `store`, `branch`, `jal`, `jalr`, `immediate`, `compare`, `static`, `key`, `nokey`, `lit` | 12 |
| CARTRIDGE to CORE | `data` (8), the byte at `cart_data_address` | 8 |

Three things used to tie a CORE to one cartridge, and are gone:

* **Analyses.** With a window open, `rom_ranges.analyze` knows nothing about any register, and `cartridge.generic_image` shows the width analyses a window that writes every register in full. So every register but x8 is 32 bits. x8 has no pins and reads as zero.
* **Pruning.** A cartridge is no longer compiled into the system. Seen from the CORE's passes its pins are opaque inputs, so nothing folds through them. `seam_interface` then turns them into gates for the export.
* **Folding** still happens per image (`cartridge.rows()`), but only inside the cartridge: it changes which rows exist, never the interface.

### Limits

1024 rows, 1024 data bytes, and no `lui`, `auipc`, shifts, `ecall`, `ebreak` or x8; `rows()` rejects those at build time. A constant that does not fit 12 bits has to be built up, or read from the data port. Word loads and stores are fine. A jump into a row must be a branch or `jal`, because folding moves rows: `jalr` is safe through a link register, and a table of addresses in the data port must be listed in the file's relocations. The `sbi` instruction (custom-0 opcode, `sbi value, offset`) stores a constant byte with no register, in one row. An interface for `lui` or shifts was not needed: BASIC's packing and unpacking of its directory became byte loads and adds, its large constants became smaller addresses, and its keyword tails became compares.

### The memory map

Everything fits a 12-bit immediate:

| Address | |
|---|---|
| 0..63 | RAM. The firmware leaves it clear |
| 256..511 | The input line, a byte at a time, through a register |
| 512..1023 | BASIC's text store, 32 addresses per line |
| 1024..2047 | The data port. Loads only, register-addressed, and they take two cycles |
| -512 + row * 16 + column | A store shows that ASCII character on the screen, in any mode, with no console transaction: rows 0..7, columns 0..11 |
| -256..-249 | The console's service commands, as before |
| -248 | A store sets the mode |
| -240..-233 | The key or line status (-240) and the next character (-236), as before |

### Modes

The mode is set by the firmware from the directory, or by a program. It no longer follows the PC. A change of mode blanks the screen in one console cycle and drops any waiting line.

| Mode | Value | |
|---|---:|---|
| console | 0 | Keys go to the line buffer and echo, with all the service commands. A register-addressed load takes two cycles |
| tap | 1 | Single keys from the queue, no echo, no cursor. A register-addressed load takes two cycles |
| board | 3 | As tap, and the board map below. Entry fills the board with `.` and draws `00 SCORE` |

## The cartridge file

```
offset  size
0       4     "RVCT"
4       2     data bytes, D
6       2     code words, W
8       4     length of the JSON, J
12      D     the data port's image, starting with the directory
12+D    4W    RV32E code, linked for 0x1000, unfolded
12+D+4W J     {"relocations": [...], "board": [[first, end], ...], "groups": [...]}
```

* `relocations` are offsets in the data of 16-bit little-endian code addresses. Rows move when they fold, and these move with them.
* `board` are the word ranges of programs in board mode, the only ones that may use its folds.
* `groups` are `{"key", "title", "program", "colour", "code": [[first word, end]...], "data": [[first byte, end]...]}`, for the gates: see below.

`--cartridge` also takes a bare RV32E binary, which becomes one program in board mode.

### The directory

The first page of the data port, in rows of 16 bytes, which is the screen's own shape:

| Row | Columns 0..11 | Column 12 | 13 | 14 | 15 |
|---|---|---|---:|---:|---|
| 0 | title, ASCII, zero-padded | magic, 0xC7 | number of programs | | |
| n | the text of the line, by convention `n NAME` | mode | entry low | entry high | |

Up to seven programs fit under the title. Without the magic, as when no cartridge is attached, the menu says `NO CARTRIDGE`.

### Making one

```
python -I cartridge/make_cartridge.py cartridge/demo/demo.json -o demo.bin --blueprint <folder>
```

The manifest lists the programs, each with a `name`, a `mode` (`console`, `tap` or `board`), an optional `colour`, and a `source` (assembly for `accelerated/isa.py`, position independent) or a `binary` (flat RV32E, with `symbols` for its parts: `compile.py` writes `snake.groups.json` from `GROUP(name)` marks in the C). In assembly, `; @group key Title` starts a part, and `; @data label "TEXT" 1 2` puts bytes in the data port with `label` their address. `make_cartridge.py`'s docstring has the rest. `--blueprint` needs nothing from the CORE and CONSOLE.

### What a program may assume

1. RAM bytes 0..63 and board rows 4..6 are zero, from RESET. The menu touches neither.
2. The screen has just been blanked, and it is in the mode the directory gives.
3. No register holds anything useful. Exit is by RESET only.
4. In console mode: the input index still counts the key that chose the program, so start with an acknowledge (`sb x0,-250(x0)`), which also drops anything typed before it. A line's first characters can be read as soon as it is ready. The console's cursor is on row 2, column 1, where page 0's prompt puts it, but the prompt itself is the program's to draw.

## Grouping

Each part of each program has its own row decoders, data decoders and field ORs, tagged `@cart_<part>` in their labels, so the layout builds a district per part with a sign, stacked per program ([layout.md](layout.md)). The ORs merge at the seam pins, which is where the sign OUTPUT BUS stands. Address drivers are shared, in ADDRESS.

The standard cartridge has 850 rows from 978 words and 264 data bytes. Its 1812 logic blocks and 20,970 wires make a 61.5 kB packet (Snake alone was 182 blocks and 4 kB). About 500 of the blocks are the groups' own ORs, which a single set of field ORs would not need, and they add one gate to every path through the cartridge. Row decoders are flat ANDs of 11 address literals, to keep the decode shallow.

| Program | Part | Rows | Logic blocks |
|---|---|---:|---:|
| | ADDRESS | | 98 |
| | DIRECTORY | | 39 |
| TBASIC | BASIC START | 19 | 33 |
| | BASIC DIRECT | 23 | 52 |
| | BASIC STATEMENT | 24 | 59 |
| | BASIC COMMANDS | 216 | 280 |
| | BASIC EDITOR | 96 | 152 |
| | BASIC EXPRESSION | 47 | 93 |
| | BASIC FAST PATH | 43 | 84 |
| SNAKE | SNAKE SETUP | 9 | 16 |
| | SNAKE MOVE | 7 | 17 |
| | SNAKE FOOD | 18 | 42 |
| | SNAKE OVER | 4 | 8 |
| PI | PI SETUP | 58 | 292 |
| | PI SERIES | 77 | 122 |
| | PI ARITHMETIC | 118 | 177 |
| | PI DISPLAY | 71 | 136 |

## Folded rows

The cartridge builder folds these into one single-cycle row. In any program:

* `lb t,-240(zero); blt/bge t,zero,target` branches on the ready flag directly.
* `addi d,a,-K; beq/bne d,zero,target` compares `a` with `K` in the same row as the subtraction. K reads as port B: a term of the port's own OR, so it adds no delay. Comparing through the operand mux did, and that was enough to break signed comparisons at 16,2,8,2.
* Any row that is not a jump or an input load, then `j target`, jumps as well. It sets both key fields, which together mean "always take the target".

Only in board mode, because they rely on its memory path:

* `lb t,0(x); sb r,0(x)` is a swap: the CPU reads during capture and writes during commit.
* `sb r,0(x); add x,x,r` stores through x and uses the ALU for the add in the same cycle.
* `add zero,p,zero; lb r,-234(zero); j target`, the steering row.

## TBASIC

`cartridge/basic.s` is the same language: every screen of the 14-command benchmark matches, and so does every screen of a longer session of 104 commands covering all the keywords, errors, 16 resident lines and a seventeenth refused, editing and deleting lines, and RUN with GOTO and IF, compared one by one with the previous build. The exception is `S` alone, which used to start Snake and is now an error like any other unknown word.

It runs in console mode, so the line buffer, echo and service commands work as they always have. The console's page 0 holds no text now, only the prompt, so BASIC draws `TBASIC+` and `16 LINES` itself, at its start and after `C`. Page 1, the help screen, is still text in the CONSOLE. It is the one thing about a program left there: drawing it from the cartridge would take about 24 s more per `H`, and any other cartridge can ignore it.

Baked fusion lanes and branch runs do not apply to cartridge rows, which would make BASIC slower. To offset that, it does its set-up between lines, after the last character is drawn rather than after the next ENTER, compares with immediates in one row, and tests `?` first.

| Command | Before | Now |
|---|---:|---:|
| `H` | 34.25 | 44.25 |
| `?2+3` | 24.88 | 29.88 |
| `A=32` | 32.38 | 38.00 |
| `B=8` | 29.25 | 32.38 |
| `?A-B` | 34.88 | 42.38 |
| `?123+45-6` | 44.25 | 59.25 |
| `10 ?A` | 56.12 | 71.75 |
| `20 B=B+1` | 67.38 | 87.38 |
| `30 ?B` | 68.00 | 84.88 |
| `LIST` (3 lines) | 200.50 | 210.50 |
| `RUN` (3 lines) | 192.38 | 205.50 |
| `NEW` | 53.62 | 49.88 |
| `C` | 36.12 | 56.12 |
| `?7` | 19.88 | 21.12 |
| **Total** | **896.5** | **1013.3, 13% slower** |

`bench_commands.py` taps the key as soon as the line starts to appear, and from there to the prompt takes 60.6 s, of which BASIC's own set-up is 31 rows (the directory's 15 slot numbers and the banner), about 26 s.

## Snake

`cartridge/snake.c` compiles to the same 46 words as before, from the same source apart from part marks. It is unchanged in play: board, keys, wrap, score and bottom row.

**The board map.** In board mode, register-addressed loads and stores use the low seven address bits. Row = address / 16, column = address & 15. Rows 0..6, columns 0..11 are the 84 cells. A store to a cell also writes the matching screen cell during its own commit phase, over a direct wire, without stalling. Value 0 shows as `.`, 2 as `+`, 4 as `Q`, anything else as `O`.

* **Everything off the board is a wall.** Register-addressed loads from hidden columns 12..15 and from row 7 return 3, and stores there do nothing.
* **Where the cells are.** Rows 0..3 are the CPU's 64-byte RAM. Rows 4..6 are 36 bytes of their own, reachable only by register-addressed accesses in board mode.
* **Static accesses reach the RAM bytes as they are**, at addresses 0..63, hidden columns included. The hidden-column bytes are free for a program's own variables, apart from those below that the console watches (13, 14 and 29).
* **Bottom row.** A static store to address 13 or 14 writes a binary digit to column 0 or 1. A static store of 5 or 6 to address 29 writes ` YOU LOST` or ` YOU WON` from column 3 in one store, over the word SCORE: `07  YOU LOST`.
* **Keys are single taps** from a 16-key queue, and need no ENTER. They bypass the console's line and its clock, as they do in tap mode. Status bit 7 (load -240) says a key is at the head of the queue, loading -236 takes it, and the queue pops as soon as the take reaches it, with no acknowledge transaction. The next queued key is ready within one move (198 updates), which is all that has been measured; a program polling faster than that has not been tested. All 43 keys arrive, including ENTER (13) and BKSP (8), as ASCII. Two keys pressed on the same update are discarded as a chord.
* **Key echo.** Column 11 of the bottom row shows the key at the head of the queue about 1.3 s after the press, before the move it steers.
* **The steering row** takes a waiting key and picks the new direction, which is the key's if it is W, A, S or D and does not reverse the direction `r` holds. It then writes `r` with what to add to `p` to go one cell that way, which at an edge is the whole way across (11 or 96, with the sign). So a key costs nothing and the head never meets a wall.

12x7 board, one byte per cell, row stride 16. Empty is 0, so testing a cell is one branch. A body cell holds the step to the next body cell, so moving the head is one add and the tail follows by adding what it reads. The head cell holds the head mark and receives the step as the head leaves it. All four edges wrap, because of what the steering row writes to the step register.

A move is eleven RV32E instructions in six rows. **That is the same whichever key is read, or none, and at an edge.** Only eating costs more: 12 more rows, about 10 s, plus 3 rows (2.5 s) for each address the food search rejects, and 3 rows (2.5 s) when the score carries. The food search steps by 53 addresses, which visits all 128 addresses and skips anything nonzero. On a nearly full board it can take up to 128 tries, about five minutes, with the game paused.

**Game over.** The swap at the new head position returns what was in that cell. Food means grow. Anything else is the snake's own body, so the program stores LOST and jumps to itself. Food number 82 fills the board, so the program stores WON and does the same. The CPU keeps running at full clock on that one jump, with the last board on the screen. RESET is the only way out.

| | Before this work | Now |
|---|---:|---:|
| Load, key to first board | 24.4 s, from ENTER at the BASIC prompt | **25.1 s**, from the menu key with the list finished |
| One move | 4.95 s | **4.95 s** |
| Rows | 41 | 38 |

Of the load, 16 s is the menu reading the directory entry and blanking the screen, and the rest Snake's own first rows. A key tapped while the list is still drawing waits for the end of the row: `sim_snake.py` taps as soon as the program's line starts to appear, and gets 43.7 s.

### Key handling

Measured with `cartridge/sim_keys.py` at 16,2,8,2 before this work: 300 single taps across ten phases of the move and five press lengths, all registered. Latency depends only on where in the move the tap lands, falling steadily from 9.4 s just after the cut-off to 4.9 s just before it, unless the move eats. On the final build a shorter run of the same tool gives the same figures at each phase it tried (4.92 s, 7.62 s and 5.38 s for phases 0, 90 and 180), and plans tapped ahead are followed. Same-update chords are still discarded, which is what the keyboard has always done. A tap that reverses the current direction, or is not a steering key, uses up its place in the queue, so the turn behind it waits one move.

## Pi

`cartridge/pi.s`, in tap mode. Machin's formula, 16 atan(1/5) - 4 atan(1/239), in fixed point with base 100 bytes. With no multiply or divide and about 0.8 s per row, a digit spigot would take hours for its first digits. Dividing by 25 here is multiplying by 4 and moving one byte along, and long division uses repeated subtraction for the tens of each byte and four binary steps for the units. Zero bytes at the top of a term are skipped, so later terms cost less. The next term to add is whichever series has the larger one.

It runs four passes, at 4, 7, 10 and 13 fraction bytes, each starting again. The last pass alone takes 187 minutes, so a single pass would finish in about half the time, but the first passes are what give digits within minutes.

**Only proven digits are shown.** Both series alternate with falling terms, so what is left of a series is at most its next term. With f the first nonzero byte of that term's T, plus one if T's first byte is under the next divisor, the two series' remainders are under 2 units of byte f-1 of the sum S. f is capped at L-1, which leaves rounding (under 2.1 units in the last place per term) far below that. If byte f-1 is in 3..96, adding or taking away that error cannot change any byte above it, so those bytes are proven. If it is not, the next byte up is tried. So digits arrive two at a time.

The screen: row 0 is the title, rows 1..4 the proven digits, row 5 `NOW` and the current estimate during the first pass, row 6 the pass and the sum of the two divisors, row 7 `SURE` and the count, with `DONE` at the end. A proven digit is never taken back: a new pass only adds to the screen when it passes what is there. `python -I cartridge/test_pi.py` checks every state of that screen against exact digits. `cartridge/sim_pi.py` does the same at logic level.

| Decimals proven | After |
|---:|---:|
| 2 (3.14) | 20.4 min |
| 4 | 62.3 min |
| 8 | 83.1 min |
| 12 | 193.8 min |
| 14 | 199.1 min |
| 18 | 379.8 min |
| 20 (3.14159265358979323846) | 396.7 min |
| finished, `DONE` | 6.67 hours |

The title is up about 25 s after the key, and `NOW 3.2` less than a minute after that. `NOW` then gives 3.15 in about 10 minutes and 3.1405 in about 17, before anything is proven. The limit is the 64-byte RAM: four arrays of 14 bytes and eight variables. A cartridge whose program ran in board mode would have 36 more bytes.

## Sizes and clock

CORE and CONSOLE blueprints are byte for byte the same with this cartridge, with the bare Snake binary, and with none, which is the point of the fixed seam. Moving BASIC out takes about 7900 gates off the CORE, even with every register now 32 bits, and the import limit (524,288 bytes) is no longer a problem there.

| Module, districts layout | Before: logic blocks, packet | Now: logic blocks, packet | Room left |
|---|---:|---:|---:|
| CORE | 25,281, 534,498 (over the limit) | 17,399, 364,071 | 160,217 |
| CONSOLE | 21,291, 494,450 | 21,471, 495,741 | 28,547 |
| CARTRIDGE | 182, 4,735 | 1,812, 61,489 | 462,799 |

The fixed seam costs 112 links instead of 62, and 7 more on the CONSOLE seam. The console's own growth is the larger mirror and the second mode bit. `--layout cube` makes 344,593, 475,845 and 54,723.

| CPU timers | Updates per cycle | BASIC (14 commands) | Snake | Pi |
|---:|---:|---|---|---|
| **16/2/8/2 (shipped)** | 33 | correct | correct, a played game to 45 food and a perfect game to 82 | correct to 20 decimals |
| 15/2/8/2 | 32 | correct | correct, a played game to 45 food | correct to 20 decimals |
| 14/2/8/2 | 31 | correct | **fails** after 9 food: the PC goes astray | **fails**: stops after `NOW 3.2` |

So the margin at the shipped clock is what it was for Snake, and the default clock has not changed. BASIC alone runs correctly at 14, and Pi and the longest Snake paths set the limit.

## Physical build

In `--beta` builds every screen pixel has a block plate behind it, and the keyboard stands well back from the screen, joined to it by one-wide rails with RUN, RESET and the manual clock switches on them beyond the keys (`docs/layout.md`).

The game's part definitions say switches and buttons attach only through their back face, which points down in these orientations, and most had air there. The build checks each module with those rules (`loose_parts` in `accelerated/supports.py`) and fails if a part is unattached. Signs are assumed to attach only through their back as well, because they are not in the game's own definitions. None of this has been loaded in the game yet.
