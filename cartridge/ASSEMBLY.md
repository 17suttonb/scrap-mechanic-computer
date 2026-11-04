# The computer with a program cartridge

Load all three blueprints: **CORE 1 of 3**, **CONSOLE 2 of 3** and **CARTRIDGE 3 of 3**. Any cartridge works with any CORE and CONSOLE of this generation, so a new cartridge never means rebuilding them.

**CORE and CONSOLE.** Weld the plain L-shaped pads labelled WELD. Along the seam there are two continuous **53-block rows**. Connect each coloured block to the grey block directly touching it across the seam: 53 short connections. Sources are green, except RUN blue and RESET red. This seam has seven more links than the two-module release.

**CARTRIDGE.** It sits on the far side of the CORE from the CONSOLE. Mate the L-shaped pad on the CORE's far end (labelled WELD, next to the sign CARTRIDGE SEAM) with the pad on the CARTRIDGE. Along that seam there are two rows of **112 blocks**. Connect each green block to the grey block touching it: 112 short connections. That number and every block's place are the same for all cartridges.

Operation:

1. RUN (blue) and RESET (red) are on the CONSOLE, above the keyboard. With RUN OFF, hold RESET ON for six seconds, release it, then turn RUN ON.
2. The screen shows the menu of what the cartridge holds, one line at a time. Tap the number of a program, with no ENTER. You need not wait for the list to finish.
3. RESET returns to the menu from any program.

The programs on the standard cartridge:

* **1 TBASIC.** The BASIC interpreter, with a prompt once `TBASIC+ / 16 LINES` is up. Type lines and press ENTER; `H` then ENTER shows the commands.
* **2 SNAKE.** Steer with one tap of **W A S D**. No ENTER. `Q` is the head, `O` the body and `+` food. All four edges wrap around. The bottom row shows the score on the left and, on the right, the key just tapped. Tap turns ahead if you like: they queue, and one applies per move. Running into the snake's own body shows `YOU LOST`, and filling the board (score 82) shows `YOU WON`. The last board and the score stay up.
* **3 PI.** Works out pi by itself and needs no keys. Rows 1 to 4 show only digits it has proved. `NOW` is the estimate during the first pass, `P` and `TERM` how far it has got, and `EXACT` how many decimals are proved. It reaches 20 decimals and then shows `OK`, in about 6.7 hours at the nominal 40 updates per second.

If the cartridge is not connected the screen says `NO CARTRIDGE`, and RESET checks again.

**CPU clock.** The CPU timers are set to 16/2/8/2 (33 updates per cycle). The older clock was 20/6/12/6 (49). This has only been run in the simulator. If the CPU misbehaves in the game, set the four CPU timers back to 20/6/12/6, which needs no rebuild: everything still works, only slower.
