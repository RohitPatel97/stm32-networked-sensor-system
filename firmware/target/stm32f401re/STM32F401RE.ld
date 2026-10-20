ENTRY(Reset_Handler)
MEMORY
{
    FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 384K
    RAM (xrw) : ORIGIN = 0x20000000, LENGTH = 96K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
PHDRS
{
    flash PT_LOAD FLAGS(5); /* read + execute */
    ram PT_LOAD FLAGS(6);   /* read + write */
    ram_zero PT_LOAD FLAGS(6);
}
SECTIONS
{
    .isr_vector : { . = ALIGN(4); KEEP(*(.isr_vector)) } > FLASH :flash
    .text : {
        . = ALIGN(4);
        *(.text .text.*) *(.rodata .rodata.*)
        KEEP(*(.init)) KEEP(*(.fini))
        . = ALIGN(4);
        _etext = .;
    } > FLASH
    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM.exidx : {
        __exidx_start = .; *(.ARM.exidx*) __exidx_end = .;
    } > FLASH
    .preinit_array : {
        PROVIDE_HIDDEN(__preinit_array_start = .);
        KEEP(*(.preinit_array*))
        PROVIDE_HIDDEN(__preinit_array_end = .);
    } > FLASH
    .init_array : {
        PROVIDE_HIDDEN(__init_array_start = .);
        KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array*))
        PROVIDE_HIDDEN(__init_array_end = .);
    } > FLASH
    .fini_array : {
        PROVIDE_HIDDEN(__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array*))
        PROVIDE_HIDDEN(__fini_array_end = .);
    } > FLASH
    _sidata = LOADADDR(.data);
    .data : {
        . = ALIGN(4); _sdata = .; *(.data .data.*) . = ALIGN(4); _edata = .;
    } > RAM AT > FLASH :ram
    .bss (NOLOAD) : AT(ADDR(.bss)) {
        . = ALIGN(8); _sbss = .; __bss_start__ = .;
        *(.bss .bss.*) *(COMMON)
        . = ALIGN(8); _ebss = .; __bss_end__ = .;
    } > RAM :ram_zero
    .libc_heap (NOLOAD) : AT(ADDR(.libc_heap)) {
        . = ALIGN(8); __libc_heap_start__ = .;
        . += 4096; __libc_heap_end__ = .;
    } > RAM :ram_zero
    .main_stack (NOLOAD) : AT(ADDR(.main_stack)) {
        . = ALIGN(8); . += 2048;
    } > RAM :ram_zero
    ASSERT(. <= _estack, "RAM overflow: no room for main/interrupt stack")
    /DISCARD/ : { *(.note.GNU-stack) }
}
