#!/bin/bash
# Install vkb-hotas system-wide.
#   sudo ./install.sh                  pick the stick(s) from a list (asked when none is configured or
#                                      the configured one isn't plugged in; otherwise keeps the config)
#   sudo ./install.sh --pick           show the picker anyway
#   sudo ./install.sh --device SEL     stick that becomes the T.16000M: VID:PID or VID:PID@usb-port
#   sudo ./install.sh --throttle SEL   optional second stick that becomes the TWCS Throttle ("none" to drop it)
#   sudo ./install.sh --list           list connected sticks and exit
#   sudo ./install.sh --user NAME      desktop user who may remap via vkb-mapper (default: the sudo caller)
# Re-run after updating the scripts. Sticks can also be switched later in HOTAS Mapper (Devices).
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1   # never leave root-owned __pycache__ in the source tree

here=$(cd "$(dirname "$0")" && pwd)
lib=/usr/local/lib/vkb-hotas
conf=/etc/default/vkb-hotas
rules=/etc/udev/rules.d/72-vkb-hotas.rules
device="" throttle="" throttle_set="" pick=""
user=${SUDO_USER:-}
selector_re='^[0-9a-f]{4}:[0-9a-f]{4}(@[A-Za-z0-9:._/-]{1,80})?$'

die() { echo "error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --device) device=${2:-}; shift 2 ;;
        --throttle) throttle=${2:-}; throttle_set=1; shift 2 ;;
        --pick) pick=1; shift ;;
        --list) list=1; shift ;;
        --user) user=${2:-}; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "unknown argument $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run as root: sudo $0 $*"
command -v python3 >/dev/null || die "python3 not found"
command -v udevadm >/dev/null && command -v systemctl >/dev/null || die "needs systemd + udev"
python3 -c 'import evdev' 2>/dev/null || die "python-evdev missing for root's python3
  Arch: pacman -S python-evdev   Debian/Ubuntu: apt install python3-evdev
  Fedora: dnf install python3-evdev   other: pip install evdev (system-wide)"

py() { PYTHONPATH=$here/common python3 "$@"; }

if [[ ${list:-} ]]; then
    py -c '
from vkb_common import list_sticks, selector_for
sticks = list_sticks()
for d in sticks:
    print(f"  {selector_for(d, sticks):40}  {d.name.strip()}  ({d.path})" + ("  <- VKB" if d.info.vendor == 0x231D else ""))
if not sticks:
    print("  no joysticks found")'
    exit 0
fi

# uhid may be a module or built in
modprobe uhid 2>/dev/null || true
[[ -c /dev/uhid ]] || die "/dev/uhid missing (kernel without CONFIG_UHID?)"
echo uhid > /etc/modules-load.d/vkb-hotas.conf

# config: keep the user's file, install the template otherwise
[[ -e $conf ]] || install -Dm644 "$here/service/vkb-hotas.default" "$conf"
cfg() { py -c 'import sys; from vkb_common import read_config; print(read_config(sys.argv[1]).get(sys.argv[2], ""))' "$conf" "$1"; }
connected() { py -c 'import sys; from vkb_common import find_devices; sys.exit(0 if find_devices(sys.argv[1]) else 1)' "$1"; }
[[ $throttle == none ]] && throttle=""
[[ $throttle_set ]] || throttle=$(cfg VKB_HOTAS_THROTTLE)
if [[ -z $device ]]; then
    device=$(cfg VKB_HOTAS_DEVICE)
    # ask when nothing usable is configured, the configured stick isn't plugged in, or with
    # --pick, as long as there is someone to ask
    ask=""
    if [[ -t 0 && -t 1 ]]; then
        if [[ $pick || -z $device || $device != *:* ]] || ! connected "$device"; then ask=1; fi
    fi
    if [[ $ask ]]; then
        picked=$(mktemp)
        py -c '
import sys
from vkb_common import EMULATED_NAMES, list_sticks, pick_one, selector_for
out, cur_stick, cur_thr = sys.argv[1:4]
sticks = list_sticks()
if not sticks:
    sys.exit("no joysticks found; plug your stick in (or pass --device VID:PID)")
rows = [(selector_for(d, sticks), d.name.strip(), selector_for(d, sticks)) for d in sticks]
emu_stick, emu_thr = EMULATED_NAMES["stick"], EMULATED_NAMES["throttle"]
stick = pick_one(f"Stick that games see as a {emu_stick}:", rows,
                 next((r[0] for r in rows if r[0] == cur_stick), None))
