from __future__ import annotations

import pytest

from bai.execution.scheduler import Scheduler


def test_toy_scheduler_records_running_pause_requested_paused() -> None:
    scheduler = Scheduler()
    task = scheduler.start_toy_task("toy-1", "background placeholder")

    assert task.status == "running"
    assert task.priority == "background"
    assert task.events == ["running"]

    scheduler.request_pause(task)
    assert task.status == "pause_requested"
    assert task.events == ["running", "pause_requested"]

    scheduler.pause(task)
    assert task.status == "paused"
    assert task.events == ["running", "pause_requested", "paused"]


def test_toy_scheduler_has_no_foreground_preemption_or_blocking_behavior() -> None:
    scheduler = Scheduler()
    task = scheduler.start_toy_task("toy-1", "background placeholder")

    assert task.priority == "background"
    assert scheduler.foreground_priority == "not_implemented_phase_one"


def test_toy_scheduler_pause_requires_pause_requested_state() -> None:
    scheduler = Scheduler()
    task = scheduler.start_toy_task("toy-1", "background placeholder")

    scheduler.pause(task)

    assert task.status == "paused"
    assert task.events == ["running", "pause_requested", "paused"]


def test_toy_scheduler_pause_rejects_non_running_terminal_state() -> None:
    scheduler = Scheduler()
    task = scheduler.start_toy_task("toy-1", "background placeholder")
    scheduler.pause(task)

    with pytest.raises(ValueError, match="only running or pause-requested tasks can be paused"):
        scheduler.pause(task)
