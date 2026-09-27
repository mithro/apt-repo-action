#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("apt-repo-selftest-hello " VERSION);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "--cpu-arch") == 0) {
#ifdef __ARM_ARCH
        printf("%d\n", __ARM_ARCH);
#else
        puts("none");
#endif
        return 0;
    }
    puts("hello");
    return 0;
}
