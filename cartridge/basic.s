; BASIC A-Z with sixteen resident 18-character lines, as a cartridge program in console mode.
; Variables 0..25; line count 28; execution budget 29; directory 32..63.
; Directory word: key bits 0..6, length bits 7..11, physical slot bits 12..15.
; The memory map is the cartridge one (docs/cartridge.md): RAM at 0, text at 512, the input stream at 256.
; `@group` lines name the program's parts for the cartridge's layout; %next, %is and %spaces are make_cartridge.py's
; macros. Between lines x13 = 10 and x14 = 26, and everything the common commands start from is set up then, after
; the last character has been drawn, rather than after the next ENTER.
; @group basic_start BASIC START
    sb x0,-250(x0)              ; the key that chose this program moved the input index
    jal x1,banner
    sbi 62,-480
    addi x2,x0,64
    sbi 16,35
    sbi 32,37
    sbi 48,39
    sbi 64,41
    sbi 80,43
    sbi 96,45
    sbi 112,47
    sbi 128,49
    sbi 144,51
    sbi 160,53
    sbi 176,55
    sbi 192,57
    sbi 208,59
    sbi 224,61
    sbi 240,63
; @group basic_direct BASIC DIRECT
wait_line:
    addi x13,x0,10
    addi x14,x0,26
    addi x9,x0,256
    addi x10,x0,-1
    addi x15,x0,0
    addi x5,x0,43
    addi x7,x0,-1
wait_ready:
    lb x1,-240(x0)
    bge x1,x0,wait_ready
    lbu x1,-236(x0)
    addi x6,x1,-63
    beq x6,x0,direct_term_read
    addi x6,x1,-48
    bltu x6,x13,direct_numbered
    addi x6,x1,-65
    bgeu x6,x14,direct_general_fresh
    mv x4,x1
    mv x7,x6
direct_letter_next:
    lbu x1,-236(x0)
    addi x6,x1,-32
    beq x6,x0,direct_letter_next
    addi x6,x1,-61
    beq x6,x0,direct_term_read
direct_keyword:
    mv x3,x4
    mv x4,x1
    j keyword_dispatch_loaded
direct_general_fresh:
    mv x4,x1
; @group basic_statement BASIC STATEMENT
statement:
    %spaces
    beq x4,x0,finished
    addi x6,x4,-48
    bltu x6,x13,numbered_line
    addi x6,x4,-63
    beq x6,x0,print_short
    addi x6,x4,-65
    addi x7,x0,26
    bgeu x6,x7,error
    mv x3,x4
    mv x7,x6
    %next
    %spaces
    addi x6,x4,-61
    beq x6,x0,assignment_equal
keyword_dispatch_loaded:
    addi x6,x3,-80
    beq x6,x0,print_long
    addi x6,x3,-71
    beq x6,x0,goto_command
    addi x6,x3,-73
    beq x6,x0,if_command
    addi x6,x3,-76
    beq x6,x0,letter_l
    addi x6,x3,-82
    beq x6,x0,letter_r
    addi x6,x3,-78
    beq x6,x0,new_command
    addi x6,x3,-69
    beq x6,x0,end_command
    addi x6,x3,-72
    beq x6,x0,help_command
    addi x6,x3,-67
    beq x6,x0,letter_c
    j error
; @group basic_commands BASIC COMMANDS
assignment:
    addi x6,x4,-65
    addi x7,x0,26
    bgeu x6,x7,error
    mv x7,x6
    %next
assignment_equal:
    %spaces
    addi x6,x4,-61
    bne x6,x0,error
    bge x10,x0,assignment_general_value
    addi x14,x0,26
    addi x15,x0,0
    addi x5,x0,43
    j direct_term_read
assignment_general_value:
    %next
    jal x11,expression
    bne x4,x0,error
    sb x15,0(x7)
    j finished
