/* Offline test for the experimental final-notice patch; no network or RF. */
#include <signal.h>
#include <errno.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "rmsgw/session.c"

static int same_mask(const sigset_t *before)
{
    sigset_t after;
    int signum;
    if (sigprocmask(SIG_SETMASK, NULL, &after) != 0) return 0;
    for (signum = 1; signum < NSIG; signum++)
        if (sigismember(before, signum) != sigismember(&after, signum)) return 0;
    return 1;
}

int main(void)
{
    int fd[2], sig;
    char text[] = "notice\n", received[sizeof(text)];
    sigset_t pipe_set, original, pending, before;

    signal(SIGPIPE, SIG_DFL);
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, fd) != 0) return 1;
    if (sigprocmask(SIG_SETMASK, NULL, &before) != 0) return 17;
    if (send_final_notice(fd[0], text, strlen(text)) != (int)strlen(text)) return 2;
    if (!same_mask(&before)) return 18;
    if (read(fd[1], received, strlen(text)) != (ssize_t)strlen(text)) return 3;
    if (memcmp(received, text, strlen(text)) != 0) return 4;
    close(fd[1]);
    if (send_final_notice(fd[0], text, strlen(text)) != -1 || errno != EPIPE) return 5;
    if (!same_mask(&before)) return 19;
    close(fd[0]);

    if (pipe(fd) != 0) return 6;
    close(fd[0]);
    if (send_final_notice(fd[1], text, strlen(text)) != -1 || errno != EPIPE) return 7;
    if (!same_mask(&before)) return 20;
    sigemptyset(&pipe_set);
    sigaddset(&pipe_set, SIGPIPE);
    if (sigprocmask(SIG_BLOCK, &pipe_set, &original) != 0) return 8;
    if (sigprocmask(SIG_SETMASK, NULL, &before) != 0) return 21;
    /* A caller that already blocked SIGPIPE must retain a newly pending one. */
    if (send_final_notice(fd[1], text, strlen(text)) != -1 || errno != EPIPE) return 14;
    if (!same_mask(&before)) return 22;
    if (sigpending(&pending) != 0 || sigismember(&pending, SIGPIPE) != 1) return 15;
    if (sigwait(&pipe_set, &sig) != 0 || sig != SIGPIPE) return 16;
    if (raise(SIGPIPE) != 0) return 9;
    if (send_final_notice(fd[1], text, strlen(text)) != -1 || errno != EPIPE) return 10;
    if (!same_mask(&before)) return 23;
    if (sigpending(&pending) != 0 || sigismember(&pending, SIGPIPE) != 1) return 11;
    if (sigwait(&pipe_set, &sig) != 0 || sig != SIGPIPE) return 12;
    if (sigprocmask(SIG_SETMASK, &original, NULL) != 0) return 13;
    close(fd[1]);
    return 0;
}
