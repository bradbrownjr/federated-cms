# Lab install with systemd user services

Runs the lab B2F server permanently and keeps it on the newest `main`
that passed CI. Everything runs as your user; no root.

| Unit | What it does |
| --- | --- |
| `fcms-b2f.service` | B2F server on `127.0.0.1:FCMS_B2F_PORT`; restarts on failure. |
| `fcms-kiss-bridge.service` | TCP KISS bridge to the radio; transmits. Started by hand, takes one session from `FCMS_PEER` within 300 s, then exits. |
| `fcms-update.timer` | Runs `update.sh` once a day (04:30 local, or at the next start if missed). |

`update.sh` fetches `origin/main`. It updates only when GitHub answers,
every check run on the new commit has completed and passed, and no
client is connected and the bridge is stopped. It then checks the new
code imports, installs changed unit files, restarts `fcms-b2f`, and goes
back to the previous commit if the server does not start. Without
GitHub, the node keeps running the code it has.

## Install

```sh
loginctl enable-linger "$USER"          # once, so services outlive logout
curl -fsSLO https://raw.githubusercontent.com/bradbrownjr/federated-cms/main/deploy/lab/install.sh
bash install.sh                         # creates ~/.local/share/fcms-lab/lab.env
$EDITOR ~/.local/share/fcms-lab/lab.env
# put the disposable lab password in ~/.local/share/fcms-lab/lab-password.txt
bash install.sh
```

Read the *Winlink Express account warning* in
[docs/TCP-KISS-B2F-LAB.md](../../docs/TCP-KISS-B2F-LAB.md) before choosing
the lab password.

## Use

```sh
systemctl --user start fcms-kiss-bridge     # before a radio session
journalctl --user -u fcms-b2f -u fcms-kiss-bridge -u fcms-update -f
~/.local/share/fcms-lab/federated-cms/deploy/lab/update.sh --wait   # after a merge: wait for CI, then update
```
