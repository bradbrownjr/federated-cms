/* Isolate the gateway's logging and hard-coded status-block paths. */
#include <string.h>
#include <sys/ipc.h>
#include <sys/stat.h>
#include <syslog.h>

#include <../pathnames.h>

key_t __real_ftok(const char *path, int project_id);
int __real_stat(const char *path, struct stat *result);

key_t __wrap_ftok(const char *path, int project_id) {
    if (strcmp(path, "/etc/rmsgw") == 0) {
        path = LAB_CONFIG_DIR;
    }
    return __real_ftok(path, project_id);
}

int __wrap_stat(const char *path, struct stat *result) {
    if (strcmp(path, "/etc/rmsgw/shm_debug") == 0) {
        path = LAB_CONFIG_DIR "/shm_debug";
    }
    return __real_stat(path, result);
}

void __wrap_openlog(const char *ident, int option, int facility) {
    (void)ident;
    (void)option;
    (void)facility;
}

void __wrap_closelog(void) {
}

void __wrap_syslog(int priority, const char *format, ...) {
    (void)priority;
    (void)format;
}
