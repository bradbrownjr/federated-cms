#!/usr/bin/env bash
# Update the lab install to the newest commit on main whose CI passed,
# then restart the server. Does nothing when GitHub cannot be reached,
# when CI has not finished or failed, or while a session is in progress.
# Run daily by fcms-update.timer, and by hand after a merge to main:
#   update.sh --wait   waits up to 30 minutes for CI on the new commit.
set -euo pipefail

main() {
    local home="${FCMS_HOME:-$HOME/.local/share/fcms-lab}"
    local repo="$home/federated-cms" kiss="$home/kissterm"
    # shellcheck disable=SC1091
    source "$home/lab.env"
    cd "$repo"

    if ! timeout 60 git fetch --quiet origin main 2>/dev/null; then
        log "GitHub not reachable; staying on $(git rev-parse --short HEAD)"
        return 0
    fi
    local old new
    old=$(git rev-parse HEAD)
    new=$(git rev-parse origin/main)
    [ "$old" = "$new" ] && return 0

    local ci waited=0 limit=0
    [ "${1:-}" = "--wait" ] && limit=1800
    ci=$(ci_state "$new")
    while [ "$ci" = pending ] && [ "$waited" -lt "$limit" ]; do
        sleep 30
        waited=$((waited + 30))
        ci=$(ci_state "$new")
    done
    case "$ci" in
        passed) ;;
        pending) log "main ${new:0:7}: CI not finished; will check again"; return 0 ;;
        *) log "main ${new:0:7}: CI $ci; staying on ${old:0:7}"; return 0 ;;
    esac

    if busy; then
        log "session in progress; update to ${new:0:7} deferred"
        return 0
    fi

    git checkout --quiet --detach "$new"
    if ! { install_kissterm "$kiss" && check_imports "$repo" "$kiss"; }; then
        log "update to ${new:0:7} failed its checks; back to ${old:0:7}"
        git checkout --quiet --detach "$old"
        install_kissterm "$kiss" || true
        return 1
    fi
    install_units "$repo"
    systemctl --user restart fcms-b2f.service
    sleep 3
    if ! systemctl --user is-active --quiet fcms-b2f.service; then
        log "server did not start on ${new:0:7}; back to ${old:0:7}"
        git checkout --quiet --detach "$old"
        install_kissterm "$kiss" || true
        install_units "$repo"
        systemctl --user restart fcms-b2f.service
        return 1
    fi
    log "updated ${old:0:7} -> ${new:0:7} and restarted fcms-b2f"
}

log() { echo "fcms-update: $*"; }

# passed, pending, failed or unknown, from the commit's GitHub check runs.
ci_state() {
    local slug
    slug=$(git remote get-url origin | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')
    local body
    body=$(timeout 30 curl -fsS -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/$slug/commits/$1/check-runs?per_page=100" 2>/dev/null) ||
        { echo unknown; return; }
    printf '%s' "$body" | python3 -c '
import json, sys
try:
    runs = json.load(sys.stdin)["check_runs"]
except Exception:
    print("unknown"); raise SystemExit
if not runs:
    print("pending")
elif any(r["status"] != "completed" for r in runs):
    print("pending")
elif all(r["conclusion"] in ("success", "neutral", "skipped") for r in runs):
    print("passed")
else:
    print("failed")
'
}

# True while a client is connected to the server or the bridge is running.
busy() {
    systemctl --user is-active --quiet fcms-kiss-bridge.service && return 0
    [ -n "$(ss -Htn state established "( sport = :${FCMS_B2F_PORT} )")" ]
}

# Check out the kissterm commit named in deploy/lab/kissterm.pin.
install_kissterm() {
    local kiss=$1 pin
    pin=$(tr -d '[:space:]' < deploy/lab/kissterm.pin)
    [ "$(git -C "$kiss" rev-parse HEAD 2>/dev/null)" = "$pin" ] && return 0
    timeout 120 git -C "$kiss" fetch --quiet origin || true
    git -C "$kiss" checkout --quiet --detach "$pin"
}

check_imports() {
    PYTHONPATH="$1/src:$2" python3 -c 'import federated_cms.b2f, federated_cms.kiss_lab'
}

# Copy the unit files when they differ from the installed ones.
install_units() {
    local dir="$HOME/.config/systemd/user" changed=0 unit
    mkdir -p "$dir"
    for unit in "$1"/deploy/lab/systemd/*; do
        if ! cmp -s "$unit" "$dir/$(basename "$unit")"; then
            cp "$unit" "$dir/"
            changed=1
        fi
    done
    [ "$changed" = 1 ] && systemctl --user daemon-reload
    return 0
}

main "$@"
exit
