# RV32E accelerated standalone toolchain

This builds the current **RV32E - accelerated CORE 1 of 2** and its required
matching **accelerated CONSOLE 2 of 2** directly from source. It contains one
release recipe, editable BASIC assembly, and the compiler, CPU/console
generators, packing and export code used by that release.

The historical `hdl_workshop/rv32e` tree has been replaced by the standalone
`accelerated` package. Study runners, earlier release profiles, validators,
simulation oracles, editor/viewer applications, saved experiment trees,
unused HDL templates, C tooling and prebuilt blueprint copies are removed.
Related CPU transforms have been consolidated in `accelerated/cpu_components.py`.

## Build

Use **Python 3.13 x64 on Windows**. Python itself is not bundled. The required
LZ4 4.4.5 block extension and its licenses are included under `vendor` for that
runtime. No compiler SDK, game installation, network connection or pip install
is needed for the tested Windows configuration.

Extract the ZIP, open a terminal in its directory and run:

```powershell
python -I build.py --output out-blueprints
```

The build takes several minutes. It assembles `firmware/basic.s`, generates and
compiles the CPU and console HDL, compacts and renumbers the gates, applies the
release clock settings, partitions the machine and creates its 46-link touching
connector layout. It checks the firmware hash, both final `blueprint.json`
hashes and their compressed import sizes against `release.json` **before
exporting**. A mismatch stops the export.

The output contains two fresh UUID folders. Copy these into your own Scrap
Mechanic account's `Blueprints` directory, or supply that directory explicitly
with `--output`. Follow [ASSEMBLY.md](ASSEMBLY.md) to weld and wire the pair.
The CORE requires its matching CONSOLE to operate.

The blueprint bytes are identical to the pinned release. Each new export has
a fresh folder UUID and matching `description.json.localId`, so those IDs vary.
Intermediate HDL, gate projects, maps and a `verification.json` record are kept
under `.build/<UUID>`. Choose another work directory with `--work PATH`.

`-I` isolates Python from global import paths and user packages; the entry point
adds only this package and its bundled dependency. Other Python/platform
configurations require a compatible LZ4 installation and are not the tested
configuration described here.

## Program cartridge (beta)

`python -I build.py --beta` also exports a third blueprint, **CARTRIDGE 3 of 3**, holding a compiled
Snake game that the CPU runs from outside its own gates: type `S` then ENTER at the BASIC prompt.
The cartridge is a ROM generated from `cartridge/snake.c`; see [docs/cartridge.md](docs/cartridge.md)
for the design and [cartridge/ASSEMBLY.md](cartridge/ASSEMBLY.md) for welding the third module.
The pinned default build is unchanged and has no cartridge.

## Source layout

- `build.py`: command-line entry point.
- `accelerated/build.py`: explicit current-release build pipeline and checks.
- `firmware/basic.s`: the current editable BASIC firmware assembly.
- `accelerated/cpu.py` and `cpu_components.py`: current CPU generation and transforms.
- Remaining `accelerated` modules: compiler, storage, console, font, clocks,
  geometry, packet encoding and export helpers used by the build.
- `release.json`: current clock settings, display text, expected hashes and sizes.
- `vendor`: the required LZ4 block extension and third-party licenses.

This package deliberately enforces the current release. Editing firmware or
hardware source can change its hashes; expected hashes should only be updated
after separately qualifying the resulting design. The packaging checks prove
reproduction of the existing blueprints; they do not constitute a new native
game test.

See `VERIFICATION.json` for the packaging validation and `ZIP-MANIFEST.json`
for the SHA256 of each packaged file.
