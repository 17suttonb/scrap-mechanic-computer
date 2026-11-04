; BASIC A-Z with sixteen resident 18-character lines, 64-byte CPU RAM.
; Variables 0..25; line count 28; execution budget 29; directory 32..63.
; Directory word: key bits 0..6, length bits 7..11, physical slot bits 12..15.
    lui x8,1
    addi x2,x8,64
    jal x1,initialize_directory
    addi x13,x0,10
wait_line:
    addi x9,x0,45
    addi x10,x0,43
    addi x11,x0,32
    addi x12,x0,10
    addi x13,x0,26
    addi x14,x0,63
wait_ready:
    lb x1,-240(x0)
    bge x1,x0,wait_ready
    lbu x1,-236(x0)
    addi x6,x1,-48
    bltu x6,x12,direct_numbered
    beq x1,x14,direct_print_expression
    addi x6,x1,-65
    bgeu x6,x13,direct_general_fresh
    mv x4,x1
    add x7,x8,x6
direct_letter_next:
    lbu x1,-236(x0)
    beq x1,x11,direct_letter_next
    addi x6,x1,-61
    beq x6,x0,direct_expression_start
    bne x1,x0,direct_keyword
    addi x6,x4,-83
    beq x6,x0,enter_cartridge
direct_keyword:
    mv x3,x4
    mv x4,x1
    li x9,4294963456
    addi x10,x0,-1
    addi x13,x0,10
    j keyword_dispatch_loaded
enter_cartridge:
    ; Clear the board and fill the hidden columns of each row, then run the cartridge:
    ; walls in columns 12 and 15, '0' in columns 13 and 14 (row 0 holds the score there).
    li x6,53489667
    sw x6,12(x8)
    sw x6,28(x8)
    sw x6,44(x8)
    sw x6,60(x8)
    sw x0,0(x8)
    sw x0,4(x8)
    sw x0,8(x8)
    sw x0,16(x8)
    sw x0,20(x8)
    sw x0,24(x8)
    sw x0,32(x8)
    sw x0,36(x8)
    sw x0,40(x8)
    sw x0,48(x8)
    sw x0,52(x8)
    sw x0,56(x8)
    addi x10,x0,21      ; arguments: head, tail, step, food, food mark, head mark
    addi x11,x0,20
    addi x12,x0,1
    addi x13,x0,38
    addi x14,x0,2
    addi x15,x0,4
    j 2048
direct_general_fresh:
    mv x4,x1
    li x9,4294963456
    addi x10,x0,-1
    addi x13,x0,10
statement:
    jal x12,spaces
    beq x4,x0,finished
    addi x6,x4,-48
    bltu x6,x13,numbered_line
    addi x6,x4,-63
    beq x6,x0,print_short
    addi x6,x4,-65
    addi x7,x0,26
    bgeu x6,x7,error
    mv x3,x4
    add x7,x8,x6
    lbu x4,0(x9)
    addi x9,x9,1
    jal x12,spaces
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
assignment:
    addi x6,x4,-65
    addi x7,x0,26
    bgeu x6,x7,error
    add x7,x8,x6
    lbu x4,0(x9)
    addi x9,x9,1
assignment_equal:
    jal x12,spaces
    addi x6,x4,-61
    bne x6,x0,error
    bge x10,x0,assignment_general_value
    addi x9,x0,45
    addi x10,x0,43
    addi x11,x0,32
    addi x12,x0,10
    addi x13,x0,26
    j direct_expression_start
assignment_general_value:
    lbu x4,0(x9)
    addi x9,x9,1
    jal x11,expression
    bne x4,x0,error
    sb x15,0(x7)
    j finished
print_long:
    li x14,1414416722
    jal x12,match_loaded
    j print_expression
print_short:
    lbu x4,0(x9)
    addi x9,x9,1
print_expression:
    jal x11,expression
    bne x4,x0,error
    sb x15,-251(x0)
    j finished
letter_l:
    addi x6,x4,-69
    beq x6,x0,let_command
    addi x6,x4,-73
    bne x6,x0,error
    li x14,21587
    jal x12,match
    jal x12,spaces
    bne x4,x0,error
    bge x10,x0,error
    addi x11,x8,32
