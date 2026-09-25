from __future__ import annotations

import pytest

from agent_system.core.errors import InvalidStateTransitionError, JobNotFoundError
from agent_system.core.jobs import JobStore, transition
from agent_system.core.models import Job, JobStatus


def test_valid_lifecycle_is_recorded():
    job = Job(request="x")
    for s in (JobStatus.PLANNING, JobStatus.RUNNING, JobStatus.QA_REVIEW,
              JobStatus.AWAITING_APPROVAL, JobStatus.COMPLETED):
        transition(job, s)
    assert [h["to"] for h in job.history][-1] == "completed"
    assert len(job.history) == 5


def test_invalid_transition_raises():
    job = Job(request="x")
    with pytest.raises(InvalidStateTransitionError):
        transition(job, JobStatus.COMPLETED)
    transition(job, JobStatus.FAILED)
    with pytest.raises(InvalidStateTransitionError):
        transition(job, JobStatus.RUNNING)  # Endzustand


def test_store_persists_snapshots(tmp_path):
    store = JobStore(tmp_path)
    job = store.add(Job(request="Hallo"))
    assert (tmp_path / f"{job.id}.json").exists()
    fresh = JobStore(tmp_path)  # z.B. neuer CLI-Prozess
    assert fresh.load_snapshot(job.id)["request"] == "Hallo"
    assert [s["id"] for s in fresh.list_snapshots()] == [job.id]
    with pytest.raises(JobNotFoundError):
        fresh.get(job.id)
    with pytest.raises(JobNotFoundError):
        fresh.load_snapshot("job_missing")
