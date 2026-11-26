#!/usr/bin/env bash
# Install the Federated CMS lab as systemd user services under
# ~/.local/share/fcms-lab. Safe to run again. Needs git, python3 3.11+,
# curl and ss; no root (run `loginctl enable-linger` once so the services
# keep running after logout).
set -euo pipefail

home="${FCMS_HOME:-$HOME/.local/share/fcms-lab}"
repo_url="${FCMS_REPO:-https://github.com/bradbrownjr/federated-cms.git}"
kiss_url="${FCMS_KISSTERM_REPO:-https://github.com/bradbrownjr/kissterm.git}"

mkdir -p "$home"
chmod 700 "$home"
[ -d "$home/federated-cms/.git" ] || git clone --quiet "$repo_url" "$home/federated-cms"
[ -d "$home/kissterm/.git" ] || git clone --quiet "$kiss_url" "$home/kissterm"
git -C "$home/federated-cms" fetch --quiet origin main
git -C "$home/kissterm" fetch --quiet origin
git -C "$home/federated-cms" checkout --quiet --detach origin/main
git -C "$home/kissterm" checkout --quiet --detach \
    "$(tr -d '[:space:]' < "$home/federated-cms/deploy/lab/kissterm.pin")"

if [ ! -f "$home/lab.env" ]; then
    cp "$home/federated-cms/deploy/lab/lab.env.example" "$home/lab.env"
    echo "Edit $home/lab.env, then run this again."
    exit 1
fi
if [ ! -s "$home/lab-password.txt" ]; then
    echo "Put the lab password in $home/lab-password.txt (mode 600), then run this again."
    exit 1
fi
chmod 600 "$home/lab.env" "$home/lab-password.txt"

mkdir -p "$HOME/.config/systemd/user"
cp "$home"/federated-cms/deploy/lab/systemd/* "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now fcms-b2f.service fcms-update.timer
systemctl --user --no-pager status fcms-b2f.service | head -5
