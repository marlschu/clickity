"""Command line entry point."""
from __future__ import annotations

import argparse
import grp
import locale
import os
import platform
import shutil
import signal
import sys
import time

from . import __version__
from .backends import open_backend
from .config import (ACTIONS, BUTTON_CHOICES, CLICK_TYPES, CONFIG_FILE, Settings, Store)
from .hotkeys import HOTKEY_CHOICES, HotkeyListener, access_status

C = {"m": "\033[38;5;201m", "c": "\033[38;5;51m", "y": "\033[38;5;226m", "d": "\033[38;5;60m",
     "g": "\033[38;5;48m", "r": "\033[38;5;197m", "b": "\033[1m", "0": "\033[0m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}


def _sigterm(*_):
    raise SystemExit(143)


# --------------------------------------------------------------------- doctor
def cmd_doctor(_args) -> int:
    def line(ok, label, detail=""):
        mark = f"{C['g']}[ OK ]" if ok is True else f"{C['r']}[FAIL]" if ok is False else f"{C['y']}[INFO]"
        print(f"  {mark}{C['0']} {label:<28}{C['d']}{detail}{C['0']}")

    print(f"{C['m']}{C['b']}▓▒░ CLICKITY DIAGNOSTICS v{__version__} ░▒▓{C['0']}\n")
    line(None, "python", platform.python_version())
    sess = os.environ.get("XDG_SESSION_TYPE", "?")
    line(None, "session type", f"{sess} (DISPLAY={os.environ.get('DISPLAY', '-')}, "
                               f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '-')})")
    exists = os.path.exists("/dev/uinput")
    line(exists, "/dev/uinput exists", "" if exists else "sudo modprobe uinput")
    w = exists and os.access("/dev/uinput", os.W_OK)
    line(w, "/dev/uinput writable", "" if w else "run ./install.sh (udev rule)")
    try:
        groups = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except KeyError:
        groups = set()
    line("input" in groups, "user in 'input' group", "needed for GLOBAL hotkeys" if "input" not in groups else "")
    ok, total = access_status()
    line(ok > 0, "input devices readable", f"{ok}/{total}")
    hk = HotkeyListener(lambda *_: None)
    line(hk.available, "keyboards for hotkeys", ", ".join(hk.keyboards[:3]) or "none")
    hk.stop()
    for tool in ("ydotool", "xdotool"):
        line(None, f"{tool} (optional fallback)", shutil.which(tool) or "not installed")
    b, notes = open_backend("auto")
    for n in notes:
        line(None, "backend probe", n)
    line(b.name != "dry-run", "selected backend", b.name)
    b.close()
    line(None, "config", CONFIG_FILE)
    enc = locale.getpreferredencoding(False)
    line("UTF" in enc.upper(), "terminal encoding", enc)
    return 0


# -------------------------------------------------------------------- profiles
def cmd_profiles(_args) -> int:
    st = Store.load()
    print(f"{C['m']}{C['b']}▓▒░ PROFILES ░▒▓{C['0']}  ({CONFIG_FILE})")
    for name, s in st.profiles.items():
        mark = f"{C['y']}▶" if name == st.active else " "
        what = s.action if s.action != "click" else f"{s.button}/{s.click_type}"
        extra = []
        if s.jitter: extra.append(f"jitter±{s.jitter}%")
        if s.burst: extra.append(f"burst {s.burst}/{s.burst_pause_ms}ms")
        if s.max_clicks: extra.append(f"max {s.max_clicks}")
        if s.max_seconds: extra.append(f"{s.max_seconds}s")
        print(f" {mark} {C['c']}{name:<14}{C['0']} {what:<14} {s.cps:>6g}/s  {C['d']}{' '.join(extra)}{C['0']}")
    print(f"\n  lifetime clicks: {C['g']}{st.lifetime_clicks:,}{C['0']}")
    return 0


# ------------------------------------------------------------------------- run
def _settings_from_args(args) -> Settings:
    st = Store.load()
    base = st.profiles.get(args.profile) if args.profile else st.current()
    if base is None:
        sys.exit(f"unknown profile: {args.profile}")
    s = Settings.from_dict(base.to_dict())
    for k in ("action", "button", "click_type", "cps", "jitter", "hold_ms", "burst",
              "burst_pause_ms", "max_clicks", "max_seconds", "start_delay", "wiggle_px",
              "scroll_step", "hotkey", "panic_key"):
        v = getattr(args, k, None)
        if v is not None:
            setattr(s, k, v)
    if args.interval is not None:
        s.cps = 1000.0 / max(1.0, args.interval)
    return s.clamp()


