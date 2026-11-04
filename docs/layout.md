# Gate placement: the districts layout

`python -I build.py --beta` lays the logic out as a map of the machine. Wires have no length or delay in the game, so placement is free; the wiring, gate ids, kinds and states are untouched (`build` asserts `joined_edges == original_edges`). Preview without the game:

```powershell
python tools/render_layout.py <export folder> <image folder>   # needs Pillow; one PNG per module: plan with signs, view from behind, view from the front
```

`--layout cube|flat|districts` picks the placement. The default is `districts` with `--beta`, otherwise `cube`. `--flat` is `--layout flat`. Anything but `cube` without `--beta` is an unpinned variant. The pinned path (no `--beta`, no `--layout`) does not run this code and its blueprints do not change.

## How a gate finds its place (`accelerated/districts.py`)

1. **Name.** The HDL elaborator labels a gate `AND @alu:alu.c(5)`: section, then the signal being assigned (`sections.site_of`; one bit if the statement drives one bit, else the vector). Label checks elsewhere only look at prefixes and `latch `, so this changes no netlist: blueprint hashes were identical with and without it. Storage labels (`latch Q line N bit B`, `timing XOR CPU line N ...`, `PULSE line N ...`) keep their exact text because passes match on it, so their signal is read from line N of `source.vhd`, where line numbers restart for `Service` and `Terminal`.
2. **District and block.** `CORE_RULES` and `CONSOLE_RULES` are ordered regexes on the signal: the first match gives the district and a block inside it (a register number, a RAM byte, a screen cell). A signal no rule knows goes to the default for its section tag (`TAG_DEFAULT`), so new HDL lands somewhere sensible without touching this file.
3. **Unnamed gates** (feedback relays, fan-out buffers) take the district and block of most of their wired neighbours.
4. **Shape.** Columns of districts run left to right (`CORE_COLUMNS`, `CONSOLE_COLUMNS`). A column's width comes from its gate count at `TARGET_HEIGHT`, and widens until nothing is taller than twice that (up to three times the width). Depth shares follow gate counts. So more gates make wider columns and then taller buildings: there are no gate counts anywhere.

Shapes (`Spec`):

| Shape | Looks like | Used for |
|---|---|---|
| tiles (`per_row`, `gap`) | a grid, one tile per block, each growing up from z=1 | RAM bytes, registers, text lines, character cells |
| bit slices (`bits`, `street`) | one tile per bit, with a street every so many | ALU, shifter, read ports, result bus |
| `strips` | one-thick bars as long as the block needs, like a bar chart | fusion lanes, cartridge row decoders and output fields |
| `solid` | one plain building in id order | glue logic: bus control, keyboard, service and so on |

Tiles alternate between two shades of the district's colour. Heights are real: an uneven skyline means an uneven amount of logic, as in the carry lookahead, whose towers rise with the bit number.

Every district has a sign in its colour at its near edge, and a colour key runs along the module (`key_row`): CORE at y=34, CONSOLE at y=2, CARTRIDGE at y=82.

## What you see

**CORE**, y 37..71 from x=-15 rightward, roughly in the order an instruction goes through it: PC FETCH, ROM LADDER and DECODE; FUSION LANES and BRANCH; REGISTERS, one wall per register with a sign X1 to X15 (a short wall is a register with less logic around it: narrower by value analysis, or written by fewer fusion paths); READ PORTS and RESULT BUS; ALU, SHIFTER and MEMORY PORT; RAM 64 BYTES, 16 words of 4 byte tiles; PROGRAM TEXT, one wall per stored BASIC line; TEXT READ, TEXT CONTROL and BUS CONTROL. The CPU timers are at y=73.

**CONSOLE**: CHARACTER CELLS is 12 by 8 tiles at the pitch of the screen, so each tile stands directly behind the 6 pixel wide column of its character, and holds that character's latches, font decoder and pixel drivers. To the right of the screen are KEY QUEUE, KEYBOARD, LINE BUFFER, CURSOR, NUMBERS and SERVICE, and the IO timers.

**CARTRIDGE**: see below.

## Attachment and blocks

There is no platform under the logic. `sparse_supports` joins everything with one-wide runs at z=0, which also keeps the gaps between tiles open as streets. Every tile has a gate at z=1, so every section reaches the plane.

