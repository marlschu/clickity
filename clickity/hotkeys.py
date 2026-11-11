"""Global hotkeys by reading /dev/input/event* (works on Wayland and X11).

Requires read access to the keyboard event devices (membership in the `input`
group). Without it, Clickity falls back to in-terminal keys only.
"""
from __future__ import annotations

import fcntl
import glob
import os
import select
import struct
import threading
from typing import Callable

from .backends import DEVICE_NAME, EV_KEY, _IOC, _IOC_READ

_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)
_KEY_MAX = 0x2FF


def EVIOCGNAME(length: int) -> int:
    return _IOC(_IOC_READ, "E", 0x06, length)


def EVIOCGBIT(ev: int, length: int) -> int:
    return _IOC(_IOC_READ, "E", 0x20 + ev, length)


# Hotkey name -> linux key code
KEYS = {f"F{i}": 58 + i for i in range(1, 11)}  # F1=59 .. F10=68
KEYS.update({"F11": 87, "F12": 88})
KEYS.update({f"F{i}": 170 + i for i in range(13, 25)})  # F13=183 .. F24=194
KEYS.update({"PAUSE": 119, "SCROLLLOCK": 70, "INSERT": 110, "HOME": 102,
             "END": 107, "PAGEUP": 104, "PAGEDOWN": 109, "ESC": 1,
             "GRAVE": 41, "CAPSLOCK": 58})
CODE_TO_NAME = {v: k for k, v in KEYS.items()}
HOTKEY_CHOICES = [f"F{i}" for i in range(1, 13)] + ["PAUSE", "SCROLLLOCK", "INSERT",
                                                     "HOME", "END", "PAGEUP", "PAGEDOWN"]


def _test_bit(buf: bytes, bit: int) -> bool:
    return bool(buf[bit // 8] & (1 << (bit % 8)))


def _probe(path: str) -> tuple[int | None, str]:
    """Open device if it is a keyboard-ish device (has F1 and A keys)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None, ""
    try:
        name = fcntl.ioctl(fd, EVIOCGNAME(256), bytes(256)).split(b"\0", 1)[0].decode(errors="replace")
        if name == DEVICE_NAME:
            os.close(fd)
            return None, name
        bits = fcntl.ioctl(fd, EVIOCGBIT(EV_KEY, _KEY_MAX // 8 + 1), bytes(_KEY_MAX // 8 + 1))
        if not (_test_bit(bits, 59) and _test_bit(bits, 30)):  # KEY_F1, KEY_A
            os.close(fd)
            return None, name
        return fd, name
    except OSError:
        os.close(fd)
        return None, ""


def input_group_state() -> str:
    """'active' | 'pending' (in /etc/group but not this session) | 'missing'."""
    import grp
    import pwd
    try:
        g = grp.getgrnam("input")
    except KeyError:
        return "missing"
    if g.gr_gid in os.getgroups():
        return "active"
    user = pwd.getpwuid(os.getuid()).pw_name
    return "pending" if user in g.gr_mem else "missing"


def hotkey_fix_hint() -> str:
    state = input_group_state()
    if state == "pending":
        return "you're in 'input' but this login predates it: log out and back in (or reboot)"
    if state == "missing":
        return "run: ./install.sh --hotkeys, then log out/in"
    return "no keyboard devices found"


def access_status() -> tuple[int, int]:
    """(#event devices readable, #total)."""
    devs = glob.glob("/dev/input/event*")
    ok = sum(1 for d in devs if os.access(d, os.R_OK))
    return ok, len(devs)


class HotkeyListener(threading.Thread):
    """Calls callback(key_name, pressed) for watched keys from any keyboard."""

    def __init__(self, callback: Callable[[str, bool], None]):
        super().__init__(daemon=True, name="clickity-hotkeys")
        self.callback = callback
        self.watch: set[int] = set()
        self._fds: dict[str, int] = {}
        self._halt = threading.Event()
        self.keyboards: list[str] = []
        self._scan()

    @property
    def available(self) -> bool:
        return bool(self._fds)

    def set_keys(self, *names: str) -> None:
        self.watch = {KEYS[n] for n in names if n in KEYS}

    def _scan(self) -> None:
        if self._halt.is_set():
            return
        seen = set()
        for path in sorted(glob.glob("/dev/input/event*")):
            seen.add(path)
            if path in self._fds:
                continue
            fd, name = _probe(path)
            if fd is not None:
                self._fds[path] = fd
                self.keyboards.append(name)
        for path in list(self._fds):
            if path not in seen:
                self._drop(path)

    def _drop(self, path: str) -> None:
        fd = self._fds.pop(path, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def run(self) -> None:
        rescan_tick = 0
        while not self._halt.is_set():
            fds = list(self._fds.items())
            if not fds:
                self._halt.wait(2.0)
                self._scan()
                continue
            try:
                ready, _, _ = select.select([fd for _, fd in fds], [], [], 0.5)
            except (OSError, ValueError):
                self._scan()
                continue
            for path, fd in fds:
                if fd not in ready:
                    continue
                try:
                    data = os.read(fd, _EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                except OSError:  # unplugged
                    self._drop(path)
                    continue
                for off in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(_EVENT_FMT, data, off)
                    if etype == EV_KEY and code in self.watch and value in (0, 1):
                        try:
                            self.callback(CODE_TO_NAME[code], value == 1)
                        except Exception:
                            pass
            rescan_tick += 1
            if rescan_tick >= 6:  # ~3s: pick up hot-plugged keyboards
                rescan_tick = 0
                self._scan()

    def stop(self) -> None:
        self._halt.set()
        for path in list(self._fds):
            self._drop(path)
