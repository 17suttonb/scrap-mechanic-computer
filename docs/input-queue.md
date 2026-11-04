# Console input queue and fast shifts

Branch: `input-queue` (from `beta-optimisations`, merged with `main`). Built by `python -I build.py --beta`, which now enables both features. The flags are `--key-queue N` (default 16 with `--beta`, 0 = original single-key tap) and `--fast-shifts` / `--no-fast-shifts` (default on with `--beta`).

## Why

The original tap keyboard holds one key. A press is only admitted while KEY READY is on, and KEY READY goes off while the console captures the previous key or the CPU is printing. Fast typing, or typing during output, silently loses keys. In the simulator the console consumes one key per ~100 updates, so any burst faster than that was dropped.

## Design (`accelerated/key_queue.py`, wired in `accelerated/tap_terminal.py`)

Physical keys -> encoder -> 16-slot FIFO -> decoder -> the existing per-key pending latches -> unchanged console.

- **Intake.** Each press is encoded to a 6-bit key index (OR trees over the 43 keys). A second OR tree over the complement code detects chords: two keys always disagree in some bit, so index AND complement flags a collision and the press is dropped. A rising-edge detector gives exactly one write per press, so a held key registers once. Delay lines align the index with the edge, so even a 1-update tap is captured.
- **Ring buffer.** 16 slots of 6 data bits plus a valid bit, with one-hot write and read pointers. Every storage element is the same hist/inv/Q latch the tap keyboard already uses, with private 3-update stretches on set and reset, so no mutual links are needed. A 3-update write lockout drops a press right behind another instead of letting it corrupt a slot.
- **Output.** When the head slot is valid and the old `available` condition says the console is free, a stable-for-5-updates rising edge loads the head into the per-key pending latches through a 6-to-43 decoder. The console's `key_taken` pulse pops the slot, so a key cleared by `busy` before it is consumed stays queued and is reloaded.
- **KEY READY lamp** keeps its original meaning (the console can take a key now, so it blinks off while a key is pending or the console is busy) and also goes off while the queue is full. Reset flushes the whole queue.
- Cost: about 1,000 gates (CONSOLE packet 246,735 -> 266,340 B, limit 524,288 B). The CORE is unaffected.

## Verification (Logic-Block-Simulator, native-logic-2, on the exported blueprint)

Keys driven with `key.X` presses; screen decoded from `terminal_pixels`.

| Test | Original single-key tap | Queue |
|---|---|---|
| `?1+2` ENTER, 40-update key spacing | ERR (keys lost) | 003 |
| same, 12-update spacing | `?` only | 003 |
| 1-update press, 12 and 7 spacing | - | 003 |
| 11-key burst (`A=5`, `?A+A+7`) at 10 spacing, console busy printing | - | 017 (queue absorbs it; 8 slots lost 2 keys, hence 16) |
| two keys on the same update (chord) | - | rejected |
| key held 300 updates | - | one `A` |
| 6 keys queued, then RESET | - | queue flushed |
| presses closer than ~6 updates | - | dropped, never corrupted |
| KEY READY lamp across `?2+3` ENTER | blinks per key | blinks per key (same) |

Limits: sustained typing is still limited by the console at ~100 updates per key; the queue only absorbs bursts of up to 16. Presses closer than about 6 updates (0.15 s) are dropped.

## Fast shifts

`cpu.py` change from the earlier beta, now on by default in `--beta`: the 2-cycle stall on fixed `slli`/`srli` is removed. Simulator results with it on and off are identical on `?123+456` (067), `?9999-1234` (061), `A=77`, `?A+A` (-102), `?7+8-3+12` (024) and a 3-line program with RUN and LIST (the BASIC is 8-bit, so results wrap). Digit-heavy statements finished about 200 updates sooner (3200 vs 3400, 3900 vs 4100). The simulator models gate behaviour only. It does not prove the clock phases hold up in the actual game, so keep `--no-fast-shifts` as the fallback.

The pinned original and the first beta also behave identically on `10A=3`, `20?A+A`, `30E`, `RUN`, `LIST`, which is the only check so far on the firmware reorderings from the beta.

## Fixes after first in-game test

- **Character flash.** The new character briefly showed as `?` before the correct glyph. In the simulator this glitch lasts 1 update on the pinned release and 3 on the first beta and the queue build, so it came from the beta netlist pass that removes double inversions, not from the queue. That pass changes the relative delays in the console display. It is now off by default (`double_inversions_enabled=False` in `compact_build`), the glitch is back to 1-2 updates, and the cost is about 380 gates.
- **KEY READY lamp** restored as above.
- **`?2+3` taking longer (26 s to 36 s in game).** Later measurements put pinned and this build both near 37 s, so the 26 s was another build: see [fast-cpu.md](fast-cpu.md). Originally recorded as not reproduced. In the simulator the busy indicator is high for the same time (about 1,380 updates) on the pinned release, the first beta and this build, and fast shifts are about 25 updates quicker. If the game still shows the slowdown, compare `--no-fast-shifts` (fast shifts are the only change that touches CPU timing; the queue sits entirely in front of the console).
