#!/bin/bash
# Isolated HOTAS Mapper test stack: dev/fake-daemon.py + a mapper instance on its own socket,
# mapping file and port. Never touches the user's ~/.config/vkb-hotas or their running mapper.
#
#   test_stack.sh start [--button-count 16|32 (default 32; ignored with --mapping)] [--layout FILE] [--mapping FILE]
#                       [--fresh (no mapping file: first-run defaults)] [--model vkb|extreme3d|multihat] [--sweep] [--throttle SEL] [--twin]
#   test_stack.sh url              print the test mapper URL (with token)
#   test_stack.sh press SOURCE [N] simulate a press: a button number or hat:up|down|left|right, on simulated
#                                  stick N (0 = right OT 231d:3200, 1 = left OT 231d:3201; default: the stick role's)
#   test_stack.sh unplug N / plug N   unplug or replug simulated stick N
#   test_stack.sh stop             stop both processes and delete the test data
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../../.." && pwd)
dev=${XDG_RUNTIME_DIR:-/tmp}/vkb-hotas-dev        # short: unix socket paths max out at 107 bytes
log=$dev.logs
port=${VKB_TEST_PORT:-8765}                        # the port the browser pane is allowed to open
export PYTHONDONTWRITEBYTECODE=1

kill_stack() {
    # select by exact cmdline, never `pkill -f` (it can match this very shell)
    for p in $(pgrep -x python3); do
        c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null) || continue
        # only processes carrying the private test dir: the user's own mapper can never match
        case "$c" in *fake-daemon.py*"--dir $dev"*|*vkb-mapper.py*"--mapping $dev/"*) kill "$p" 2>/dev/null;; esac
    done
    sleep 0.6
}

case "${1:-}" in
start)
    shift
    count=32 layout="" mapping="" fresh="" still=--still extra=()
    while [[ $# -gt 0 ]]; do
        case $1 in
            --button-count) count=$2; shift 2;;
            --layout) layout=$2; shift 2;;
            --mapping) mapping=$2; shift 2;;
            --sweep) still=""; shift;;
            --throttle) extra+=(--throttle "$2"); shift 2;;
            --twin) extra+=(--twin); shift;;
            --model) extra+=(--model "$2"); shift 2;;
            --fresh) fresh=1; shift;;
            *) echo "unknown option $1" >&2; exit 2;;
        esac
    done
    kill_stack
    rm -rf "$dev"; mkdir -p "$dev" "$log"
    [[ -n $layout ]] && cp "$layout" "$dev/layout.json"     # e.g. a copy of the user's file
    if [[ -n $mapping ]]; then
        cp "$mapping" "$dev/mapping.json"
    elif [[ -z $fresh ]]; then  # the built-in default, with the T.16000M's button count from --button-count
        (cd "$repo" && python3 -c '
import json, sys
sys.path.insert(0, "common")
import vkb_common as c
p = c.default_profile()
p["stick"] = c.default_role_profile("stick", int(sys.argv[2]))
json.dump(c.normalize_mapping({"version": 2, "active": "Default", "profiles": {"Default": p}}), open(sys.argv[1], "w"), indent=2)' "$dev/mapping.json" "$count") || exit 1
    fi
    # setsid -f always forks and returns at once, fully detached (no "( ... & )" tricks)
    (cd "$repo" && exec setsid -f dev/fake-daemon.py --dir "$dev" $still "${extra[@]}" < /dev/null > "$log/fake-daemon.log" 2>&1)
    for _ in $(seq 20); do [[ -S $dev/ctl.sock ]] && break; sleep 0.2; done
    [[ -S $dev/ctl.sock ]] || { echo "fake daemon did not start:"; cat "$log/fake-daemon.log"; exit 1; }
    (cd "$repo" && VKB_HOTAS_CTL=$dev/ctl.sock exec setsid -f mapper/vkb-mapper.py --no-browser --port "$port" --mapping "$dev/mapping.json" < /dev/null > "$log/mapper.log" 2>&1)
    for _ in $(seq 20); do grep -q 'http://' "$log/mapper.log" 2>/dev/null && break; sleep 0.2; done
    url=$(grep -o 'http://[^ ]*' "$log/mapper.log" | head -1)
    case "$url" in
        "") echo "mapper did not start:"; cat "$log/mapper.log"; kill_stack; exit 1;;
        *":$port/"*) echo "$url";;
        *) echo "REFUSING: mapper handed over to another instance ($url), not an isolated test server"; kill_stack; exit 1;;
    esac
    ;;
url)
    grep -o 'http://[^ ]*' "$log/mapper.log" 2>/dev/null | head -1 || { echo "not running"; exit 1; } ;;
press)
    [[ -n ${2:-} ]] || { echo "usage: $0 press SOURCE [STICK]" >&2; exit 2; }
    (cd "$repo" && dev/fake-daemon.py --dir "$dev" --press "$2" ${3:+--stick "$3"}) ;;
unplug|plug)
    [[ -n ${2:-} ]] || { echo "usage: $0 $1 STICK" >&2; exit 2; }
    (cd "$repo" && dev/fake-daemon.py --dir "$dev" "--$1" "$2") ;;
stop)
    kill_stack
    rm -rf "$dev" "$log"; find "$repo" -name __pycache__ -type d -prune -exec rm -rf {} +
    echo "stopped" ;;
*)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
