"""Click engine: a background thread with drift-corrected scheduling."""
from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Callable, Optional

from .backends import Backend
from .config import Settings

IDLE, ARMING, FIRING = "IDLE", "ARMING", "FIRING"
_MULT = {"single": 1, "double": 2, "triple": 3}


class Engine:
    def __init__(self, backend: Backend, settings: Settings,
                 log: Optional[Callable[[str], None]] = None):
        self.backend = backend
        self.settings = settings
        self.log = log or (lambda m: None)
        self.state = IDLE
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._quit = False
        # stats
        self.actions = 0          # actions in current run
        self.clicks = 0           # physical clicks in current run
        self.session_clicks = 0   # since app start
        self.run_started = 0.0
        self.arm_deadline = 0.0
        self.last_run_seconds = 0.0
        self.in_burst_pause = False
        self._next = 0.0
        self._burst_count = 0
        self._stamps: deque = deque()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clickity-engine")
        self._thread.start()

    # ---- control ------------------------------------------------------------
    def start(self, delay: Optional[float] = None) -> None:
        with self.lock:
            if self.state != IDLE:
                return
            d = self.settings.start_delay if delay is None else delay
            self.actions = self.clicks = 0
            self._stamps.clear()
            if d > 0:
                self.state = ARMING
                self.arm_deadline = time.perf_counter() + d
                self.log(f"armed // firing in {d:.1f}s")
            else:
                self._begin_firing()
        self._wake.set()

    def _begin_firing(self) -> None:
        # called with self.lock held; publish state LAST so the worker never
        # sees FIRING with stale timing fields
        self.run_started = time.perf_counter()
        self._next = self.run_started
        self._burst_count = 0
        self.in_burst_pause = False
        self.state = FIRING
        s = self.settings
        what = s.action if s.action != "click" else f"{s.button} {s.click_type}"
        self.log(f"FIRING >> {what} @ {s.cps:g}/s")

    def stop(self, reason: str = "stopped") -> None:
        with self.lock:
            if self.state == IDLE:
                return
            was_firing = self.state == FIRING
            self.state = IDLE
            self.in_burst_pause = False
            if was_firing:
                self.last_run_seconds = time.perf_counter() - self.run_started
        try:
            self.backend.release_all()
        except Exception:
            pass
        self._wake.set()
        if was_firing:
            self.log(f"{reason} // {self.clicks} clicks in {self.last_run_seconds:.1f}s")
        else:
            self.log(f"disarmed ({reason})")

    def toggle(self) -> None:
        if self.state == IDLE:
            self.start()
        else:
            self.stop("halted")

    def shutdown(self) -> None:
        self.stop("shutdown")
        self._quit = True
        self._wake.set()
        self._thread.join(timeout=2)
        try:
            self.backend.close()
        except Exception:
            pass

    # ---- stats --------------------------------------------------------------
    def real_cps(self) -> float:
        with self.lock:
            now = time.perf_counter()
            st = self._stamps
            while st and now - st[0] > 1.0:
                st.popleft()
            return float(len(st))

    def elapsed(self) -> float:
        if self.state == FIRING:
            return time.perf_counter() - self.run_started
        return self.last_run_seconds if self.state == IDLE else 0.0

    def arm_remaining(self) -> float:
        return max(0.0, self.arm_deadline - time.perf_counter()) if self.state == ARMING else 0.0

    # ---- worker -------------------------------------------------------------
    def _interval(self) -> float:
        s = self.settings
        base = 1.0 / max(0.1, s.cps)
        if s.jitter:
            base *= 1.0 + random.uniform(-s.jitter, s.jitter) / 100.0
        return max(0.0005, base)

    def _do_action(self) -> int:
        s = self.settings
        b = self.backend
        if s.action.startswith("scroll"):
            amt = s.scroll_step
            if s.action == "scroll-up":
                b.scroll(amt)
            elif s.action == "scroll-down":
                b.scroll(-amt)
            elif s.action == "scroll-right":
                b.scroll(amt, horizontal=True)
            else:
                b.scroll(-amt, horizontal=True)
            return 1
        n = _MULT[s.click_type]
        interval = 1.0 / max(0.1, s.cps)
        # never hold longer than ~half the slot, or we'd fall behind the rate
        hold = min(s.hold_ms / 1000.0, interval / (2 * n))
        gap = min(0.035, interval / (2 * n))
        dx = dy = 0
        if s.wiggle_px and b.supports_move:
            dx = random.randint(-s.wiggle_px, s.wiggle_px)
            dy = random.randint(-s.wiggle_px, s.wiggle_px)
            if dx or dy:
                b.move(dx, dy)
        for i in range(n):
            b.click(s.button, hold)
            if i < n - 1:
                time.sleep(gap)
        if dx or dy:
            b.move(-dx, -dy)
        return n

    def _loop(self) -> None:
        while not self._quit:
            state = self.state
            if state == IDLE:
                self._wake.wait(0.25)
                self._wake.clear()
                continue
            now = time.perf_counter()
            if state == ARMING:
                rem = self.arm_deadline - now
                if rem <= 0:
                    with self.lock:
                        if self.state == ARMING:
                            self._begin_firing()
                else:
                    self._wake.wait(min(rem, 0.05))
                    self._wake.clear()
                continue
            # FIRING
            s = self.settings
            if s.max_seconds and now - self.run_started >= s.max_seconds:
                self.stop(f"time limit {s.max_seconds}s reached")
                continue
            if now < self._next:
                rem = self._next - now
                if rem > 0.004:  # coarse, interruptible wait
                    self._wake.wait(min(rem - 0.002, 0.05))
                    self._wake.clear()
                else:            # fine-grained final approach
                    time.sleep(rem)
                continue
            self.in_burst_pause = False
            try:
                n = self._do_action()
            except Exception as e:
                self.stop(f"backend error: {e}")
                continue
            with self.lock:
                if self.state != FIRING:
                    continue
                self.actions += 1
                self.clicks += n
                self.session_clicks += n
                t = time.perf_counter()
                for _ in range(n):
                    self._stamps.append(t)
            if s.max_clicks and self.actions >= s.max_clicks:
                self.stop(f"click limit {s.max_clicks} reached")
                continue
            interval = self._interval()
            self._burst_count += 1
            if s.burst and self._burst_count >= s.burst:
                self._burst_count = 0
                interval = max(interval, s.burst_pause_ms / 1000.0)
                self.in_burst_pause = True
            self._next += interval
            # drift correction without "catch-up" spam after a stall
            now = time.perf_counter()
            if self._next < now - max(0.15, 3 * interval):
                self._next = now
