"""Engine scheduler tests. Timing bounds are deliberately loose for slow CI runners."""
import time
import unittest
from unittest import mock

from clickity import engine as engine_mod
from clickity.config import Settings
from clickity.engine import ARMING, FIRING, IDLE, Engine

from .helpers import RecordingBackend, wait_for


class EngineCase(unittest.TestCase):
    def make(self, backend=None, **settings):
        self.backend = backend or RecordingBackend()
        self.logs = []
        eng = Engine(self.backend, Settings(**settings).clamp(), self.logs.append)
        self.addCleanup(eng.shutdown)
        return eng


class LifecycleTest(EngineCase):
    def test_starts_idle(self):
        eng = self.make()
        self.assertEqual(eng.state, IDLE)
        time.sleep(0.05)
        self.assertEqual(self.backend.calls, [])

    def test_start_stop(self):
        eng = self.make(cps=100)
        eng.start()
        self.assertEqual(eng.state, FIRING)
        self.assertTrue(wait_for(lambda: eng.clicks >= 3))
        eng.stop()
        self.assertEqual(eng.state, IDLE)
        self.assertEqual(self.backend.calls[-1][0], "release_all")
        n = self.backend.count("press")
        time.sleep(0.1)
        self.assertEqual(self.backend.count("press"), n)
        self.assertTrue(any("clicks in" in m for m in self.logs))

    def test_toggle(self):
        eng = self.make(cps=50)
        eng.toggle()
        self.assertEqual(eng.state, FIRING)
        eng.toggle()
        self.assertEqual(eng.state, IDLE)

    def test_start_while_running_is_noop(self):
        eng = self.make(cps=50)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.clicks >= 2))
        before = eng.run_started
        eng.start()
        self.assertEqual(eng.run_started, before)

    def test_stop_when_idle_is_noop(self):
        eng = self.make()
        eng.stop()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.logs, [])

    def test_shutdown_closes_backend(self):
        eng = self.make(cps=50)
        eng.start()
        eng.shutdown()
        self.assertTrue(self.backend.closed)
        self.assertFalse(eng._thread.is_alive())

    def test_counters_reset_per_run_but_session_accumulates(self):
        eng = self.make(cps=200, max_clicks=5)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        self.assertEqual(eng.clicks, 5)
        self.assertEqual(eng.session_clicks, 10)


class CountdownTest(EngineCase):
    def test_arming_then_firing(self):
        eng = self.make(cps=100, start_delay=0.3)
        eng.start()
        self.assertEqual(eng.state, ARMING)
        self.assertGreater(eng.arm_remaining(), 0.1)
        time.sleep(0.15)
        self.assertEqual(self.backend.count("press"), 0)
        self.assertTrue(wait_for(lambda: eng.state == FIRING, timeout=2))
        self.assertTrue(wait_for(lambda: eng.clicks > 0))

    def test_stop_during_countdown_disarms(self):
        eng = self.make(cps=100, start_delay=0.3)
        eng.start()
        eng.stop()
        time.sleep(0.45)
        self.assertEqual(eng.state, IDLE)
        self.assertEqual(self.backend.count("press"), 0)
        self.assertTrue(any("disarmed" in m for m in self.logs))

    def test_explicit_zero_delay_overrides_setting(self):
        eng = self.make(cps=100, start_delay=5)
        eng.start(delay=0)
        self.assertEqual(eng.state, FIRING)


class LimitsTest(EngineCase):
    def test_click_limit_is_exact(self):
        eng = self.make(cps=300, max_clicks=25)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        self.assertEqual(self.backend.count("press"), 25)
        self.assertTrue(any("click limit" in m for m in self.logs))

    def test_click_limit_counts_actions_not_clicks(self):
        eng = self.make(cps=100, max_clicks=4, click_type="triple")
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        self.assertEqual(eng.actions, 4)
        self.assertEqual(eng.clicks, 12)
        self.assertEqual(self.backend.count("press"), 12)

    def test_time_limit(self):
        eng = self.make(cps=50, max_seconds=1)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE, timeout=3))
        self.assertAlmostEqual(eng.last_run_seconds, 1.0, delta=0.25)
        self.assertTrue(any("time limit" in m for m in self.logs))

    def test_backend_error_stops_engine(self):
        eng = self.make(backend=RecordingBackend(fail_after=3), cps=200)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        self.assertEqual(eng.clicks, 3)
        self.assertTrue(any("backend error" in m for m in self.logs))