if stick is None:
    sys.exit("cancelled")
rest = [r for r in rows if r[0] != stick]
thr = ""
if rest:
    thr = pick_one(f"Second stick that games see as a {emu_thr} (optional):",
                   [("", "none", "")] + rest, cur_thr if any(r[0] == cur_thr for r in rest) else "")
    if thr is None:
        sys.exit("cancelled")
open(out, "w").write(f"{stick}\n{thr}\n")' "$picked" "$device" "$throttle" < /dev/tty || { rm -f "$picked"; exit 1; }
        { read -r device; read -r throttle; } < "$picked"; rm -f "$picked"
    elif [[ -z $device || $device != *:* ]]; then
        # no terminal (or a legacy vendor-only value): autodetect, as before
        device=$(py -c '
import sys
from vkb_common import find_one, vidpid
d = find_one(sys.argv[1])
print(vidpid(d) if d else "")' "$device") || exit 1
        [[ -n $device ]] || die "no VKB stick found; plug it in or pass --device VID:PID (see --list)"
    fi
fi
device=${device,,} throttle=${throttle,,}
[[ $device =~ $selector_re ]] || die "device must be VID:PID or VID:PID@port in hex, got '$device'"
[[ -z $throttle || $throttle =~ $selector_re ]] || die "throttle must be VID:PID or VID:PID@port, got '$throttle'"
[[ $device != "$throttle" ]] || die "the stick and the throttle must be different devices"
echo "stick (T.16000M): $device"
echo "throttle (TWCS Throttle): ${throttle:-none}"

# desktop user: owns the control socket and ~/.config/vkb-hotas/mapping.json
[[ -n $user ]] || user=$(cfg VKB_HOTAS_USER)
if [[ -n $user && $user != root ]]; then
    id -u "$user" >/dev/null 2>&1 || die "user '$user' does not exist"
    echo "mapper user: $user (mapping file: ~$user/.config/vkb-hotas/mapping.json)"
else
    user=""
    echo "warning: no desktop user set; vkb-mapper won't be able to talk to the daemon (use --user NAME)"
fi
py -c 'import sys; from vkb_common import write_config; write_config(dict(a.split("=", 1) for a in sys.argv[2:]), sys.argv[1])' \
    "$conf" "VKB_HOTAS_DEVICE=$device" "VKB_HOTAS_THROTTLE=$throttle" "VKB_HOTAS_USER=$user"

install -Dm755 "$here/service/vkb-hotas.py"  "$lib/vkb-hotas.py"
install -Dm644 "$here/common/vkb_common.py" "$lib/vkb_common.py"
install -Dm644 "$here/README.md"     "$lib/README.md"
install -Dm755 "$here/mapper/vkb-mapper.py"   "$lib/vkb-mapper.py"
install -Dm644 "$here/mapper/vkb-mapper.html" "$lib/vkb-mapper.html"
ln -sf "$lib/vkb-mapper.py" /usr/local/bin/vkb-mapper
install -Dm644 "$here/mapper/vkb-mapper.desktop" /usr/local/share/applications/vkb-mapper.desktop
install -Dm644 "$here/service/72-vkb-hotas.rules" "$rules"
install -Dm644 "$here/service/vkb-hotas.service" /etc/systemd/system/vkb-hotas.service

systemctl daemon-reload
udevadm control --reload
systemctl reset-failed vkb-hotas.service 2>/dev/null || true
systemctl restart vkb-hotas.service
sleep 2
systemctl --no-pager --lines=8 status vkb-hotas.service || true
cat <<MSG

Installed: $device -> Thrustmaster T.16000M${throttle:+, $throttle -> Thrustmaster TWCS Throttle}.
The service starts automatically whenever a joystick is plugged in.
  1. Restart Steam so it lets go of the real stick(s).
  2. As your user, configure game prefixes:  ./tools/proton-setup.py
  3. Remap buttons/axes, or switch sticks:  vkb-mapper   (or "HOTAS Mapper" in your app menu)
Give games the real stick instead:  vkb-mapper --expose stick|throttle|all   (undo: --hide)
Stop the service entirely:  sudo systemctl stop vkb-hotas
MSG