print_long:
    %is 82
    %next
    %is 73
    %next
    %is 78
    %next
    %is 84
    %next
    j print_expression
print_short:
    %next
print_expression:
    jal x11,expression
    bne x4,x0,error
    sb x15,-251(x0)
    j finished
letter_l:
    addi x6,x4,-69
    beq x6,x0,let_command
    %is 73
    %next
    %is 83
    %next
    %is 84
    %next
    %spaces
    bne x4,x0,error
    bge x10,x0,error
    addi x11,x0,32
list_next:
    bgeu x11,x2,prompt
    lhu x14,0(x11)
    andi x6,x14,127
    beq x6,x0,prompt
    jal x1,load_line
    sb x14,-251(x0)
    sbi 32,-252
list_text:
    lbu x4,0(x9)
    beq x4,x0,list_after
    sb x4,-252(x0)
    addi x9,x9,1
    j list_text
list_after:
    sb x13,-252(x0)
    addi x11,x11,2
    j list_next
let_command:
    %next
    %is 84
    %next
    %spaces
    j assignment
letter_r:
    addi x6,x4,-69
    beq x6,x0,rem_command
    %is 85
    %next
    %is 78
    %next
    %spaces
    bne x4,x0,error
    bge x10,x0,error
    sbi 255,29
    addi x11,x0,32
    j run_next
rem_command:
    %next
    %is 77
    %next
    j finished
new_command:
    %is 69
    %next
    %is 87
    %next
    %spaces
    bne x4,x0,error
    bge x10,x0,error
    ; every slot stays in the directory, in whatever order deletes left them: only the keys go
    lbu x6,28(x0)
    sbi 0,28
    add x6,x6,x6
    addi x6,x6,32
    addi x11,x0,32
new_clear:
    bgeu x11,x6,prompt
    sb x0,0(x11)
    addi x11,x11,2
    j new_clear
goto_command:
    %is 79
    %next
    %is 84
    %next
    %is 79
    %next
    addi x7,x0,1
    j jump_expression
if_command:
    %is 70
    %next
    jal x11,expression
    mv x7,x15
    addi x6,x4,-61
    bne x6,x0,if_then
    %next
    jal x11,expression
    xor x7,x7,x15
    sltiu x7,x7,1
if_then:
    %is 84
    %next
    %is 72
    %next
    %is 69
    %next
    %is 78
    %next
jump_expression:
    jal x11,expression
    bne x4,x0,error
    beq x7,x0,finished
    addi x6,x15,-1
    addi x14,x0,127
    bgeu x6,x14,error
    mv x12,x15
    addi x11,x0,32
find_target:
    bgeu x11,x2,error
    lhu x14,0(x11)
    andi x6,x14,127
    beq x6,x12,target_found
    beq x6,x0,error
    bltu x12,x6,error
    addi x11,x11,2
    j find_target
target_found:
    bge x10,x0,run_record
    sbi 255,29
    j run_record
end_command:
    %is 78
    %next
    %is 68
    %next
    %spaces
    bne x4,x0,error
    j prompt
help_command:
    bge x10,x0,error
    beq x4,x0,help_emit
    %is 69
    %next
    %is 76
    %next
    %is 80
    %next
    %spaces
    bne x4,x0,error
help_emit:
    sbi 1,-252
    j acknowledge
letter_c:
    beq x4,x0,clear
    %is 76
    %next
    %is 69
    %next
    %is 65
    %next
    %is 82
    %next
    %spaces
    bne x4,x0,error
clear:
    sw x0,0(x0)
    sw x0,4(x0)
    sw x0,8(x0)
    sw x0,12(x0)
    sw x0,16(x0)
    sw x0,20(x0)
    sw x0,24(x0)
    sbi 12,-252
    sbi 0,-252                  ; the page has been drawn once the console takes this
    jal x1,banner
    blt x10,x0,acknowledge
finished:
    blt x10,x0,prompt
    mv x11,x10