**Keyboard (`--beta`, any layout).** The keyboard stands `height * 3 // 4` blocks further from the screen (48 for the 8-row screen): the near key row is at y=-52 and the switch row at y=-70. Standing at the front of it, with the eye about 7 blocks up, the 64-high screen spans about 43 degrees. The slab that used to lie under the keyboard, the screen and the seam is gone. What remains between the keyboard and the screen is one spine, one rail under each key row, and a stub of one block per key: the rail has to run under the keys, which attach only through their backs, and each key's flat sign lies one row further on and attaches the same way. All of it comes from `sparse_supports`, which finds each key and sign its shortest run; only the screen's foot and backing plate are placed by hand. On the current build the console has 6155 structural blocks against 9774 with the slab, of which 697 are runs. `bench_commands.py` finds the keys from their rows, so nothing there changes.

## Packet size

Each module must stay under 524288 bytes after LZ4. Grouping costs size because neighbouring ids no longer sit in neighbouring cells, so on this layout:

- The children of a module are written sorted by (colour, id) rather than by id, and positions in the order z, y, x (`POS_ORDER`). Ids and wiring are unchanged; only the order in the file differs.
- Gates are placed within a tile in id order, not bit order, since a tile's colour does not show its bits anyway.
- Glue districts are `solid`, which is the cheapest shape.

The numbers tried and rejected: more than two shades per district costs 15-25 kB, ordering by tile costs 15 kB, filling tiles by bit costs 9 kB. Going taller to shorten x saves about 1 kB, and all the signs together cost under 2 kB.

The layout still costs about 22 kB on CORE and 20 kB on CONSOLE compared with the cube, and **CORE is the module near the limit**. If new CPU logic takes it over, the build stops with a message saying so; `--layout cube` builds it, and the first thing to look at is whatever made the CPU grow.

## Cartridge groups: the contract

The cartridge is laid out from whatever tags its gates carry. Nothing here knows about programs.

**Gate tag.** A gate belongs to group `<group>` if its label ends in ` @cart_<group>` or ` @cart_<group>:<signal>`, where `<group>` matches `\w+`. In HDL terms this is the section mechanism: `sections.classify` returning `cart_<group>` for a statement in `rv_cartridge` gives it to all the gates that statement makes (`combine` adds the `Cartridge ` prefix to the front of the label, which is fine). The untagged default for that entity is `@cart:<signal>`.

**Map file (optional).** `cartridge-map.json` in the export folder, the one holding `circuit-map.json`. Write it before `prepare` runs, next to where `build_current` saves `program-timers.json`; `prepare` and `prototype` copy the folder along.

```json
{"groups": [
  {"key": "snake_move", "title": "SNAKE MOVE", "program": "SNAKE"},
  {"key": "snake_food", "title": "SNAKE FOOD", "program": "SNAKE"},
  {"key": "pong", "title": "PONG", "program": "PONG", "colour": "E0559B"}
]}
```

| Field | | Meaning |
|---|---|---|
| `key` | required | the `<group>` of the tag |
| `title` | optional | sign text, up to 18 characters; default the key in capitals |
| `program` | optional | groups with the same program stack in one column; default each group has its own |
| `colour` | optional | RRGGBB; default the next of `CART_PALETTE`. Not `222222`, which marks screen pixels |

Group order on the module is map order. A tag not in the map gets its own district after the mapped ones. A map entry nothing is tagged with is ignored.

**Without a tag**, by signal name:

| Signal | District |
|---|---|
| `select_row(n)` | ROW DECODERS, a strip per 8 rows |
| `address*`, `cart_address*` | ADDRESS |
| `cart_<field>*` | OUTPUT BUS, a strip per field |
| anything else | follows its wiring neighbours, else OUTPUT BUS |

So shared decode and output logic is left untagged and lands in ADDRESS and OUTPUT BUS, and a group's tagged `select_row(n)` gates form strips in its own district. A tagged gate's block is its row (by 8) or its signal.

The output field ORs currently span all rows, so splitting them per program is up to the cartridge generator: the layout only places the tags it finds. Seam blocks are placed by the seam code, not here, so more links only lengthen that row. The cartridge has no upper limit in y, but its depth grows only with the square root of its gates: strips wrap into more columns, so it stays about twice as wide as deep.
