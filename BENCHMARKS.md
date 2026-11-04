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
