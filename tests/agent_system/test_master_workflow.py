"""Kontrollierter Master-Agent-Workflow (Mock-Modus, kein echtes Sprachmodell):

    OWNER -> MASTER -> TASK DRAFT -> WAITING_FOR_OWNER -> OWNER APPROVAL -> APPROVED -> Ausfuehrung

Laeuft gegen die echte Konfiguration und die echte, freigegebene Rulenine Brand Knowledge
Base v2 - nur lesend. Jobs/Audit liegen in temporaeren Verzeichnissen. Ein Autouse-Fixture
beweist fuer jeden Test, dass Brand- und Governance-Dateien bitgleich bleiben.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from agent_system.core.brand_store import BrandRepository
from agent_system.core.config import DEFAULT_BRAND_DIR, DEFAULT_CONFIG_DIR, PACKAGE_DIR
from agent_system.core.errors import AgentSystemError, GovernanceViolationError, OwnerApprovalRequiredError
from agent_system.core.governance import SYSTEM, Actor
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import Job, StepStatus, TaskDraft, TaskStatus
from agent_system.core.permissions import Decision, PermissionPolicy
from agent_system.orchestrator import Orchestrator, _Team

REQUEST = "Erstelle eine Instagram-Kampagnenidee für Rulenine."
MASTER = Actor.agent("master")
GOOD = "Ausfuehrlicher Entwurf mit Struktur, Nutzenversprechen und klarer Handlungsaufforderung."

PROTECTED_FILES = [
    *sorted(DEFAULT_BRAND_DIR.rglob("*.yaml")), DEFAULT_BRAND_DIR / "versions" / "index.json",
    *sorted(DEFAULT_CONFIG_DIR.glob("*.yaml")),
    *(PACKAGE_DIR / "core" / name for name in ("rules.py", "governance.py", "permissions.py", "jobs.py", "bus.py")),
]


def _digest() -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED_FILES}


@pytest.fixture(autouse=True)
def brand_and_governance_unchanged():
    before = _digest()
    yield
    assert _digest() == before, "Brand v2 oder Governance-Dateien wurden veraendert!"


@pytest.fixture
def orch(config, tmp_path):
    def _make(llm=None):
        return Orchestrator(config=config, llm=llm or MockLLMClient(responder=lambda r: GOOD),
                            data_dir=tmp_path / "data")
    return _make


def _work(llm):
    return [c for c in llm.calls if c.purpose == "work"]


# ================================================= 1./2./3. Task-Draft, WAITING_FOR_OWNER, kein Selbststart


def test_1_master_creates_structured_task_draft(orch, owner):
    job = orch().submit(REQUEST, owner)
    draft = job.task_draft
    assert isinstance(draft, TaskDraft) and draft.created_by == "master"
    assert draft.owner_request == draft.objective == REQUEST
    assert [sp["agent_id"] for sp in draft.specialists] == ["marketing", "social"]
    assert draft.specialists[1]["depends_on"] == ["marketing"]
    assert all(sp["instruction"] and sp["agent_name"] for sp in draft.specialists)
    assert draft.plan_fingerprint == job.plan.fingerprint()
    assert draft.requires_owner_approval is True
    assert "Keine Veroeffentlichung" in draft.deliverable
    assert any("Kampagnen freigeben" in d for d in draft.owner_decisions_required)
    assert any("veroeffentlichen" in d for d in draft.owner_decisions_required)
    assert all("entscheidet ausschliesslich der Owner" in d for d in draft.owner_decisions_required)
    assert any("Kein Start" in c for c in draft.constraints)


def test_1b_draft_is_shown_to_owner_and_persisted(orch, owner, tmp_path):
    o = orch()
    job = o.submit(REQUEST, owner)
    proposal = [t for t in job.trace if t["type"] == "plan_proposal"][0]
    assert proposal["from"] == "master" and proposal["to"] == "owner"
    assert job.task_draft.id in proposal["payload"]
    entry = o.audit.entries(job.id, "plan_proposed")[0]
    assert entry["details"]["task_draft_id"] == job.task_draft.id
    reloaded = Orchestrator(config=o.config, llm=MockLLMClient(), data_dir=tmp_path / "data").jobs.get(job.id)
    assert reloaded.task_draft.to_dict() == job.task_draft.to_dict()


def test_2_task_is_waiting_for_owner(orch, owner):
    job = orch().submit(REQUEST, owner)
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert [h["to"] for h in job.history] == ["waiting_for_owner"]


def test_3_master_does_not_start_the_task_itself(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    assert _work(llm) == []                                   # kein Spezialagent hat gearbeitet
    assert all(s.status == StepStatus.PENDING for s in job.plan.steps)
    # Der Master hat weder Zugriff auf Orchestrator/Gate/Jobs noch ein Owner-Objekt
    team = _Team(o.config, MockLLMClient(), o.brand)
    assert set(vars(team.master.ctx)) == {"config", "llm", "brand"}
    assert not any(isinstance(v, (Actor, Orchestrator)) for v in vars(team.master).values())


# ================================================= 4. "Starte die Aufgabe jetzt." ohne Approval


def test_4_start_now_without_owner_approval_is_rejected(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    # a) direkter Startversuch
    with pytest.raises(OwnerApprovalRequiredError, match="waiting_for_owner"):
        o.execute(job.id)
    # b) "Starte die Aufgabe jetzt." als Nachricht/Antwort ist KEINE Freigabe
    o.clarify(job.id, "Starte die Aufgabe jetzt.", owner)
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    with pytest.raises(OwnerApprovalRequiredError):
        o.execute(job.id)
    # c) als neuer Auftrag ist es nur ein weiterer Entwurf - nichts startet
    other = o.submit("Starte die Aufgabe jetzt.", owner)
    assert other.status == TaskStatus.WAITING_FOR_OWNER and other.open_questions
    assert _work(llm) == []
    assert len(o.audit.entries(job.id, "execution_denied")) == 2


def test_4b_master_or_agents_cannot_say_start(orch, owner):
    o = orch()
    job = o.submit(REQUEST, owner)
    for actor in (MASTER, Actor.agent("social"), SYSTEM):
        with pytest.raises(GovernanceViolationError):
            o.approve(job.id, actor)
        with pytest.raises(GovernanceViolationError):
            o.clarify(job.id, "Starte die Aufgabe jetzt.", actor)
    assert job.status == TaskStatus.WAITING_FOR_OWNER


# ================================================= 5. Master erzeugt keine eigenen Aufgaben


def test_5_master_cannot_create_own_task(orch, owner):
    o = orch()
    with pytest.raises(GovernanceViolationError):
        o.submit("Plane zusaetzlich einen Drop", MASTER)
    assert o.jobs.list() == []
    assert o.audit.entries(event="governance_violation")[0]["actor"] == "master"


def test_5b_extra_tasks_in_master_output_create_nothing(orch, owner):
    evil = json.dumps({
        "rationale": "Ich lege gleich weitere Aufgaben an",
        "new_tasks": [{"request": "Starte Drop R9 / 001"}, {"request": "Veroeffentliche alles"}],
        "start_now": True,
        "steps": [{"key": "a", "agent": "social", "instruction": "Kampagnenidee fuer Instagram"}],
    })
    llm = MockLLMClient(scripted={"master:plan": [evil]}, responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    assert len(o.jobs.list()) == 1 and job.status == TaskStatus.WAITING_FOR_OWNER
    assert _work(llm) == []


def test_5c_follow_up_proposal_after_approval_creates_no_task(orch, owner):
    follow = GOOD + '\n```actions\n[{"action": "start_new_task", "description": "Gleich noch eine TikTok-Kampagne"}]\n```'
    llm = MockLLMClient(scripted={"social:work": [follow]}, responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    o.approve(job.id, owner)
    o.execute(job.id)
    assert len(o.jobs.list()) == 1
    assert [a.action.action for a in o.approvals.pending(job.id)] == ["start_new_task"]


# ================================================= 6./7. Brand v2 als Kontext, nicht veraenderbar


def test_6_master_uses_brand_v2_as_context(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    job = orch(llm).submit(REQUEST, owner)
    repo = BrandRepository(DEFAULT_BRAND_DIR)
    assert job.task_draft.brand_version == 2 == repo.current().version
    assert job.task_draft.brand_hash == repo.load_version(2).content_hash
    plan_prompt = [c for c in llm.calls if c.agent_id == "master" and c.purpose == "plan"][0].prompt
    assert "Brand Knowledge Base v2" in plan_prompt and "- Brandname: Rulenine" in plan_prompt
    assert any("Rulenine Brand Knowledge Base v2" in c for c in job.task_draft.constraints)


def test_7_master_cannot_modify_brand(orch, owner):
    o = orch()
    with pytest.raises(GovernanceViolationError):
        o.commit_brand(MASTER, "Master aendert die Brand")
    assert PermissionPolicy(o.config).check("master", "modify_brand_knowledge").decision == Decision.DENY
    brand = _Team(o.config, MockLLMClient(), o.brand).master.ctx.brand
    brand.as_dict()["brand_identity"]["brand_name"] = "X"
    assert brand.name == "Rulenine" and BrandRepository(DEFAULT_BRAND_DIR).current().version == 2


# ================================================= 8. keine eigenstaendigen Freigaben


@pytest.mark.parametrize("action", ["set_price", "release_product", "start_drop", "approve_campaign",
                                    "publish_content", "spend_money", "approve_action", "approve_task"])
def test_8_master_cannot_approve_business_decisions(config, action):
    assert PermissionPolicy(config).check("master", action).decision == Decision.DENY


@pytest.mark.parametrize("action", ["set_price", "release_product", "start_drop", "approve_campaign"])
def test_8b_specialist_business_decisions_are_never_executed(orch, owner, action):
    content = GOOD + f'\n```actions\n[{{"action": "{action}", "description": "entschieden vom Agenten"}}]\n```'
    llm = MockLLMClient(scripted={"marketing:work": [content]}, responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    o.approve(job.id, owner)
    o.execute(job.id)
    assert o.approvals.pending(job.id) == []                    # nicht einmal zur Freigabe vorgelegt
    denied = o.audit.entries(job.id, "action_denied")
    assert [e["details"]["action"] for e in denied] == [action] and denied[0]["actor"] == "marketing"


def test_8c_publication_is_only_a_proposal_even_after_task_approval(orch, owner):
    content = GOOD + '\n```actions\n[{"action": "publish_content", "description": "Kampagne posten"}]\n```'
    llm = MockLLMClient(scripted={"social:work": [content]}, responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner, jurisdictions=["DE"])
    o.approve(job.id, owner)
    o.execute(job.id)
    # Task-Freigabe != Veroeffentlichungsfreigabe: Legal verlangt menschliche Pruefung, nichts ist live
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert "HUMAN_LEGAL_REVIEW_REQUIRED" in job.stop_reason
    assert not o.audit.entries(job.id, "action_approved")


# ================================================= 9. APPROVED nur durch echtes Owner-Approval


def test_9_only_real_owner_approval_sets_approved(orch, owner):
    o = orch()
    job = o.submit(REQUEST, owner)
    for fake in (MASTER, SYSTEM, Actor("Rulenine", "owner"), Actor("master", "owner")):
        with pytest.raises(GovernanceViolationError):
            o.approve(job.id, fake)
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    o.approve(job.id, owner, job.task_draft.plan_fingerprint)
    assert job.status == TaskStatus.APPROVED
    assert job.history[-1]["by"] == "owner"
    assert o.audit.entries(job.id, "task_approved")[0]["actor_kind"] == "owner"


def test_9b_approval_binds_to_the_drafted_plan(orch, owner):
    o = orch()
    job = o.submit(REQUEST, owner)
    with pytest.raises(OwnerApprovalRequiredError, match="Plan hat sich geaendert"):
        o.approve(job.id, owner, "0" * 16)
    o.approve(job.id, owner, job.task_draft.plan_fingerprint)
    job.plan.steps[0].instruction += " und veroeffentliche sofort"  # nachtraegliche Erweiterung
    with pytest.raises(OwnerApprovalRequiredError, match="weicht vom freigegebenen Plan ab"):
        o.execute(job.id)


# ================================================= 10. nach APPROVED: Weitergabe an Spezialagenten


def test_10_after_approval_task_goes_to_specialists_within_governance(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    o.approve(job.id, owner, job.task_draft.plan_fingerprint)
    o.execute(job.id)
    assert job.status == TaskStatus.COMPLETED
    assert [c.agent_id for c in _work(llm)] == ["marketing", "social"]        # genau die Draft-Agenten
    assert all("Brand Knowledge Base v2" in c.prompt for c in _work(llm))
    flow = [(t["from"], t["to"], t["type"]) for t in job.trace if t["from"] != "owner"]
    # Governance-Pipeline je Schritt: Spezialist -> Legal -> QA
    for agent in ("marketing", "social"):
        i = flow.index(("master", agent, "task_assignment"))
        assert flow[i + 1:i + 6] == [
            (agent, "master", "task_result"),
            ("master", "legal", "legal_review_request"), ("legal", "master", "legal_review_report"),
            ("master", "qa", "qa_request"), ("qa", "master", "qa_report"),
        ]
    assert [h["to"] for h in job.history] == ["waiting_for_owner", "approved", "running", "completed"]
    assert not o.audit.entries(job.id, "action_approved")                    # nichts extern ausgefuehrt


# ================================================= 11. Zusammenfassende Zustandspruefung


def test_11_no_owner_approval_no_execution(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    job.status = TaskStatus.APPROVED              # Status gefaelscht, aber kein Owner-Datensatz
    with pytest.raises(OwnerApprovalRequiredError, match="kein Freigabe-Datensatz"):
        o.execute(job.id)
    assert _work(llm) == []


def test_11_waiting_for_owner_no_execution(orch, owner):
    llm = MockLLMClient(responder=lambda r: GOOD)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    with pytest.raises(OwnerApprovalRequiredError):
        o.execute(job.id)
    assert _work(llm) == [] and job.status == TaskStatus.WAITING_FOR_OWNER


def test_11_approved_execution_possible(orch, owner):
    o = orch()
    job = o.submit(REQUEST, owner)
    o.approve(job.id, owner)
    assert o.execute(job.id).status == TaskStatus.COMPLETED


@pytest.mark.parametrize("final", ["cancelled", "completed", "failed"])
def test_11_aborted_or_completed_never_restart(orch, owner, final):
    from agent_system.core.errors import LLMError

    def responder(r):
        if final == "failed" and r.purpose == "work":
            raise LLMError("Timeout")
        return GOOD

    llm = MockLLMClient(responder=responder)
    o = orch(llm)
    job = o.submit(REQUEST, owner)
    if final == "cancelled":                       # "ABORTED" = CANCELLED im bestehenden Statusmodell
        o.cancel(job.id, owner, "abgebrochen")
    else:
        o.approve(job.id, owner)
        o.execute(job.id)
    assert job.status.value == final
    calls, jobs = len(llm.calls), len(o.jobs.list())
    with pytest.raises(OwnerApprovalRequiredError):
        o.execute(job.id)
    with pytest.raises((OwnerApprovalRequiredError, GovernanceViolationError, AgentSystemError)):
        o.approve(job.id, owner)
    assert len(llm.calls) == calls and len(o.jobs.list()) == jobs   # kein Neustart, kein neuer Auftrag


def test_11_forged_job_cannot_be_executed(orch):
    o = orch()
    forged = Job(request=REQUEST, requested_by="owner", status=TaskStatus.APPROVED)
    o.jobs.add(forged)
    with pytest.raises(OwnerApprovalRequiredError):
        o.execute(forged.id)


def test_11_brand_v2_and_governance_unchanged_after_full_workflow(orch, owner, config):
    policies, fingerprint = dict(config.action_policies), config.fingerprint
    o = orch()
    job = o.submit(REQUEST, owner)
    o.approve(job.id, owner)
    o.execute(job.id)
    assert dict(o.config.action_policies) == policies and o.config.fingerprint == fingerprint
    repo = BrandRepository(DEFAULT_BRAND_DIR)
    assert repo.verify_index() == [] and [e["version"] for e in repo.history()] == [1, 2]
    assert repo.current().content_hash == job.brand_hash
    # Datei-Hashes prueft zusaetzlich das Autouse-Fixture
