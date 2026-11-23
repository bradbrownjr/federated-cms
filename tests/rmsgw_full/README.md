# Isolated full rmsgw gateway experiment

This is a research harness, not a production gateway or a Federated CMS
adapter. It builds the actual upstream Linux `rmsgw` main, session, login and
gateway relay code from a **separate clean checkout** of commit
`3147f017545f43c4b83ef6a037b8b0e38d0719af`. It does not copy GPL-licensed
upstream source into this harness or alter the upstream checkout. The upstream
gateway is GPL licensed; review its LICENSE before distributing any binary.

Run on Linux with user/network/IPC/mount namespaces, `unshare`, `ip`,
Git, `tar`, a C compiler, `pkg-config`, and libxml2 development headers installed:

```text
python3 -I tests/rmsgw_full/run_experiment.py --source /path/to/clean/rmsgw
```

An unavailable namespace or missing prerequisite fails the experiment; there
is no unsafe fallback. The runner verifies source revision and checkout state,
then builds a Git archive of the pinned commit in a private directory under
`/tmp`, so ignored checkout artifacts cannot affect compilation. It generates a lab-only
`pathnames.h` pointing gateway configuration paths into that directory. A lab
shim redirects the two hardcoded paths used for shared-memory setup and
disables `openlog`, `closelog` and `syslog` to keep the gateway disconnected
from the host logger. The build omits `HOOKS`, `rmsgw_aci`, and updater processes.
No `HOME` variable is set or altered.

Run only the `run_experiment.py` entrypoint. The internal `namespace_case.py`
helper trusts namespace IDs supplied by that launcher; it cannot establish
isolation if invoked directly with fabricated arguments.

For each test case the launcher creates new user, network, IPC and mount
namespaces with private mount propagation. It brings up loopback inside the
new network namespace. The case driver verifies all four namespace IDs differ
from the launcher's, checks that only loopback is up, requires empty main IPv4
and IPv6 routing tables and no IPv6 address except `::1`, and creates a host
table containing exactly one numeric `127.0.0.1` entry. The runner kills the
full test process group if a case fails, is interrupted, or times out.
A kernel-created tunnel interface may
exist but must remain down and have no route. The test never opens real RF,
uses no production credentials or hostnames, and runs no network-reporting
or hook processes. It uses a synthetic local fake CMS and an AF_UNIX
socketpair to simulate the client-facing stream.

The valid case verifies the upstream gateway's Secure Gateway Login against a
known disposable fixture, forwards all 256 byte values in each direction, and
checks multiple synchronized fragments in both directions. It then checks a
clean gateway exit following simulated CMS disconnect. The wrong-password
case checks that the fake CMS rejects an incorrect gateway response and the
gateway relays that rejection before any B2F data. It does not establish a
real AX.25 connection, execute an actual B2F client through the gateway, or
interoperate with the Federated CMS server. The mock CMS is a test fixture,
not a secure implementation.
