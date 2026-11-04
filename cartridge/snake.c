/* Snake for the RV32E console computer: 12x7 board, compiled to a program cartridge.
 *
 * Written for speed: a move is eleven RV32E instructions, which the cartridge builder folds into six
 * single-cycle rows, with no console transactions at all.  Reading a key and crossing an edge add nothing:
 * every move takes the same time unless it eats.
 *
 * Memory map (see docs/cartridge.md):
 *   board   Register-addressed cartridge loads and stores use the low seven address bits.  Row = addr /
 *           16, column = addr & 15.  Rows 0..6, columns 0..11 are cells.  Everything else reads as a
 *           wall, 3, and ignores stores, which is what keeps food on the board.  A store to a cell also
 *           writes the matching screen cell, in the same cycle and without stalling, so drawing costs
 *           nothing extra.  The screen shows 0 as '.', 2 as '+', 4 as 'Q' and else 'O'.
 *   -234    The steering row, STEER below, which folds into one row with the jump after it: it takes a
 *           waiting key, and writes `step` with what to add to `head` to go one cell in the new direction,
 *           which is the key's if it is W, A, S or D and does not reverse `step`.  At an edge that is the
 *           whole way across, 11 or 96 with the sign.
 *
 *   13, 14  The score, as binary digits, statically addressed, shown directly on the bottom row.
 *   29      A store of LOST or WON here writes the result on the bottom row, over the word SCORE.
 *
 * Before jumping here the firmware has cleared the 64-byte RAM.  Rows 4..6 are clear after any RESET,
 * and only a cartridge can write them.  The console has blanked the screen, drawn "00 SCORE" and
 * stopped echoing keys.  No registers need setting.
 *
 * A body cell holds the step to the next body cell, so moving the head is one add and the tail
 * follows by adding what it reads.  The head cell holds HEAD and receives the step as the head
 * leaves it.  Empty is 0, so "is this cell free" is a single test.
 *
 * All four edges wrap, because of what the steering row writes to `step`.  The cell the head leaves gets
 * that step like any other, so the tail crosses the same way with no extra code.
 *
 * Only the snake's own body loses the game, including the cell the tail is about to leave.  Eating
 * the last food wins it: the snake starts 2 long, so that is food number 82, when it fills all 84
 * cells.  Either way the program writes the result and spins on one jump forever, with the last
 * board and the score on the screen; see docs/cartridge.md.
 *
 * Steer with a single tap of W, A, S or D.  No ENTER.  Taps queue, and one is applied per move.  RESET
 * returns to BASIC.  Registers x1, x8 and x9 are left alone so the function needs no stack.
 */
#define BOARD ((volatile signed char *)0)
#define CELLS ((volatile unsigned char *)0)   /* the same bytes, when only the kind of cell matters */

#define TENS (*(volatile unsigned char *)13)
#define ONES (*(volatile unsigned char *)14)
#define RESULT (*(volatile unsigned char *)29)

#define LOST 5
#define WON 6
#define WINNING_TENS 8                        /* 84 cells less the 2 the snake starts on */
#define WINNING_ONES 2
#define STRIDE 16
#define FOOD_STRIDE 53                        /* odd, so it visits all 128 addresses; 3 rows and 5 columns on */

/* Keeps the compiler from moving rarely used constants out of the loop, which would need a stack. */
#define LOCAL(x) __asm__ volatile("" : "+r"(x))
#define STEER(step, position) __asm__ volatile("add zero,%1,zero\n\tlb %0,-234(zero)" : "+r"(step) : "r"(position))

void __attribute__((noreturn)) _start(void) {
    int head = 3 * STRIDE + 6, tail = 3 * STRIDE + 5, step = 1, food = 5 * STRIDE + 6, food_mark = 2, head_mark = 4;
    int cell, near;
    LOCAL(head);
    LOCAL(tail);
    LOCAL(step);
    LOCAL(food);
    LOCAL(food_mark);
    LOCAL(head_mark);

    BOARD[tail] = step;
    BOARD[head] = head_mark;
    BOARD[food] = food_mark;

    for (;;) {
        BOARD[head] = step;
        head += step;
        LOCAL(head);
        cell = CELLS[head];
        BOARD[head] = head_mark;
        if (__builtin_expect(cell != 0, 0)) {
            if (cell == food_mark) {
                int ones = ONES + 1;
                near = 9;
                LOCAL(near);
                if (ones > near) {
                    TENS = TENS + 1;
                    ones = 0;
                    LOCAL(ones);
                }
                ONES = ones;
                near = WINNING_ONES;
                LOCAL(near);
                if (ones == near) {
                    near = WINNING_TENS;
                    LOCAL(near);
                    if (TENS == near) {
                        near = WON;
                        goto over;
                    }
                }
                do {
                    food += FOOD_STRIDE;
                    LOCAL(food);
                } while (BOARD[food] != 0);
                BOARD[food] = food_mark;
                goto steer;
            }
            if (cell != 0) {
                near = LOST;
over:
                LOCAL(near);
                RESULT = near;
                for (;;) { }
            }
        }
        cell = BOARD[tail];
        BOARD[tail] = 0;
        tail += cell;
steer:
        STEER(step, head);
    }
}
