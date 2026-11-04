/* Snake for the RV32E console computer: 12x4 board, compiled to a program cartridge.
 *
 * Written for speed: a move is ten RV32E instructions, which the cartridge builder folds into six
 * single-cycle rows, with no console transactions at all.
 *
 * Memory map (see docs/cartridge.md):
 *   board   Register-addressed cartridge loads and stores reach the 64-byte RAM using only the low
 *           six address bits, so positions wrap.  A store also writes the matching screen cell, in
 *           the same cycle and without stalling, so drawing costs nothing extra.  Row = addr / 16,
 *           column = addr & 15.  The screen shows 0 as '.', 2 as '+', 4 as 'Q' and else 'O'.
 *   -240    bit 7 set = a key is waiting.  -236 reads that key and takes it.
 *
 * Before jumping here the firmware has cleared the board and set wall bytes in columns 12 and 15
 * of every row, and the console has blanked the screen and stopped echoing keys.
 *
 * A body cell holds the step to the next body cell, so moving the head is one add and the tail
 * follows by adding what it reads.  The head cell holds HEAD and receives the step as the head
 * leaves it.  Empty is 0, so "is this cell free" is a single test.
 *
 * All four edges wrap.  Top and bottom wrap by themselves, through the six-bit addressing.  Left
 * and right hit a wall byte, which takes the slow path: the head moves to the other side and the
 * cell it left gets a step of 11 instead of 1, so the tail crosses the same way with no extra code.
 *
 * Only the snake's own body ends the game, including the cell the tail is about to leave.  The
 * program then spins on one jump forever with the last board on the screen; see docs/cartridge.md.
 *
 * Steer with a single tap of W, A, S or D.  No ENTER.  RESET returns to BASIC.
 * Registers x1, x8 and x9 are left alone so the function needs no stack.
 */
#define BOARD ((volatile signed char *)0)
#define CELLS ((volatile unsigned char *)0)   /* the same bytes, when only the kind of cell matters */
#define STATUS (*(volatile signed char *)-240)
#define KEY (*(volatile unsigned char *)-236)

#define WALL 3

/* Keeps the compiler from moving rarely used constants out of the loop, which would need a stack. */
#define LOCAL(x) __asm__ volatile("" : "+r"(x))

/* The firmware passes the starting state in registers, which it can set all at once. */
void __attribute__((noreturn)) _start(int head, int tail, int step, int food, int food_mark, int head_mark) {
    int cell, near;

    BOARD[tail] = step;
    BOARD[head] = head_mark;
    BOARD[food] = food_mark;

    for (;;) {
        do {
            BOARD[head] = step;
            head += step;
            cell = CELLS[head];
            BOARD[head] = head_mark;
            if (__builtin_expect(cell != 0, 0)) {
                near = WALL;
                LOCAL(near);
                if (cell == near) {
                    BOARD[head] = near;
                    LOCAL(step);
                    if (step > 0) {
                        near = -11;
                        LOCAL(near);
                        BOARD[head - 1] = near;
                        head -= 12;
                    } else {
                        near = 11;
                        LOCAL(near);
                        BOARD[head + 1] = near;
                        head += 12;
                    }
                    cell = CELLS[head];
                    BOARD[head] = head_mark;
                }
                if (cell == food_mark) {
                    do {
                        food = (food + 17) & 63;
                    } while (BOARD[food] != 0);
                    BOARD[food] = food_mark;
                    continue;
                }
                if (cell != 0) {
                    for (;;) { }
                }
            }
            cell = BOARD[tail];
            BOARD[tail] = 0;
            tail += cell;
        } while (__builtin_expect(STATUS >= 0, 1));

        int key = KEY, next = 0;
        if (key == 'W') next = -16;
        else if (key == 'S') next = 16;
        else if (key == 'A') next = -1;
        else if (key == 'D') next = 1;
        if (next != 0 && next + step != 0) step = next;
    }
}
