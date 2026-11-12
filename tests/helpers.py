"""Shared test helpers."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from clickity import config
from clickity.backends import Backend


class IsolatedConfig(unittest.TestCase):
    """Points the config store at a throwaway directory for each test."""

    def setUp(self) -> None:
        super().setUp()
        self.cfg_dir = tempfile.mkdtemp(prefix="clickity-test-")
        self.addCleanup(shutil.rmtree, self.cfg_dir, True)
        self.cfg_file = os.path.join(self.cfg_dir, "config.json")
        for name, value in (("CONFIG_DIR", self.cfg_dir), ("CONFIG_FILE", self.cfg_file)):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)


class RecordingBackend(Backend):
    """Records every call in order, with timestamps."""
    name = "recording"
    supports_move = True

    def __init__(self, fail_after: int | None = None):
        self.calls: list[tuple] = []
        self.lock = threading.Lock()
        self.fail_after = fail_after
        self.closed = False

    def _rec(self, *call) -> None:
        with self.lock:
            self.calls.append(call + (time.perf_counter(),))

    def press(self, button: str) -> None:
        if self.fail_after is not None and self.count("press") >= self.fail_after:
            raise OSError("device gone")
        self._rec("press", button)

    def release(self, button: str) -> None:
        self._rec("release", button)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        self._rec("scroll", amount, horizontal)

    def move(self, dx: int, dy: int) -> None:
        self._rec("move", dx, dy)

    def release_all(self) -> None:
        self._rec("release_all")

    def close(self) -> None:
        self.closed = True

    def count(self, kind: str) -> int:
        with self.lock:
            return sum(1 for c in self.calls if c[0] == kind)

    def of(self, kind: str) -> list[tuple]:
        with self.lock:
            return [c for c in self.calls if c[0] == kind]


def wait_for(pred, timeout: float = 5.0, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()
