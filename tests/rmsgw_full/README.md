# Isolated full rmsgw gateway experiment

This research harness builds the upstream Linux `rmsgw` main, session, login and
gateway relay code from a **separate clean checkout** of commit
`3147f017545f43c4b83ef6a037b8b0e38d0719af`. It does not copy GPL-licensed
upstream source into this harness or alter the upstream checkout. The upstream
gateway is GPL licensed; review its LICENSE before distributing any binary.

Run on Linux with user/network/IPC/mount namespaces, `unshare`, `ip`,
Git, `tar`, a C compiler, `pkg-config`, and libxml2 development headers installed:

```text
python3 -I tests/rmsgw_full/run_experiment.py --source /path/to/clean/rmsgw
```

To run the client exchange as well, supply a local kissterm Git checkout
containing commit `f3310c71efa8e3e23263ed25745a9c365a9efc74` and use a Python
interpreter with `platformdirs` installed. The runner exports the client package
from that commit. The client runs with its upstream test profile isolation
helper, which redirects its data directories and disables the OS keyring.

```text
python3 -I tests/rmsgw_full/run_experiment.py --source /path/to/clean/rmsgw --kissterm-source /path/to/kissterm
```

To include the Pat CLI case, supply a trusted local binary built in a clean
checkout of Pat commit `2ad31e8fc1f1748ac1b00cac9d88da54e3e0efe2`. The
launcher uses `go version -m` to check the staged binary's module path,
revision, and clean build metadata before running it inside the namespaces.
The two client options can be used independently or together.

```text
python3 -I tests/rmsgw_full/run_experiment.py --source /path/to/clean/rmsgw --kissterm-source /path/to/kissterm --pat-binary /path/to/pat
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
or hook processes. The radio-side stream is an AF_UNIX socketpair for the raw
relay and kissterm cases and an accepted loopback socket for Pat. The gateway's
CMS side connects to a separate isolated loopback listener in every case.

The default run checks a known SGL response, all 256 byte values in both
directions, synchronized fragments, and clean process exit. It also runs
the test-only SGL adapter with the wrong gateway secret. The client stream
must contain a denial, a B2F greeting must be absent, and the seeded mailbox
must remain intact after reopening SQLite.

With `--kissterm-source`, the pinned kissterm B2F client runs on the
socketpair. The gateway connects to `sgl_adapter.py`, which challenges the
configured synthetic caller and gateway, then hands the same socket to
Federated CMS's B2F session. B2F issues its own challenge using the separate
mailbox password. The positive case verifies a newly accepted upload and the
exact downloaded B2 bytes, including binary attachments. After the exchange
it reopens the shared mailbox to check the upload bytes, retained download,
and delivery acknowledgement. Another case supplies a wrong B2F password
and checks that the client receives the protocol rejection and both mailbox
directions retain their original state. These cases require clean gateway exit.

The Pat case composes an upload and supplies the CLI with two synthetic
Telnet prompts. The accepted socket then becomes the gateway's stdin and
stdout for the complete exchange. The test checks the uploaded B2 bytes
against Pat's original outbox file, the move to its sent folder, the downloaded
message body and attachment in its inbox, and the durable server-side
acknowledgement. Pat adds a local unread header to its inbox copy.

Pat completed the exchange and returned zero. The upstream gateway exited
on SIGPIPE during Pat teardown in this build. The harness
reports the observed gateway exit and accepts zero or SIGPIPE only for this
case, while requiring all client and database checks to pass. The kissterm
terminal UI, AX.25, TNCs and RF are outside these experiments.
