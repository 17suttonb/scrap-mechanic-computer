# Accelerated modular BASIC with a program cartridge

Load all three blueprints from this build: **CORE 1 of 3**, **CONSOLE 2 of 3** and **CARTRIDGE 3 of 3**. They only work with each other.

**CORE and CONSOLE.** Weld the plain L-shaped pads labelled WELD. Along the seam there are two continuous **51-block rows**. Connect each coloured block to the grey block directly touching it across the seam: 51 short connections. Sources are green, except RUN blue and RESET red. This seam has five more links than the two-module release.

**CARTRIDGE.** It sits on the far side of the CORE from the CONSOLE. Mate the L-shaped pad on the CORE's far end (labelled WELD, next to the sign CARTRIDGE SEAM) with the pad on the CARTRIDGE. Along that seam there are two rows of **62 blocks**. Connect each coloured block to the grey block touching it: 62 short connections.

Operation:

1. RUN (blue) and RESET (red) are on the CONSOLE, above the keyboard. With RUN OFF, hold RESET ON for six seconds, release it, then turn RUN ON.
2. At the BASIC prompt tap **S** then **ENTER**. The screen fills with dots and Snake starts.
3. Steer with one tap of **W A S D**. No ENTER. `Q` is the head, `O` the body and `+` food. All four edges wrap around. The bottom row shows the score on the left and, on the right, the key just tapped. Tap turns ahead if you like: they queue, and one applies per move. Running into the snake's own body shows `YOU LOST`, and filling the board (score 82) shows `YOU WON`. The last board and the score stay up.
4. RESET returns to BASIC.

If the cartridge is not connected, `S` stops the CPU with the CPU ERR screen, and RESET recovers.

**CPU clock.** The CPU timers are set to 16/2/8/2 (33 updates per cycle). The older clock was 20/6/12/6 (49). This has only been run in the simulator. If the CPU misbehaves in the game, set the four CPU timers back to 20/6/12/6, which needs no rebuild: everything still works, only slower.
