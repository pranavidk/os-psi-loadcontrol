/*
 * wsload - workload generator with a known working-set size.
 *
 * usage: wsload <total_MB> <ws_MB> <million_touches>
 *
 * Allocates total_MB, touches every page once, then performs
 * million_touches * 10^6 random writes inside the first ws_MB.
 * The working set is therefore ~ws_MB, independent of total_MB.
 *
 * On normal completion, or when stopped early with SIGTERM, it prints:
 *   pid=<pid> done=<0|1> touches=<n> elapsed=<s> throughput=<M touches/s>
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stop_requested = 0;
static void on_term(int sig) { (void)sig; stop_requested = 1; }

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: wsload <total_MB> <ws_MB> <million_touches>\n");
        return 1;
    }
    size_t page  = (size_t)sysconf(_SC_PAGESIZE);
    size_t total = (size_t)atol(argv[1]) << 20;
    size_t ws    = (size_t)atol(argv[2]) << 20;
    unsigned long work = (unsigned long)atol(argv[3]) * 1000000UL;
    if (ws == 0 || ws > total) {
        fprintf(stderr, "ws_MB must be between 1 and total_MB\n");
        return 1;
    }
    signal(SIGTERM, on_term);

    char *buf = malloc(total);
    if (!buf) { perror("malloc"); return 1; }
    for (size_t i = 0; i < total; i += page) buf[i] = 1;   /* touch each page once */

    size_t ws_pages = ws / page;
    unsigned int seed = (unsigned int)getpid();
    unsigned long n = 0;
    double t0 = now();
    while (n < work) {                                       /* random touches in W  */
        buf[((size_t)rand_r(&seed) % ws_pages) * page]++;
        n++;
        if ((n & 0xFFF) == 0 && stop_requested) break;
    }
    double secs = now() - t0;
    printf("pid=%d done=%d touches=%lu elapsed=%.2f throughput=%.3f\n",
           getpid(), n >= work, n, secs, secs > 0 ? n / secs / 1e6 : 0.0);
    fflush(stdout);
    free(buf);
    return 0;
}