list_next:
    bgeu x11,x2,prompt
    lhu x14,0(x11)
    andi x6,x14,127
    beq x6,x0,prompt
    jal x1,load_line
    sb x14,-251(x0)
    addi x6,x0,32
    sb x6,-252(x0)
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
    addi x14,x0,84
    jal x12,match
    jal x12,spaces
    j assignment
letter_r:
    addi x6,x4,-69
    beq x6,x0,rem_command
    addi x6,x4,-85
    bne x6,x0,error
    addi x14,x0,78
    jal x12,match
    jal x12,spaces
    bne x4,x0,error
    bge x10,x0,error
    addi x6,x0,255
    sb x6,29(x8)
    addi x11,x8,32
    j run_next
rem_command:
    addi x14,x0,77
    jal x12,match
    j finished
new_command:
    li x14,22341
    jal x12,match_loaded
    jal x12,spaces
    bne x4,x0,error
    bge x10,x0,error
    jal x1,initialize_directory
    j prompt
goto_command:
    li x14,5198927
    jal x12,match_loaded
    addi x7,x0,1
    j jump_expression
if_command:
    addi x14,x0,70
    jal x12,match_loaded
    jal x11,expression
    mv x7,x15
    addi x6,x4,-61
    bne x6,x0,if_then
    lbu x4,0(x9)
    addi x9,x9,1
    jal x11,expression
    xor x7,x7,x15
    sltiu x7,x7,1
if_then:
    addi x6,x4,-84
    bne x6,x0,error
    li x14,5129544
    jal x12,match
jump_expression:
    jal x11,expression
    bne x4,x0,error
    beq x7,x0,finished
    addi x6,x15,-1
    addi x14,x0,127
    bgeu x6,x14,error
    mv x12,x15
    addi x11,x8,32
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
    addi x6,x0,255
    sb x6,29(x8)
    j run_record
end_command:
    li x14,17486
    jal x12,match_loaded
    jal x12,spaces
    bne x4,x0,error
    j prompt
help_command:
    bge x10,x0,error
    beq x4,x0,help_emit
    addi x6,x4,-69
    bne x6,x0,error
    li x14,20556
    jal x12,match
    jal x12,spaces
    bne x4,x0,error
help_emit:
    addi x6,x0,1
    sb x6,-252(x0)
    j acknowledge
letter_c:
    beq x4,x0,clear
    addi x6,x4,-76
    beq x6,x0,clear_long
    j error
clear_long:
    li x14,5390661
    jal x12,match
    jal x12,spaces
    bne x4,x0,error
clear:
    sw x0,0(x8)
    sw x0,4(x8)
    sw x0,8(x8)
    sw x0,12(x8)
    sw x0,16(x8)
    sw x0,20(x8)
    sw x0,24(x8)
    addi x6,x0,12
    sb x6,-252(x0)
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
    lbu x6,29(x8)
    beq x6,x0,error
    addi x6,x6,-1
    sb x6,29(x8)
    addi x10,x11,2
    jal x1,load_line
    lbu x4,0(x9)
    addi x9,x9,1
    j statement
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
    addi x11,x8,32
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
    srli x9,x14,7
    andi x9,x9,480
    j edit_address
edit_new:
    beq x7,x0,prompt
    lbu x15,28(x8)
    addi x6,x0,16
    bgeu x15,x6,error
    slli x5,x15,1
    add x5,x8,x5
    addi x5,x5,32
    lhu x14,0(x5)
    srli x9,x14,7
    andi x9,x9,480
    addi x6,x15,1
    sb x6,28(x8)
edit_shift:
    bgeu x11,x5,edit_address
    addi x5,x5,-2
    lhu x14,0(x5)
    sh x14,2(x5)
    j edit_shift
edit_address:
    or x14,x9,x7
    slli x14,x14,7
    or x14,x14,x12
    addi x9,x9,512
    add x9,x9,x8
edit_stream:
    sb x4,0(x9)
    addi x9,x9,1
    lbu x4,-236(x0)
    bne x4,x0,edit_stream