class RateTest(EngineCase):
    def measure(self, cps, seconds=1.0, **kw):
        """Returns (rate from median gap, rate from raw count).

        The median gap is the real scheduler accuracy and is immune to a one-off
        VM/CI stall (which the engine deliberately does not "catch up" on).
        The raw count only gets a loose sanity bound.
        """
        eng = self.make(cps=cps, **kw)
        eng.start()
        time.sleep(seconds)
        eng.stop()
        t = sorted(c[-1] for c in self.backend.of("press"))
        gaps = sorted(b - a for a, b in zip(t, t[1:]))
        median = gaps[len(gaps) // 2]
        return 1.0 / median, len(t) / seconds

    def check_rate(self, cps, **kw):
        rate, raw = self.measure(cps, **kw)
        self.assertAlmostEqual(rate, cps, delta=cps * 0.1)
        self.assertGreater(raw, cps * 0.5)
        self.assertLess(raw, cps * 1.1 + 2)

    def test_rate_20(self):
        self.check_rate(20)

    def test_rate_100(self):
        self.check_rate(100)

    def test_rate_300(self):
        # After a stall the engine replays up to 150 ms of backlog (~45 clicks at
        # 300/s) in a quick burst, which skews the median gap. Total throughput
        # is the meaningful number here; it must never exceed the target.
        _, raw = self.measure(300)
        self.assertGreater(raw, 150)
        self.assertLess(raw, 330)

    def test_jitter_keeps_average_rate(self):
        _, raw = self.measure(50, seconds=1.5, jitter=50)
        self.assertGreater(raw, 30)
        self.assertLess(raw, 60)

    def test_jitter_varies_intervals(self):
        self.measure(50, seconds=0.6, jitter=50)
        t = [c[-1] for c in self.backend.of("press")]
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertGreater(max(gaps) - min(gaps), 0.005)

    def test_interval_bounds(self):
        eng = self.make(cps=500, jitter=90)
        for _ in range(500):
            self.assertGreaterEqual(eng._interval(), 0.0005)
        eng.settings.cps = 10
        eng.settings.jitter = 30
        for _ in range(500):
            self.assertTrue(0.07 - 1e-9 <= eng._interval() <= 0.13 + 1e-9)

    def test_real_cps_reports_recent_rate(self):
        eng = self.make(cps=40)
        eng.start()
        time.sleep(1.2)
        self.assertAlmostEqual(eng.real_cps(), 40, delta=8)
        eng.stop()

    def test_no_catch_up_spam_after_stall(self):
        eng = self.make(cps=100)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.clicks >= 2))
        with eng.lock:            # simulate the process being stalled ...
            eng._next -= 2.0      # ... so the schedule is 2s behind
        time.sleep(0.3)
        eng.stop()
        # without drift clamping this would fire ~200 clicks immediately
        self.assertLess(eng.clicks, 80)


class BurstTest(EngineCase):
    def test_bursts_pause_between_groups(self):
        eng = self.make(cps=100, burst=5, burst_pause_ms=300)
        eng.start()
        time.sleep(0.75)
        eng.stop()
        t = [c[-1] for c in self.backend.of("press")]
        self.assertGreaterEqual(len(t), 10)
        gaps = [b - a for a, b in zip(t, t[1:])]
        long_idx = [i for i, g in enumerate(gaps) if g > 0.2]
        self.assertTrue(long_idx, "expected a pause between bursts")
        self.assertEqual(long_idx[0], 4)  # after the 5th click
        for i in long_idx:
            self.assertEqual((i + 1) % 5, 0)


class ActionTest(EngineCase):
    def run_n(self, n=3, backend=None, **kw):
        kw.setdefault("cps", 100)
        eng = self.make(backend=backend, max_clicks=n, **kw)
        eng.start()
        self.assertTrue(wait_for(lambda: eng.state == IDLE))
        return eng

    def test_buttons(self):
        for button in ("left", "right", "middle", "side", "extra"):
            with self.subTest(button=button):
                self.run_n(2, button=button)
                self.assertEqual({c[1] for c in self.backend.of("press")}, {button})

    def test_every_press_is_released(self):
        self.run_n(5, click_type="double")
        self.assertEqual(self.backend.count("press"), self.backend.count("release"))

    def test_scroll_directions(self):
        expect = {"scroll-up": (2, False), "scroll-down": (-2, False),
                  "scroll-right": (2, True), "scroll-left": (-2, True)}
        for action, (amount, horiz) in expect.items():
            with self.subTest(action=action):
                eng = self.run_n(3, action=action, scroll_step=2)
                self.assertEqual([c[1:3] for c in self.backend.of("scroll")], [(amount, horiz)] * 3)
                self.assertEqual(self.backend.count("press"), 0)
                self.assertEqual(eng.clicks, 3)

    def test_hold_time(self):
        self.run_n(3, cps=2, hold_ms=60)
        calls = [c for c in self.backend.calls if c[0] in ("press", "release")]
        for press, release in zip(calls[::2], calls[1::2]):
            self.assertGreaterEqual(release[-1] - press[-1], 0.055)

    def test_hold_capped_to_half_slot(self):
        eng = self.make(cps=100, hold_ms=1000)
        with mock.patch.object(engine_mod.time, "sleep"):
            with mock.patch.object(self.backend, "click") as click:
                eng._do_action()
        self.assertLessEqual(click.call_args[0][1], 0.005 + 1e-9)

    def test_wiggle_returns_to_origin(self):
        with mock.patch.object(engine_mod.random, "randint", side_effect=[3, -2] * 10):
            self.run_n(4, wiggle_px=3)
        moves = [c[1:3] for c in self.backend.of("move")]
        self.assertEqual(moves, [(3, -2), (-3, 2)] * 4)
        order = [c[0] for c in self.backend.calls if c[0] in ("move", "press")]
        self.assertEqual(order[:3], ["move", "press", "move"])

    def test_no_wiggle_without_move_support(self):
        b = RecordingBackend()
        b.supports_move = False
        self.run_n(3, backend=b, wiggle_px=5)
        self.assertEqual(b.count("move"), 0)
