from __future__ import annotations

import pytest

from agent_system.core.errors import GovernanceViolationError, InvalidStateTransitionError, JobNotFoundError
from agent_system.core.governance import SYSTEM, Actor
from agent_system.core.jobs import JobStore, transition
from agent_system.core.models import Job, Plan, PlanStep, TaskStatus

OWNER = Actor("owner", "owner")


def test_exactly_the_seven_owner_statuses_exist():
    assert [s.value for s in TaskStatus] == [
        "draft", "waiting_for_owner", "approved", "running", "completed", "failed", "cancelled"]


def test_valid_lifecycle_is_recorded_with_actor():
    job = Job(request="x")
    assert job.status == TaskStatus.DRAFT
    transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM)
    transition(job, TaskStatus.APPROVED, OWNER)
    transition(job, TaskStatus.RUNNING, SYSTEM)
    transition(job, TaskStatus.COMPLETED, SYSTEM)
    assert [(h["to"], h["by"]) for h in job.history] == [
        ("waiting_for_owner", "orchestrator"), ("approved", "owner"),
        ("running", "orchestrator"), ("completed", "orchestrator")]
    assert job.approval_round == 1


def test_invalid_transition_raises():
    job = Job(request="x")
    with pytest.raises(InvalidStateTransitionError):
        transition(job, TaskStatus.RUNNING, SYSTEM)  # DRAFT -> RUNNING gibt es nicht
    transition(job, TaskStatus.FAILED, SYSTEM)
    with pytest.raises(InvalidStateTransitionError):
        transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM)  # Endzustand: kein Neustart


@pytest.mark.parametrize("actor", [SYSTEM, Actor.agent("master"), Actor.agent("social")])
def test_only_owner_can_approve_or_cancel(actor):
    job = Job(request="x")
    transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM)
    for target in (TaskStatus.APPROVED, TaskStatus.CANCELLED):
        with pytest.raises(GovernanceViolationError):
            transition(job, target, actor)
    assert job.status == TaskStatus.WAITING_FOR_OWNER


def test_agents_cannot_change_task_status_at_all():
    job = Job(request="x")
    with pytest.raises(GovernanceViolationError):
        transition(job, TaskStatus.WAITING_FOR_OWNER, Actor.agent("master"))


def test_job_roundtrip_from_disk(tmp_path):
    store = JobStore(tmp_path)
    job = Job(request="Hallo")
    job.plan = Plan(steps=[PlanStep("social", "Post")])
    store.add(job)
    loaded = JobStore(tmp_path).get(job.id)
    assert loaded.to_dict() == job.to_dict()
    assert loaded.plan.fingerprint() == job.plan.fingerprint()


def test_store_persists_snapshots(tmp_path):
    store = JobStore(tmp_path)
    job = store.add(Job(request="Hallo"))
    assert (tmp_path / f"{job.id}.json").exists()
    fresh = JobStore(tmp_path)  # z.B. neuer CLI-Prozess
    assert fresh.get(job.id).request == "Hallo"
    assert [j.id for j in fresh.list()] == [job.id]
    with pytest.raises(JobNotFoundError):
        fresh.get("job_missing")
