"""Sleep when done (ui-17, D-149): the Mac sleeps once nothing has run for the quiet period, then the switch is off."""

from __future__ import annotations

import sys

from photovault.web.sleep import SleepWhenDone


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _make(tmp_path, own: list[int], other: list[int]):
    marker = tmp_path / "slept"
    clock = Clock()
    s = SleepWhenDone(lambda: own[0], other_runs=lambda: other[0], quiet_s=60, poll_s=3600, clock=clock,
                      sleep_argv=[sys.executable, "-c", f"open({str(marker)!r}, 'w').write('z')"])
    return s, clock, marker


def test_never_sleeps_while_anything_runs_and_sleeps_once_after_the_quiet_period(tmp_path) -> None:
    own, other = [1], [0]
    s, clock, marker = _make(tmp_path, own, other)
    assert s.tick() is False  # off: nothing happens
    s.set(True)
    assert s.tick() is False and s.status()["running"] == 1
    own[0], other[0] = 0, 1  # a terminal run (or a task of an earlier serve) still counts
    clock.t = 500
    assert s.tick() is False
    other[0] = 0
    assert s.tick() is False and s.status()["sleeps_in_s"] == 60
    clock.t = 530
    assert s.tick() is False and s.status()["sleeps_in_s"] == 30
    clock.t = 561
    assert s.tick() is True and marker.exists()
    assert s.status() == {"enabled": False, "running": 0, "sleeps_in_s": None, "slept": True}
    clock.t = 9999
    assert s.tick() is False  # one-shot: it turned itself off


def test_a_run_starting_in_the_quiet_period_restarts_the_wait(tmp_path) -> None:
    own, other = [0], [0]
    s, clock, marker = _make(tmp_path, own, other)
    s.set(True)
    s.tick()
    clock.t = 50
    own[0] = 1
    s.tick()
    own[0] = 0
    clock.t = 70
    s.tick()
    clock.t = 120
    assert s.tick() is False and not marker.exists()
    clock.t = 131
    assert s.tick() is True


def test_cannot_list_runs_counts_as_busy(tmp_path) -> None:
    def broken() -> int:
        raise OSError("ps failed")

    s = SleepWhenDone(lambda: 0, other_runs=broken, quiet_s=0, poll_s=3600, sleep_argv=["/bin/false"])
    s.set(True)
    assert s.tick() is False and s.status()["running"] == 1
