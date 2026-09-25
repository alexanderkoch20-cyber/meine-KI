"""End-to-End-Tests: User -> Master -> Spezialisten -> QA -> Master -> User."""

from __future__ import annotations

import json

import pytest

from agent_system.core.errors import AgentSystemError, LLMError
from agent_system.core.llm import AnthropicLLMClient, MockLLMClient
from agent_system.core.models import JobStatus, QAVerdict, StepStatus

GOOD = "Ausfuehrlicher Entwurf mit Struktur, Nutzenversprechen und klarer Handlungsaufforderung."
POST_WITH_PUBLISH = GOOD + '\n```actions\n[{"action": "publish_content", "description": "Post live schalten"}]\n```'


def test_end_to_end_happy_path(make_orch):
    orch = make_orch()
    job = orch.handle("Recherchiere Trends und plane eine Kampagne mit Instagram-Reels")
    assert job.status == JobStatus.COMPLETED
    assert [s.agent_id for s in job.plan.steps] == ["research", "marketing", "social"]
    assert all(s.status == StepStatus.DONE for s in job.plan.steps)
    assert all(s.qa_report.verdict == QAVerdict.APPROVED for s in job.plan.steps)
    for name in ("Research-Agent", "Marketing-Agent", "Social-Agent"):
        assert f"## {name}" in job.final_output
    assert [h["to"] for h in job.history] == ["planning", "running", "qa_review", "completed"]


def test_communication_flows_through_master_and_qa(make_orch):
    job = make_orch().handle("Schreibe einen Instagram-Post")
    flow = [(t["from"], t["to"], t["type"]) for t in job.trace if "type" in t]
    assert flow == [
        ("user", "master", "task_assignment"),
        ("master", "master", "task_result"),       # Plan
        ("master", "social", "task_assignment"),
        ("social", "master", "task_result"),
        ("master", "qa", "qa_request"),
        ("qa", "master", "qa_report"),
        ("master", "user", "final_result"),
    ]


def test_upstream_results_are_passed_to_dependent_steps(make_orch):
    llm = MockLLMClient(responder=lambda r: f"{GOOD} [{r.agent_id}]")
    make_orch(llm=llm).handle("Recherchiere den Markt und schreibe Instagram-Posts")
    social_prompt = next(c.prompt for c in llm.calls if c.agent_id == "social" and c.purpose == "work")
    assert "[research]" in social_prompt


def test_models_per_agent(make_orch):
    llm = MockLLMClient()
    make_orch(llm=llm).handle("Formatiere diese Liste als Tabelle")
    tiers = {(c.agent_id, c.purpose): c.tier.name for c in llm.calls}
    assert tiers[("master", "plan")] == "opus"
    assert tiers[("routine", "work")] == "haiku"
    assert tiers[("qa", "qa")] == "sonnet"


def test_external_action_waits_for_approval_and_is_never_executed(make_orch):
    llm = MockLLMClient(scripted={"social:work": [POST_WITH_PUBLISH]})
    orch = make_orch(llm=llm)
    job = orch.handle("Schreibe einen Instagram-Post")
    assert job.status == JobStatus.AWAITING_APPROVAL
    pending = orch.approvals.pending(job.id)
    assert [a.action.action for a in pending] == ["publish_content"]
    assert "Wartet auf deine Freigabe" in job.final_output

    orch.decide_approval(pending[0].id, approve=True)
    assert job.status == JobStatus.COMPLETED
    assert "Dry-Run" in job.history[-1]["note"]
    assert orch.approvals.get(pending[0].id).status == "approved"


def test_rejected_approval_also_closes_job(make_orch):
    llm = MockLLMClient(scripted={"social:work": [POST_WITH_PUBLISH]})
    orch = make_orch(llm=llm)
    job = orch.handle("Schreibe einen Instagram-Post")
    orch.decide_approval(orch.approvals.pending(job.id)[0].id, approve=False)
    assert job.status == JobStatus.COMPLETED


def test_qa_revision_loop_fixes_brand_violation(make_orch, filled_brand):
    llm = MockLLMClient(scripted={"social:work": [GOOD + " Total billig!", GOOD]})
    job = make_orch(llm=llm, brand=filled_brand).handle("Schreibe einen Instagram-Post")
    step = job.plan.steps[0]
    assert job.status == JobStatus.COMPLETED
    assert step.attempts == 2 and "billig" not in step.result.content
    revision_prompt = [c.prompt for c in llm.calls if c.agent_id == "social"][1]
    assert "QA-Rueckmeldung" in revision_prompt and "billig" in revision_prompt


