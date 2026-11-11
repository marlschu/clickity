#!/usr/bin/env bash
# Clickity installer for Debian/Ubuntu (apt) and Arch/Manjaro (pacman).
#
#   ./install.sh                 interactive
#   ./install.sh --hotkeys       also enable global hotkeys (adds you to 'input')
#   ./install.sh --no-hotkeys    skip global hotkeys
#   ./install.sh --dry-run       print what would be done, change nothing
set -euo pipefail

PREFIX=/opt/clickity
BIN_LINK=/usr/local/bin/clickity
UDEV_RULE=/etc/udev/rules.d/60-clickity-uinput.rules
MODLOAD=/etc/modules-load.d/clickity-uinput.conf
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/clickity.desktop"
HOTKEYS=ask
DRY=0

for a in "$@"; do
  case "$a" in
    --hotkeys) HOTKEYS=yes ;;
    --no-hotkeys) HOTKEYS=no ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

M=$'\e[38;5;201m'; C=$'\e[38;5;51m'; Y=$'\e[38;5;226m'; G=$'\e[38;5;48m'; R=$'\e[38;5;197m'; D=$'\e[38;5;60m'; N=$'\e[0m'
step() { printf '%s▓▒░%s %s\n' "$M" "$N" "$*"; }
ok()   { printf '  %s[ OK ]%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s[WARN]%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '  %s[FAIL]%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
run()  { printf '  %s$ %s%s\n' "$D" "$*" "$N"; [ "$DRY" = 1 ] || "$@"; }
# write stdin to a root-owned file
put()  { local dst=$1; local body; body=$(cat); printf '  %s> %s%s\n' "$D" "$dst" "$N";
         [ "$DRY" = 1 ] || printf '%s\n' "$body" | sudo tee "$dst" >/dev/null; }

[ "$(id -u)" -eq 0 ] && die "run as your normal user (sudo is used where needed)"
command -v sudo >/dev/null || die "sudo is required"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ -f "$SRC/clickity/cli.py" ] || die "run this from the Clickity source folder"

cat <<EOF
${M}
   ██████╗██╗     ██╗ ██████╗██╗  ██╗██╗████████╗██╗   ██╗
  ██╔════╝██║     ██║██╔════╝██║ ██╔╝██║╚══██╔══╝╚██╗ ██╔╝
  ██║     ██║     ██║██║     █████╔╝ ██║   ██║    ╚████╔╝
  ╚██████╗███████╗██║╚██████╗██║  ██╗██║   ██║      ██║
   ╚═════╝╚══════╝╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝   ╚═╝      ╚═╝   ${C}installer${N}
EOF
[ "$DRY" = 1 ] && warn "dry run: nothing will be changed"

# ---------------------------------------------------------------- distro / deps
step "detecting distro"
if command -v pacman >/dev/null; then
  DISTRO=arch; ok "pacman found (Arch family)"
  command -v python3 >/dev/null || run sudo pacman -S --needed --noconfirm python
elif command -v apt-get >/dev/null; then
  DISTRO=debian; ok "apt found (Debian family)"
  command -v python3 >/dev/null || { run sudo apt-get update; run sudo apt-get install -y python3; }
else
  DISTRO=other; warn "unknown package manager - make sure python3 >= 3.8 is installed"
fi
if [ "$DRY" = 0 ] || command -v python3 >/dev/null; then
  python3 -c 'import sys, curses; assert sys.version_info >= (3, 8)' 2>/dev/null \
    || die "python3 >= 3.8 with curses is required"
  ok "python $(python3 -c 'import platform; print(platform.python_version())')"
fi

# ---------------------------------------------------------------------- files
step "installing to $PREFIX"
run sudo rm -rf "$PREFIX"
run sudo install -d "$PREFIX/bin" "$PREFIX/clickity"
run sudo install -m 644 "$SRC"/clickity/*.py "$PREFIX/clickity/"
run sudo install -m 755 "$SRC/bin/clickity" "$PREFIX/bin/clickity"
if [ -f "$SRC/README.md" ]; then run sudo install -m 644 "$SRC/README.md" "$PREFIX/README.md"; fi
run sudo install -m 755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"
run sudo ln -sf "$PREFIX/bin/clickity" "$BIN_LINK"
ok "command: clickity"

# ------------------------------------------------------------- uinput access
step "granting access to /dev/uinput (virtual mouse)"
# uaccess: the logged-in seat user gets an ACL. Must sort before 73-seat-late.rules.
put "$UDEV_RULE" <<'EOF'
# Clickity: let the active desktop user create virtual input devices
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"
EOF
echo uinput | put "$MODLOAD"
run sudo modprobe uinput
run sudo udevadm control --reload-rules
run sudo udevadm trigger --name-match=uinput || run sudo udevadm trigger --subsystem-match=misc
ok "udev rule + module autoload installed"

# ------------------------------------------------------------ global hotkeys
step "global hotkeys"
cat <<EOF
  Hotkeys like ${C}F8${N} work in ${C}any${N} window only if Clickity can read your
  keyboard (/dev/input/event*). That means joining the ${Y}'input'${N} group.
  ${Y}Security note:${N} any program running as you could then read all
  keystrokes. Without it, hotkeys only work while the terminal is focused.
EOF
if [ "$HOTKEYS" = ask ]; then
  read -r -p "  enable global hotkeys? [y/N] " ans || ans=n
  case "$ans" in [yY]*) HOTKEYS=yes ;; *) HOTKEYS=no ;; esac
fi
if [ "$HOTKEYS" = yes ]; then
  getent group input >/dev/null || run sudo groupadd -r input
  if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    ok "already in 'input' group"
  else
    run sudo usermod -aG input "$USER"
    warn "log out and back in (or reboot) for the group to take effect"
  fi
else
  ok "skipped (re-run with --hotkeys to enable later)"
fi

# ------------------------------------------------------------- desktop entry
step "desktop launcher"
if [ "$DRY" = 0 ]; then
  mkdir -p "$(dirname "$DESKTOP")"
  cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Clickity
Comment=Cyberpunk autoclicker
Exec=clickity
Icon=input-mouse
Terminal=true
Categories=Utility;
EOF
fi
ok "$DESKTOP"

# --------------------------------------------------------------------- verify
step "verifying"
if [ "$DRY" = 0 ]; then
  sleep 1
  if [ -w /dev/uinput ]; then
    ok "/dev/uinput is writable - real clicks enabled"
  else
    warn "/dev/uinput not writable yet - log out/in or reboot, then run: clickity doctor"
  fi
fi
printf '\n%s▓▒░ done.%s run %sclickity%s  (or %sclickity doctor%s to check the setup)\n' "$M" "$N" "$C" "$N" "$C" "$N"