run_next:
    bgeu x11,x2,prompt
    lhu x14,0(x11)
    andi x6,x14,127
    beq x6,x0,prompt
run_record:
    lbu x6,29(x0)
    beq x6,x0,error
    addi x6,x6,-1
    sb x6,29(x0)
    addi x10,x11,2
    jal x1,load_line
    %next
    j statement
; @group basic_editor BASIC EDITOR
numbered_line:
    bge x10,x0,error
    addi x12,x4,-48
    j line_number_read
line_number:
    add x6,x12,x12
    add x12,x6,x6
    add x12,x12,x12
    add x12,x12,x6
    addi x6,x4,-48
    add x12,x12,x6
    addi x6,x0,128
    bgeu x12,x6,error
line_number_read:
    lbu x4,-236(x0)
    addi x6,x4,-48
    bltu x6,x13,line_number
    beq x12,x0,error
line_spaces:
    addi x6,x4,-32
    bne x6,x0,line_length
    lbu x4,-236(x0)
    j line_spaces
line_length:
    lbu x7,-240(x0)
    andi x7,x7,31
    lbu x6,-239(x0)
    sub x7,x7,x6
    addi x7,x7,1
    sltiu x6,x7,19
    beq x6,x0,error
    addi x11,x0,32
edit_find:
    bgeu x11,x2,edit_new
    lhu x14,0(x11)
    andi x6,x14,127
    beq x6,x12,edit_existing
    beq x6,x0,edit_new
    bltu x12,x6,edit_new
    addi x11,x11,2
    j edit_find
edit_existing:
    beq x7,x0,delete_line
    lbu x9,1(x11)
    andi x9,x9,240
    add x9,x9,x9
    j edit_address
edit_new:
    beq x7,x0,prompt
    lbu x15,28(x0)
    addi x6,x0,16
    bgeu x15,x6,error
    add x5,x15,x15
    addi x5,x5,32
    lbu x9,1(x5)
    andi x9,x9,240
    add x9,x9,x9
    addi x6,x15,1
    sb x6,28(x0)
edit_shift:
    bgeu x11,x5,edit_address
    addi x5,x5,-2
    lhu x14,0(x5)
    sh x14,2(x5)
    j edit_shift
edit_address:
    or x14,x9,x7
    add x14,x14,x14
    add x14,x14,x14
    add x14,x14,x14
    add x14,x14,x14
    add x14,x14,x14
    add x14,x14,x14
    add x14,x14,x14
    or x14,x14,x12
    addi x9,x9,512
edit_stream:
    sb x4,0(x9)
    addi x9,x9,1
    lbu x4,-236(x0)
    bne x4,x0,edit_stream
edit_commit:
    sh x14,0(x11)
prompt:
    sbi 2,-252
acknowledge:
    sb x0,-250(x0)
    j wait_line
delete_line:
    andi x3,x14,-128
    lbu x5,28(x0)
    addi x5,x5,-1
    sb x5,28(x0)
    add x5,x5,x5
    addi x5,x5,32
delete_shift:
    bgeu x11,x5,delete_clear
    lhu x14,2(x11)
    sh x14,0(x11)
    addi x11,x11,2
    j delete_shift
delete_clear:
    sh x3,0(x11)
    j prompt
error:
    sbi 3,-252
    j prompt
banner:
    sbi 84,-512
    sbi 66,-511
    sbi 65,-510
    sbi 83,-509
    sbi 73,-508
    sbi 67,-507
    sbi 43,-506
    sbi 49,-496
    sbi 54,-495
    sbi 76,-493
    sbi 73,-492
    sbi 78,-491
    sbi 69,-490
    sbi 83,-489
    jalr x0,0(x1)
; @group basic_expression BASIC EXPRESSION
spaces:
    addi x6,x4,-32
    bne x6,x0,spaces_done
    %next
    j spaces
spaces_done:
    jalr x0,0(x12)
