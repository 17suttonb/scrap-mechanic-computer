# Accelerated modular BASIC with a program cartridge

Load all three blueprints from this build: **RV32E - accelerated CORE 1 of 3**,
**CONSOLE 2 of 3** and **CARTRIDGE 3 of 3**. The CORE and CONSOLE weld together
exactly as in the two-module release (see the 46-block seam rows there).

The CARTRIDGE sits on the far side of the CORE from the CONSOLE. Mate the plain
L-shaped pad on the CORE's far end (labelled WELD, next to the sign
CARTRIDGE SEAM) with the pad on the CARTRIDGE. Along that seam there is one
continuous row of **62 blocks**. Connect each coloured block to the grey block
directly touching it across the seam: 62 short connections. Sources are green.
The CORE's own side carries the address pins going out; the CARTRIDGE's side
carries the instruction fields coming back.

Operation:

1. With RUN OFF, hold RESET ON for six seconds, release it, then turn RUN ON.
2. At the BASIC prompt tap **S** then **ENTER**. The CPU leaves the built-in
   interpreter and executes the program stored in the cartridge: Snake.
3. Steer with **W A S D** then **ENTER**. With no input the snake keeps going.
   `O` is the head, `+` food, `.` empty; body segments show the direction to
   the next segment. Walls and the body end the game; the last board stays up.
4. RESET returns to BASIC.

If the cartridge is not connected (or not welded), `S` reaches an empty program
location and the CPU stops with the CPU ERR screen; RESET recovers.

The game is slow: the CPU runs one instruction per 49 updates and each screen
character is a console transaction, so one move takes several minutes at the
game's 40 updates per second. See docs/cartridge.md for measurements.
