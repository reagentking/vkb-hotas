#!/bin/bash
# Install vkb-hotas system-wide.
#   sudo ./install.sh                  autodetect the stick (works if exactly one VKB device is plugged in)
#   sudo ./install.sh --device VID:PID use a specific stick (any vendor)
#   sudo ./install.sh --list           list connected sticks and exit
#   sudo ./install.sh --user NAME      desktop user who may remap via vkb-mapper (default: the sudo caller)
# Re-run after changing /etc/default/vkb-hotas or updating the scripts.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1   # never leave root-owned __pycache__ in the source tree

here=$(cd "$(dirname "$0")" && pwd)
lib=/usr/local/lib/vkb-hotas
conf=/etc/default/vkb-hotas
rules=/etc/udev/rules.d/72-vkb-hotas.rules
device=""
user=${SUDO_USER:-}

die() { echo "error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --device) device=${2:-}; shift 2 ;;
        --list) list=1; shift ;;
        --user) user=${2:-}; shift 2 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) die "unknown argument $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run as root: sudo $0 $*"
command -v python3 >/dev/null || die "python3 not found"
command -v udevadm >/dev/null && command -v systemctl >/dev/null || die "needs systemd + udev"
python3 -c 'import evdev' 2>/dev/null || die "python-evdev missing for root's python3
  Arch: pacman -S python-evdev   Debian/Ubuntu: apt install python3-evdev
  Fedora: dnf install python3-evdev   other: pip install evdev (system-wide)"

if [[ ${list:-} ]]; then
    PYTHONPATH=$here python3 - <<'PY'
import evdev
from evdev import ecodes as E
for p in evdev.list_devices():
    d = evdev.InputDevice(p)
    if E.EV_ABS in d.capabilities() and E.EV_KEY in d.capabilities():
        tag = "  <- VKB" if d.info.vendor == 0x231D else ""
        print(f"  {d.info.vendor:04x}:{d.info.product:04x}  {d.name.strip()}  ({d.path}){tag}")
PY
    exit 0
fi

# uhid may be a module or built in
modprobe uhid 2>/dev/null || true
[[ -c /dev/uhid ]] || die "/dev/uhid missing (kernel without CONFIG_UHID?)"
echo uhid > /etc/modules-load.d/vkb-hotas.conf

# config: keep the user's file, install the template otherwise
[[ -e $conf ]] || install -Dm644 "$here/vkb-hotas.default" "$conf"
grep -q '^VKB_HOTAS_DEVICE=' "$conf" || printf '\nVKB_HOTAS_DEVICE=""\n' >> "$conf"
[[ -n $device ]] || device=$(sed -n 's/^VKB_HOTAS_DEVICE="\{0,1\}\([^"#]*\)"\{0,1\}.*/\1/p' "$conf" | tr -d ' ')
if [[ -z $device || $device != *:* ]]; then
    device=$(PYTHONPATH=$here python3 -c '
import sys
from vkb_common import find_one
d = find_one(sys.argv[1])
print(f"{d.info.vendor:04x}:{d.info.product:04x}" if d else "")' "$device") || exit 1
    [[ -n $device ]] || die "no VKB stick found; plug it in or pass --device VID:PID (see --list)"
fi
[[ $device =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$ ]] || die "device must be VID:PID in hex, got '$device'"
device=${device,,}
vid=${device%:*} pid=${device#*:}
sed -i "s/^VKB_HOTAS_DEVICE=.*/VKB_HOTAS_DEVICE=\"$device\"/" "$conf"
echo "stick: $device"

# desktop user: owns the control socket and ~/.config/vkb-hotas/mapping.json
grep -q '^VKB_HOTAS_USER=' "$conf" || printf 'VKB_HOTAS_USER=""\n' >> "$conf"
[[ -n $user ]] || user=$(sed -n 's/^VKB_HOTAS_USER="\{0,1\}\([^"#]*\)"\{0,1\}.*/\1/p' "$conf" | tr -d ' ')
if [[ -n $user && $user != root ]]; then
    id -u "$user" >/dev/null 2>&1 || die "user '$user' does not exist"
    sed -i "s/^VKB_HOTAS_USER=.*/VKB_HOTAS_USER=\"$user\"/" "$conf"
    echo "mapper user: $user (mapping file: ~$user/.config/vkb-hotas/mapping.json)"
else
    echo "warning: no desktop user set; vkb-mapper won't be able to talk to the daemon (use --user NAME)"
fi

install -Dm755 "$here/vkb-hotas.py"  "$lib/vkb-hotas.py"
install -Dm644 "$here/vkb_common.py" "$lib/vkb_common.py"
install -Dm644 "$here/README.md"     "$lib/README.md"
install -Dm755 "$here/vkb-mapper.py"   "$lib/vkb-mapper.py"
install -Dm644 "$here/vkb-mapper.html" "$lib/vkb-mapper.html"
ln -sf "$lib/vkb-mapper.py" /usr/local/bin/vkb-mapper
install -Dm644 "$here/vkb-mapper.desktop" /usr/local/share/applications/vkb-mapper.desktop
sed -e "s/@VID@/$vid/g" -e "s/@PID@/$pid/g" "$here/72-vkb-hotas.rules.in" > "$rules"
chmod 644 "$rules"
install -Dm644 "$here/vkb-hotas.service" /etc/systemd/system/vkb-hotas.service

systemctl daemon-reload
udevadm control --reload
systemctl reset-failed vkb-hotas.service 2>/dev/null || true
systemctl restart vkb-hotas.service
sleep 2
systemctl --no-pager --lines=8 status vkb-hotas.service || true
cat <<MSG

Installed for $device. The service starts automatically whenever the stick is
plugged in.
  1. Restart Steam so it lets go of the real stick.
  2. As your user, configure game prefixes:  ./proton-setup.py
  3. Remap buttons/axes:  vkb-mapper   (or "HOTAS Mapper" in your app menu)
Toggle the remap:  sudo systemctl stop|start vkb-hotas
MSG
