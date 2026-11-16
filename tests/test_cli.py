import contextlib
import io
import json
import os
import subprocess
import sys
from unittest import mock

from clickity import cli
from clickity.config import Store

from .helpers import IsolatedConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeListener:
    """Stands in for the global hotkey listener so tests never read real keyboards."""
    available = False
    keyboards: list = []
    instances: list = []

    def __init__(self, callback):
        self.callback = callback
        self.keys = ()
        self.started = False
        FakeListener.instances.append(self)

    def set_keys(self, *names):
        self.keys = names

    def start(self):
        self.started = True

    def stop(self):
        pass


class CliCase(IsolatedConfig):
    def setUp(self):
        super().setUp()
        FakeListener.instances = []
        FakeListener.available = False
        p = mock.patch.object(cli, "HotkeyListener", FakeListener)
        p.start()
        self.addCleanup(p.stop)

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()


class ArgsToSettingsTest(CliCase):
    def parse(self, *argv):
        captured = {}
        with mock.patch.object(cli, "cmd_run", side_effect=lambda a: captured.setdefault("a", a) and 0):
            cli.main(["run", *argv])
        return cli._settings_from_args(captured["a"])

    def test_flags_override_profile(self):
        s = self.parse("--profile", "human", "-c", "33", "-b", "right", "-t", "double",
                       "-n", "7", "-s", "9", "-d", "1.5", "-j", "20", "--hold", "12",
                       "--burst", "4", "--burst-pause", "250", "--wiggle", "3",
                       "-a", "scroll-up", "--scroll-step", "2", "--hotkey", "F6",
                       "--panic-key", "PAUSE")
        self.assertEqual((s.cps, s.button, s.click_type, s.max_clicks, s.max_seconds),
                         (33, "right", "double", 7, 9))
        self.assertEqual((s.start_delay, s.jitter, s.hold_ms, s.burst, s.burst_pause_ms),
                         (1.5, 20, 12, 4, 250))
        self.assertEqual((s.wiggle_px, s.action, s.scroll_step, s.hotkey, s.panic_key),
                         (3, "scroll-up", 2, "F6", "PAUSE"))

    def test_profile_values_kept_when_not_overridden(self):
        s = self.parse("--profile", "human")
        self.assertEqual((s.cps, s.jitter, s.hold_ms), (7, 35, 45))

    def test_interval_overrides_cps(self):
        self.assertEqual(self.parse("-c", "99", "-i", "250").cps, 4.0)

    def test_interval_floor(self):
        self.assertEqual(self.parse("-i", "0").cps, 500.0)  # 1000/1 then clamped

    def test_values_clamped(self):
        self.assertEqual(self.parse("-c", "100000").cps, 500.0)

    def test_global_flags_after_subcommand(self):
        captured = {}
        with mock.patch.object(cli, "cmd_run", side_effect=lambda a: captured.setdefault("a", a) and 0):
            cli.main(["run", "--dry-run", "--profile", "turbo"])
        self.assertTrue(captured["a"].dry_run)
        self.assertEqual(captured["a"].profile, "turbo")

    def test_unknown_profile_exits(self):
        with self.assertRaises(SystemExit) as cm:
            self.parse("--profile", "nope")
        self.assertIn("unknown profile", str(cm.exception.code))

    def test_bad_choice_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["run", "-b", "thumb"])


class RunCommandTest(CliCase):
    def test_click_limit_run(self):
        code, out = self.main("run", "--dry-run", "-c", "200", "-n", "15")
        self.assertEqual(code, 0)
        self.assertIn("total 15 clicks", out)
        self.assertIn("click limit 15 reached", out)
        self.assertIn("hotkeys offline", out)

    def test_lifetime_counter_persisted(self):
        self.main("run", "--dry-run", "-c", "200", "-n", "6", "-t", "double")
        self.main("run", "--dry-run", "-c", "200", "-n", "4")
        self.assertEqual(Store.load().lifetime_clicks, 16)

    def test_profile_settings_are_not_modified_by_run(self):
        self.main("run", "--dry-run", "-c", "200", "-n", "3")
        with open(self.cfg_file) as f:
            saved = json.load(f)
        self.assertEqual(saved["profiles"]["default"]["cps"], 10.0)

    def test_hotkeys_wired_when_available(self):
        FakeListener.available = True
        orig_start = FakeListener.start

        def start_and_kill(self_):
            orig_start(self_)
            self_.callback("F7", True)   # the configured kill switch fires immediately

        with mock.patch.object(FakeListener, "start", start_and_kill):
            code, out = self.main("run", "--dry-run", "-c", "50", "--wait",
                                  "--hotkey", "F6", "--panic-key", "F7")
        hk = FakeListener.instances[-1]
        self.assertEqual(hk.keys, ("F6", "F7"))
        self.assertTrue(hk.started)
        self.assertIn("F6: toggle", out)
        self.assertIn("total 0 clicks", out)


class OtherCommandsTest(CliCase):
    def test_profiles_lists_presets(self):
        code, out = self.main("profiles")
        self.assertEqual(code, 0)
        for name in ("default", "turbo", "human", "burst-5", "idle-keeper", "scroller"):
            self.assertIn(name, out)
        self.assertIn("lifetime clicks: 0", out)

    def test_doctor_runs(self):
        from clickity.backends import DryRunBackend
        with mock.patch.object(cli, "open_backend", return_value=(DryRunBackend(), ["uinput: nope"])):
            code, out = self.main("doctor")
        self.assertEqual(code, 0)
        self.assertIn("DIAGNOSTICS", out)
        self.assertIn("uinput: nope", out)
        self.assertIn("dry-run", out)

    def test_version(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            cli.main(["--version"])
        self.assertIn("clickity", out.getvalue())


class LauncherTest(IsolatedConfig):
    def test_bin_launcher_runs(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.cfg_dir, NO_COLOR="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "clickity"), "profiles"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("turbo", r.stdout)

    def test_module_entry_runs(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.cfg_dir)
        r = subprocess.run([sys.executable, "-m", "clickity", "--version"], cwd=ROOT,
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