def test_persistent_brand_violation_is_not_delivered(make_orch, filled_brand):
    llm = MockLLMClient(responder=lambda r: GOOD + " billig" if r.agent_id == "social" else GOOD)
    job = make_orch(llm=llm, brand=filled_brand).handle("Recherchiere den Markt und schreibe Instagram-Posts")
    research, social = job.plan.steps
    assert research.status == StepStatus.DONE
    assert social.status == StepStatus.FAILED and social.result is None
    assert job.status == JobStatus.PARTIALLY_COMPLETED
    assert GOOD + " billig" not in job.final_output
    assert "## Nicht erledigt" in job.final_output


def test_transient_llm_errors_are_retried(make_orch):
    calls = {"n": 0}

    def flaky(req):
        if req.agent_id == "social" and req.purpose == "work":
            calls["n"] += 1
            if calls["n"] < 3:
                raise LLMError("Timeout")
        return GOOD

    job = make_orch(llm=MockLLMClient(responder=flaky)).handle("Schreibe einen Instagram-Post")
    assert job.status == JobStatus.COMPLETED and job.plan.steps[0].attempts == 3


def test_retries_exhausted_fails_step_and_skips_dependents(make_orch):
    def broken(req):
        if req.agent_id == "research" and req.purpose == "work":
            raise LLMError("dauerhaft kaputt")
        return GOOD

    job = make_orch(llm=MockLLMClient(responder=broken)).handle("Recherchiere den Markt und schreibe Instagram-Posts")
    research, social = job.plan.steps
    assert research.status == StepStatus.FAILED and research.attempts == 3
    assert social.status == StepStatus.SKIPPED
    assert job.status == JobStatus.FAILED and "llm_error" in job.error


def test_unexpected_exception_does_not_crash(make_orch):
    def boom(req):
        if req.purpose == "work":
            raise ZeroDivisionError("oops")
        return GOOD

    job = make_orch(llm=MockLLMClient(responder=boom)).handle("Schreibe einen Instagram-Post")
    assert job.status == JobStatus.FAILED
    assert "ZeroDivisionError" in job.plan.steps[0].error


def test_unapproved_paid_api_is_never_called(make_orch):
    job = make_orch(llm=AnthropicLLMClient()).handle("Schreibe einen Instagram-Post")
    assert job.status == JobStatus.FAILED
    assert "external_service_not_approved" in job.error


def test_llm_budget_limits_calls(make_orch, config):
    from dataclasses import replace
    job = make_orch(cfg=replace(config, max_llm_calls_per_job=2)).handle(
        "Recherchiere den Markt und schreibe Instagram-Posts")
    assert job.trace[-1] == {"llm_calls": 2}
    assert job.status in (JobStatus.FAILED, JobStatus.PARTIALLY_COMPLETED)
    assert any("budget_exceeded" in (s.error or "") for s in job.plan.steps)


def test_secrets_never_reach_output_or_disk(make_orch, tmp_path):
    key = "sk-ant-api03-" + "Z" * 40
    llm = MockLLMClient(scripted={"social:work": [f"{GOOD} Nutze {key}"] * 2})
    orch = make_orch(llm=llm, data_dir=tmp_path / "d")
    job = orch.handle("Schreibe einen Instagram-Post")
    assert job.plan.steps[0].qa_report.verdict == QAVerdict.BLOCKED
    assert job.status == JobStatus.FAILED
    stored = (tmp_path / "d" / "jobs" / f"{job.id}.json").read_text(encoding="utf-8")
    assert key not in stored


def test_job_is_persisted(make_orch, tmp_path):
    orch = make_orch(data_dir=tmp_path / "d")
    job = orch.handle("Schreibe einen Instagram-Post")
    snap = json.loads((tmp_path / "d" / "jobs" / f"{job.id}.json").read_text(encoding="utf-8"))
    assert snap["status"] == "completed" and snap["plan"]["steps"][0]["agent_id"] == "social"


def test_empty_request_is_rejected(make_orch):
    with pytest.raises(AgentSystemError):
        make_orch().submit("   ")
