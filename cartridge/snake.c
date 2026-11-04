/* Snake for the RV32E console computer (12x4 text screen, 5x7 font).
 *
 * Memory map used (the cartridge sees the 64-byte RAM at address 0; see docs/cartridge.md):
 *   RAM 0..63   the board: 4 rows of 16 bytes, of which columns 0..11 are the playfield
 *   -252        write: character to the screen (10 = newline, 12 = clear)
 *   -250        write: acknowledge the submitted input line
 *   -240        read:  bit 7 set = a line is waiting
 *   -236        read:  next character of the waiting line
 *
 * The board doubles as screen buffer and body list:
 *   '.' empty   '+' food   'O' head   W A S D  a body cell holding the direction to the next cell
 * so drawing is a straight copy and the tail advances by reading the letter in its cell.
 * A cell's address is its position: pos = row * 16 + column.  Columns 12..15 are walls
 * (any byte that is neither '.' nor '+' stops the snake).
 *
 * Steer by typing W, A, S or D then ENTER; with no input the snake keeps going.
 * Hitting a wall or the body ends the game; the final board stays on screen.
 * Only byte loads/stores, no calls, no stack: every variable lives in a register.
 */
#define RAM ((volatile unsigned char *)0)
#define SCREEN (*(volatile unsigned char *)-252)
#define ACK (*(volatile unsigned char *)-250)
#define STATUS (*(volatile signed char *)-240)
#define KEY (*(volatile unsigned char *)-236)

/* step in pos for a direction letter */
#define MOVE(c) ((c) == 'D' ? 1 : (c) == 'A' ? -1 : (c) == 'S' ? 16 : -16)

#define ROW(r) \
    SCREEN = RAM[r * 16 + 0];  SCREEN = RAM[r * 16 + 1];  SCREEN = RAM[r * 16 + 2]; \
    SCREEN = RAM[r * 16 + 3];  SCREEN = RAM[r * 16 + 4];  SCREEN = RAM[r * 16 + 5]; \
    SCREEN = RAM[r * 16 + 6];  SCREEN = RAM[r * 16 + 7];  SCREEN = RAM[r * 16 + 8]; \
    SCREEN = RAM[r * 16 + 9];  SCREEN = RAM[r * 16 + 10]; SCREEN = RAM[r * 16 + 11]

void __attribute__((noreturn)) _start(void) {
    int i, head, tail, food, dir, c;

    for (i = 0; i < 64; i++) RAM[i] = (i & 12) == 12 ? 'X' : '.';

    head = 21;                  /* row 1, column 5 */
    tail = 20;
    dir = 'D';
    RAM[tail] = 'D';
    RAM[head] = 'O';
    food = 38;                  /* row 2, column 6 */
    RAM[food] = '+';

    for (;;) {
        SCREEN = 12;
        ROW(0); SCREEN = 10;
        ROW(1); SCREEN = 10;
        ROW(2); SCREEN = 10;
        ROW(3);

        if (STATUS < 0) {
            c = KEY;
            ACK = 0;
            /* accept only W/A/S/D, and not a reversal of the current direction */
            if ((c == 'W' || c == 'A' || c == 'S' || c == 'D') && MOVE(c) + MOVE(dir) != 0)
                dir = c;
        }

        RAM[head] = dir;
        head += MOVE(dir);
        if ((unsigned)head >= 64) break;

        c = RAM[head];
        if (c == '+') {
            /* grow: keep the tail; the next free cell, stepping by 17 (mod 64), gets the food */
            for (i = 0; i < 64; i++) {
                food = (food + 17) & 63;
                if (RAM[food] == '.') { RAM[food] = '+'; break; }
            }
        } else if (c != '.') {
            break;
        } else {
            c = RAM[tail];
            RAM[tail] = '.';
            tail += MOVE(c);
        }
        RAM[head] = 'O';
    }
    /* game over: keep the final board and stop */
    for (;;) { }
}
