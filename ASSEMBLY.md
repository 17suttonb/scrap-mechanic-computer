# Accelerated modular BASIC

Load **RV32E - accelerated CORE 1 of 2** and
**RV32E - accelerated CONSOLE 2 of 2**, both from this release.
The screen sign reads **FAST BASIC**.

Weld the matching plain L-shaped pads labelled WELD. Along the seam there
are two continuous **46-block rows**. Connect each coloured block to the grey
block directly touching it across the seam: 46 short connections, one more
than the previous pair. Sources are green, except RUN blue and RESET red.
The structural block ignores paint, so pad shape and signs guide alignment.

With RUN OFF, hold RESET ON for six seconds, release it, then turn RUN ON.
Tap keys when KEY READY is green and wait for it to return between taps.
Try H then ENTER, followed by ?2+3 without resetting. Expect HELP, then 005
and the prompt, with BUSY turning off after each command.

This release keeps the same RV32E firmware, Aâ€“Z byte variables, 16 stored
program lines, 72Ã—32 screen, 5Ã—7 font and tap keyboard. Hardware executes
eligible groups of up to six ordinary instructions together. Operand
forwarding, parallel register writes and bounded static RAM writes preserve
the instruction sequence's final state. Instructions inside a group remain
independently executable when reached by a jump.

The CPU uses direct operand predecode and flat carry lookahead. Constants
are forwarded during ROM construction; initialization and CLEAR/NEW can
write several proven RAM locations together. Memory loads and long branch
dependencies stay outside the new groups. The existing qualified ADDI/branch
unit remains available. The CPU clock stays at 49 updates per cycle
(20/6/12/6 timer settings); console timing drops from 31 to 25 updates
(0/8/0/12). The game's timer ring adds five updates to these setting sums.

The simulator checks the actual serialized assembly. Its timings assume
40 native updates per second; larger hardware can affect real simulation
throughput. See PERFORMANCE.md in either installed module for measurements
and qualification evidence. Native operation of this accelerated release
still needs confirmation.
