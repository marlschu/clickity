import os
import struct
import tempfile
import unittest
from unittest import mock

from clickity import backends
from clickity.backends import (BUTTONS, EV_KEY, EV_REL, EV_SYN, REL_HWHEEL, REL_WHEEL, REL_X,
                               REL_Y, BackendError, DryRunBackend, UInputBackend, XdotoolBackend,
                               YdotoolBackend, open_backend)
from clickity.hotkeys import EVIOCGBIT, EVIOCGNAME


class IoctlNumbersTest(unittest.TestCase):
    """Values from linux/uinput.h and linux/input.h on x86_64 / aarch64."""

    def test_uinput_ioctls(self):
        self.assertEqual(backends.UI_DEV_CREATE, 0x5501)
        self.assertEqual(backends.UI_DEV_DESTROY, 0x5502)
        self.assertEqual(backends.UI_DEV_SETUP, 0x405C5503)
        self.assertEqual(backends.UI_SET_EVBIT, 0x40045564)
        self.assertEqual(backends.UI_SET_KEYBIT, 0x40045565)
        self.assertEqual(backends.UI_SET_RELBIT, 0x40045566)

    def test_evdev_ioctls(self):
        self.assertEqual(EVIOCGNAME(256), 0x81004506)
        self.assertEqual(EVIOCGBIT(EV_KEY, 96), 0x80604521)

    def test_struct_sizes(self):
        self.assertEqual(struct.calcsize(backends._SETUP_FMT), 92)
        self.assertEqual(struct.calcsize(backends._EVENT_FMT), 24)  # 64-bit timeval

    def test_button_codes(self):
        self.assertEqual(BUTTONS, {"left": 0x110, "right": 0x111, "middle": 0x112,
                                   "side": 0x113, "extra": 0x114})


