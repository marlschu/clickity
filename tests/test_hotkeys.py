import os
import queue
import struct
import unittest
from types import SimpleNamespace
from unittest import mock

from clickity import hotkeys
from clickity.backends import EV_KEY, EV_SYN
from clickity.hotkeys import (CODE_TO_NAME, HOTKEY_CHOICES, KEYS, HotkeyListener, _test_bit,
                              hotkey_fix_hint, input_group_state)


class KeyTableTest(unittest.TestCase):
    def test_codes_match_input_event_codes_h(self):
        expected = {"F1": 59, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
                    "F13": 183, "F24": 194, "PAUSE": 119, "SCROLLLOCK": 70,
                    "INSERT": 110, "HOME": 102, "END": 107, "PAGEUP": 104,
                    "PAGEDOWN": 109, "ESC": 1, "CAPSLOCK": 58}
        for name, code in expected.items():
            with self.subTest(key=name):
                self.assertEqual(KEYS[name], code)

    def test_codes_unique_and_reversible(self):
        self.assertEqual(len(set(KEYS.values())), len(KEYS))
        for name, code in KEYS.items():
            self.assertEqual(CODE_TO_NAME[code], name)

    def test_choices_are_known_keys(self):
        self.assertTrue(set(HOTKEY_CHOICES) <= set(KEYS))
        self.assertIn("F8", HOTKEY_CHOICES)
        self.assertIn("F9", HOTKEY_CHOICES)

    def test_test_bit(self):
        buf = bytes([0b00000101, 0b10000000])
        self.assertTrue(_test_bit(buf, 0))
        self.assertFalse(_test_bit(buf, 1))
        self.assertTrue(_test_bit(buf, 2))
        self.assertTrue(_test_bit(buf, 15))
        self.assertFalse(_test_bit(buf, 14))


class InputGroupTest(unittest.TestCase):
    def state(self, members, session_gids, user="alice", exists=True):
        group = SimpleNamespace(gr_gid=104, gr_mem=members)
        getgrnam = mock.Mock(return_value=group) if exists else mock.Mock(side_effect=KeyError)
        with mock.patch("grp.getgrnam", getgrnam), \
             mock.patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name=user)), \
             mock.patch.object(hotkeys.os, "getgroups", return_value=session_gids):
            return input_group_state(), hotkey_fix_hint()

    def test_active(self):
        state, _ = self.state(["alice"], [1000, 104])
        self.assertEqual(state, "active")

    def test_active_via_primary_or_session_gid_even_if_not_listed(self):
        state, _ = self.state([], [104])
        self.assertEqual(state, "active")

    def test_pending_needs_relogin(self):
        state, hint = self.state(["bob", "alice"], [1000])
        self.assertEqual(state, "pending")
        self.assertIn("log out", hint)

    def test_missing(self):
        state, hint = self.state(["bob"], [1000])
        self.assertEqual(state, "missing")
        self.assertIn("install.sh --hotkeys", hint)

    def test_no_input_group_on_system(self):
        state, _ = self.state([], [1000], exists=False)
        self.assertEqual(state, "missing")


def ev(code, value, etype=EV_KEY):
    return struct.pack(hotkeys._EVENT_FMT, 0, 0, etype, code, value)


class ListenerTest(unittest.TestCase):
    """Feeds synthetic kernel events through a pipe into the real listener loop."""

    def setUp(self):
        p = mock.patch.object(hotkeys.glob, "glob", return_value=[])
        p.start()
        self.addCleanup(p.stop)
        self.got = queue.Queue()
        self.hk = HotkeyListener(lambda name, pressed: self.got.put((name, pressed)))
        self.assertFalse(self.hk.available)
        self.r, self.w = os.pipe()
        os.set_blocking(self.r, False)
        self.hk._fds["/dev/input/event-test"] = self.r
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.hk.stop()
        if self.hk.is_alive():
            self.hk.join(timeout=2)
        try:
            os.close(self.w)
        except OSError:
            pass

    def drain(self, timeout=0.6):
        out = []
        try:
            while True:
                out.append(self.got.get(timeout=timeout))
                timeout = 0.2
        except queue.Empty:
            return out

    def test_watched_keys_reported(self):
        self.hk.set_keys("F8", "F9")
        self.hk.start()
        os.write(self.w, ev(66, 1) + ev(0, 0, EV_SYN) + ev(66, 0) + ev(67, 1))
        self.assertEqual(self.drain(), [("F8", True), ("F8", False), ("F9", True)])

    def test_ignores_unwatched_keys_and_autorepeat(self):
        self.hk.set_keys("F8")
        self.hk.start()
        os.write(self.w, ev(30, 1) + ev(67, 1) + ev(66, 2) + ev(66, 1))
        self.assertEqual(self.drain(), [("F8", True)])

    def test_unknown_names_ignored_by_set_keys(self):
        self.hk.set_keys("F8", "NOPE")
        self.assertEqual(self.hk.watch, {66})

    def test_callback_exception_does_not_kill_listener(self):
        calls = []

        def cb(name, pressed):
            calls.append(name)
            if len(calls) == 1:
                raise RuntimeError("boom")
            self.got.put((name, pressed))

        self.hk.callback = cb
        self.hk.set_keys("F8")
        self.hk.start()
        os.write(self.w, ev(66, 1))
        os.write(self.w, ev(66, 0))
        self.assertEqual(self.drain(), [("F8", False)])
        self.assertTrue(self.hk.is_alive())

    def test_rescan_drops_vanished_device(self):
        self.hk._scan()  # glob returns [] so the test device has "disappeared"
        self.assertFalse(self.hk.available)
        with self.assertRaises(OSError):
            os.fstat(self.r)

    def test_stop_closes_fds(self):
        self.hk.start()
        self.hk.stop()
        self.hk.join(timeout=2)
        self.assertFalse(self.hk.is_alive())
        with self.assertRaises(OSError):
            os.fstat(self.r)
