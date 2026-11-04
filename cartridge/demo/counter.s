; A second cartridge's first program: counts seconds of its own on row 2, in tap mode. Any key restarts the count.
; It shows that a cartridge is free to run anything: built with make_cartridge.py, on a CORE and CONSOLE that have never
; seen it.
; @group counter_setup COUNTER SETUP
; @data digits "0123456789"
    sbi 67,-512
    sbi 79,-511
    sbi 85,-510
    sbi 78,-509
    sbi 84,-508
    addi x9,x0,10
restart:
    addi x2,x0,0
    addi x3,x0,0
; @group counter_loop COUNTER LOOP
count:
    lbu x4,digits(x3)
    sb x4,-479(x0)
    lbu x4,digits(x2)
    sb x4,-480(x0)
    addi x3,x3,1
    bne x3,x9,poll
    addi x3,x0,0
    addi x2,x2,1
    bne x2,x9,poll
    addi x2,x0,0
poll:
    lb x4,-240(x0)
    bge x4,x0,count
    lbu x4,-236(x0)
    j restart
