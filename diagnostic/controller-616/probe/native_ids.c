#include <stdio.h>
#include <sys/syscall.h>

int main(void) {
    printf("{\"fsync\":%ld,\"fdatasync\":%ld,\"write\":%ld}\n",
           (long)SYS_fsync, (long)SYS_fdatasync, (long)SYS_write);
    return 0;
}
