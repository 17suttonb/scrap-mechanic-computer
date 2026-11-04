# World file helper

`tools/make_world.py` takes a folder of exported modules and writes a save file with the whole computer already in the world as one welded, wired creation.

**Welding and wiring by hand ([ASSEMBLY.md](../ASSEMBLY.md), [cartridge/ASSEMBLY.md](../cartridge/ASSEMBLY.md)) stays the normal, fully supported way to build the computer.** This tool only reads exports. No build step uses it, and it changes nothing in the blueprints, seam connectors or weld pads.

**Status: not yet loaded in the game.** Every row has been compared with a real save (below), but nobody has opened an output file in Scrap Mechanic. Do the first try below on a throwaway world.

## Usage

```powershell
python -I tools/make_world.py out-blueprints-beta --template <new-world.db> --output out-world/computer.db
python -I tools/test_make_world.py
```

`--position X Y Z` sets where blueprint cell 0,0,0 goes, in metres. The default is z = 0, clear of the players saved in the template. `--force` replaces an existing output.

The template is a flat or custom-world Creative save: make one in the game, leave it, close the game, and pass that `.db` from `%APPDATA%\Axolot Games\Scrap Mechanic\User\User_<steamid>\Save`. The tool copies it byte for byte and only ever opens the copy. It stops if the save has a `-journal` or `-wal` file beside it (the game is using it), and it refuses to write anywhere under `Axolot Games` or the game's install folder, so installing the output is your own step.

## First try in the game

1. Make a new custom-world Creative game called something like `scratch` (the kind every version 29 sample is), leave it and close the game.
2. Run the command above with `--template "%APPDATA%\Axolot Games\Scrap Mechanic\User\User_<steamid>\Save\scratch.db"`.
3. Copy the output to that `Save` folder under a name no save has yet, such as `computer-world.db`. Don't overwrite anything.
4. Start the game and load `computer-world` from the Creative load list.
5. Expect the computer at the printed position, standing on the ground, with the modules' signs readable and wires visible with the connect tool. Follow the Operation steps in the assembly notes: RUN OFF, RESET ON for six seconds, OFF, RUN ON, then `H` ENTER and `?2+3` ENTER.
6. If it won't load, crashes or looks wrong, quit and delete `computer-world.db`. Nothing else was touched. The reason is usually in the newest `game-*.log` in the game's `Logs` folder (search for `ERROR`).

## Route

Three routes were considered. The binary save route was chosen.

- **Insert rows into a copy of a save (chosen).** A save is an SQLite file with binary rows. A real save exists that holds this exact build welded and wired by hand, so each row can be compared with what the game itself wrote. No mod is needed, and nothing passes through the blueprint import.
- **A mod or custom game calling `sm.creation.importFromFile` on the joined blueprint.** The game's own scripts use this call (`SurvivalGame.lua`, `NodeSpawnManager.lua`), and it would avoid the binary format. But it needs a custom game with its own world script, it has to spawn the whole machine in one call, and it is the same kind of import that the module split exists to avoid. Whether it hits the same limit could not be tested here.
- **A world-builder `.world` file.** These are JSON tile layouts. The one inspected holds only `cellData`, `cornerData`, `dependencies` and `version`, with no place for a creation, so this was dropped.

## Limits

The 524,288-byte limit does not apply to the file: it is checked in `accelerated/layout.py` on the compressed blueprint for import, and this route has no import step. SQLite has no limit near this size.

The game **has** written a body this big: the sample save holds one body of 45,764 shapes in an 8.3 MB file. What is **not proven** is loading such a body back. The game logs show saves being reopened, but none with a single body this large. The server also sends the world to its own client over a network connection, even in single player (`Connecting to self` in the log), so a limit there can't be ruled out. A hand-welded computer has exactly the same exposure. Reloading a copy of a save with one in it answers the question without this tool.

Other limits, each refused with a clear message unless marked:

- Savegame version 29 only (the current version on this machine). Older versions have a different layout: for instance, shape rows there lack the last byte.
- Custom (flags 4) and flat (flags 1) Creative saves only, with bodies in world 0 only. Terrain worlds (flags 7) are refused: their bodies live in world 1 and their ground isn't at z = 0. Any other flags, including Survival's, are refused. Flat gets a warning, since all version 29 samples are custom worlds; flags were matched to world types by the world script named in each save.
- Timers with `seconds` above 0 are refused. No sample pairs them with a blueprint, so the encoding isn't confirmed. Current builds use ticks only.
- At most 255 outputs per controller (a one-byte count). The largest seen is 128.
- Only the part kinds in current exports are written: logic gate, timer, button, switch, sign and block. Blocks must be unrotated.
- Rotations other than the three in use get a warning, not a refusal.
- An overlap with a creation already in the template gets a warning.

## Checks

On every run the tool checks that controller ids are unique across modules, module wires stay inside their module, each seam link has one end in each of two modules and the two ends touch, no cells overlap, and the joined machine is one connected piece. It then decodes what it wrote and compares it with the exports, and runs SQLite's integrity and foreign key checks on the copy. The output file only appears once all of that has passed.

`tools/test_make_world.py` has no external dependencies and uses synthetic fixtures, plus single rows copied from a real save as golden values. With `--export <folder> --sample <copy of a save>` it also compares every row with a save where that build was assembled by hand, and `--sim <folder>` writes the output's connections for `bench_commands.py`.

Results for the `--beta` export with 3 modules against a hand-assembled save of the same build:

