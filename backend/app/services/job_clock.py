"""
app/services/job_clock.py
When each episode's current step began, so the editor's progress ring can
estimate how far along the step is (JobResponse.step_elapsed_secs).

Kept in memory only: steps run inside this process, and a restart ends them
(pipeline_common.recover_orphaned_jobs). Every status change goes through the
ORM attribute, set by update_job_status or by a route, so one listener sees
them all. Setting the status it already has, as update_job_status does after a
route started the step, keeps the step's clock.

The step that's ending keeps its start too: the status is set before it's
saved, and a poll in between still reads the old one. Without its time the
editor's ring would fall back to that step's start.
"""
import time
from typing import Dict, Optional

from sqlalchemy import event

from app.models.models import Job

_started: Dict[str, Dict[str, float]] = {}   # job id → {status: monotonic start}, this step and the one before


def _key(status) -> str:
    return getattr(status, "value", str(status))


def mark(job_id: str, status) -> None:
    """The step `status` of this episode starts now."""
    key = _key(status)
    steps = _started.get(job_id, {})
    ending = max(steps, key=steps.__getitem__) if steps else None
    kept = {ending: steps[ending]} if ending is not None and ending != key else {}
    _started[job_id] = {**kept, key: time.monotonic()}


def elapsed(job_id: str, status) -> Optional[float]:
    """Seconds since the step `status` began (this step or the one just ending), or None if unknown."""
    start = _started.get(job_id, {}).get(_key(status))
    return None if start is None else time.monotonic() - start


@event.listens_for(Job.status, "set")
def _status_set(target: Job, value, oldvalue, initiator) -> None:
    # A brand-new Job has no id until it's flushed; its first step is a moment long
    if target.id and _key(value) != _key(oldvalue):
        mark(target.id, value)
