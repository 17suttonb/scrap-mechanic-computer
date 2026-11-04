; Pi by Machin's formula, 16 atan(1/5) - 4 atan(1/239), showing the digits it can prove as it goes.
;
; Fixed-point numbers of base 100 bytes, most significant first: byte 0 is the integer part. There is no multiply
; or divide, and about 0.8 s per row, so:
;   * Each series keeps T, its next term before dividing by 2k+1. The term's size is what bounds the error.
;   * T/25 is T*4 moved one byte along, which is a third of the work of a division. T/57121 is a long division.
;   * Long division takes the tens of each quotient byte by repeated subtraction, the units in four binary steps.
;   * Zero bytes at the top of T are skipped, so later terms are cheaper.
;   * Whole passes run at more and more bytes. The first gives a few digits in minutes, and each later one starts
;     again with more guard digits. Proven digits are only ever added to the screen.
;
; Error. pi = S + e, with |e| <= T5/d5 + T239/d239 + rounding, because both series alternate and their terms fall, so
; what is left of a series is at most its next term. Take f = the index of the first nonzero byte of T, plus one if that
; byte is under the next divisor d, so T/d < 100^-(f-1). The two series together are then under 2 units of byte f-1 of
; S. f is capped at L-1 to leave rounding, which is under 2.1 units in the last place per term, far below that. If
; byte j = f-1 of S is in 3..96, adding or taking off e cannot change any byte above it, so bytes 0..j-1 are proven. If
; it is not, the next byte up is tried.
; cartridge/test_pi.py checks every state of the screen against the true digits.
;
; RAM: S 0..13, T5 14..27, T239 28..41, Q 42..55, then the variables below. Registers: x15 is the next proven
; character's screen address. divide, accumulate and scale5 may use the rest.
; @group pi_setup PI SETUP
; @data tens "0000000000" "1111111111" "2222222222" "3333333333" "4444444444" "5555555555" "6666666666" "7777777777" "8888888888" "9999999999"
; @data ones "0123456789" "0123456789" "0123456789" "0123456789" "0123456789" "0123456789" "0123456789" "0123456789" "0123456789" "0123456789"
    sbi 80,-512
    sbi 73,-511
    sbi 77,-509
    sbi 65,-508
    sbi 67,-507
    sbi 72,-506
    sbi 73,-505
    sbi 78,-504
    sbi 78,-432
    sbi 79,-431
    sbi 87,-430
    sbi 80,-416
    sbi 84,-413
    sbi 69,-412
    sbi 82,-411
    sbi 77,-410
    sbi 83,-400
    sbi 85,-399
    sbi 82,-398
    sbi 69,-397
    addi x15,x0,-496
    sbi 4,56                    ; L, the last byte in use in this pass
new_pass:
    sw x0,0(x0)
    sw x0,4(x0)
    sw x0,8(x0)
    sw x0,12(x0)
    sw x0,16(x0)
    sw x0,20(x0)
    sw x0,24(x0)
    sw x0,28(x0)
    sw x0,32(x0)
    sw x0,36(x0)
    sw x0,40(x0)
    sw x0,44(x0)
    sw x0,48(x0)
    sw x0,52(x0)
    sbi 3,0                     ; S is the first term, 16/5
    sbi 20,1
    sbi 12,15                   ; T5 is 16/125
    sbi 80,16
    sbi 3,57                    ; the next divisor of series 5
    sbi 1,58                    ; the next divisor of series 239
    sbi 1,59                    ; f of T5
    lbu x3,63(x0)               ; the pass number
    addi x3,x3,1
    sb x3,63(x0)
    jal x1,show_now
    ; T239 is 4/239
    sbi 4,28
    addi x2,x0,28
    addi x3,x0,28
    lbu x4,56(x0)
    addi x4,x4,29
    addi x5,x0,239
    jal x1,divide
    addi x2,x0,28
    addi x3,x0,0
    jal x1,first_byte
    sb x3,60(x0)
; @group pi_series PI SERIES
term_loop:
    lbu x3,59(x0)
    lbu x4,60(x0)
    lbu x9,56(x0)
    addi x9,x9,-1
    bltu x4,x3,series_239
    bltu x9,x3,pass_done
    ; series 5: Q = T5 / d, S -+= Q, T5 /= 25
    addi x2,x3,14
    addi x3,x3,42
    lbu x4,56(x0)
    addi x4,x4,15
    lbu x5,57(x0)
    jal x1,divide
    lbu x3,57(x0)
    andi x3,x3,2
    lbu x5,59(x0)
    jal x1,accumulate
    jal x1,scale5
    lbu x3,57(x0)
    addi x3,x3,2
    sb x3,57(x0)
    addi x2,x0,14
    lbu x3,59(x0)
    jal x1,first_byte
    sb x3,59(x0)
    j term_done
