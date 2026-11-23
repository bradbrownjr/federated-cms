/* Offline harness for the upstream GPL-licensed rmsgw cmslogin() routine.
 * The input descriptor is an inherited AF_UNIX socketpair; no network or RF
 * configuration is loaded. Credentials are disposable test fixtures only.
 */
#include <stdlib.h>
#include <syslog.h>
#include "rmslib.h"

int main(int argc, char **argv) {
    config config_fixture = {0};
    channel channel_fixture = {0};
    if (argc != 3) return 2;
    /* Upstream debug logs include the gateway password. */
    setlogmask(LOG_MASK(LOG_EMERG));
    channel_fixture.ch_password = argv[2];
    channel_fixture.ch_callsign = "N0LAB-10";
    return cmslogin(atoi(argv[1]), &config_fixture, &channel_fixture,
                    "N0CALL", "ignored-host-password") == 0 ? 0 : 1;
}
