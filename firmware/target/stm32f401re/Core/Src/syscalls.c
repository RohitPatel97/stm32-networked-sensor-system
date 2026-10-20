#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>
#include "main.h"

/* There is no POSIX console/filesystem. Telemetry uses HAL UART directly. */
int _close(int file) { (void)file; errno = EBADF; return -1; }
int _fstat(int file, struct stat *info) {
    (void)file; (void)info; errno = EBADF; return -1;
}
int _isatty(int file) { (void)file; return 0; }
off_t _lseek(int file, off_t offset, int origin) {
    (void)file; (void)offset; (void)origin; errno = EBADF; return (off_t)-1;
}
int _read(int file, void *buffer, size_t length) {
    (void)file; (void)buffer; (void)length; errno = EBADF; return -1;
}
int _write(int file, const void *buffer, size_t length) {
    (void)file; (void)buffer; (void)length; errno = EBADF; return -1;
}
int _getpid(void) { return 1; }
int _kill(int process, int signal) {
    (void)process; (void)signal; errno = ENOSYS; return -1;
}
void _exit(int status) {
    (void)status;
    Error_Handler();
    for (;;) { }
}

/* newlib-nano float formatting may allocate. Reserve a separate bounded libc
 * heap; FreeRTOS heap_4 uses its own static array. Only telemetry uses libc
 * formatting after the scheduler starts. */
void *_sbrk(ptrdiff_t increment) {
    extern char __libc_heap_start__;
    extern char __libc_heap_end__;
    static uintptr_t current;
    const uintptr_t start = (uintptr_t)&__libc_heap_start__;
    const uintptr_t end = (uintptr_t)&__libc_heap_end__;
    uintptr_t previous;
    if (current == 0U) { current = start; }
    if (increment < 0 || (uintptr_t)increment > end - current) {
        errno = ENOMEM;
        return (void *)-1;
    }
    previous = current;
    current += (uintptr_t)increment;
    return (void *)previous;
}
