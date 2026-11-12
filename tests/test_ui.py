import curses
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unittest
from unittest import mock

from clickity import ui
from clickity.backends import DryRunBackend
from clickity.config import Settings, Store
from clickity.engine import ARMING, FIRING, IDLE

from .helpers import IsolatedConfig, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HelpersTest(unittest.TestCase):
    def test_fmt_val(self):
        self.assertEqual(ui._fmt_val("max_clicks", 0), "∞")
        self.assertEqual(ui._fmt_val("max_seconds", 30), "30")
        self.assertEqual(ui._fmt_val("jitter", 0), "OFF")
        self.assertEqual(ui._fmt_val("cps", 12.0), "12")
        self.assertEqual(ui._fmt_val("cps", 0.5), "0.5")
        self.assertEqual(ui._fmt_val("button", "left"), "LEFT")

    def test_cps_step_up_and_down_are_inverse(self):
        v = 0.1
        seen = [v]
        while v < 500:
            v = ui._cps_step(v, True)
            seen.append(v)
        self.assertIn(1, seen)
        self.assertIn(10, seen)
        self.assertIn(100, seen)
        for a, b in zip(seen, seen[1:]):
            self.assertAlmostEqual(ui._cps_step(b, False), a, places=6)

    def test_hms(self):
        self.assertEqual(ui._hms(0), "00:00:00.0")
        self.assertEqual(ui._hms(3725.5), "01:02:05.5")

    def test_fields_cover_every_setting(self):
        self.assertEqual({f[0] for f in ui.FIELDS}, set(Settings().to_dict()))
        self.assertEqual(set(ui.HINTS), set(Settings().to_dict()))

    def test_banner_rows_equal_width(self):
        self.assertEqual(len({len(r) for r in ui.BANNER}), 1)