def cmd_run(args) -> int:
    from .engine import ARMING, FIRING, IDLE, Engine

    s = _settings_from_args(args)
    backend, notes = open_backend("dry-run" if args.dry_run else args.backend)
    for n in notes:
        print(f"{C['d']}  backend {n}{C['0']}")
    if backend.name == "dry-run" and not args.dry_run:
        print(f"{C['r']}!! no input backend available - simulating. run ./install.sh{C['0']}")
    logs = []
    eng = Engine(backend, s, logs.append)
    stop_all = {"v": False}

    def on_key(name, pressed):
        if name == s.panic_key and pressed:
            eng.stop("KILL SWITCH")
            stop_all["v"] = True
        elif name == s.hotkey and pressed:
            eng.toggle()

    hk = HotkeyListener(on_key)
    hk.set_keys(s.hotkey, s.panic_key)
    if hk.available:
        hk.start()
    what = s.action if s.action != "click" else f"{s.button} {s.click_type}"
    print(f"{C['m']}{C['b']}▓▒░ CLICKITY ░▒▓{C['0']} {C['c']}{what} @ {s.cps:g}/s{C['0']} "
          f"via {C['y']}{backend.name}{C['0']}")
    if hk.available:
        print(f"{C['d']}  {s.hotkey}: toggle   {s.panic_key}: kill switch   Ctrl+C: exit{C['0']}")
    elif args.wait:
        print(f"{C['r']}!! --wait needs global hotkeys (input group); starting now{C['0']}")
    if not (args.wait and hk.available):
        eng.start()

    spin = "◢◣◤◥"
    i = 0
    tty = sys.stdout.isatty()
    try:
        while not stop_all["v"]:
            while logs:
                msg = logs.pop(0)
                print(f"\r\033[K{C['d']}[{time.strftime('%H:%M:%S')}]{C['0']} {msg}" if tty else msg)
            if eng.state == IDLE and not hk.available:
                break  # nothing can restart it
            if eng.state == IDLE and (s.max_clicks or s.max_seconds) and eng.clicks and not args.wait:
                break
            if tty:
                if eng.state == FIRING:
                    st = f"{C['m']}{spin[i % 4]} FIRING{C['0']}"
                elif eng.state == ARMING:
                    st = f"{C['y']}◎ T-{eng.arm_remaining():.1f}{C['0']}"
                else:
                    st = f"{C['d']}○ STANDBY{C['0']}"
                sys.stdout.write(f"\r\033[K  {st}  clicks {C['c']}{eng.clicks:,}{C['0']}  "
                                 f"cps {C['y']}{eng.real_cps():.0f}{C['0']}  t {eng.elapsed():.1f}s")
                sys.stdout.flush()
            i += 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        eng.shutdown()
        hk.stop()
        clr = "\r\033[K" if tty else ""
        while logs:
            print(f"{clr}{logs.pop(0)}")
        print(f"{clr}{C['g']}» total {eng.session_clicks:,} clicks{C['0']}")
        st = Store.load()
        st.lifetime_clicks += eng.session_clicks
        try:
            st.save()
        except OSError:
            pass
    return 0


# ------------------------------------------------------------------------- tui
def cmd_tui(args) -> int:
    import curses

    from .ui import App

    if not sys.stdout.isatty():
        sys.exit("the TUI needs a terminal; use `clickity run` for headless mode")
    print("\033[38;5;201m▓▒░ jacking in...\033[0m", flush=True)
    backend, notes = open_backend("dry-run" if args.dry_run else args.backend)
    store = Store.load()
    if args.profile:
        if args.profile not in store.profiles:
            sys.exit(f"unknown profile: {args.profile}")
        store.active = args.profile
    os.environ.setdefault("ESCDELAY", "25")

    def _main(scr):
        App(scr, backend, notes, store, use_hotkeys=not args.no_hotkeys).run()

    try:
        curses.wrapper(_main)
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()
    print("\033[38;5;51m▓▒░ disconnected. stay frosty.\033[0m")
    return 0


def main(argv=None) -> int:
    locale.setlocale(locale.LC_ALL, "")
    signal.signal(signal.SIGTERM, _sigterm)
    p = argparse.ArgumentParser(prog="clickity", description="Cyberpunk autoclicker for Linux (X11 + Wayland)")
    p.add_argument("-V", "--version", action="version", version=f"clickity {__version__}")
    p.add_argument("--backend", default="auto", choices=["auto", "uinput", "ydotool", "xdotool"])
    p.add_argument("--dry-run", action="store_true", help="simulate clicks (no input injected)")
    p.add_argument("--profile", help="profile to start with")
    p.add_argument("--no-hotkeys", action="store_true", help="disable global hotkey listener")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="interactive interface (default)")
    sub.add_parser("doctor", help="check permissions and backends")
    sub.add_parser("profiles", help="list saved profiles")

    r = sub.add_parser("run", help="headless clicking from the command line")
    r.add_argument("-c", "--cps", type=float)
    r.add_argument("-i", "--interval", type=float, help="ms between actions (overrides --cps)")
    r.add_argument("-b", "--button", choices=BUTTON_CHOICES)
    r.add_argument("-a", "--action", choices=ACTIONS)
    r.add_argument("-t", "--click-type", dest="click_type", choices=CLICK_TYPES)
    r.add_argument("-n", "--max-clicks", dest="max_clicks", type=int)
    r.add_argument("-s", "--max-seconds", dest="max_seconds", type=int)
    r.add_argument("-d", "--delay", dest="start_delay", type=float)
    r.add_argument("-j", "--jitter", type=int, help="± percent interval randomisation")
    r.add_argument("--hold", dest="hold_ms", type=int, help="button hold time in ms")
    r.add_argument("--burst", type=int)
    r.add_argument("--burst-pause", dest="burst_pause_ms", type=int)
    r.add_argument("--wiggle", dest="wiggle_px", type=int)
    r.add_argument("--scroll-step", dest="scroll_step", type=int)
    r.add_argument("--hotkey", choices=HOTKEY_CHOICES)
    r.add_argument("--panic-key", dest="panic_key", choices=HOTKEY_CHOICES)
    r.add_argument("-w", "--wait", action="store_true", help="wait for the hotkey before firing")
    for sp in (r,):
        # repeat global flags so they also work after the subcommand
        sp.add_argument("--profile", default=argparse.SUPPRESS, help="base profile")
        sp.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="simulate clicks")
        sp.add_argument("--backend", default=argparse.SUPPRESS,
                        choices=["auto", "uinput", "ydotool", "xdotool"])

    args = p.parse_args(argv)
    cmd = args.cmd or "tui"
    return {"tui": cmd_tui, "doctor": cmd_doctor, "profiles": cmd_profiles, "run": cmd_run}[cmd](args)
