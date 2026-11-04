# Beta optimisations

Branch: `beta-optimisations`. Build with:

```powershell
python -I build.py --beta                 # cube layout -> out-blueprints-beta/
python -I build.py --beta --fast-shifts   # also re-enable single-cycle fixed shifts
```

`--beta` skips the `release.json` hash checks (firmware, module SHA256s, packet sizes) because any design change breaks them. The import limit (packet < 524,288 B) is still enforced. The default pinned build is unchanged, but it fails on the firmware hash while `firmware/basic.s` differs from the release. `release.json` has not been updated.

**Status: untested.** Nothing here has been run in game or in a simulator. There is no CPU simulator in the repo.

## Result

| Module | Pinned release | Beta |
|---|---|---|
| CORE packet | 480,971 B | 460,467 B |
| CONSOLE packet | 246,602 B | 246,735 B |
| ROM image | 485 words | 477 words |

Beta netlist: 32,231 blocks, 113,251 wires. Compaction removed 350 double inversions and fused 410 gates in total.

## Implemented

### Firmware (`firmware/basic.s`)
- Removed `direct_prompt`, `direct_acknowledge` and `direct_syntax_error`. They were byte-identical to `prompt`, `acknowledge` and `error`, and references now point at those.
- Removed `j statement` and `j finished` where the jump targeted the next instruction.
- `term` now tests digit and letter before space and signs, which saves about 6 instructions per term. Behaviour is the same: scratch registers x3/x6 are only clobbered earlier, and invalid characters still reach `error`.
- `keyword_dispatch_loaded` checks G and I right after P, before L, R and N, so GOTO/IF dispatch in running programs is faster. Each check tests a different value, so order does not change behaviour.

### Assembler (`accelerated/isa.py`)
- `assemble(source, compact_li=False)`: with `compact_li`, a numeric `li` whose low 12 bits are zero emits only `lui` (1 word instead of `lui` + a no-op `addi`). Used by beta only, so the pinned firmware hash is unaffected.

### Netlist (`accelerated/compaction.py`, `compact_build(..., beta=True)`)
- Double-inversion removal: a NOR-inverter fed by another NOR-inverter is replaced by the original source. It skips protected ports, timing nodes, latch cells, XOR/XNOR parity cases and fan-out above 128. This also shortens those paths by 2 stages.
- The two-consumer fusion wire rule allows up to 4 extra wires (`added_wires > old_wires + 4`).

### Build plumbing
- `build.py`: `--beta` (default output `out-blueprints-beta`) and `--fast-shifts`.
- `accelerated/build.py`: `beta` and `fast_shifts` parameters. `accelerated/cpu.py`: `fast_shifts` replaces the former `if False and ...` guard on the shift change.

## Not implemented (from the analysis)

Ranked roughly by value; all would also need re-qualification.

- **Single-cycle fixed shifts** (`--fast-shifts`, off by default). Removes a stall cycle on every `slli`/`srli`. It was deliberately disabled upstream, probably for clock-phase timing (`cpu_ticks` 20/6/12/6), so test it in game separately.
- **NAND staging-latch relays** (`circuit.py:112-120`): 1,005 relay blocks, about 3% of blocks. They may be removable, or the banks convertible to the pulse XOR cell (about 1,000 gates). The reason for the relays is not documented, so test it in game first.
- **128 fan-in/fan-out cap**: hard-coded in 8 places while `circuit.validate` allows 255. Raising it would remove about 150 buffers if the real game limit allows it.
- **Load/store stall for computed addresses**: a disabled `dedicated_address_adder` exists in `cpu.py`.
- **Trap and legality logic** (`d_legal`, `pc_valid`, alignment, `stop_now`): about 100-250 gates, but it loses the CPU error display.
- **Unused seam links**: up to 11 of 46. Needs an interval proof that no load reaches the GPIO region, plus changes to `split.py`, `layout.py` and `release.json`.
- **Register width**: 10 registers stay at 32 bits; about 20-60 storage bits could be saved.
- **Wider inversion folding, console font predecode, `initialize_directory` loop** (about 16 words), fusion lane count (6 vs 4-5).
