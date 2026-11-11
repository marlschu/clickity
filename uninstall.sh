#!/usr/bin/env bash
# Remove Clickity. Your profiles in ~/.config/clickity are kept unless --purge.
set -euo pipefail
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1
echo "▓▒░ removing Clickity"
sudo rm -f /usr/local/bin/clickity
sudo rm -rf /opt/clickity
sudo rm -f /etc/udev/rules.d/60-clickity-uinput.rules /etc/modules-load.d/clickity-uinput.conf
sudo udevadm control --reload-rules || true
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/clickity.desktop"
[ "$PURGE" = 1 ] && rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/clickity"
echo "  done. If you were added to the 'input' group for hotkeys and want out:"
echo "    sudo gpasswd -d $USER input"
