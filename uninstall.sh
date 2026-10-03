#!/bin/bash
# Remove vkb-hotas. Run with: sudo ./uninstall.sh   (keeps /etc/default/vkb-hotas)
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo $0"; exit 1; }
systemctl stop vkb-hotas.service 2>/dev/null
rm -f /etc/udev/rules.d/72-vkb-hotas.rules /etc/systemd/system/vkb-hotas.service \
      /etc/modules-load.d/vkb-hotas.conf /run/vkb-hotas/active \
      /usr/local/bin/vkb-mapper /usr/local/share/applications/vkb-mapper.desktop
rm -rf /usr/local/lib/vkb-hotas
systemctl daemon-reload
udevadm control --reload
udevadm trigger --action=change --subsystem-match=input --subsystem-match=hidraw
echo "Removed. Kept: /etc/default/vkb-hotas and ~/.config/vkb-hotas/ (your mappings); delete by hand if you like."
echo "Proton prefixes: ./proton-setup.py --undo (as your user) restores the SDL backend."
