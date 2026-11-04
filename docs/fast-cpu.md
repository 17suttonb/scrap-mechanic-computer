# Faster CPU clock, and comparison with the Branch Runs build

Everything here applies to `--beta` only. A rebuild of the pinned release with this generator is still byte-identical
to `release.json`. Nothing has been run in the game: all times are simulator runs at a nominal 40 updates per second.

## Results

Same command sequence on every build, measured by `bench_commands.py` from the ENTER press to the last pixel change.
The screens after every command match the pinned release on the final build. The Branch Runs screen has six rows, so its
text is laid out differently, but its results are the same.

| Command | Pinned (C49) | Previous beta (C49) | Branch Runs (C49) | Now (C33) |
|---|---:|---:|---:|---:|
| `H` | 44.88 | 46.75 | 41.12 | **34.25** |
| `?2+3` | 36.75 | 37.38 | 26.75 | **25.50** |
| `A=32` | 51.12 | 49.88 | 40.50 | **33.62** |
| `B=8` | 44.88 | 44.88 | 34.88 | **30.50** |
| `?A-B` | 56.12 | 56.12 | 40.50 | **35.50** |
| `?123+45-6` | 69.25 | 65.50 | 51.75 | **44.25** |
| `10 ?A` | 81.75 | 79.25 | 73.62 | **56.12** |
| `20 B=B+1` | 101.75 | 98.00 | 86.75 | **68.00** |
| `30 ?B` | 99.25 | 95.50 | 85.50 | **66.75** |
| `LIST` (3 lines) | 254.88 | 253.62 | 249.88 | **201.12** |
| `RUN` (3 lines) | 269.25 | 253.62 | 265.50 | **191.75** |
| `NEW` | 72.38 | 69.88 | 69.25 | **53.62** |
| `C` | 48.00 | 48.00 | 43.00 | **36.12** |
| `?7` | 29.88 | 29.88 | 21.75 | **20.50** |
| **Total** | 1260.1 | 1228.3 | 1130.8 | **897.6** |

This also corrects `docs/input-queue.md`, which called the in-game report of `?2+3` slowing from 26 s to 36 s "not
reproduced". The simulator does reproduce a figure close to it, and the 26 s build was not the pinned release: pinned and
previous beta are both about 37 s here and Branch Runs is about 27 s. The key queue is not the cause: removing it made
`?2+3` slower (41.75 s).

## What the Branch Runs build does differently

It ships as blueprints only, without source, so this comes from its `PERFORMANCE.md` and from running it. It has the same
family of parts and the same clocks as ours (C49, S25), so its gain is logic and firmware, not clock speed.

| | Branch Runs | Previous beta |
|---|---:|---:|
| CPU logic blocks | 24,328 | 22,943 |
| Console logic blocks | 15,238 | 12,174 |
| Screen | 12x6 (72x48) | 12x4 (72x32) |
| Firmware | 1,972 bytes | 1,908 bytes |

* **Branch runs** evaluate up to four adjacent `beq`/`bne` together. Ported here. Alone it took `?2+3` from 37.4 s to
  33.6 s at the old clock.
* **Fewer executed groups in BASIC's direct path.** It reports 14 groups for `?2+3`, where ours took 19 before branch runs.
  Its firmware is not available to read, so how it does that is not known. This gain is not ported.
* **Screen and keyboard.** The six-row screen and a keyboard moved 50 blocks forward are not speed changes, and were not
  ported. It also reports fixing a race that could hide the cursor underline on a cold `?255+1`, by delaying cursor capture
  from one update to two. That was not checked or ported here.

## What was changed here

**The clock.** Only one part of the timer loop limits logic depth: the time from commit rising to capture closing,
`capture + commit + gap after commit + 3` updates. The gap after capture does not count, and the old settings left slack
everywhere. The timers go from 20/6/12/6 (49 updates per cycle) to **16/2/8/2 (33)**, set with `--cpu-ticks`.

Capture must stay long: the ready and acknowledge samplers need 11 stable updates inside capture, so a capture phase
of 8 stops the machine however short the logic is. Time comes out of commit instead.

**Shorter paths**, in `flat_result_path` in `accelerated/cpu.py`:

* Load, result, next-PC and branch-condition muxes use one-hot AND-OR instead of priority chains. Their cases are mutually
  exclusive, so priority was never needed.
* The trap test no longer goes through the encoded cause. A bad memory access stops the CPU one instruction late, so the
  address decoders are off the path to every write enable. A misaligned store could now write before the CPU stops.
* Console registers are decoded from the instruction alone, not from the computed address.
* Register-addressed firmware loads take a second cycle. **This makes each read of program text or a BASIC variable
  through a register one cycle longer**, which is why `LIST` gains less than other commands. Cartridge board reads stay
  single-cycle.

**Branch runs**, in `bundles` and `fusion` in `accelerated/cpu.py`, with equality comparators shared across sites. About 320
more logic blocks. The retired-instruction count is no longer exact in a run, but that counter is removed from exported
builds anyway.

## Margin

| CPU timers | Updates per cycle | BASIC (14 commands) | Snake |
|---:|---:|---|---|
| 20/6/12/6 | 49 | correct | correct |
| **16/2/8/2 (shipped)** | 33 | correct | correct, including a played game to 45 food and a perfect game to 82 on the 12x7 board |
| 15/2/8/2 | 32 | not run | correct, a played game to 45 food on the 12x7 board |
| 14/2/8/2 | 31 | correct | **fails**: the tail stops clearing |
| 12/2/8/2 | 29 | correct | **fails** |
| 12/1/8/1 | 27 | **fails** | not run |
| 8/2/12/2 | 29 | **fails**: capture too short for the samplers | not run |

So Snake has two updates of margin at the shipped clock. If the game disagrees with the simulator, set the four CPU timers
back to 20/6/12/6 in-game; no rebuild is needed.

## Tools

* `fast_sim/` is a Rust port of the simulator's run loop, roughly 260 times faster than the Python one, which stays the authority. Build it with `cargo build --release` there. `fast_sim/verify.py` compares every controller's state against the Python model, on random circuits and on an export.
* `bench_commands.py` times BASIC commands on any build and decodes the screen. It accepts a folder of module blueprints
  with link maps, so it can run builds without source, and `--cpu-ticks` overrides the clock for a sweep.
* `cartridge/sim_snake.py` measures Snake's load time, and `cartridge/sim_keys.py` its key handling, with `--play` for a checked game.
