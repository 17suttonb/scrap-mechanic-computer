/* Snake for the RV32E console computer: 12x4 board, compiled to a program cartridge.
 *
 * Written for speed: a move is nine RV32E instructions, which the cartridge builder folds into six
 * single-cycle rows, with no console transactions at all.
 *
 * Memory map (see docs/cartridge.md):
 *   board   Register-addressed cartridge loads and stores reach the 64-byte RAM using only the low
 *           six address bits, so positions wrap.  A store also writes the matching screen cell, in
 *           the same cycle and without stalling, so drawing costs nothing extra.  Row = addr / 16,
 *           column = addr & 15.  The screen shows 0 as '.', 2 as '+' and anything else as 'O'.
 *   -240    bit 7 set = a key is waiting.  -236 reads that key and takes it.
 *
 * Before jumping here the firmware has cleared the board and set wall bytes in columns 12 and 15
 * of every row, and the console has blanked the screen and stopped echoing keys.
 *
 * A body cell holds the step to the next body cell, so moving the head is one add and the tail
 * follows by adding what it reads.  The head cell always holds the current direction; a turn
 * rewrites it.  Empty is 0, so "is this cell free" is a single test.  The left and right walls and
 * the body end the game (including the cell the tail is about to leave), and the last board stays
 * on the screen.  The top and bottom edges wrap around.
 *
 * Steer with a single tap of W, A, S or D.  No ENTER.  RESET returns to BASIC.
 * Registers x1, x8 and x9 are left alone so the function needs no stack.
 */
#define BOARD ((volatile signed char *)0)
#define STATUS (*(volatile signed char *)-240)
#define KEY (*(volatile unsigned char *)-236)

/* The firmware passes the starting state in registers, which it can set all at once. */
void __attribute__((noreturn)) _start(int head, int tail, int step, int food, int food_mark) {
    int cell;

    BOARD[tail] = step;
    BOARD[head] = step;
    BOARD[food] = food_mark;

    for (;;) {
        do {
            head += step;
            cell = BOARD[head];
            BOARD[head] = step;
            if (__builtin_expect(cell != 0, 0)) {
                if (cell != food_mark) {
                    for (;;) { }
                }
                do {
                    food = (food + 17) & 63;
                } while (BOARD[food] != 0);
                BOARD[food] = food_mark;
            } else {
                cell = BOARD[tail];
                BOARD[tail] = 0;
                tail += cell;
            }
        } while (__builtin_expect(STATUS >= 0, 1));

        int key = KEY, next = 0;
        if (key == 'W') next = -16;
        else if (key == 'S') next = 16;
        else if (key == 'A') next = -1;
        else if (key == 'D') next = 1;
        if (next != 0 && next + step != 0) {
            step = next;
            BOARD[head] = step;
        }
    }
}
