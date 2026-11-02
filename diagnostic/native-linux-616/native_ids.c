#include <sys/syscall.h>
#include <stdio.h>
int main(void) {
    printf("%ld\n", (long)SYS_fdatasync);
    return 0;
}
