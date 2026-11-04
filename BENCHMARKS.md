# Simulator benchmarks

Measured with `Logic-Block-Simulator/runtime/python.exe bench_sim.py <joined-export-folder> --ticks 20000`
on the Logic-Block-Simulator (native-logic-2). Workload: reset high at tick 0, low at 480, run high at 500,
then idle until tick 20000 (no keyboard input, so the firmware sits in its key-wait loop).
All watched outputs (gpio, halted, faulted, trap_cause, busy) read 0 at the end, i.e. no halt or fault.
Speed is host simulation speed in ticks/sec; higher is better. Single run each, same machine.

| Date | Commit | Variant | Joined blueprint sha256 (prefix) | Ticks | Wall (s) | Ticks/sec | vs. pinned release |
|---|---|---|---|---|---|---|---|
| 2026-11-04 | c4f1e16 | default (pinned release) | a02366a2121c | 20000 | 19.44 | 1028.8 | baseline |
| 2026-11-04 | 9a61f6d (main) | `--beta` | bb3d04792965 | 20000 | 13.88 | 1441.4 | +40.1% |

Note: on main the default build currently fails ("Firmware differs from the current release") because
commit 9bbe5c1 edited `firmware/basic.s` without updating `release.json`. The default row was therefore built
from c4f1e16, the last commit whose default build matches the pinned release (joined hash matches `release.json`).

| 2026-11-04 | input-queue | `--beta` (16-deep key queue + fast shifts) | 21993414f7ac | 20000 | 24.63 | 812.2 | +7.1% vs beta in the same session (beta re-measured at 758.1 ticks/s; the machine was slower than for the rows above) |

Snake cartridge (branch `snake-rom`), measured in one session, two runs each, same 20000-tick idle workload (no halt or fault in any run):

| Date | Commit | Variant | Joined blueprint sha256 (prefix) | Ticks | Wall (s) | Ticks/sec | vs. previous |
|---|---|---|---|---|---|---|---|
| 2026-11-04 | fb5fa7f (input-queue) | `--beta` (previous version, re-measured) | b276c586395d | 20000 | 16.67 / 16.92 | 1199.7 / 1182.2 | baseline |
| 2026-11-04 | e55ca1c (snake-rom) | `--beta` with cartridge (3 modules) | 5c7278ae53bc | 20000 | 19.19 / 19.03 | 1042.4 / 1051.1 | -12.1% (mean 1047 vs 1191) |

The slowdown is host simulation speed only: the cartridge build has about 1.3k more logic blocks (+3.6%: CORE 22.9k to 23.8k, plus the 0.4k CARTRIDGE), and the CPU clock settings (20/6/12/6) and BASIC behaviour are unchanged.
The BASIC idle workload does not run the cartridge; the Snake workload is in docs/cartridge.md.

Fast clock and cartridge mode (branch `snake-fast`), one session, two runs each, same idle workload, no halt or fault:

| Date | Commit | Variant | Joined blueprint sha256 (prefix) | Ticks | Wall (s) | Ticks/sec | vs. previous |
|---|---|---|---|---|---|---|---|
| 2026-11-04 | fb5fa7f (input-queue) | `--beta`, C49 (re-measured) | b276c586395d | 20000 | 43.45 / 39.81 | 460.3 / 502.4 | baseline |
| 2026-11-04 | 834ad2f | `--beta` with cartridge, C33 | 7a8aa3de5076 | 20000 | 66.80 / 59.38 | 299.4 / 336.8 | -33% (mean 318 vs 481) |

Host simulation is slower because the CPU clock runs about 1.5 times as often per update, so more gates switch. The machine was busier than for earlier rows. This measures simulator speed, not machine speed: command times are in docs/fast-cpu.md (897.6 s against 1228.3 s for the same 14 commands).

8-row screen, flat layout, side wrap, `Q` head (834ad2f), same idle workload, no halt or fault. `bench_sim.py --fast` uses the Rust simulator, which matched the Python model at every check (`fast_sim/verify.py`):

| Date | Commit | Variant | Joined blueprint sha256 (prefix) | Ticks | Simulator | Wall (s) | Ticks/sec | vs. previous |
|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | f14e496 | `--beta` with cartridge, C33, 4 rows (re-measured) | 7a8aa3de5076 | 20000 | Python | 29.86 | 669.9 | baseline |
| 2026-11-04 | 834ad2f | `--beta --flat` with cartridge, C33, 8 rows | 848f8f236b07 | 20000 | Python | 31.51 | 634.7 | -5.3% |
| 2026-11-04 | f14e496 | `--beta` with cartridge, C33, 4 rows | 7a8aa3de5076 | 20000 | Rust | 0.106 | 187910 | 281 times the Python speed |
| 2026-11-04 | 834ad2f | `--beta --flat` with cartridge, C33, 8 rows | 848f8f236b07 | 20000 | Rust | 0.098 | 204351 | 322 times the Python speed |

Score, result lines, solid console and console switches (7a685db), same idle workload, no halt or fault. Moves stay at 4.95 s on average and load at 21.05 s.

| Date | Commit | Variant | Joined blueprint sha256 (prefix) | Ticks | Simulator | Wall (s) | Ticks/sec | vs. previous |
|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | 7a685db | `--beta --flat` with cartridge, C33, 8 rows | b98d2aef2fa5 | 20000 | Python | 31.54 | 634.0 | -0.1% vs 634.7 |
| 2026-11-04 | 7a685db | `--beta --flat` with cartridge, C33, 8 rows | b98d2aef2fa5 | 20000 | Rust | 0.099 | 202420 | 319 times the Python speed |
