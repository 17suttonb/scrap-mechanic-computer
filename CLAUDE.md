# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Two independent parts live here: a Python toolchain that builds a Scrap Mechanic RV32E computer (CPU + console blueprints), and a Rust desktop viewer for circuit/blueprint files in `circuit_viewer/`.

## Python blueprint toolchain

Requires **Python 3.13 x64 on Windows**; the LZ4 extension is bundled in `vendor/` (tracked on purpose, `build.py` puts it on `sys.path`). There are no tests or linters.

```powershell
python -I build.py --output out-blueprints   # several minutes; --work PATH changes the .build dir
```

`-I` is intentional: the entry point adds only the repo and `vendor/` to the import path. Outputs (`out-blueprints/`, `.build/<UUID>/` with `verification.json`) are gitignored.

### Architecture

`build.py` calls `accelerated.build.build_current`, a single linear pipeline (read it top to bottom): assemble `firmware/basic.s` (`isa.py`) → generate CPU (`cpu.py`, `cpu_components.py`) and console (`tap_terminal.py`, `complete_console.py`) HDL → `compile_system` → compact/optimise/renumber gates → export → retime clocks → encode packet (`packet.py`) → split into CORE/CONSOLE modules and lay out the 46-link seam connector (`split.py`, `layout.py`) → copy to fresh UUID folders.

The build **enforces a pinned release**: `release.json` holds the firmware hash, clock settings, packet orders, and expected blueprint SHA256s/sizes. The build aborts before export if any hash differs. So any change to firmware or hardware source will fail the build until `release.json` hashes are deliberately updated after qualifying the new design. Don't update hashes just to make the build pass. `ZIP-MANIFEST.json` and `VERIFICATION.json` are packaging records of the pinned release. Blueprint bytes are reproducible, but folder UUIDs/`localId` vary per export.

`ASSEMBLY.md` is copied into each exported blueprint folder (in-game weld/wire instructions).

### Gate placement

`--layout cube|flat|districts` (`accelerated/layout.py`). `--beta` defaults to `districts` (`accelerated/districts.py`): named, signed, coloured districts derived from gate labels (`AND @alu:alu.c(5)`) with no hard-coded gate counts. `python tools/render_layout.py <export> <image folder>` draws each module. Design, the cartridge group contract (`@cart_<group>` tags, `cartridge-map.json`) and packet-size findings: `docs/layout.md`. CORE is close to the packet limit on this layout. `--beta` also moves the keyboard back from the screen and replaces the console slab with one-wide rails.

### Program cartridge (`--beta` only)

`--beta` builds a third blueprint, CARTRIDGE 3 of 3, holding the compiled Snake game (`cartridge/snake.c` -> `cartridge/snake.bin`); the CPU runs it when `S` ENTER is typed at the BASIC prompt. `--beta` also uses a faster CPU clock (`--cpu-ticks`, default 16,2,8,2) and transforms in `accelerated/cpu.py` that keep it possible: see `docs/fast-cpu.md` before changing either. `accelerated/cartridge.py` generates the cartridge netlist and patches the CPU HDL; `--no-cartridge` or `--cartridge FILE` change it. Design, limits and measurements: `docs/cartridge.md`. Rebuild `snake.bin` with `python cartridge/compile.py` from a Python that has `ziglang` installed (not needed to build blueprints). `python -I cartridge/test_snake.py` plays the binary on a host RV32E simulator; `cartridge/sim_snake.py` drives a joined export in the Logic-Block-Simulator. Cartridge code may not use `lui`/`auipc`/shifts/`x8`. `bench_commands.py` times BASIC commands and decodes the screen on any build, including blueprint-only ones, using the Rust simulator in `fast_sim/` when it is built (`cargo build --release` there; `fast_sim/verify.py <export>` checks it against the Python model, which stays the authority). `--beta` defaults to an 8-row screen (`--screen-rows`), keeps RUN and RESET on the console, and checks every module against the game's part attachment rules (`loose_parts` in `accelerated/supports.py`). CPU HDL transforms must keep existing lines on their line numbers, because storage selection is keyed by source line.

### World file helper (`tools/make_world.py`)

Manual assembly (`ASSEMBLY.md`) stays the normal, fully supported path; no build step depends on this tool and it only reads exports. It inserts the joined modules as one body into a copy of a Creative save (`--template`, savegame version 29), and refuses to write under the game's folders. It has not been loaded in the game yet. `python -I tools/test_make_world.py` needs no game; `--export` and `--sample` compare rows with a hand-assembled save (always pass a copy). Treat `%APPDATA%\Axolot Games` and the Steam install as read-only, and don't commit saves or Steam ids. Format and evidence: `docs/world-file.md`.

## Rust viewer (`circuit_viewer/circuit_viewer/`)

Read-only egui/eframe viewer for `scrap-circuit` v1 project files and version-4 `blueprint.json` exports. Needs Rust 1.85+ (edition 2024) and MSVC build tools. Run from that directory (paths in the README use `circuit_viewer/Cargo.toml` from a parent layout that is not in this repo):

```powershell
cargo build --release --locked
cargo test --locked
cargo test --locked --test compatibility full_name   # single integration test
cargo run --release --locked -- path/to/file.circuit.json
```

Modules: `model.rs` (parsing, `Circuit` simulation, blueprint/library scanning), `overview.rs` (cluster hierarchy for 15k+ block circuits), `physics.rs`/`layout.rs` (background force layout, planar and 3D), `camera.rs` (3D projection), `viewer.rs` + `viewer/overview_ui.rs` (egui UI). The viewer starts with an empty circuit (no embedded example). Integration tests build their fixtures in code; there are no sample project files in the repo.

## Simulator benchmarking and deployment (required after CPU changes)

`Logic-Block-Simulator/` is a portable bundle (own Python runtime, see its README) that simulates exported blueprints with the native logic model. Run it with `Logic-Block-Simulator\runtime\python.exe hdl_workshop\native_sim_cli.py <export-folder> --ticks N --watch <port> --output <file>.json` (or `Simulator.bat`). Its `results/` output is gitignored.

Whenever a change alters the CPU (firmware, `cpu*.py`, console/HDL, netlist passes) and the build succeeds:

1. Build the new blueprints (see above) and run the new CORE/CONSOLE export on the simulator for a fixed tick count, timing the wall-clock run and recording simulated ticks per second. Use the same firmware workload and tick count every time so results are comparable.
2. Compare against the previous version's recorded speed in `BENCHMARKS.md` (repo root; create it if missing). If the previous version has no entry, check out/build that previous commit first, run the same test on it, and record it before recording the new one.
3. Record each result as a row: date, git commit, build variant (default/`--beta`/`--flat`), tick count, wall time, ticks/sec, and workload outcome (pass/fail). Report the new-vs-previous delta to the user.
4. Once documented, **ask the user** which existing blueprint in the Scrap Mechanic blueprint folder should be overwritten, listing the candidates (folder name and the `name` in each `description.json`), with a further option to save under a new blueprint name instead. The folder is `%APPDATA%\Axolot Games\Scrap Mechanic\User\User_<steamid>\Blueprints\<UUID>`. Do not overwrite anything until the user answers; look at the target before replacing it.
