"""Click injection backends.

Primary backend talks to /dev/uinput directly with ioctl + struct (stdlib only),
so it works on Wayland *and* X11 without python-evdev. Fallbacks shell out to
ydotool / xdotool. A dry-run backend counts events for testing.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import struct
import subprocess
import threading
import time

# ---- linux/input-event-codes.h ------------------------------------------------
EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
BUS_VIRTUAL = 0x06

BUTTONS = {
    "left": 0x110,    # BTN_LEFT
    "right": 0x111,   # BTN_RIGHT
    "middle": 0x112,  # BTN_MIDDLE
    "side": 0x113,    # BTN_SIDE  (mouse 4 / back)
    "extra": 0x114,   # BTN_EXTRA (mouse 5 / forward)
}

DEVICE_NAME = "Clickity Virtual Mouse"

# ---- ioctl number helpers (asm-generic/ioctl.h) -------------------------------
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(direction: int, typ: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


_SETUP_FMT = "HHHH80sI"  # struct uinput_setup { input_id; char name[80]; u32 ff }
_EVENT_FMT = "llHHi"     # struct input_event { timeval; u16 type; u16 code; s32 value }

UI_DEV_CREATE = _IOC(_IOC_NONE, "U", 1, 0)
UI_DEV_DESTROY = _IOC(_IOC_NONE, "U", 2, 0)
UI_DEV_SETUP = _IOC(_IOC_WRITE, "U", 3, struct.calcsize(_SETUP_FMT))
UI_SET_EVBIT = _IOC(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _IOC(_IOC_WRITE, "U", 101, 4)
UI_SET_RELBIT = _IOC(_IOC_WRITE, "U", 102, 4)


class BackendError(RuntimeError):
    pass


class Backend:
    name = "base"
    supports_move = False

    def press(self, button: str) -> None: ...
    def release(self, button: str) -> None: ...

    def click(self, button: str, hold_s: float = 0.0) -> None:
        self.press(button)
        if hold_s > 0:
            time.sleep(hold_s)
        self.release(button)

    def scroll(self, amount: int, horizontal: bool = False) -> None: ...
    def move(self, dx: int, dy: int) -> None: ...
    def release_all(self) -> None: ...
    def close(self) -> None: ...


class UInputBackend(Backend):
    name = "uinput"
    supports_move = True

    def __init__(self, path: str = "/dev/uinput"):
        try:
            self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            raise BackendError("/dev/uinput missing (run: sudo modprobe uinput)")
        except PermissionError:
            raise BackendError("no write access to /dev/uinput (run install.sh)")
        self._lock = threading.Lock()
        self._held: set[int] = set()
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for code in BUTTONS.values():
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for code in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
                fcntl.ioctl(self.fd, UI_SET_RELBIT, code)
            setup = struct.pack(_SETUP_FMT, BUS_VIRTUAL, 0x1209, 0xC11C, 1,
                                DEVICE_NAME.encode(), 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError as e:
            os.close(self.fd)
            raise BackendError(f"uinput setup failed: {e}")
        # Give the compositor / udev time to pick up the new device.
        time.sleep(0.5)
        self._closed = False

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self.fd, struct.pack(_EVENT_FMT, 0, 0, etype, code, value))

    def _syn(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    def press(self, button: str) -> None:
        code = BUTTONS[button]
        with self._lock:
            self._emit(EV_KEY, code, 1)
            self._syn()
            self._held.add(code)

    def release(self, button: str) -> None:
        code = BUTTONS[button]
        with self._lock:
            self._emit(EV_KEY, code, 0)
            self._syn()
            self._held.discard(code)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        with self._lock:
            self._emit(EV_REL, REL_HWHEEL if horizontal else REL_WHEEL, amount)
            self._syn()

    def move(self, dx: int, dy: int) -> None:
        with self._lock:
            if dx:
                self._emit(EV_REL, REL_X, dx)
            if dy:
                self._emit(EV_REL, REL_Y, dy)
            self._syn()

    def release_all(self) -> None:
        with self._lock:
            for code in list(self._held):
                try:
                    self._emit(EV_KEY, code, 0)
                    self._syn()
                except OSError:
                    pass
            self._held.clear()

    def close(self) -> None:
        if self._closed:
            return
        self.release_all()
        self._closed = True
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


class YdotoolBackend(Backend):
    """Fallback via ydotool (needs ydotoold running). Slow: one process per event."""
    name = "ydotool"
    supports_move = True
    _IDX = {"left": 0, "right": 1, "middle": 2, "side": 3, "extra": 4}

    def __init__(self):
        if not shutil.which("ydotool"):
            raise BackendError("ydotool not installed")
        r = subprocess.run(["ydotool", "click", "0x00"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0 or "failed" in (r.stderr + r.stdout).lower():
            raise BackendError("ydotool present but ydotoold not reachable")

    def _run(self, *args: str) -> None:
        subprocess.run(["ydotool", *args], capture_output=True, timeout=3)

    def press(self, button: str) -> None:
        self._run("click", hex(0x40 | self._IDX[button]))

    def release(self, button: str) -> None:
        self._run("click", hex(0x80 | self._IDX[button]))

    def click(self, button: str, hold_s: float = 0.0) -> None:
        if hold_s <= 0:
            self._run("click", hex(0xC0 | self._IDX[button]))
        else:
            super().click(button, hold_s)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        # ydotool wheel: positive y scrolls down; uinput REL_WHEEL positive = up
        if horizontal:
            self._run("mousemove", "-w", "-x", str(amount), "-y", "0")
        else:
            self._run("mousemove", "-w", "-x", "0", "-y", str(-amount))

    def move(self, dx: int, dy: int) -> None:
        self._run("mousemove", "-x", str(dx), "-y", str(dy))

    def release_all(self) -> None:
        for b in self._IDX:
            self.release(b)


class XdotoolBackend(Backend):
    """Fallback for X11 sessions only."""
    name = "xdotool"
    supports_move = True
    _IDX = {"left": 1, "middle": 2, "right": 3, "side": 8, "extra": 9}

    def __init__(self):
        if not shutil.which("xdotool"):
            raise BackendError("xdotool not installed")
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
            raise BackendError("xdotool needs an X11 session")

    def _run(self, *args: str) -> None:
        subprocess.run(["xdotool", *args], capture_output=True, timeout=3)

    def press(self, button: str) -> None:
        self._run("mousedown", str(self._IDX[button]))

    def release(self, button: str) -> None:
        self._run("mouseup", str(self._IDX[button]))

    def click(self, button: str, hold_s: float = 0.0) -> None:
        if hold_s <= 0:
            self._run("click", str(self._IDX[button]))
        else:
            super().click(button, hold_s)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        if horizontal:
            btn = "7" if amount > 0 else "6"
        else:
            btn = "4" if amount > 0 else "5"
        self._run("click", "--repeat", str(abs(amount)), btn)

    def move(self, dx: int, dy: int) -> None:
        self._run("mousemove_relative", "--", str(dx), str(dy))

    def release_all(self) -> None:
        for b in self._IDX:
            self.release(b)


class DryRunBackend(Backend):
    """Counts events instead of injecting them. For testing / demo mode."""
    name = "dry-run"
    supports_move = True

    def __init__(self):
        self.events: list[tuple] = []
        self.presses = 0

    def press(self, button: str) -> None:
        self.presses += 1
        if len(self.events) < 10000:
            self.events.append(("press", button, time.perf_counter()))

    def release(self, button: str) -> None:
        if len(self.events) < 10000:
            self.events.append(("release", button, time.perf_counter()))

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        self.presses += 1

    def move(self, dx: int, dy: int) -> None:
        pass


ORDER = ["uinput", "ydotool", "xdotool"]
_CLASSES = {"uinput": UInputBackend, "ydotool": YdotoolBackend,
            "xdotool": XdotoolBackend, "dry-run": DryRunBackend}


def open_backend(preferred: str = "auto") -> tuple[Backend, list[str]]:
    """Return (backend, notes). Falls back to dry-run if nothing works."""
    notes: list[str] = []
    names = ORDER if preferred == "auto" else [preferred]
    for n in names:
        try:
            return _CLASSES[n](), notes
        except BackendError as e:
            notes.append(f"{n}: {e}")
        except Exception as e:  # pragma: no cover - defensive
            notes.append(f"{n}: {e!r}")
    notes.append("falling back to DRY-RUN (no real clicks)")
    return DryRunBackend(), notes
