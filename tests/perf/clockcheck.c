/* clockcheck.c — the real clock of the core it runs on: 2e9 dependent integer adds, one cycle each, so
 * adds per second = cycles per second. Independent of what the kernel or the driver reports (on 2026-10-05 it showed that
 * CPU boost was silently on during bench runs). Used by tests/bench/hostprofile.sh.
 * Build: gcc -O1 -o clockcheck clockcheck.c   Run pinned: taskset -c N ./clockcheck */
#include <stdio.h>
#include <time.h>
#include <stdint.h>
int main(void) {
    struct timespec a, b; uint64_t x = 0, n = 2000000000ULL;   /* 2e9 dependent adds, 1 cycle each */
    clock_gettime(CLOCK_MONOTONIC, &a);
    for (uint64_t i = 0; i < n; i += 8)
        __asm__ volatile("add $1,%0\n\tadd $1,%0\n\tadd $1,%0\n\tadd $1,%0\n\tadd $1,%0\n\tadd $1,%0\n\tadd $1,%0\n\tadd $1,%0" : "+r"(x));
    clock_gettime(CLOCK_MONOTONIC, &b);
    double s = (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;
    printf("%.0f MHz (%.3f s, x=%lu)\n", n / s / 1e6, s, (unsigned long) x);
    return 0;
}