series_239:
    addi x2,x4,28
    addi x3,x4,42
    lbu x4,56(x0)
    addi x4,x4,29
    lbu x5,58(x0)
    jal x1,divide
    lbu x3,58(x0)
    andi x3,x3,2
    xori x3,x3,2
    lbu x5,60(x0)
    jal x1,accumulate
    ; T239 /= 57121
    lbu x3,60(x0)
    addi x2,x3,28
    mv x3,x2
    lbu x4,56(x0)
    addi x4,x4,29
    addi x5,x0,1785
    add x5,x5,x5
    add x5,x5,x5
    add x5,x5,x5
    add x5,x5,x5
    add x5,x5,x5
    addi x5,x5,1
    jal x1,divide
    lbu x3,58(x0)
    addi x3,x3,2
    sb x3,58(x0)
    addi x2,x0,28
    lbu x3,60(x0)
    jal x1,first_byte
    sb x3,60(x0)
term_done:
    jal x1,show_now
    jal x1,prove
    j term_loop
pass_done:
    lbu x3,56(x0)
    addi x3,x3,3
    addi x4,x0,14
    bgeu x3,x4,finished
    sb x3,56(x0)
    j new_pass
finished:
    sbi 68,-392
    sbi 79,-391
    sbi 78,-390
    sbi 69,-389
stay:
    j stay
; x2 = array, x3 = the byte to start at. Returns in x3 the first nonzero byte, or L+1.
first_byte:
    lbu x4,56(x0)
    addi x4,x4,1
    add x2,x2,x3
first_byte_next:
    bgeu x3,x4,first_byte_done
    lbu x5,0(x2)
    bne x5,x0,first_byte_done
    addi x2,x2,1
    addi x3,x3,1
    j first_byte_next
first_byte_done:
    jalr x0,0(x1)
; @group pi_arithmetic PI ARITHMETIC
; Q(x3..) = T(x2..x4) / x5, a byte at a time from the top.
divide:
    add x6,x5,x5
    add x7,x6,x6
    add x9,x7,x7
    add x10,x9,x6
    addi x11,x0,0
divide_byte:
    add x13,x11,x11
    add x13,x13,x13
    add x14,x13,x13
    add x14,x14,x14
    add x14,x14,x14
    add x11,x13,x14
    add x14,x14,x14
    add x11,x11,x14
    lbu x13,0(x2)
    add x11,x11,x13
    addi x12,x0,0
divide_tens:
    bltu x11,x10,divide_8
    sub x11,x11,x10
    addi x12,x12,10
    j divide_tens
divide_8:
    bltu x11,x9,divide_4
    sub x11,x11,x9
    addi x12,x12,8
divide_4:
    bltu x11,x7,divide_2
    sub x11,x11,x7
    addi x12,x12,4
divide_2:
    bltu x11,x6,divide_1
    sub x11,x11,x6
    addi x12,x12,2
divide_1:
    bltu x11,x5,divide_store
    sub x11,x11,x5
    addi x12,x12,1
divide_store:
    sb x12,0(x3)
    addi x3,x3,1
    addi x2,x2,1
    bne x2,x4,divide_byte
    jalr x0,0(x1)
; S -= Q if x3 is nonzero, else S += Q, for Q bytes L..x5, then any carry on up.
accumulate:
    lbu x4,56(x0)
    addi x2,x4,42
    addi x5,x5,42
    addi x9,x0,0
    addi x11,x0,100
    bne x3,x0,subtract_byte
add_byte:
    lbu x6,0(x4)
    lbu x7,0(x2)
    add x6,x6,x7
    add x6,x6,x9
    addi x9,x0,0
    bltu x6,x11,add_store
    addi x6,x6,-100
    addi x9,x0,1
add_store:
    sb x6,0(x4)
    addi x4,x4,-1
    addi x2,x2,-1
    bgeu x2,x5,add_byte
add_carry:
    beq x9,x0,accumulate_done
    lbu x6,0(x4)
    add x6,x6,x9
    addi x9,x0,0
    bltu x6,x11,add_carry_store
    addi x6,x6,-100
    addi x9,x0,1
add_carry_store:
    sb x6,0(x4)
    addi x4,x4,-1
    j add_carry
subtract_byte:
    lbu x6,0(x4)
    lbu x7,0(x2)
    sub x6,x6,x7
    sub x6,x6,x9
    addi x9,x0,0
    bge x6,x0,subtract_store
    addi x6,x6,100
    addi x9,x0,1
subtract_store:
    sb x6,0(x4)
    addi x4,x4,-1
    addi x2,x2,-1
    bgeu x2,x5,subtract_byte
subtract_carry:
    beq x9,x0,accumulate_done
    lbu x6,0(x4)
    sub x6,x6,x9
    addi x9,x0,0
    bge x6,x0,subtract_carry_store
    addi x6,x6,100
    addi x9,x0,1
