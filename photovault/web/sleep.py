"""Sleep when done (ui-17, D-149): put the Mac to sleep once every PhotoVault run has finished.

The user turns it on from the top bar. A server thread checks every `POLL_S` seconds whether anything is running:
tasks this server started (they run headless, ui-16) and any other PhotoVault process (a terminal run, or a task left
by an earlier `serve`), found the same way the Tasks page finds them (`activity.list_processes`). After `QUIET_S`
seconds with nothing running it runs `pmset sleepnow` once and turns itself off. Nothing is written to any drive.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable

from photovault import activity

logger = logging.getLogger(__name__)

POLL_S = 15.0
QUIET_S = 60.0
SLEEP_ARGV = ["/usr/bin/pmset", "sleepnow"]


def _other_runs() -> int:
    """PhotoVault processes running on this Mac, other than UI servers (`serve`)."""
    procs = activity.list_processes()
    if procs is None:
        return 0
    return sum(1 for p in procs if not (p.words and p.words[0] in activity.UNTRACKED))


class SleepWhenDone:
    def __init__(self, own_running: Callable[[], int], *, other_runs: Callable[[], int] = _other_runs,
                 sleep_argv: list[str] | None = None, poll_s: float = POLL_S, quiet_s: float = QUIET_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.own_running = own_running
        self.other_runs = other_runs
        self.sleep_argv = list(sleep_argv or SLEEP_ARGV)
        self.poll_s = poll_s
        self.quiet_s = quiet_s
        self.clock = clock
        self._lock = threading.Lock()
        self._enabled = False
        self._quiet_since: float | None = None
        self._busy = 0
        self._slept_at: float | None = None
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def busy(self) -> int:
        try:
            return int(self.own_running()) + int(self.other_runs())
        except Exception as exc:  # noqa: BLE001  (when unsure, count as busy: never sleep in the middle of a run)
            logger.warning("sleep when done: cannot list runs (%s)", exc)
            return 1

    def set(self, enabled: bool) -> dict:
        with self._lock:
            self._enabled = bool(enabled)
            self._quiet_since = None
            if enabled:
                self._slept_at = None
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._loop, daemon=True, name="sleep-when-done")
                    self._thread.start()
        self._wake.set()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            sleeps_in = None
            if self._enabled and self._quiet_since is not None:
                sleeps_in = max(0, round(self.quiet_s - (self.clock() - self._quiet_since)))
            return {"enabled": self._enabled, "running": self._busy, "sleeps_in_s": sleeps_in,
                    "slept": self._slept_at is not None}

    def tick(self) -> bool:
        """One check; True when it put the Mac to sleep."""
        busy = self.busy()
        with self._lock:
            self._busy = busy
            if not self._enabled:
                return False
            now = self.clock()
            if busy:
                self._quiet_since = None
                return False
            if self._quiet_since is None:
                self._quiet_since = now
            if now - self._quiet_since < self.quiet_s:
                return False
            self._enabled = False
            self._quiet_since = None
            self._slept_at = now
        logger.info("sleep when done: nothing running for %.0f s; sleeping the Mac", self.quiet_s)
        try:
            subprocess.run(self.sleep_argv, check=False, timeout=30,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("sleep when done: %s failed: %s", self.sleep_argv[0], exc)
        return True

    def _loop(self) -> None:
        while True:
            self._wake.wait(self.poll_s)
            self._wake.clear()
            with self._lock:
                if not self._enabled:
                    self._thread = None
                    return
            self.tick()
