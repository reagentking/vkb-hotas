#!/bin/bash
# Remove vkb-hotas. Run with: sudo ./uninstall.sh   (keeps /etc/default/vkb-hotas)
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root: sudo $0"; exit 1; }
systemctl stop vkb-hotas.service 2>/dev/null
rm -f /etc/udev/rules.d/72-vkb-hotas.rules /etc/systemd/system/vkb-hotas.service \
      /etc/modules-load.d/vkb-hotas.conf /run/vkb-hotas/active \
      /run/udev/rules.d/72-vkb-hotas-hide.rules \
      /usr/local/bin/vkb-mapper /usr/local/share/applications/vkb-mapper.desktop \
      /usr/local/bin/vkb-tray /usr/local/share/applications/vkb-tray.desktop /etc/xdg/autostart/vkb-tray.desktop \
      /usr/local/share/icons/hicolor/scalable/apps/vkb-hotas.svg \
      /usr/local/share/icons/hicolor/scalable/apps/vkb-hotas-attention.svg
rm -rf /usr/local/lib/vkb-hotas
for d in /usr/local/share/icons/hicolor/scalable/apps /usr/local/share/icons/hicolor/scalable \
         /usr/local/share/icons/hicolor /usr/local/share/icons; do
    rmdir "$d" 2>/dev/null  # only if now empty
done
systemctl daemon-reload
udevadm control --reload
udevadm trigger --action=change --subsystem-match=input --subsystem-match=hidraw
echo "Removed. Kept: /etc/default/vkb-hotas and ~/.config/vkb-hotas/ (your mappings); delete by hand if you like."
echo "Proton prefixes: ./tools/proton-setup.py --undo (as your user) restores the SDL backend."
