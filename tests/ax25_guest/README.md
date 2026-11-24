# Offline AX.25 gateway guest

This opt-in experiment boots a fresh Debian amd64 kernel and RAM filesystem
for each of three cases. A synthetic N0CALL station calls N0LAB-10 over a
virtual KISS link. Stock ax25d accepts the connected Linux AX.25 socket and
hands it to the pinned rmsgw gateway. The gateway opens a separate guest
loopback connection to the test SGL adapter and Federated CMS mailbox.

| Case | Client | Required result |
| --- | --- | --- |
| `pat` | Pat CLI, libax25, second Linux KISS port | Exact uploaded B2 bytes, both binary attachments, sent and inbox files, persisted upload and download acknowledgement |
| `kissterm` | kissterm AX25Station, TCP KISS transport, B2F client | Exact upload and download with attachments, persisted upload and download acknowledgement |
| `wrong-mailbox` | Same kissterm client with the gateway secret in its B2F password field | SGL succeeds, B2F issues a mailbox challenge and rejects the client, no upload and original download still pending after reopening SQLite |

Each case checks the callsigns, SSIDs, KISS port 0 and empty repeater path.
The bridge must see SABM, UA, bidirectional I frames, and a DISC with an
answering UA. The dispatch check requires the connected peer N0CALL-0 and
the same AF_AX25/SOCK_SEQPACKET socket on stdin and stdout. The launcher
requires exactly one successful dispatch per boot. The fixture shuts down
ax25d before closing the PTYs. All addresses and credentials are synthetic.

## Local inputs

Use an unprivileged Debian 13 amd64 host with user, IPC and network namespaces,
`unshare`, Python 3.13, Git, GNU tar, GCC, binutils, pkg-config, Go
(`go version -m`), dpkg and libxml2 development headers. The run uses
approximately 0.5 GiB of temporary storage and 384 MiB of guest RAM.
Supply the following trusted local inputs before starting.

* An rmsgw Git checkout containing commit
  `3147f017545f43c4b83ef6a037b8b0e38d0719af` from `nwdigitalradio/rmsgw`.
* A kissterm Git checkout containing commit
  `f3310c71efa8e3e23263ed25745a9c365a9efc74` from `bradbrownjr/kissterm`.
  The launcher archives these exact commits and builds from the private
  exports. Federated CMS source and fixtures come from the current checkout.
* A trusted linux/amd64 Pat binary from the clean `la5nta/pat` commit
  `2ad31e8fc1f1748ac1b00cac9d88da54e3e0efe2`, built with cgo and
  `-tags libax25`. The launcher checks the staged binary's embedded Go module
  path, revision, clean tree, OS, architecture, cgo and build tag. Pat only
  executes in the guest. These metadata checks rely on trusting the builder.
* A local directory containing the Debian archives named in
  [packages.sha256](packages.sha256). The exact names, including escaped
  epochs, are the filenames produced by `apt download`. Use a prepared
  Debian archive cache or obtain those named versions separately. The
  launcher copies, hashes and extracts each archive into its private
  work directory. These include kernel 6.12.118, its matching AX.25, mkiss
  and crc16 modules, BusyBox, ax25-tools, libax25, QEMU and SeaBIOS.
* The installed guest runtime files matching [runtime.sha256](runtime.sha256).
  For each Python source tree the digest covers sorted
  lines of the form `SHA256  relative/path\n` for included, dpkg-listed
  `.py` and `.so` files. The guest omits sitecustomize and cached bytecode.
  The interpreter and shared libraries have individual file hashes.
  The following Debian package versions supply these files.

```text
libbz2-1.0              1.0.8-6
libc6                   2.41-12+deb13u5
libdb5.3t64             5.3.28+dfsg2-9
libexpat1               2.9.0-1~deb13u1
libffi8                 3.4.8-2
libgcc-s1               14.2.0-19
liblzma5                5.8.1-1+deb13u2
libncursesw6            6.5+20250216-2
libpython3.13-minimal   3.13.5-2+deb13u5
libpython3.13-stdlib    3.13.5-2+deb13u5
libreadline8t64         8.2-6
libsqlite3-0            3.46.1-7+deb13u2
libssl3t64              3.5.9-1~deb13u1
libtinfo6               6.5+20250216-2
libuuid1                2.41.5-0+deb13u1
libxml2                 2.12.7+dfsg+really2.9.14-2.1+deb13u3
libzstd1                1.5.7+dfsg-1
python3-markdown-it     3.0.0-3
python3-mdurl           0.1.2-1
python3-platformdirs    4.3.7-1
python3-pygments        2.18.0+dfsg-2+deb13u1
python3-rich            13.9.4-1
python3.13-minimal      3.13.5-2+deb13u5
zlib1g                  1:1.3.dfsg+really1.3.1-1+deb13u1
```

To prepare Pat offline, use Go 1.26.0, a prepopulated module cache matching
the checkout's go.sum, and the cached Debian amd64 libax25 and libax25-dev
packages. The development package version is `0.0.12-rc5+git20230513+5581005-1`
and its SHA-256 is
`c8c58c00ca6a4ded79148de4864236851364494537787c0910b09461f778af6f`.
Extract those two verified archives with `dpkg-deb -x` into a private
directory, set AX25_PREFIX to its absolute path, and run the command below
from the clean pinned Pat checkout with Go 1.26.0 on PATH. Choose an output
path outside the checkout.

```sh
GOTOOLCHAIN=local GOENV=off GOTELEMETRY=off GOPROXY=off GOSUMDB=off \
  CGO_ENABLED=1 CGO_CFLAGS="-I$AX25_PREFIX/usr/include" \
  CGO_LDFLAGS="-L$AX25_PREFIX/usr/lib" \
  go build -mod=readonly -buildvcs=true -tags libax25 -o /path/to/pat-lab .
```

## Run

From the Federated CMS checkout, run the launcher with all four local inputs.

```sh
python3 -IB tests/ax25_guest/run.py \
  --source /path/to/rmsgw --kissterm-source /path/to/kissterm \
  --pat-binary /path/to/pat-lab --packages /path/to/debian-cache \
  --log-dir /path/to/new-console-log-directory
```

The default runs all three cases. Select one with `--case pat`,
`--case kissterm`, or `--case wrong-mailbox`. Each selection builds the same
complete guest. The optional log directory must be new. Otherwise logs live
in the disposable build directory, and the launcher prints the console tail
on failure. A missing archive, checksum mismatch, missing revision or failed
namespace prevents boot. A timeout, exception or missing case assertion fails
the run. Each successful boot prints its own `PASS` line on the host.

The launcher sets up QEMU TCG inside fresh host user, network and IPC
namespaces with no NIC, disks, host shares or device passthrough. The only
guest-to-host console is QEMU's serial output. Guest init loads the AX.25
modules and raises guest loopback. The guest checks its exact interfaces and
routes before dispatch. There is no RF hardware, physical TNC, public CMS
or real account on this path. The harness performs no downloads.

The host compiler, tools and remaining native libraries used by QEMU are
trusted host prerequisites. The fingerprints cover the listed inputs and
guest runtime. This is a reproducible functional experiment with timestamps,
challenges and message IDs generated at run time. The host namespaces do
not sandbox the host filesystem. Each boot exercises one caller, one session
and one exchange or rejection. The host log records frame categories and
fixture shutdown. Gateway process exit status and signal counts require a
separate traced run. See the [full rmsgw harness](../rmsgw_full/README.md)
for the gateway build shims and licence information.
