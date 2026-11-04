; A second cartridge's second program, in console mode: reads a typed line through the console's line buffer, prints
; it back on the line below, and asks again. No BASIC is involved, but the console works the same.
; @group echo_loop ECHO LOOP
    sb x0,-250(x0)
    sbi 69,-512
    sbi 67,-511
    sbi 72,-510
    sbi 79,-509
    sbi 62,-480
wait:
    lb x1,-240(x0)
    bge x1,x0,wait
    ; the console is still moving to the new line as the line becomes ready
    addi x2,x0,8
settle:
    addi x2,x2,-1
    bne x2,x0,settle
next:
    lbu x1,-236(x0)
    beq x1,x0,done
    sb x1,-252(x0)
    j next
done:
    sbi 2,-252
    sb x0,-250(x0)
    j wait
