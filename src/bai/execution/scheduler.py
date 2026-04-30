from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CooperativeTask:
    task_id: str
    description: str
    status: str = "running"
    priority: str = "background"
    events: list[str] | None = None


class Scheduler:
    foreground_priority = "not_implemented_phase_one"

    def start_toy_task(self, task_id: str, description: str) -> CooperativeTask:
        return CooperativeTask(
            task_id=task_id,
            description=description,
            status="running",
            priority="background",
            events=["running"],
        )

    def request_pause(self, task: CooperativeTask) -> CooperativeTask:
        if task.status != "running":
            raise ValueError("only running tasks can request pause")
        task.status = "pause_requested"
        task.events = [*(task.events or []), "pause_requested"]
        return task

    def pause(self, task: CooperativeTask) -> CooperativeTask:
        if task.status == "running":
            self.request_pause(task)
        if task.status != "pause_requested":
            raise ValueError("only running or pause-requested tasks can be paused")
        task.status = "paused"
        task.events = [*(task.events or []), "paused"]
        return task