edit_commit:
    sh x14,0(x11)
prompt:
    addi x6,x0,2
    sb x6,-252(x0)
acknowledge:
    sb x0,-250(x0)
    j wait_line


spaces:
    addi x6,x4,-32
    bne x6,x0,spaces_done
    lbu x4,0(x9)
    addi x9,x9,1
    j spaces
spaces_done:
    jalr x0,0(x12)
; x14 contains a little-endian keyword tail, starting after current char.
match:
    lbu x4,0(x9)
    addi x9,x9,1
match_loaded:
    andi x6,x14,255
    bne x4,x6,error
    srli x14,x14,8
    bne x14,x0,match
    lbu x4,0(x9)
    addi x9,x9,1
    jalr x0,0(x12)
 ; x14 is packed directory record. Cursor/end point into resident text.
load_line:
    srli x6,x14,7
    andi x9,x6,480
    addi x9,x9,512
    add x9,x9,x8
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
    add x6,x8,x6
    lbu x3,0(x6)
    lbu x4,0(x9)
    addi x9,x9,1
    j apply
term_negative:
    xori x5,x5,1
term_space:
    lbu x4,0(x9)
    addi x9,x9,1
    j term
digits:
    lbu x4,0(x9)
    addi x9,x9,1
    addi x6,x4,-48
    bgeu x6,x13,apply
    slli x4,x3,1
    slli x3,x3,3
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
    lbu x4,0(x9)
    addi x9,x9,1
    j operator
expression_done:
    jalr x0,0(x11)

delete_line:
    andi x3,x14,-128
    lbu x5,28(x8)
    addi x5,x5,-1
    sb x5,28(x8)
    slli x5,x5,1
    add x5,x8,x5
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
    addi x6,x0,3
    sb x6,-252(x0)
    j prompt

initialize_directory:
    li x6,268435456
    sw x6,32(x8)
    li x6,805314560
    sw x6,36(x8)
    li x6,1342193664
    sw x6,40(x8)
    li x6,1879072768
    sw x6,44(x8)
    li x6,2415951872
    sw x6,48(x8)
    li x6,2952830976
    sw x6,52(x8)
    li x6,3489710080
    sw x6,56(x8)
    li x6,4026589184
    sw x6,60(x8)
    sw x0,28(x8)
    jalr x0,0(x1)

; Fast direct-mode expression path.
direct_print_expression:
    addi x7,x0,0
direct_expression_start:
    addi x15,x0,0
    addi x5,x0,43
direct_term_read:
    lbu x1,-236(x0)
direct_term_loaded:
    addi x3,x1,-48
    bltu x3,x12,direct_digit_read
    beq x1,x11,direct_term_read
    beq x1,x9,direct_unary_minus
    beq x1,x10,direct_unary_plus
    j direct_variable_term
direct_term_value:
    addi x3,x1,-48
    bgeu x3,x12,direct_variable_term
direct_digit_read:
    lbu x1,-236(x0)
    addi x4,x1,-48
    bltu x4,x12,direct_more_digits
direct_apply_term:
    beq x5,x9,direct_subtract_term
    add x15,x15,x3
direct_after_term:
direct_operator_ready:
    mv x5,x1
    beq x1,x10,direct_term_read
    beq x1,x9,direct_term_read
    bne x1,x0,direct_operator_tail
    bne x7,x0,direct_assign_result
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
    beq x1,x11,direct_after_space
    j direct_operator_ready
direct_operator_tail:
    beq x1,x11,direct_after_space
    j error
direct_unary_minus:
    xori x5,x5,6
direct_unary_plus:
    lbu x1,-236(x0)
    j direct_term_loaded
direct_more_digits:
    slli x6,x3,1
    slli x3,x3,3
    add x3,x3,x6
    add x3,x3,x4
    j direct_digit_read
direct_variable_term:
    addi x6,x1,-65
    bgeu x6,x13,error
    add x6,x6,x8
    lbu x3,0(x6)
    lbu x1,-236(x0)
    j direct_apply_term

direct_numbered:
    mv x12,x6
    addi x13,x0,10
    j line_number_read