class UInputBackendTest(unittest.TestCase):
    """Runs the real backend against a regular file with ioctl stubbed out."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="clickity-uinput-")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.ioctls = []
        fake_ioctl = lambda fd, req, arg=0: self.ioctls.append((req, arg))  # noqa: E731
        for p in (mock.patch.object(backends.fcntl, "ioctl", fake_ioctl),
                  mock.patch.object(backends.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        self.b = UInputBackend(self.path)

    def events(self):
        with open(self.path, "rb") as f:
            data = f.read()
        size = struct.calcsize(backends._EVENT_FMT)
        return [struct.unpack_from(backends._EVENT_FMT, data, o)[2:]
                for o in range(0, len(data), size)]

    def test_device_setup_sequence(self):
        reqs = [r for r, _ in self.ioctls]
        self.assertEqual(reqs[-1], backends.UI_DEV_CREATE)
        self.assertEqual(reqs[-2], backends.UI_DEV_SETUP)
        self.assertIn((backends.UI_SET_EVBIT, EV_KEY), self.ioctls)
        self.assertIn((backends.UI_SET_EVBIT, EV_REL), self.ioctls)
        for code in BUTTONS.values():
            self.assertIn((backends.UI_SET_KEYBIT, code), self.ioctls)
        for code in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            self.assertIn((backends.UI_SET_RELBIT, code), self.ioctls)
        setup = self.ioctls[-2][1]
        bus, vendor, product, version, name, ff = struct.unpack(backends._SETUP_FMT, setup)
        self.assertEqual(name.rstrip(b"\0").decode(), backends.DEVICE_NAME)

    def test_click_byte_stream(self):
        self.b.click("right")
        syn = (EV_SYN, 0, 0)
        self.assertEqual(self.events(), [(EV_KEY, 0x111, 1), syn, (EV_KEY, 0x111, 0), syn])

    def test_scroll_and_move(self):
        self.b.scroll(3)
        self.b.scroll(-2, horizontal=True)
        self.b.move(5, 0)
        self.b.move(0, -4)
        syn = (EV_SYN, 0, 0)
        self.assertEqual(self.events(), [
            (EV_REL, REL_WHEEL, 3), syn,
            (EV_REL, REL_HWHEEL, -2), syn,
            (EV_REL, REL_X, 5), syn,
            (EV_REL, REL_Y, -4), syn,
        ])

    def test_release_all_lifts_held_buttons_only(self):
        self.b.press("left")
        self.b.press("middle")
        self.b.release("middle")
        n = len(self.events())
        self.b.release_all()
        self.assertEqual(self.events()[n:], [(EV_KEY, 0x110, 0), (EV_SYN, 0, 0)])
        self.b.release_all()  # nothing held any more
        self.assertEqual(len(self.events()), n + 2)

    def test_close_releases_and_destroys_once(self):
        self.b.press("left")
        self.b.close()
        self.assertEqual(self.ioctls[-1][0], backends.UI_DEV_DESTROY)
        self.assertEqual(self.events()[-2], (EV_KEY, 0x110, 0))
        count = len(self.ioctls)
        self.b.close()
        self.assertEqual(len(self.ioctls), count)


class UInputErrorsTest(unittest.TestCase):
    def test_missing_device(self):
        with self.assertRaisesRegex(BackendError, "modprobe"):
            UInputBackend("/nonexistent/uinput")

    def test_no_permission(self):
        with mock.patch.object(backends.os, "open", side_effect=PermissionError):
            with self.assertRaisesRegex(BackendError, "install.sh"):
                UInputBackend("/dev/uinput")

    def test_ioctl_failure_closes_fd(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.unlink, path)
        closed = []
        real_close = os.close
        with mock.patch.object(backends.fcntl, "ioctl", side_effect=OSError("ENOTTY")), \
             mock.patch.object(backends.os, "close", side_effect=lambda f: (closed.append(f), real_close(f))):
            with self.assertRaisesRegex(BackendError, "setup failed"):
                UInputBackend(path)
        self.assertEqual(len(closed), 1)


class SubprocessBackendsTest(unittest.TestCase):
    def setUp(self):
        self.cmds = []
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        patches = [
            mock.patch.object(backends.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(backends.subprocess, "run",
                              side_effect=lambda cmd, **kw: (self.cmds.append(cmd), ok)[1]),
            mock.patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_ydotool_mapping(self):
        y = YdotoolBackend()
        self.cmds.clear()
        y.click("right")
        y.click("left", hold_s=0.001)
        y.scroll(2)
        y.scroll(-1, horizontal=True)
        self.assertEqual(self.cmds, [
            ["ydotool", "click", "0xc1"],
            ["ydotool", "click", "0x40"],
            ["ydotool", "click", "0x80"],
            ["ydotool", "mousemove", "-w", "-x", "0", "-y", "-2"],  # ydotool +y is down
            ["ydotool", "mousemove", "-w", "-x", "-1", "-y", "0"],
        ])

    def test_ydotool_daemon_unreachable(self):
        bad = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch.object(backends.subprocess, "run", return_value=bad):
            with self.assertRaisesRegex(BackendError, "ydotoold"):
                YdotoolBackend()

    def test_xdotool_mapping(self):
        x = XdotoolBackend()
        x.click("right")
        x.click("extra")
        x.scroll(2)
        x.scroll(-3)
        x.scroll(1, horizontal=True)
        x.scroll(-1, horizontal=True)
        x.move(-4, 7)
        self.assertEqual(self.cmds, [
            ["xdotool", "click", "3"],
            ["xdotool", "click", "9"],
            ["xdotool", "click", "--repeat", "2", "4"],
            ["xdotool", "click", "--repeat", "3", "5"],
            ["xdotool", "click", "--repeat", "1", "7"],
            ["xdotool", "click", "--repeat", "1", "6"],
            ["xdotool", "mousemove_relative", "--", "-4", "7"],
        ])

    def test_xdotool_refuses_wayland(self):
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            with self.assertRaisesRegex(BackendError, "X11"):
                XdotoolBackend()


class OpenBackendTest(unittest.TestCase):
    def _fail(self, msg):
        def ctor():
            raise BackendError(msg)
        return ctor

    def test_falls_through_to_dry_run(self):
        classes = {"uinput": self._fail("a"), "ydotool": self._fail("b"),
                   "xdotool": self._fail("c"), "dry-run": DryRunBackend}
        with mock.patch.dict(backends._CLASSES, classes):
            b, notes = open_backend()
        self.assertIsInstance(b, DryRunBackend)
        self.assertEqual(notes[:3], ["uinput: a", "ydotool: b", "xdotool: c"])
        self.assertIn("DRY-RUN", notes[-1])

    def test_first_working_backend_wins(self):
        sentinel = object()
        classes = {"uinput": self._fail("a"), "ydotool": lambda: sentinel}
        with mock.patch.dict(backends._CLASSES, classes):
            b, notes = open_backend()
        self.assertIs(b, sentinel)
        self.assertEqual(notes, ["uinput: a"])

    def test_explicit_dry_run(self):
        b, notes = open_backend("dry-run")
        self.assertIsInstance(b, DryRunBackend)
        self.assertEqual(notes, [])


class DryRunTest(unittest.TestCase):
    def test_counts(self):
        d = DryRunBackend()
        d.click("left")
        d.scroll(1)
        self.assertEqual(d.presses, 2)
        self.assertEqual([e[:2] for e in d.events], [("press", "left"), ("release", "left")])
