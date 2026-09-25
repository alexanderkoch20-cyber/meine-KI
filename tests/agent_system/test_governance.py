"""Tests der Owner-Regel. Jede Gruppe sichert eine Vorgabe des Owners ab.

 1. OwnerApproval-Gate                 7. keine Aenderung von Berechtigungen/Governance
 2./3. Status, Start in WAITING_FOR_OWNER  8. Revert/Reset/Delete nur mit Freigabe
 4. nur der Owner gibt frei            9. neue Agenten nur mit Freigabe (siehe test_config)
 5. nur APPROVED wird ausgefuehrt     10. externe Aktionen nur mit Freigabe
 6. kein Agent erzeugt APPROVED       11. alles nachvollziehbar im Audit-Log
 +  Unklar -> Rueckfrage, Fehler -> Stopp, kein Auto-Neustart, Vorschlag statt Handlung
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_system.core.bus import MessageBus
from agent_system.core.errors import (
    AgentSystemError,
    GovernanceViolationError,
    LLMError,
    OwnerApprovalRequiredError,
)
from agent_system.core.governance import SYSTEM, Actor, AuditLog
from agent_system.core.jobs import transition
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import AgentMessage, Job, MessageType, PlanStep, StepStatus, TaskStatus

GOOD = "Ausfuehrlicher Entwurf mit Struktur, Nutzenversprechen und klarer Handlungsaufforderung."
AGENT = Actor.agent("master")
IMPOSTOR = Actor("mallory", "owner")  # behauptet Owner zu sein, ist aber nicht der konfigurierte


def _actions_block(*actions):
    return GOOD + "\n```actions\n" + json.dumps(
        [{"action": a, "description": f"Vorschlag {a}"} for a in actions]) + "\n```"


def _work_calls(llm):
    return [c for c in llm.calls if c.purpose == "work"]


# ---------------------------------------------------------------- 1 / 3 / 5


def test_new_task_starts_waiting_for_owner_and_nothing_runs(make_orch, owner):
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert job.history[0]["from"] == "draft"
    assert _work_calls(llm) == []
    assert all(s.status == StepStatus.PENDING for s in job.plan.steps)


def test_execute_without_approval_is_refused_and_logged(make_orch, owner):
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)
    assert _work_calls(llm) == []
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert orch.audit.entries(job.id, "execution_denied")


def test_only_agents_are_never_able_to_submit_tasks(make_orch):
    orch = make_orch()
    for actor in (AGENT, Actor.agent("social"), SYSTEM, IMPOSTOR):
        with pytest.raises(GovernanceViolationError):
            orch.submit("Schreibe einen Instagram-Post", actor)
    assert orch.jobs.list() == []
    assert len(orch.audit.entries(event="governance_violation")) == 4


# ---------------------------------------------------------------- 4 / 6


@pytest.mark.parametrize("actor", [AGENT, Actor.agent("qa"), SYSTEM, IMPOSTOR])
def test_only_owner_can_approve(make_orch, owner, actor):
    orch = make_orch()
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    with pytest.raises(GovernanceViolationError):
        orch.approve(job.id, actor)
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    violation = orch.audit.entries(job.id, "governance_violation")[0]
    assert violation["actor"] == actor.id and violation["details"]["attempted"] == "approve_task"


def test_status_set_directly_to_approved_is_not_enough(make_orch, owner):
    """Ein Agent/Fehler, der den Status manipuliert, umgeht das Gate nicht."""
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    job.status = TaskStatus.APPROVED  # Manipulation ohne Gate
    with pytest.raises(OwnerApprovalRequiredError, match="kein Freigabe-Datensatz"):
        orch.execute(job.id)
    assert _work_calls(llm) == []


def test_tampered_job_file_cannot_start_execution(make_orch, owner, tmp_path):
    data = tmp_path / "d"
    orch = make_orch(data_dir=data)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    path = data / "jobs" / f"{job.id}.json"
    snap = json.loads(path.read_text(encoding="utf-8"))
    snap["status"] = "approved"
    path.write_text(json.dumps(snap), encoding="utf-8")

    fresh = make_orch(data_dir=data)  # neuer Prozess liest die manipulierte Datei
    with pytest.raises(OwnerApprovalRequiredError):
        fresh.execute(job.id)


def test_approval_is_bound_to_the_exact_plan(make_orch, owner):
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    with pytest.raises(OwnerApprovalRequiredError, match="Plan hat sich geaendert"):
        orch.approve(job.id, owner, expected_plan="0000000000000000")
    orch.approve(job.id, owner, job.plan.fingerprint())
    job.plan.steps.append(PlanStep("coding", "Zusaetzlich die Website umbauen"))  # Erweiterung
    with pytest.raises(OwnerApprovalRequiredError, match="weicht vom freigegebenen Plan ab"):
        orch.execute(job.id)
    assert _work_calls(llm) == []


def test_config_change_after_approval_requires_new_approval(make_orch, config, owner):
    orch = make_orch()
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    orch.approve(job.id, owner)
    orch.config = replace(config, fingerprint="geaendert")
    orch.gate.config = orch.config
    with pytest.raises(OwnerApprovalRequiredError, match="Konfiguration"):
        orch.execute(job.id)
    assert job.status == TaskStatus.WAITING_FOR_OWNER  # zurueck zum Owner
    orch.approve(job.id, owner)
    assert orch.execute(job.id).status == TaskStatus.COMPLETED


def test_agent_actor_cannot_produce_approved_state(owner):
    job = Job(request="x")
    transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM)
    with pytest.raises(GovernanceViolationError):
        transition(job, TaskStatus.APPROVED, Actor.agent("master"))
    with pytest.raises(GovernanceViolationError):
        transition(job, TaskStatus.APPROVED, SYSTEM)


def test_agent_proposing_approval_is_blocked(make_orch, run_approved):
    orch = make_orch(llm=MockLLMClient(scripted={"social:work": [_actions_block("approve_task")]}))
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.FAILED
    assert orch.audit.entries(job.id, "governance_violation")[0]["actor"] == "social"


def test_bus_refuses_work_for_tasks_that_are_not_running():
    bus = MessageBus()
    bus.register("social", lambda m: "gearbeitet", requires_running_job=True)
    for status in (TaskStatus.DRAFT, TaskStatus.WAITING_FOR_OWNER, TaskStatus.APPROVED,
                   TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        job = Job(request="x", status=status)
        with pytest.raises(GovernanceViolationError):
            bus.send(job, AgentMessage("master", "social", MessageType.TASK_ASSIGNMENT, job.id))
    assert bus.send(Job(request="x", status=TaskStatus.RUNNING),
                    AgentMessage("master", "social", MessageType.TASK_ASSIGNMENT, "j")) == "gearbeitet"


# ------------------------------------------------ kein Wiederholen / Neustart


@pytest.mark.parametrize("final", ["completed", "failed", "cancelled"])
def test_finished_tasks_can_never_run_again(make_orch, owner, final):
    llm = MockLLMClient(responder=lambda r: (_ for _ in ()).throw(LLMError("x"))
                        if final == "failed" and r.purpose == "work" else GOOD)
    orch = make_orch(llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    if final == "cancelled":
        orch.cancel(job.id, owner)
    else:
        orch.approve(job.id, owner)
        orch.execute(job.id)
    assert job.status.value == final
    calls = len(llm.calls)
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)
    with pytest.raises((OwnerApprovalRequiredError, GovernanceViolationError, AgentSystemError)):
        orch.approve(job.id, owner)
    assert len(llm.calls) == calls


def test_failed_task_is_not_restarted_automatically(make_orch, owner, run_approved):
    def broken(req):
        if req.purpose == "work":
            raise LLMError("Timeout")
        return GOOD

    llm = MockLLMClient(responder=broken)
    orch = make_orch(llm=llm)
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.FAILED and len(_work_calls(llm)) == 1
    assert len(orch.jobs.list()) == 1  # kein neuer Auftrag erzeugt


def test_resubmit_is_an_owner_decision_and_needs_new_approval(make_orch, owner, run_approved):
    llm = MockLLMClient(responder=lambda r: (_ for _ in ()).throw(LLMError("x")) if r.purpose == "work" else GOOD)
    orch = make_orch(llm=llm)
    old = run_approved(orch, "Schreibe einen Instagram-Post")
    with pytest.raises(GovernanceViolationError):
        orch.resubmit(old.id, AGENT)
    new = orch.resubmit(old.id, owner)
    assert new.id != old.id and new.resubmitted_from == old.id
    assert new.status == TaskStatus.WAITING_FOR_OWNER and old.status == TaskStatus.FAILED
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(new.id)


def test_completed_task_cannot_be_resubmitted_or_extended(make_orch, owner, run_approved):
    orch = make_orch()
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    with pytest.raises(AgentSystemError):
        orch.resubmit(job.id, owner)
    with pytest.raises(OwnerApprovalRequiredError):
        orch.clarify(job.id, "mach noch mehr", owner)


def test_retries_and_revisions_are_off_by_default(config):
    assert config.orchestration.max_retries == 0
    assert config.orchestration.max_revisions == 0


# ------------------------------------------------------------ Unklar -> fragen


def test_unclear_instruction_stops_and_asks(make_orch, owner):
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Mach mal was Schoenes", owner)
    assert job.status == TaskStatus.WAITING_FOR_OWNER and job.open_questions and not job.plan.steps
    with pytest.raises(OwnerApprovalRequiredError, match="Rueckfragen"):
        orch.approve(job.id, owner)
    with pytest.raises(GovernanceViolationError):
        orch.clarify(job.id, "Instagram-Post", AGENT)

    orch.clarify(job.id, "Einen Instagram-Post ueber unser Produkt", owner)
    assert not job.open_questions and [s.agent_id for s in job.plan.steps] == ["social"]
    assert _work_calls(llm) == []  # auch nach der Antwort: erst Freigabe
    orch.approve(job.id, owner)
    assert orch.execute(job.id).status == TaskStatus.COMPLETED


# -------------------------------------------------- 7 / 8 / 9 / 10 Vorschlaege


@pytest.mark.parametrize("action", [
    "revert_commit", "reset_files", "reset_commits", "delete_data",       # 8
    "create_agent", "enable_agent", "disable_agent",                      # 9
    "change_config", "change_model",                                       # Owner-Hoheit
    "start_new_task", "extend_task", "retry_task",                         # Vorschlag statt Handlung
])
def test_sensitive_actions_are_only_proposals(make_orch, config, owner, run_approved, action):
    # Owner hat dem Coding-Agenten erlaubt, diese Aktion VORZUSCHLAGEN - ausgefuehrt wird sie nie.
    coding = replace(config.agents["coding"], allowed_actions=config.agents["coding"].allowed_actions | {action})
    cfg = replace(config, agents={**config.agents, "coding": coding})
    orch = make_orch(cfg=cfg, llm=MockLLMClient(scripted={"coding:work": [_actions_block(action)]}))
    job = run_approved(orch, "Behebe den Bug im Skript")
    assert job.status == TaskStatus.COMPLETED
    pending = orch.approvals.pending(job.id)
    assert [a.action.action for a in pending] == [action]
    proposed = orch.audit.entries(job.id, "action_proposed")[0]
    assert proposed["details"]["executed"] is False
    assert len(orch.jobs.list()) == 1  # "start_new_task" hat KEINEN Auftrag erzeugt

    with pytest.raises(GovernanceViolationError):
        orch.decide_action(pending[0].id, True, Actor.agent("coding"))
    orch.decide_action(pending[0].id, True, owner)
    assert len(orch.jobs.list()) == 1  # auch nach Freigabe: Dry-Run, nichts ausgefuehrt


@pytest.mark.parametrize("action", ["spend_money", "publish_content"])
def test_external_actions_need_owner_approval(make_orch, run_approved, action):
    orch = make_orch(llm=MockLLMClient(scripted={"marketing:work": [_actions_block(action)]}))
    job = run_approved(orch, "Plane eine Kampagne")
    assert [a.action.action for a in orch.approvals.pending(job.id)] == [action]


@pytest.mark.parametrize("action", ["modify_permissions", "modify_governance", "approve_action",
                                    "reveal_secret"])
def test_agents_can_never_touch_governance(make_orch, run_approved, action):
    orch = make_orch(llm=MockLLMClient(scripted={"marketing:work": [_actions_block(action)]}))
    job = run_approved(orch, "Plane eine Kampagne")
    assert job.status == TaskStatus.FAILED  # QA blockiert das Ergebnis
    assert orch.approvals.pending(job.id) == []
    assert orch.audit.entries(job.id, "governance_violation")[0]["details"]["action"] == action


def test_agent_cannot_get_hold_of_owner_or_mutate_policies(make_orch, config):
    orch = make_orch()
    from agent_system.orchestrator import _Team
    team = _Team(orch.config, MockLLMClient(), orch.brand)
    ctx = team.master.ctx
    assert not any(isinstance(v, Actor) for v in vars(ctx).values())
    with pytest.raises(TypeError):
        ctx.config.action_policies["spend_money"] = "allow"


# ------------------------------------------------------------------ 11 Audit


def test_full_audit_trail_for_a_task(make_orch, owner, run_approved):
    orch = make_orch(llm=MockLLMClient(scripted={"social:work": [_actions_block("publish_content")]}))
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    apr = orch.approvals.pending(job.id)[0]
    orch.decide_action(apr.id, False, owner)
    events = [(e["event"], e["actor_kind"]) for e in orch.audit.entries(job.id)]
    assert events == [
        ("task_created", "owner"),
        ("plan_proposed", "agent"),
        ("task_approved", "owner"),
        ("execution_started", "system"),
        ("action_proposed", "agent"),
        ("task_completed", "system"),
        ("action_rejected", "owner"),
    ]
    assert orch.audit.verify()


def test_audit_log_is_tamper_evident_and_redacted(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    key = "sk-ant-api03-" + "Q" * 40
    log.record("task_created", Actor("owner", "owner"), "job_1", request=f"nutze {key}")
    log.record("task_approved", Actor("owner", "owner"), "job_1")
    assert key not in path.read_text(encoding="utf-8")
    assert AuditLog(path).verify()

    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["actor"] = "social"  # nachtraegliche Faelschung
    path.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n", encoding="utf-8")
    assert not AuditLog(path).verify()


def test_audit_persists_across_processes(make_orch, owner, tmp_path):
    data = tmp_path / "d"
    job = make_orch(data_dir=data).submit("Schreibe einen Instagram-Post", owner)
    second = make_orch(data_dir=data)  # z.B. naechster CLI-Aufruf
    second.approve(job.id, owner)
    second.execute(job.id)
    third = make_orch(data_dir=data)
    assert [e["event"] for e in third.audit.entries(job.id)][:3] == [
        "task_created", "plan_proposed", "task_approved"]
    assert third.audit.verify()