subtract_carry_store:
    sb x6,0(x4)
    addi x4,x4,-1
    j subtract_carry
accumulate_done:
    jalr x0,0(x1)
; T5 = T5 * 4 / 100, moving up one byte from the bottom: the old last byte's own low part drops off.
scale5:
    lbu x2,56(x0)
    addi x2,x2,14
    addi x4,x2,1
    lbu x5,59(x0)
    addi x5,x5,14
    addi x9,x0,0
    addi x10,x0,100
    addi x11,x0,200
    lbu x6,0(x2)
    add x6,x6,x6
    add x6,x6,x6
    j scale_carry
scale_byte:
    lbu x6,0(x2)
    add x6,x6,x6
    add x6,x6,x6
    add x6,x6,x9
    addi x9,x0,0
    bltu x6,x11,scale_low
    addi x6,x6,-200
    addi x9,x0,2
scale_low:
    bltu x6,x10,scale_store
    addi x6,x6,-100
    addi x9,x9,1
scale_store:
    sb x6,0(x4)
    j scale_next
scale_carry:
    bltu x6,x11,scale_carry_low
    addi x6,x6,-200
    addi x9,x0,2
scale_carry_low:
    bltu x6,x10,scale_next
    addi x9,x9,1
scale_next:
    addi x4,x4,-1
    addi x2,x2,-1
    bgeu x2,x5,scale_byte
    sb x9,0(x4)
    jalr x0,0(x1)
; @group pi_display PI DISPLAY
; The pass and how far through it, and in the first pass the value now.
show_now:
    lbu x2,63(x0)
    addi x2,x2,-1
    bne x2,x0,show_term
    lbu x2,0(x0)
    addi x3,x2,48
    sb x3,-428(x0)
    sbi 46,-427
    addi x4,x0,1
    addi x5,x0,-426
    addi x9,x0,4
show_now_byte:
    lbu x2,0(x4)
    lbu x3,tens(x2)
    sb x3,0(x5)
    lbu x3,ones(x2)
    sb x3,1(x5)
    addi x5,x5,2
    addi x4,x4,1
    bne x4,x9,show_now_byte
show_term:
    lbu x2,63(x0)
    addi x2,x2,48
    sb x2,-415(x0)
    lbu x2,57(x0)
    lbu x3,58(x0)
    add x2,x2,x3
    lbu x3,tens(x2)
    sb x3,-408(x0)
    lbu x3,ones(x2)
    sb x3,-407(x0)
    jalr x0,0(x1)
; Print any bytes newly proven, see the top of this file.
prove:
    lbu x2,59(x0)               ; series 5: f, plus one if the first byte is under the next divisor
    addi x3,x2,14
    lbu x3,0(x3)
    lbu x4,57(x0)
    bgeu x3,x4,prove_other
    addi x2,x2,1
prove_other:
    lbu x6,60(x0)               ; the same for series 239
    addi x3,x6,28
    lbu x3,0(x3)
    lbu x4,58(x0)
    bgeu x3,x4,prove_both
    addi x6,x6,1
prove_both:
    mv x3,x6
    bgeu x3,x2,prove_smaller
    mv x2,x3
prove_smaller:
    lbu x3,56(x0)
    addi x3,x3,-1
    bgeu x3,x2,prove_capped
    mv x2,x3
prove_capped:
    addi x2,x2,-1               ; x2 = j, byte of S that may yet change by 2 units
    addi x5,x0,94
prove_check:
    beq x2,x0,prove_print       ; j = 0: nothing is proven yet
    lbu x3,0(x2)
    addi x3,x3,-3
    bgeu x5,x3,prove_print
    addi x2,x2,-1
    j prove_check
prove_print:
    lbu x4,61(x0)               ; bytes shown so far
    bgeu x4,x2,prove_done
    bne x4,x0,prove_digits
    sbi 51,-496
    sbi 46,-495
    addi x15,x15,2
    addi x4,x0,1
    j prove_next
prove_digits:
    lbu x3,0(x4)
    lbu x5,tens(x3)
    sb x5,0(x15)
    jal x9,prove_step
    lbu x3,0(x4)
    lbu x5,ones(x3)
    sb x5,0(x15)
    jal x9,prove_step
    addi x4,x4,1
prove_next:
    bltu x4,x2,prove_digits
    sb x4,61(x0)
    ; two decimals to a byte, not counting the integer part
    addi x4,x4,-1
    add x4,x4,x4
    lbu x3,tens(x4)
    sb x3,-395(x0)
    lbu x3,ones(x4)
    sb x3,-394(x0)
prove_done:
    jalr x0,0(x1)
prove_step:
    addi x15,x15,1
    andi x6,x15,15
    addi x6,x6,-12
    bne x6,x0,prove_step_done
    addi x15,x15,4
prove_step_done:
    jalr x0,0(x9)
