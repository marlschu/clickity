```
 ██████╗██╗     ██╗ ██████╗██╗  ██╗██╗████████╗██╗   ██╗
██╔════╝██║     ██║██╔════╝██║ ██╔╝██║╚══██╔══╝╚██╗ ██╔╝
██║     ██║     ██║██║     █████╔╝ ██║   ██║    ╚████╔╝
██║     ██║     ██║██║     ██╔═██╗ ██║   ██║     ╚██╔╝
╚██████╗███████╗██║╚██████╗██║  ██╗██║   ██║      ██║
 ╚═════╝╚══════╝╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝   ╚═╝      ╚═╝
        ▓▒░ cyberpunk autoclicker for linux ░▒▓
```

A terminal autoclicker for **Debian/Ubuntu** and **Arch** that works on both
**Wayland and X11**. It is pure Python standard library, so there's nothing to
`pip install`.

Clicks come from a virtual kernel mouse (`/dev/uinput`), the same way `ydotool`
works. That means they go through libinput like a real mouse, so they should work
under any Wayland compositor and under X11.

## Features

| | |
|---|---|
| **Actions** | left / right / middle / side (back) / extra (forward) clicks · scroll up/down/left/right |
| **Click types** | single · double · triple |
| **Rate** | 0.1 – 500 actions/s, drift-corrected scheduler (held ~300/s in timing tests) |
| **Humanize** | ±% interval jitter · adjustable hold time · random cursor wiggle (returns to origin) |
| **Bursts** | fire N clicks, pause M ms, repeat |
| **Failsafes** | stop after N clicks · stop after N seconds · global kill switch · start countdown |
| **Triggers** | global hotkey (any window) in *toggle* or *hold-to-fire* mode, F1–F12 / Pause / ScrollLock / … |
| **Profiles** | built-in presets (`turbo`, `human`, `burst-5`, `idle-keeper`, `scroller`) plus your own, saved to `~/.config/clickity` |
| **Telemetry** | live real-CPS, load meter, scrolling CPS graph, session + lifetime counters |
| **Themes** | `neon` · `matrix` · `amber` · `bloodmoon` · `ice` (256-colour, degrades to 8-colour) |
| **Headless** | `clickity run …` for scripts and keybinds |
| **Backends** | native uinput → ydotool → xdotool (X11) → dry-run simulation |

## Install

```bash
./install.sh            # asks whether to enable global hotkeys
./install.sh --hotkeys  # non-interactive, enable them
```

The installer:
1. installs `python3` if it's missing (apt or pacman),
2. copies the app to `/opt/clickity` and links `/usr/local/bin/clickity`,
3. adds a udev rule (`60-clickity-uinput.rules`) that gives the logged-in user
   access to `/dev/uinput`, and autoloads the `uinput` module (Arch doesn't by default),
4. optionally adds you to the `input` group for global hotkeys (you need to
   **log out and back in** after this),
5. adds a desktop launcher.

Run `clickity doctor` to check everything. Use `./uninstall.sh` (or `--purge`) to remove it.

> **Security note on global hotkeys:** reading keys while another window is
> focused needs read access to `/dev/input/event*`, which means joining the
> `input` group. Any process running as you can then read keystrokes. If you'd
> rather not, skip it. Hotkeys then only work while the Clickity terminal is
> focused (**LOCAL** mode), and every start gets at least a 3 s countdown so you
> can move the cursor to the target. To stop, click back into the terminal and
> press `SPACE`, or set a click/time limit.

Try it without installing: `./bin/clickity --dry-run`

## Usage

```bash
clickity                         # TUI
clickity --profile human         # TUI starting on a profile
clickity doctor                  # diagnostics
clickity profiles                # list profiles

# headless
clickity run -c 20               # 20 clicks/s until Ctrl+C (F8 toggles, F9 kills)
clickity run -c 15 -j 30 -d 3    # 15/s, ±30% jitter, 3s countdown
clickity run -b right -n 100     # 100 right clicks then exit
clickity run -t double -i 500    # a double-click every 500 ms
clickity run -a scroll-down -c 5 -s 60
clickity run --burst 5 --burst-pause 1000 -c 30
clickity run --wait              # stay armed; F8 starts/stops
```

### TUI keys

| key | action |
|---|---|
| `SPACE` / `F8` | start / stop |
| `F9` | kill switch: stop and exit |
| `↑↓` `jk` | select parameter |
| `←→` `hl` | adjust (`PgUp`/`PgDn` = ×10) |
| `ENTER` | type an exact value |
| `P` / `O` | next / previous profile |
| `S` · `N` · `D` | save · save as · delete profile |
| `T` | cycle theme |
| `R` | reset counters |
| `?` | help |
| `Q` | quit |

## Notes

- **Clicks go wherever the cursor is.** Wayland doesn't let apps read or warp
  the global cursor position, so this version has no "click at X,Y".
- Without uinput access, Clickity tries `ydotool` (which needs `ydotoold`
  running), then `xdotool` (X11 only). If none of them work it runs in
  **DRY-RUN** mode and says so in red.
- Above ~300/s, accuracy depends on your kernel timer resolution and on how
  fast the target app reads input.
- Needs a UTF-8 terminal at least 70×22. 100×34 or larger gets the full banner.

## Testing status (v1.0.0)

**Verified** on Ubuntu 26.04 / Python 3.14 / Wayland, using the dry-run backend:
- scheduler rate accuracy, bursts, click and time limits, countdown
- the global-hotkey reader (fed synthetic kernel events) and toggle, hold and kill-switch logic
- the exact uinput event byte stream; ioctl numbers match the kernel headers
- TUI rendering at 70×22 through 110×42, in 256 and 8 colours
- `install.sh --dry-run` on an apt system; source is Python 3.9 syntax compatible

**Not yet verified:**
- real click injection through `/dev/uinput` (needs the installer's permissions)
- global hotkeys reading a real keyboard
- the privileged steps of `install.sh`, and the whole pacman/Arch path
- running on an actual Python 3.9–3.11 interpreter (only syntax was checked)

## Layout

```
bin/clickity          launcher
clickity/backends.py  uinput (ioctl/struct) + ydotool/xdotool/dry-run backends
clickity/hotkeys.py   global hotkeys from /dev/input/event*
clickity/engine.py    click scheduler thread
clickity/config.py    settings, presets, profile store
clickity/ui.py        curses interface
clickity/cli.py       argparse entry: tui / run / doctor / profiles
```
