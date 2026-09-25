"""End-to-End: Owner -> Master (Plan) -> Owner-Freigabe -> Spezialisten -> QA -> Master -> Owner."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_system.core.errors import AgentSystemError, LLMError, OwnerApprovalRequiredError
from agent_system.core.llm import AnthropicLLMClient, MockLLMClient
from agent_system.core.models import QAVerdict, StepStatus, TaskStatus

GOOD = "Ausfuehrlicher Entwurf mit Struktur, Nutzenversprechen und klarer Handlungsaufforderung."
POST_WITH_PUBLISH = GOOD + '\n```actions\n[{"action": "publish_content", "description": "Post live schalten"}]\n```'


def _work_calls(llm, agent=None):
    return [c for c in llm.calls if c.purpose == "work" and (agent is None or c.agent_id == agent)]


def test_happy_path_requires_owner_approval_before_any_work(make_orch, owner):
    llm = MockLLMClient()
    orch = make_orch(llm=llm)
    job = orch.submit("Recherchiere Trends und plane eine Kampagne mit Instagram-Reels", owner)

    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert [s.agent_id for s in job.plan.steps] == ["research", "marketing", "social"]
    assert _work_calls(llm) == []  # Master hat nur geplant - kein Agent hat gearbeitet

    orch.approve(job.id, owner, job.plan.fingerprint())
    orch.execute(job.id)
    assert job.status == TaskStatus.COMPLETED
    assert all(s.status == StepStatus.DONE for s in job.plan.steps)
    assert [h["to"] for h in job.history] == ["waiting_for_owner", "approved", "running", "completed"]
    for name in ("Research-Agent", "Marketing-Agent", "Social-Agent"):
        assert f"## {name}" in job.final_output


def test_communication_flows_through_master_and_qa(make_orch, run_approved):
    job = run_approved(make_orch(), "Schreibe einen Instagram-Post")
    flow = [(t["from"], t["to"], t["type"]) for t in job.trace]
    assert flow == [
        ("owner", "master", "task_assignment"),
        ("master", "legal", "legal_review_request"),      # Legal-Vorpruefung des Auftrags
        ("legal", "master", "legal_review_report"),
        ("master", "owner", "plan_proposal"),
        ("master", "social", "task_assignment"),
        ("social", "master", "task_result"),
        ("master", "legal", "legal_review_request"),      # Legal VOR QA
        ("legal", "master", "legal_review_report"),
        ("master", "qa", "qa_request"),
        ("qa", "master", "qa_report"),
        ("master", "owner", "final_result"),
    ]


def test_upstream_results_are_passed_to_dependent_steps(make_orch, run_approved):
    llm = MockLLMClient(responder=lambda r: f"{GOOD} [{r.agent_id}]")
    run_approved(make_orch(llm=llm), "Recherchiere den Markt und schreibe Instagram-Posts")
    assert "[research]" in _work_calls(llm, "social")[0].prompt


def test_models_per_agent(make_orch, run_approved):
    llm = MockLLMClient()
    run_approved(make_orch(llm=llm), "Formatiere diese Liste als Tabelle")
    tiers = {(c.agent_id, c.purpose): c.tier.name for c in llm.calls}
    assert tiers[("master", "plan")] == "opus"
    assert tiers[("routine", "work")] == "haiku"
    assert tiers[("qa", "qa")] == "sonnet"


def test_external_action_is_only_proposed_never_executed(make_orch, owner, run_approved, verified_config):
    orch = make_orch(cfg=verified_config, llm=MockLLMClient(scripted={"social:work": [POST_WITH_PUBLISH]}))
    job = run_approved(orch, "Schreibe einen Instagram-Post", ["DE"])
    assert job.status == TaskStatus.COMPLETED
    pending = orch.approvals.pending(job.id)
    assert [a.action.action for a in pending] == ["publish_content"]
    assert "warten auf deine Freigabe" in job.final_output

    orch.decide_action(pending[0].id, True, owner)
    entry = orch.audit.entries(job.id, "action_approved")[0]
    assert entry["details"]["executed"] is False


def test_qa_rejection_stops_by_default_no_automatic_rework(make_orch, filled_brand, run_approved):
    llm = MockLLMClient(scripted={"social:work": [GOOD + " Total billig!", GOOD]})
    job = run_approved(make_orch(llm=llm, brand=filled_brand), "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.FAILED
    assert len(_work_calls(llm, "social")) == 1  # keine eigenmaechtige Ueberarbeitung
    assert "billig" in job.stop_reason and job.plan.steps[0].result is None


def test_owner_allowed_revision_round_fixes_brand_violation(make_orch, config, filled_brand, run_approved):
    cfg = replace(config, orchestration=replace(config.orchestration, max_revisions=1))
    llm = MockLLMClient(scripted={"social:work": [GOOD + " Total billig!", GOOD]})
    orch = make_orch(llm=llm, brand=filled_brand, cfg=cfg)
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    step = job.plan.steps[0]
    assert job.status == TaskStatus.COMPLETED and step.attempts == 2
    assert "QA-Rueckmeldung" in _work_calls(llm, "social")[1].prompt
    assert orch.audit.entries(job.id, "step_revision")


def test_failure_stops_the_job_and_reports(make_orch, run_approved):
    def broken(req):
        if req.agent_id == "research" and req.purpose == "work":
            raise LLMError("dauerhaft kaputt")
        return GOOD

    llm = MockLLMClient(responder=broken)
    job = run_approved(make_orch(llm=llm), "Recherchiere den Markt, plane eine Kampagne und Instagram-Posts")
    research, marketing, social = job.plan.steps
    assert research.status == StepStatus.FAILED and research.attempts == 1  # kein Auto-Retry
    assert marketing.status == social.status == StepStatus.SKIPPED
    assert "Auftrag gestoppt" in marketing.error
    assert _work_calls(llm, "marketing") == [] and _work_calls(llm, "social") == []
    assert job.status == TaskStatus.FAILED
    assert "GESTOPPT" in job.final_output and "resubmit" in job.final_output


def test_owner_allowed_retries(make_orch, config, run_approved):
    calls = {"n": 0}

    def flaky(req):
        if req.agent_id == "social" and req.purpose == "work":
            calls["n"] += 1
            if calls["n"] < 3:
                raise LLMError("Timeout")
        return GOOD

    cfg = replace(config, orchestration=replace(config.orchestration, max_retries=2))
    orch = make_orch(llm=MockLLMClient(responder=flaky), cfg=cfg)
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.COMPLETED and job.plan.steps[0].attempts == 3
    assert len(orch.audit.entries(job.id, "step_retry")) == 2


def test_unexpected_exception_does_not_crash(make_orch, run_approved):
    def boom(req):
        if req.purpose == "work":
            raise ZeroDivisionError("oops")
        return GOOD

    job = run_approved(make_orch(llm=MockLLMClient(responder=boom)), "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.FAILED
    assert "ZeroDivisionError" in job.plan.steps[0].error


def test_agent_decision_request_pauses_until_owner_decides(make_orch, owner, verified_config):
    decision = GOOD + ('\n```actions\n[{"action": "request_owner_decision", '
                       '"description": "Rabatt 10% oder 20%? Empfehlung: 10%"}]\n```')
    llm = MockLLMClient(scripted={"marketing:work": [decision]}, responder=lambda r: GOOD)
    orch = make_orch(llm=llm, cfg=verified_config)
    job = orch.submit("Plane eine Kampagne und Instagram-Posts", owner, jurisdictions=["DE"])
    orch.approve(job.id, owner)
    orch.execute(job.id)

    marketing, social = job.plan.steps
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert marketing.status == StepStatus.DONE and social.status == StepStatus.PENDING
    assert _work_calls(llm, "social") == []  # gestoppt
    assert "Rabatt" in job.open_questions[0]
    with pytest.raises(OwnerApprovalRequiredError, match="Rueckfragen"):
        orch.approve(job.id, owner)
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)

    orch.clarify(job.id, "10% Rabatt", owner)
    orch.approve(job.id, owner)
    orch.execute(job.id)
    assert job.status == TaskStatus.COMPLETED
    assert len(_work_calls(llm, "marketing")) == 1  # nicht wiederholt
    assert "10% Rabatt" in _work_calls(llm, "social")[0].prompt
    assert job.stop_reason is None and "GESTOPPT" not in job.final_output


def test_step_aborted_mid_work_is_marked_failed(make_orch, owner, monkeypatch):
    from agent_system.core.errors import ExternalServiceNotApprovedError

    def blocked(req):
        if req.purpose == "work":
            raise ExternalServiceNotApprovedError("Replicate nicht freigegeben")
        return GOOD

    orch = make_orch(llm=MockLLMClient(responder=blocked))
    job = orch.submit("Recherchiere den Markt und schreibe Instagram-Posts", owner)
    orch.approve(job.id, owner)
    orch.execute(job.id)
    research, social = job.plan.steps
    assert job.status == TaskStatus.FAILED and "nicht freigegeben" in job.stop_reason
    assert research.status == StepStatus.FAILED and social.status == StepStatus.SKIPPED
    assert "Owner entscheidet" in job.final_output


def test_unapproved_paid_api_is_never_called(make_orch, owner):
    job = make_orch(llm=AnthropicLLMClient()).submit("Schreibe einen Instagram-Post", owner)
    assert job.status == TaskStatus.FAILED
    assert "external_service_not_approved" in job.error


def test_llm_budget_limits_calls(make_orch, config, run_approved):
    job = run_approved(make_orch(cfg=replace(config, max_llm_calls_per_job=2)),
                       "Recherchiere den Markt und schreibe Instagram-Posts")
    assert job.llm_calls == 2
    assert job.status == TaskStatus.FAILED
    assert any("budget_exceeded" in (s.error or "") for s in job.plan.steps)


def test_secrets_never_reach_output_or_disk(make_orch, tmp_path, run_approved):
    key = "sk-ant-api03-" + "Z" * 40
    orch = make_orch(llm=MockLLMClient(scripted={"social:work": [f"{GOOD} Nutze {key}"]}), data_dir=tmp_path / "d")
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    assert job.plan.steps[0].qa_report.verdict == QAVerdict.BLOCKED
    assert job.status == TaskStatus.FAILED
    for path in (tmp_path / "d").rglob("*"):
        if path.is_file():
            assert key not in path.read_text(encoding="utf-8")


def test_job_is_persisted(make_orch, tmp_path, run_approved):
    job = run_approved(make_orch(data_dir=tmp_path / "d"), "Schreibe einen Instagram-Post")
    snap = json.loads((tmp_path / "d" / "jobs" / f"{job.id}.json").read_text(encoding="utf-8"))
    assert snap["status"] == "completed" and snap["plan"]["steps"][0]["agent_id"] == "social"


def test_empty_request_is_rejected(make_orch, owner):
    with pytest.raises(AgentSystemError):
        make_orch().submit("   ", owner)
