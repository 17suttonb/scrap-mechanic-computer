; The firmware of a cartridge machine: show what the cartridge holds, and run what is picked.
;
; It knows no program. The directory is the first page of the cartridge's data port, a screen page of 16 byte rows
; (docs/cartridge.md): row 0 is the cartridge's title, with the magic and the number of programs in hidden columns
; 12 and 13; each following row is one program's text, with its mode and its entry address in hidden columns 12..14.
; A key picks a row: 1 is the first program. It may be tapped before the page has finished drawing.
;
; A screen cell and its byte of the page are 1536 apart, so one register walks both.
; No RAM is touched, so a program starts with all of it clear, as RESET left it. The screen blanks on a change of
; mode, so going through console mode blanks it for a program in the mode the menu runs in.
    addi x1,x0,1
    sb x1,-248(x0)          ; single keys; the screen blanks
    addi x2,x0,1024
    lbu x3,12(x2)
    addi x3,x3,-199
    bne x3,x0,no_cartridge
    lbu x7,13(x2)           ; programs
    addi x10,x0,-512        ; the screen row being drawn
    slli x9,x7,4
    addi x9,x9,-496         ; the row after the last
next_row:
    mv x4,x10
    lb x3,-240(x0)
    blt x3,x0,read_key
    addi x5,x10,12
next_character:
    lbu x3,1536(x4)
    beq x3,x0,row_done
    sb x3,0(x4)
    addi x4,x4,1
    bne x4,x5,next_character
row_done:
    addi x10,x10,16
    bne x10,x9,next_row
wait_key:
    lb x3,-240(x0)
    bge x3,x0,wait_key
read_key:
    lbu x3,-236(x0)
    addi x3,x3,-49
    bgeu x3,x7,not_listed
    slli x3,x3,4
    addi x3,x3,1040
    lbu x1,12(x3)           ; mode
    lbu x2,14(x3)           ; entry address
    lbu x4,13(x3)
    slli x2,x2,8
    add x2,x2,x4
    sb x0,-248(x0)
    sb x1,-248(x0)
    jalr x0,0(x2)
not_listed:
    bne x10,x9,next_row
    j wait_key
no_cartridge:
    addi x3,x0,78
    sb x3,-512(x0)
    addi x3,x0,79
    sb x3,-511(x0)
    addi x3,x0,67
    sb x3,-509(x0)
    addi x3,x0,65
    sb x3,-508(x0)
    addi x3,x0,82
    sb x3,-507(x0)
    addi x3,x0,84
    sb x3,-506(x0)
    addi x3,x0,82
    sb x3,-505(x0)
    addi x3,x0,73
    sb x3,-504(x0)
    addi x3,x0,68
    sb x3,-503(x0)
    addi x3,x0,71
    sb x3,-502(x0)
    addi x3,x0,69
    sb x3,-501(x0)
halt:
    j halt