class AppLogicTest(IsolatedConfig):
    """Drives App's key handling without a real terminal."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(ui.App, "apply_theme")
        p.start()
        self.addCleanup(p.stop)
        self.store = Store.load()
        self.app = ui.App(mock.Mock(), DryRunBackend(), [], self.store, use_hotkeys=False)
        self.addCleanup(self.app.engine.shutdown)

    def sel(self, attr):
        self.app.sel = [f[0] for f in ui.FIELDS].index(attr)

    def test_navigation_wraps(self):
        self.app.handle_key("k")
        self.assertEqual(self.app.sel, len(ui.FIELDS) - 1)
        self.app.handle_key("j")
        self.assertEqual(self.app.sel, 0)

    def test_adjust_choice_and_numbers(self):
        self.sel("button")
        self.app.handle_key("l")
        self.assertEqual(self.app.settings.button, "right")
        self.sel("cps")
        self.app.handle_key(curses.KEY_RIGHT)
        self.assertEqual(self.app.settings.cps, 15)
        self.app.handle_key(curses.KEY_NPAGE)  # 10 steps down: 15 -> 10 -> 9 ... -> 1
        self.assertAlmostEqual(self.app.settings.cps, 1, places=6)
        self.sel("jitter")
        self.app.handle_key("h")
        self.assertEqual(self.app.settings.jitter, 0)  # clamped

    def test_engine_sees_changes_live(self):
        self.sel("cps")
        self.app.handle_key("l")
        self.assertIs(self.app.engine.settings, self.app.settings)
        self.assertEqual(self.app.engine.settings.cps, 15)

    def test_local_mode_forces_countdown(self):
        self.app.handle_key(" ")
        self.assertEqual(self.app.engine.state, ARMING)
        self.assertGreaterEqual(self.app.engine.arm_remaining(), ui.LOCAL_MIN_DELAY - 0.2)
        self.app.handle_key(" ")
        self.assertEqual(self.app.engine.state, IDLE)

    def test_local_f_keys(self):
        self.app.handle_key(curses.KEY_F8)
        self.assertEqual(self.app.engine.state, ARMING)
        self.app.handle_key(curses.KEY_F9)
        self.assertEqual(self.app.engine.state, IDLE)
        self.assertTrue(self.app.quit)

    def test_global_hold_mode(self):
        self.app.settings.mode = "hold"
        self.app._on_hotkey("F8", True)
        self.assertEqual(self.app.engine.state, FIRING)  # no countdown when held
        self.app._on_hotkey("F8", False)
        self.assertEqual(self.app.engine.state, IDLE)

    def test_global_toggle_and_panic(self):
        self.app._on_hotkey("F8", True)
        self.app._on_hotkey("F8", False)
        self.assertNotEqual(self.app.engine.state, IDLE)
        self.app._on_hotkey("F9", True)
        self.assertEqual(self.app.engine.state, IDLE)
        self.assertTrue(self.app.quit)

    def test_cycle_profiles(self):
        names = list(self.store.profiles)
        self.app.handle_key("p")
        self.assertEqual(self.store.active, names[1])
        self.assertEqual(self.app.settings, self.store.profiles[names[1]])
        self.app.handle_key("o")
        self.assertEqual(self.store.active, names[0])

    def test_save_new_and_delete_profile(self):
        self.sel("cps")
        self.app.handle_key("l")
        with mock.patch.object(self.app, "prompt", return_value="sniper"):
            self.app.handle_key("n")
        self.assertEqual(Store.load().profiles["sniper"].cps, 15)
        self.assertEqual(Store.load().active, "sniper")

        with mock.patch.object(self.app, "prompt", return_value="yes"):
            self.app.handle_key("d")
        reloaded = Store.load()
        self.assertNotIn("sniper", reloaded.profiles)
        self.assertEqual(reloaded.active, "default")

    def test_cannot_delete_default(self):
        with mock.patch.object(self.app, "prompt") as prompt:
            self.app.handle_key("d")
        prompt.assert_not_called()
        self.assertIn("default", self.store.profiles)

    def test_deleted_preset_stays_gone(self):
        self.store.active = "turbo"
        with mock.patch.object(self.app, "prompt", return_value="y"):
            self.app.handle_key("d")
        self.assertNotIn("turbo", Store.load().profiles)

    def test_edit_value(self):
        self.sel("max_clicks")
        with mock.patch.object(self.app, "prompt", return_value="250"):
            self.app.handle_key("\n")
        self.assertEqual(self.app.settings.max_clicks, 250)
        with mock.patch.object(self.app, "prompt", return_value="abc"):
            self.app.handle_key("\n")
        self.assertEqual(self.app.settings.max_clicks, 250)
        self.assertIn("invalid number", self.app.logs[-1][1])

    def test_lifetime_clicks_persist_incrementally(self):
        self.app.engine.session_clicks = 40
        self.app.persist()
        self.app.engine.session_clicks = 55
        self.app.persist()
        self.assertEqual(Store.load().lifetime_clicks, 55)

    def test_same_hotkey_and_panic_warns(self):
        self.sel("hotkey")
        self.app.settings.hotkey = "F8"
        self.app.settings.panic_key = "F8"
        self.app._after_change("hotkey")
        self.assertTrue(any("same key" in m for _, m in self.app.logs))


@unittest.skipUnless(sys.platform.startswith("linux"), "pty test needs Linux")
class TuiSmokeTest(IsolatedConfig):
    """Runs the real curses UI in a pseudo-terminal and presses keys."""

    def run_tui(self, cols, rows, term="xterm-256color", keys=b""):
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.environ.update(TERM=term, XDG_CONFIG_HOME=self.cfg_dir, LANG="C.UTF-8",
                              LC_ALL="C.UTF-8", ESCDELAY="25")
            os.environ.pop("COLUMNS", None)
            os.environ.pop("LINES", None)
            os.chdir(ROOT)
            os.execv(sys.executable, [sys.executable, "-m", "clickity",
                                      "--dry-run", "--no-hotkeys"])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        out = bytearray()

        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError as e:
                        if e.errno == errno.EIO:
                            return False
                        raise
                    if not chunk:
                        return False
                    out.extend(chunk)
            return True

        try:
            pump(1.5)
            for k in keys:
                os.write(fd, bytes([k]))
                pump(0.1)
            os.write(fd, b"q")
            pump(3)
            _, status = os.waitpid(pid, 0)
        except BaseException:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise
        finally:
            os.close(fd)
        return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace")

    def test_sizes_and_keys(self):
        cases = [(70, 22, "xterm-256color"), (110, 42, "xterm-256color"), (80, 24, "xterm")]
        for cols, rows, term in cases:
            with self.subTest(size=f"{cols}x{rows}", term=term):
                code, out = self.run_tui(cols, rows, term, keys=b"jjlhT?x p")
                self.assertEqual(code, 0, out[-2000:])
                self.assertIn("DRY-RUN", out)
                self.assertIn("disconnected", out)
                self.assertNotIn("Traceback", out)

    def test_too_small_terminal_does_not_crash(self):
        code, out = self.run_tui(40, 10)
        self.assertEqual(code, 0, out[-2000:])
        self.assertNotIn("Traceback", out)


class InstallerTest(unittest.TestCase):
    def test_scripts_parse(self):
        for script in ("install.sh", "uninstall.sh"):
            with self.subTest(script=script):
                r = subprocess.run(["bash", "-n", os.path.join(ROOT, script)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