; x14 is the packed directory record and x11 points at it. Cursor points into resident text.
load_line:
    lbu x9,1(x11)
    andi x9,x9,240
    add x9,x9,x9
    addi x9,x9,512
    andi x14,x14,127
    jalr x0,0(x1)
; Left-to-right + and -, repeated unary signs, A-Z or decimal terms.
; Return sum x15 and first unconsumed delimiter x4; preserve x7/x10..14.
expression:
    addi x15,x0,0
    addi x5,x0,0
term:
    addi x3,x4,-48
    bltu x3,x13,digits
    addi x6,x4,-65
    addi x3,x0,26
    bltu x6,x3,term_variable
    addi x6,x4,-32
    beq x6,x0,term_space
    addi x6,x4,-45
    beq x6,x0,term_negative
    addi x6,x4,-43
    beq x6,x0,term_space
    j error
term_variable:
    lbu x3,0(x6)
    %next
    j apply
term_negative:
    xori x5,x5,1
term_space:
    %next
    j term
digits:
    %next
    addi x6,x4,-48
    bgeu x6,x13,apply
    add x4,x3,x3
    add x3,x4,x4
    add x3,x3,x3
    add x3,x3,x4
    add x3,x3,x6
    j digits
apply:
    bne x5,x0,negative
    add x15,x15,x3
    j operator
negative:
    sub x15,x15,x3
operator:
    addi x6,x4,-32
    beq x6,x0,operator_space
    addi x6,x4,-43
    beq x6,x0,plus
    addi x6,x4,-45
    bne x6,x0,expression_done
    addi x5,x0,1
    j term_space
plus:
    addi x5,x0,0
    j term_space
operator_space:
    %next
    j operator
expression_done:
    jalr x0,0(x11)
; @group basic_fast BASIC FAST PATH
; Fast direct-mode expression path. x7 is the variable's address, or -1 to print.
direct_term_read:
    lbu x1,-236(x0)
direct_term_loaded:
    addi x3,x1,-48
    bltu x3,x13,direct_digit_read
    addi x6,x1,-32
    beq x6,x0,direct_term_read
    addi x6,x1,-45
    beq x6,x0,direct_unary_minus
    addi x6,x1,-43
    beq x6,x0,direct_unary_plus
    j direct_variable_term
direct_digit_read:
    lbu x1,-236(x0)
    addi x4,x1,-48
    bltu x4,x13,direct_more_digits
direct_apply_term:
    addi x6,x5,-45
    beq x6,x0,direct_subtract_term
    add x15,x15,x3
direct_after_term:
direct_operator_ready:
    beq x1,x0,direct_line_end
    mv x5,x1
    addi x6,x1,-43
    beq x6,x0,direct_term_read
    addi x6,x1,-45
    beq x6,x0,direct_term_read
    j direct_operator_tail
direct_line_end:
    bge x7,x0,direct_assign_result
    sb x15,-249(x0)
    j wait_line
direct_assign_result:
    sb x15,0(x7)
    j prompt
direct_subtract_term:
    sub x15,x15,x3
    j direct_after_term
direct_after_space:
    lbu x1,-236(x0)
    addi x6,x1,-32
    beq x6,x0,direct_after_space
    j direct_operator_ready
direct_operator_tail:
    addi x6,x1,-32
    beq x6,x0,direct_after_space
    j error
direct_unary_minus:
    xori x5,x5,6
direct_unary_plus:
    lbu x1,-236(x0)
    j direct_term_loaded
direct_more_digits:
    add x6,x3,x3
    add x3,x6,x6
    add x3,x3,x3
    add x3,x3,x6
    add x3,x3,x4
    j direct_digit_read
direct_variable_term:
    addi x6,x1,-65
    bgeu x6,x14,error
    lbu x3,0(x6)
    lbu x1,-236(x0)
    j direct_apply_term
direct_numbered:
    mv x12,x6
    j line_number_read
