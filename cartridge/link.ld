ENTRY(_start)
PHDRS { text PT_LOAD FLAGS(5); }
SECTIONS
{
  .text 0x800 : { *(.text.start) *(.text .text.*) } :text
  /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.eh_frame) }
}