| Compared | Result |
| --- | --- |
| Parts other than blocks | 45,724 of 45,724 ChildShape rows byte-identical |
| Controllers | 39,296 byte-identical; 6,428 differ only in saved run state (output on, timer contents) |
| Wires | 159,427 in both; every controller has the same receivers |
| Signs | 79 of 79 ScriptData rows byte-identical |
| Blocks | 55 boxes in the export, 40 in the save after the game merged them; all 10,797 cells covered |
| Body | Row layout byte-identical; bounds equal except one edge, 0.043 m off (see below) |

The output's connections, rebuilt from its rows alone, were then run in the repo simulator (the Python model) with `bench_commands.py`, next to the modules joined by their own maps. Boot, `H` and `?2+3` gave the same screens at the same times (34.25 s and 26.12 s, `005`). On the output's connections, `S` ENTER started Snake from the cartridge: board, `Q`, `+` and `SCORE 00`. That shows the wiring is right; it says nothing about how the game reads the file.

## Format (savegame version 29, big-endian unless noted)

**Tables.** The tool adds one row to `RigidBody` and `RigidBodyBounds`, one to `ChildShape` per part, one to `Controller` per interactive part and one to `ScriptData` per sign, and updates `Game.uniqueIds`. No other table changes. Nothing else in the sample save refers to the body id.

**`Game.uniqueIds`.** A count, then u32 counters. Slots 1, 3 and 4 (counting the count as slot 0) are the next ids for `RigidBody`, `ChildShape` and `Controller`. In the saves checked each counter is above the table's largest id, and usually exactly one above.

**`RigidBody.data`, static, 60 bytes.** `00 01 01`, id u32, world u16, then f32 maxY, minY, maxX, minX, then world u16, quaternion f32 w x y z, position f32 x y z, `00 FF FF FF FF`. Dynamic bodies start `00 02 01` and run to 80 bytes; they aren't written.

**`RigidBodyBounds`.** An SQLite rtree: id, minX, maxX, minY, maxY, the same values as the row. Each is the cell edge 0.004 m inside. The sample's minY is 0.043 m further out than the cells give, probably from a part's collision shape. The game presumably refreshes it, but that is an assumption.

**`ChildShape.data`.** `01 kind 01`, id u32, body u32, shape UUID (16 bytes, reversed), body u32, x y z i16 (quarter metres, same axes as the blueprint), `FF`, colour as B G R. Then:

- Parts (kind `20`, 43 bytes): rotation byte, `00`.
- Blocks (kind `1F`, 48 bytes): bounds x y z u16, `00`.

**Rotation byte.** High nibble from the blueprint's `zaxis`, low nibble from `xaxis`, using {1: 5, 2: 6, 3: 7, -1: 3, -2: 2, -3: 1}. Three pairs are confirmed by paired rows: (-1, 3) = `73`, (1, 3) = `75`, (1, -2) = `25`. The formula yields exactly the 24 bytes seen in real saves, but the other 21 aren't paired with a blueprint.

**`Controller.data`.** Columns: id, hostId (the shape), hostType 1. Data: `03 kind 01`, id u32, host u32, `01`, host u32, `01`, then by kind:

| Part | Kind | Before the wires | After the wires |
| --- | --- | --- | --- |
| Logic gate | `14` | | `00`, active bit 7 + mode (same numbers as the blueprint) |
| Button | `0A` | | `00`, active bit 7 |
| Switch | `0B` | | `00`, active bit 7 |
| Timer | `15` | u16 length, LZ4 delay line | `00 00`, active bit 7 + `40` + ticks |
| Sign | `1B` | | `01`, 8 zero bytes |

Wires are a u8 count then u32 Controller ids. The game keeps them in wiring order, so the order doesn't appear to matter. An idle delay line is always the same 12 bytes (`1F 00 01 00 FF 18 50 00 00 00 00 00`, in 211 timers across the saves checked).

**Signs.** A `ScriptData` row: uid `5130C47B0A1953FCA5A1205C19264574` (every sign in every save checked), key = controller id as u32 little-endian, worldId, flags 2. The data repeats those: uid, u16 key length, key, u16 world, u8 flags, u32 blob length, then the blob, which is exactly the blueprint controller's base64 `data` decoded.

## Unconfirmed assumptions

Only loading in the game can settle these:

- **The body stays put.** It is written as static (`00 01`), like the sample. The sample was anchored by two blocks added by hand that reach the ground. This output has no such blocks; instead the bottom plate sits at z = 0 where those blocks ended. If the game only keeps a body static through contact with the ground, a template whose ground isn't at z = 0 may behave differently: use `--position`.
- **Unmerged blocks load fine.** The game merged 55 boxes into 40 in the sample; the tool writes all 55 as they are.
- **Stored gate state.** Gates that the blueprint marks `active` are written with the active bit, and timers with an empty delay line. This should match a fresh import, but the sample only shows the state after hours of running. RESET per the assembly notes clears this either way.
- **The player's saved position** is read from `GenericData` by the layout seen in one save. If it is wrong, you may load in a different spot relative to the computer; nothing else depends on it.

## Sources

- The user's own saves and game logs, through copies, as described above. These are the main evidence and nothing from them is committed apart from single format rows in the test.
- [plummet555/scrap-mechanic-finder](https://github.com/plummet555/scrap-mechanic-finder): the partly decoded RigidBody and ChildShape layout that this work extends.
- [Scrap Mechanic Lua reference, `sm.creation`](https://scrapmechanic.com/api/namespace_Game_sm_creation.html): for the import functions. The page itself wouldn't load here, so this rests on the search summary and the game's own scripts.
