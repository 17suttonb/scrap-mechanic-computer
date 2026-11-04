/* Snake for the RV32E console computer (12x4 text screen, 5x7 font).
 *
 * Memory map used (same as firmware/basic.s):
 *   RAM 0..47   the board, one byte per cell, row-major, 12 columns x 4 rows
 *   -252        write: character to the screen (10 = newline, 12 = clear)
 *   -250        write: acknowledge the submitted input line
 *   -240        read: bit 7 set = a line is waiting
 *   -236        read: next character of the waiting line
 *
 * The board doubles as the screen buffer and the snake's body list:
 *   '.' empty   '+' food   'O' head
 *   W A S D     a body cell, holding the direction towards the next cell
 * so drawing is a straight copy of the board and the tail is advanced by
 * reading the letter in its cell.  Only byte loads/stores, no calls, no stack.
 *
 * Steer by typing W, A, S or D then ENTER; with no input the snake keeps going.
 * Walls and the snake's own body (including the cell the tail is leaving)
 * end the game; the final board stays on screen.  Reset to play again.
 */
#define RAM ((volatile unsigned char *)0)
#define SCREEN (*(volatile unsigned char *)-252)
#define ACK (*(volatile unsigned char *)-250)
#define STATUS (*(volatile signed char *)-240)
#define KEY (*(volatile unsigned char *)-236)

#define EMPTY '.'
#define FOOD '+'
#define HEAD 'O'
#define COLS 12
#define CELLS 48

#define ROW(r) \
    SCREEN = RAM[r * COLS + 0];  SCREEN = RAM[r * COLS + 1];  SCREEN = RAM[r * COLS + 2]; \
    SCREEN = RAM[r * COLS + 3];  SCREEN = RAM[r * COLS + 4];  SCREEN = RAM[r * COLS + 5]; \
    SCREEN = RAM[r * COLS + 6];  SCREEN = RAM[r * COLS + 7];  SCREEN = RAM[r * COLS + 8]; \
    SCREEN = RAM[r * COLS + 9];  SCREEN = RAM[r * COLS + 10]; SCREEN = RAM[r * COLS + 11]

/* Entry point: point the stack at the 16 spare bytes above the board, then play. */
void __attribute__((naked, section(".text.start"))) _start(void) {
    __asm__ volatile("li sp, 64; j game");
}

void __attribute__((noreturn, used)) game(void) {
    int i, head, tail, food, x, y, dir, key;

    for (i = 0; i < CELLS; i++) RAM[i] = EMPTY;
    x = 5;
    y = 1;
    head = y * COLS + x;
    tail = head - 1;
    dir = 'D';
    RAM[tail] = 'D';
    RAM[head] = HEAD;
    food = 30;
    RAM[food] = FOOD;

    for (;;) {
        SCREEN = 12;
        ROW(0); SCREEN = 10;
        ROW(1); SCREEN = 10;
        ROW(2); SCREEN = 10;
        ROW(3);

        if (STATUS < 0) {
            key = KEY;
            ACK = 0;
            if ((key == 'W' && dir != 'S') || (key == 'S' && dir != 'W') ||
                (key == 'A' && dir != 'D') || (key == 'D' && dir != 'A'))
                dir = key;
        }

        RAM[head] = dir;
        if (dir == 'D') { head++; x++; }
        else if (dir == 'A') { head--; x--; }
        else if (dir == 'S') { head += COLS; y++; }
        else { head -= COLS; y--; }
        if ((unsigned)x >= COLS || (unsigned)y >= 4) break;

        key = RAM[head];
        if (key == FOOD) {
            /* grow: keep the tail, put new food on the next free cell (stride 17 visits all 48) */
            for (i = 0; i < CELLS; i++) {
                food += 17;
                if (food >= CELLS) food -= CELLS;
                if (RAM[food] == EMPTY) { RAM[food] = FOOD; break; }
            }
        } else if (key != EMPTY) {
            break;
        } else {
            key = RAM[tail];
            RAM[tail] = EMPTY;
            if (key == 'D') tail++;
            else if (key == 'A') tail--;
            else if (key == 'S') tail += COLS;
            else tail -= COLS;
        }
        RAM[head] = HEAD;
    }
    /* game over: keep the final board and stop */
    for (;;) { }
}
