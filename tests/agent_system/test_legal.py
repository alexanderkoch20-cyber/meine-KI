"""Tests fuer den Chief Legal & Compliance Agent.

Geforderte Szenarien:
  - Datenschutzrisiko                        - fehlende Lizenz
  - unbekannte Jurisdiktion                  - rechtlich unklare Situation
  - internationale Kampagne                  - Versuch eines Agenten, Legal zu umgehen
  - problematische Werbeaussage              - Versuch, trotz LEGAL_REVIEW_BLOCKED auszufuehren
  - Versuch des Legal-Agenten, selbst eine externe Aktion auszufuehren
Zusaetzlich: Dokumentation auch bei "nicht erforderlich", keine Garantie-Aussagen,
Legal ersetzt nie die Owner-Freigabe.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_system.agents.base import AgentContext
from agent_system.agents.legal import LegalAgent
from agent_system.core.errors import (
    ConfigError,
    GovernanceViolationError,
    LegalReviewRequiredError,
    LLMError,
    OwnerApprovalRequiredError,
)
from agent_system.core.governance import SYSTEM, Actor
from agent_system.core.legal import evaluate, remove_legal_guarantees
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import (
    AgentResponse,
    Job,
    LegalStatus,
    PlanStep,
    ProposedAction,
    StepStatus,
    TaskStatus,
)
from agent_system.core.permissions import Decision, PermissionPolicy

GOOD = "Ausfuehrlicher Entwurf mit Struktur, Nutzenversprechen und klarer Handlungsaufforderung."
LEGAL = Actor.agent("legal")


def _with_actions(text, *actions):
    return text + "\n```actions\n" + json.dumps(
        [{"action": a, "description": f"Vorschlag {a}"} for a in actions]) + "\n```"


def _work(llm, agent=None):
    return [c for c in llm.calls if c.purpose == "work" and (agent is None or c.agent_id == agent)]


def _statuses(review):
    return {(f.topic, f.jurisdiction): f.status for f in review.findings}


# ============================================================ Grundprinzipien


def test_legal_agent_is_configured_as_protected_control_instance(config):
    legal = config.agents["legal"]
    assert legal.role == "legal" and legal.model_tier == "opus"
    assert "legal" not in config.specialists()  # kann nicht als Arbeits-Schritt geplant werden
    assert not legal.allowed_actions & {"publish_content", "send_customer_message", "sign_contract",
                                        "spend_money"}


def test_not_required_is_documented(make_orch, owner, run_approved):
    orch = make_orch()
    job = run_approved(orch, "Formatiere diese Liste als Tabelle")
    assert job.status == TaskStatus.COMPLETED
    assert job.legal_precheck.status == LegalStatus.NOT_REQUIRED
    assert "keine Legal-Pruefung erforderlich" in job.legal_precheck.reasons[0]
    assert job.plan.steps[0].legal_review.status == LegalStatus.NOT_REQUIRED
    events = orch.audit.entries(job.id, "legal_review")
    assert [e["details"]["status"] for e in events] == ["legal_review_not_required"] * 2
    assert "## Legal & Compliance" in job.final_output


def test_every_review_carries_disclaimer_and_never_a_guarantee(make_orch, owner, run_approved, verified_config):
    job = run_approved(make_orch(cfg=verified_config), "Schreibe einen Newsletter an die Kundenliste", ["DE"])
    assert job.status == TaskStatus.COMPLETED
    for review in job.legal_reviews():
        assert "Keine Rechtsberatung" in review.disclaimer
        assert "garantiert nicht, dass eine Handlung legal oder straffrei ist" in review.disclaimer
    text = job.final_output.lower()
    for phrase in ("garantiert legal", "rechtssicher", "100% legal", "ist legal"):
        assert phrase not in text


def test_remove_legal_guarantees():
    text, removed = remove_legal_guarantees("Die Kampagne ist rechtssicher. Bitte Kennzeichnung pruefen.")
    assert text == "Bitte Kennzeichnung pruefen." and removed == ["Die Kampagne ist rechtssicher."]


def test_legal_passed_never_replaces_owner_approval(make_orch, owner, verified_config):
    llm = MockLLMClient(scripted={"social:work": [_with_actions(GOOD, "publish_content")]})
    orch = make_orch(cfg=verified_config, llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post", owner, jurisdictions=["DE"])
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)  # Legal ok, aber keine Owner-Freigabe
    orch.approve(job.id, owner)
    orch.execute(job.id)
    assert job.plan.steps[0].legal_review.status == LegalStatus.PASSED
    apr = orch.approvals.pending(job.id)[0]
    assert apr.status == "pending" and apr.legal_status == "legal_review_passed"
    orch.decide_action(apr.id, True, owner)  # erst der Owner entscheidet - Dry-Run
    assert orch.audit.entries(job.id, "action_approved")[0]["details"]["executed"] is False


def test_findings_document_jurisdiction_rule_source_dates_and_uncertainty(config):
    review = evaluate(config.legal, scope="step", subject_id="s", text="Newsletter an die Kundenliste",
                      jurisdictions=["DE"])
    f = next(f for f in review.findings if f.topic == "personal_data")
    assert f.jurisdiction == "DE"
    assert f.rule_id == "EU-GDPR-6" and "2016/679" in f.reference and f.url.startswith("https://eur-lex")
    assert f.version_date == "2016-04-27" and f.reviewed_at
    assert f.source_verified_at is None  # ausgelieferter Katalog ist unverifiziert ...
    assert "Quelle nicht von einem Menschen verifiziert" in f.uncertainties
    assert f.status == LegalStatus.HUMAN_REQUIRED  # ... daher: STOPP + menschliche Pruefung


# ============================================================ Datenschutz


def test_privacy_risk_with_unverified_sources_requires_human_review(make_orch, owner):
    orch = make_orch()  # ausgelieferter, unverifizierter Katalog
    job = orch.submit("Sende einen Newsletter an unsere Kundenliste mit Tracking-Pixel", owner,
                      jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.HUMAN_REQUIRED
    assert {f.topic for f in job.legal_precheck.findings} >= {"personal_data", "tracking_cookies"}
    with pytest.raises(LegalReviewRequiredError, match="HUMAN_LEGAL_REVIEW_REQUIRED"):
        orch.approve(job.id, owner)
    assert orch.audit.entries(job.id, "legal_block")


def test_privacy_risk_with_verified_sources_passes_but_is_documented(make_orch, owner, run_approved,
                                                                     verified_config):
    job = run_approved(make_orch(cfg=verified_config),
                       "Sende einen Newsletter an unsere Kundenliste mit Tracking-Pixel", ["DE"])
    assert job.legal_precheck.status == LegalStatus.REQUIRED  # Aufgabe als rechtlich relevant markiert
    step_review = job.plan.steps[0].legal_review
    assert step_review.status == LegalStatus.PASSED
    rules = {f.rule_id for f in step_review.findings}
    assert {"EU-GDPR-6", "DE-TDDDG-25"} <= rules | {f.rule_id for f in job.legal_precheck.findings}
    assert "DSGVO" in job.final_output and "Keine Rechtsberatung" in job.final_output


def test_special_category_data_always_needs_human(make_orch, owner, verified_config):
    job = make_orch(cfg=verified_config).submit(
        "Analysiere Gesundheitsdaten unserer Kunden fuer eine Kampagne", owner, jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.HUMAN_REQUIRED
    assert any(f.topic == "special_category_data" and f.risk == "high" for f in job.legal_precheck.findings)


# ============================================================ Jurisdiktion


def test_missing_jurisdiction_stops_and_asks_never_assumes_germany(make_orch, owner, verified_config):
    llm = MockLLMClient()
    orch = make_orch(cfg=verified_config, llm=llm)
    job = orch.submit("Sende einen Newsletter an unsere Kundenliste", owner)
    assert job.jurisdictions == []
    assert job.legal_precheck.status == LegalStatus.REQUIRED
    assert any(q.startswith("[Legal]") and "Rechtsraeume" in q for q in job.open_questions)
    assert all(f.jurisdiction is None for f in job.legal_precheck.findings)
    assert any("auch nicht Deutschland" in u for f in job.legal_precheck.findings for u in f.uncertainties)
    with pytest.raises(OwnerApprovalRequiredError, match="Rueckfragen"):
        orch.approve(job.id, owner)

    orch.set_jurisdictions(job.id, ["DE"], owner)
    assert job.jurisdictions == ["DE"] and not job.open_questions
    orch.approve(job.id, owner)
    assert orch.execute(job.id).status == TaskStatus.COMPLETED


@pytest.mark.parametrize("how", ["explicit", "text"])
def test_unknown_jurisdiction_requires_human_review(make_orch, owner, verified_config, how):
    orch = make_orch(cfg=verified_config)
    if how == "explicit":
        job = orch.submit("Sende einen Newsletter an unsere Kundenliste", owner, jurisdictions=["BR"])
    else:
        job = orch.submit("Sende einen Newsletter an unsere Kundenliste in Brasilien", owner)
    assert "BR" in job.jurisdictions
    assert job.legal_precheck.status == LegalStatus.HUMAN_REQUIRED
    assert any(f.reason_code == "unknown_jurisdiction" and f.jurisdiction == "BR"
               for f in job.legal_precheck.findings)
    with pytest.raises(LegalReviewRequiredError):
        orch.approve(job.id, owner)


def test_invalid_jurisdiction_code_is_rejected(make_orch, owner):
    with pytest.raises(ConfigError):
        make_orch().submit("Newsletter", owner, jurisdictions=["Germany!"])


def test_international_campaign_is_checked_per_jurisdiction(make_orch, owner, verified_config):
    job = make_orch(cfg=verified_config).submit(
        "Plane eine internationale Kampagne mit 20 Prozent Rabatt fuer Deutschland, Oesterreich, "
        "die Schweiz, UK und die USA", owner)
    assert sorted(job.jurisdictions) == ["AT", "CH", "DE", "UK", "US"]
    review = job.legal_precheck
    assert review.status == LegalStatus.HUMAN_REQUIRED
    st = _statuses(review)
    assert st[("price_claims", "DE")] == LegalStatus.PASSED           # verifizierte DE-Quelle
    for code in ("AT", "CH", "UK", "US"):
        assert st[("price_claims", code)] == LegalStatus.HUMAN_REQUIRED  # keine Quelle -> Mensch
        assert st[("international_activity", code)] == LegalStatus.HUMAN_REQUIRED
    # Deutsches Recht wird NICHT auf andere Laender uebertragen:
    for f in review.findings:
        if f.jurisdiction in ("CH", "UK", "US"):
            assert f.rule_id is None or not f.rule_id.startswith("DE-")
    at_privacy = evaluate(verified_config.legal, scope="step", subject_id="s", text="Newsletter",
                          jurisdictions=["AT"])
    assert at_privacy.findings[0].rule_id == "EU-GDPR-6"  # AT erbt EU-Recht, nicht DE-Recht


# ============================================================ Werbung / Lizenzen


def test_problematic_health_claim_blocks_task(make_orch, owner, verified_config):
    llm = MockLLMClient()
    orch = make_orch(cfg=verified_config, llm=llm)
    job = orch.submit("Schreibe einen Instagram-Post: Unser Tee heilt Erkaeltungen, klinisch bewiesen!",
                      owner, jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.BLOCKED
    assert any(f.topic == "health_claim" for f in job.legal_precheck.findings)
    with pytest.raises(LegalReviewRequiredError, match="LEGAL_REVIEW_BLOCKED"):
        orch.approve(job.id, owner)
    assert _work(llm) == []


def test_misleading_claim_requires_human_review(make_orch, owner, verified_config):
    job = make_orch(cfg=verified_config).submit("Bewirb uns als Testsieger und Nr. 1 im Markt", owner,
                                                jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.HUMAN_REQUIRED
    assert any(f.topic == "misleading_claim" and f.rule_id == "DE-UWG-5" for f in job.legal_precheck.findings)


def test_problematic_claim_in_agent_output_blocks_step_and_withholds_content(
        make_orch, owner, run_approved, verified_config):
    bad = GOOD + " Unser Produkt heilt Rueckenschmerzen - ohne Nebenwirkungen!"
    llm = MockLLMClient(scripted={"social:work": [_with_actions(bad, "publish_content")]})
    orch = make_orch(cfg=verified_config, llm=llm)
    job = run_approved(orch, "Schreibe einen Instagram-Post", ["DE"])
    step = job.plan.steps[0]
    assert step.legal_review.status == LegalStatus.BLOCKED
    assert step.status == StepStatus.FAILED and step.result is None
    assert job.status == TaskStatus.FAILED
    assert "heilt Rueckenschmerzen" not in job.final_output
    assert orch.approvals.pending(job.id) == []            # nichts zur Freigabe vorgelegt
    assert not [c for c in llm.calls if c.agent_id == "qa"]  # Legal stoppt VOR der QA
    assert "Kein Agent kann eine Legal-Blockade aufheben" in job.final_output


def test_missing_license_blocks(make_orch, owner, run_approved, verified_config):
    content = GOOD + " Im Reel laeuft ein bekannter Song - ohne Lizenz, faellt schon keinem auf."
    orch = make_orch(cfg=verified_config, llm=MockLLMClient(scripted={"video:work": [content]}))
    job = run_approved(orch, "Erstelle ein Storyboard fuer ein Video", ["DE"])
    review = job.plan.steps[0].legal_review
    assert review.status == LegalStatus.BLOCKED
    assert {"unlicensed_use", "third_party_content"} <= {f.topic for f in review.findings}
    assert job.status == TaskStatus.FAILED


def test_third_party_image_needs_license_check(config):
    review = evaluate(config.legal, scope="step", subject_id="s",
                      text="Wir nehmen einfach ein Bild aus dem Internet", jurisdictions=["DE"])
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert review.findings[0].topic == "third_party_content"


def test_generate_video_action_triggers_license_and_ai_review(config):
    review = evaluate(config.legal, scope="step", subject_id="s", text=GOOD, jurisdictions=["DE"],
                      actions=["generate_video"])
    assert {f.topic for f in review.findings} == {"licenses", "ai_generated"}


# ============================================================ Vertraege


def test_contract_analysis_flags_risk_and_signing_stays_with_owner(make_orch, owner, verified_config):
    marketing = replace(verified_config.agents["marketing"],
                        allowed_actions=verified_config.agents["marketing"].allowed_actions | {"sign_contract"})
    cfg = replace(verified_config, agents={**verified_config.agents, "marketing": marketing})
    content = _with_actions(GOOD + " Klausel 7 (Haftung) ist einseitig.", "sign_contract")
    llm = MockLLMClient(scripted={"marketing:work": [content]})
    orch = make_orch(cfg=cfg, llm=llm)
    job = orch.submit("Plane eine Kampagne mit Werbepartner", owner, jurisdictions=["DE"])
    orch.approve(job.id, owner)
    orch.execute(job.id)
    review = job.plan.steps[0].legal_review
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert any(f.topic == "contracts" for f in review.findings)
    apr = orch.approvals.pending(job.id)[0]
    assert apr.action.action == "sign_contract"
    with pytest.raises(LegalReviewRequiredError):
        orch.decide_action(apr.id, True, owner)  # ohne menschliche Rechtspruefung: nein
    orch.record_human_legal_review(job.id, "RA Muster (fiktiv)", "Klausel 7 nachverhandeln", owner)
    orch.decide_action(apr.id, True, owner)       # Owner entscheidet - und es wird NICHTS unterschrieben
    assert orch.audit.entries(job.id, "action_approved")[0]["details"]["executed"] is False


# ============================================================ Unklare Situation


def test_legally_unclear_situation_pauses_for_human_review(make_orch, owner, verified_config):
    content = GOOD + " Wir erwaehnen den Markennamen anderer Anbieter im Stil von deren Logo."
    llm = MockLLMClient(scripted={"creative:work": [content]}, responder=lambda r: GOOD)
    orch = make_orch(cfg=verified_config, llm=llm)
    job = orch.submit("Entwickle ein Moodboard und dann Instagram-Posts", owner, jurisdictions=["DE"])
    orch.approve(job.id, owner)
    orch.execute(job.id)

    creative, social = job.plan.steps
    assert creative.legal_review.status == LegalStatus.HUMAN_REQUIRED
    assert job.status == TaskStatus.WAITING_FOR_OWNER
    assert "HUMAN_LEGAL_REVIEW_REQUIRED" in job.stop_reason
    assert social.status == StepStatus.PENDING and _work(llm, "social") == []  # gestoppt
    with pytest.raises(LegalReviewRequiredError):
        orch.approve(job.id, owner)                                   # erst menschliche Pruefung
    with pytest.raises(GovernanceViolationError):
        orch.record_human_legal_review(job.id, "Legal-Agent", "passt schon", LEGAL)

    orch.record_human_legal_review(job.id, "RA Muster (fiktiv)", "Markennennung entfernen", owner)
    orch.approve(job.id, owner)
    orch.execute(job.id)
    assert job.status == TaskStatus.COMPLETED
    assert creative.legal_review.human_review["reviewer"] == "RA Muster (fiktiv)"
    assert orch.audit.entries(job.id, "human_legal_review_recorded")


def _legal_agent(cfg, brand, responses):
    llm = MockLLMClient(scripted={"legal:legal": responses})
    return LegalAgent(cfg.agents["legal"], AgentContext(cfg, llm, brand))


def _step_review(agent, content, metadata=None, jurisdictions=("DE",)):
    job = Job(request="x", jurisdictions=list(jurisdictions))
    step = PlanStep("social", "x")
    resp = AgentResponse("social", step.id, content, "m", metadata=metadata or {})
    return agent.review_step(job, step, resp)


def test_model_finding_without_source_requires_human(verified_config, empty_brand):
    agent = _legal_agent(verified_config, empty_brand, [json.dumps({
        "status": "legal_review_passed", "summary": "Moegliches Problem mit Gewinnspielrecht.",
        "findings": [{"area": "werbung", "jurisdiction": "DE", "rule": "Gewinnspiel"}]})])
    review = _step_review(agent, GOOD)
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert any("Quellenlage unvollstaendig" in r for r in review.reasons)


def test_model_can_only_tighten_never_loosen(verified_config, empty_brand):
    agent = _legal_agent(verified_config, empty_brand, ['{"status": "legal_review_passed", "summary": "ok"}'])
    review = _step_review(agent, GOOD + " Bewertungen kaufen fuer mehr Reichweite.")
    assert review.status == LegalStatus.BLOCKED  # Regel sagt BLOCKED - Modell kann das nicht aufheben


def test_model_guarantee_claims_are_removed(verified_config, empty_brand):
    agent = _legal_agent(verified_config, empty_brand, [
        '{"status": "legal_review_passed", "summary": "Das ist garantiert legal. Kennzeichnung ergaenzen."}'])
    review = _step_review(agent, GOOD)
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert not any("garantiert legal" in r for r in review.reasons)
    assert any("Kennzeichnung ergaenzen" in r for r in review.reasons)


def test_model_outage_on_relevant_content_requires_human(verified_config, empty_brand):
    def down(req):
        raise LLMError("Timeout")
    agent = LegalAgent(verified_config.agents["legal"],
                       AgentContext(verified_config, MockLLMClient(responder=down), empty_brand))
    review = _step_review(agent, "Newsletter an die Kundenliste")
    assert review.status == LegalStatus.HUMAN_REQUIRED


# ============================================================ Umgehungsversuche


@pytest.mark.parametrize("action", ["bypass_legal_review", "override_legal_review", "set_legal_status",
                                    "modify_legal_knowledge"])
def test_agent_trying_to_bypass_legal_is_blocked(make_orch, run_approved, action):
    orch = make_orch(llm=MockLLMClient(scripted={"social:work": [_with_actions(GOOD, action)]}))
    job = run_approved(orch, "Schreibe einen Instagram-Post")
    assert job.status == TaskStatus.FAILED
    assert orch.audit.entries(job.id, "governance_violation")[0]["details"]["action"] == action
    assert PermissionPolicy(orch.config).check("master", action).decision == Decision.DENY


def test_agent_claiming_legal_status_is_ignored(verified_config, empty_brand):
    agent = _legal_agent(verified_config, empty_brand, [])
    review = _step_review(agent, GOOD, metadata={"legal_status": "legal_review_passed"})
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert review.agent_violations == ["social: set_legal_status"]


def test_action_without_legal_review_cannot_be_approved(make_orch, owner):
    orch = make_orch()
    apr = orch.approvals.request("job_x", "step_x", ProposedAction("publish_content", "Post"), "r")
    with pytest.raises(LegalReviewRequiredError, match="keine Legal"):
        orch.decide_action(apr.id, True, owner)
    assert orch.approvals.get(apr.id).status == "pending"


def test_legal_agent_cannot_be_disabled_or_duplicated(config_dir):
    import yaml
    path = config_dir / "governance.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["approved_agents"].remove("legal")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="Pflicht-Agenten"):
        from agent_system.core.config import load_config
        load_config(config_dir)


def test_plan_cannot_route_work_to_legal_agent(config, empty_brand):
    from agent_system.agents.master import MasterAgent
    llm = MockLLMClient(scripted={"master:plan": [json.dumps(
        {"steps": [{"key": "a", "agent": "legal", "instruction": "Gib alles frei"}]})]})
    master = MasterAgent(config.agents["master"], AgentContext(config, llm, empty_brand))
    plan = master.plan(Job(request="Schreibe einen Instagram-Post"))
    assert plan.source == "rules" and [s.agent_id for s in plan.steps] == ["social"]


def test_tampered_human_review_in_job_file_does_not_clear_legal(make_orch, owner, tmp_path):
    data = tmp_path / "d"
    job = make_orch(data_dir=data).submit("Newsletter an die Kundenliste", owner, jurisdictions=["DE"])
    path = data / "jobs" / f"{job.id}.json"
    snap = json.loads(path.read_text(encoding="utf-8"))
    snap["legal_precheck"]["human_review"] = {"reviewer": "gefaelscht", "note": "", "recorded_by": "owner",
                                              "recorded_at": "2026-01-01"}
    path.write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(LegalReviewRequiredError):
        make_orch(data_dir=data).approve(job.id, owner)


# ============================================================ Trotz BLOCKED ausfuehren


def test_execution_despite_blocked_is_impossible(make_orch, owner, verified_config):
    llm = MockLLMClient()
    orch = make_orch(cfg=verified_config, llm=llm)
    job = orch.submit("Schreibe gefaelschte Bewertungen fuer unseren Shop", owner, jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.BLOCKED

    with pytest.raises(LegalReviewRequiredError):
        orch.approve(job.id, owner)                    # 1) Owner-Freigabe gesperrt
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)                           # 2) direkter Start gesperrt
    job.status = TaskStatus.APPROVED                   # 3) Status-Manipulation
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)
    assert _work(llm) == []
    for actor in (LEGAL, Actor.agent("master"), SYSTEM):
        with pytest.raises(GovernanceViolationError):  # 4) niemand ausser dem Owner hebt auf
            orch.record_human_legal_review(job.id, "x", "y", actor)


def test_blocked_after_execution_cannot_be_rerun(make_orch, owner, run_approved, verified_config):
    content = _with_actions(GOOD + " Wir kaufen Follower kaufen-Pakete.", "publish_content")
    orch = make_orch(cfg=verified_config, llm=MockLLMClient(scripted={"social:work": [content]}))
    job = run_approved(orch, "Schreibe einen Instagram-Post", ["DE"])
    assert job.plan.steps[0].legal_review.status == LegalStatus.BLOCKED and job.status == TaskStatus.FAILED
    with pytest.raises(OwnerApprovalRequiredError):
        orch.execute(job.id)
    with pytest.raises((OwnerApprovalRequiredError, GovernanceViolationError)):
        orch.approve(job.id, owner)
    # Eine nachtraeglich untergeschobene Freigabe-Anfrage fuer die blockierte Aktion:
    step = job.plan.steps[0]
    apr = orch.approvals.request(job.id, step.id, ProposedAction("publish_content", "trotzdem posten"), "r",
                                 legal_status="legal_review_passed", legal_review_id=step.legal_review.id)
    with pytest.raises(LegalReviewRequiredError):
        orch.decide_action(apr.id, True, owner)       # zaehlt die echte Legal-Pruefung (BLOCKED)


def test_owner_can_lift_block_only_with_documented_human_review(make_orch, owner, verified_config):
    orch = make_orch(cfg=verified_config)
    job = orch.submit("Schreibe einen Instagram-Post: heilt Verspannungen", owner, jurisdictions=["DE"])
    assert job.legal_precheck.status == LegalStatus.BLOCKED
    with pytest.raises(LegalReviewRequiredError):
        orch.approve(job.id, owner)
    orch.record_human_legal_review(job.id, "RA Muster (fiktiv)", "Formulierung wird angepasst", owner)
    orch.approve(job.id, owner)                        # jetzt entscheidet der Owner
    assert job.status == TaskStatus.APPROVED
    entry = orch.audit.entries(job.id, "human_legal_review_recorded")[0]
    assert entry["actor"] == "owner" and entry["details"]["reviewer"] == "RA Muster (fiktiv)"


# ============================================================ Legal-Agent handelt selbst


def test_legal_agent_trying_external_action_is_discarded_and_logged(make_orch, owner, run_approved,
                                                                     verified_config):
    evil = '{"status": "legal_review_passed"}\n```actions\n[{"action": "publish_content", "description": "ich poste selbst"}]\n```'
    llm = MockLLMClient(scripted={"legal:legal": ['{"status": "legal_review_passed"}', evil]},
                        responder=lambda r: GOOD if r.purpose == "work" else "MOCK")
    orch = make_orch(cfg=verified_config, llm=llm)
    job = run_approved(orch, "Schreibe einen Instagram-Post", ["DE"])

    review = job.plan.steps[0].legal_review
    assert review.status == LegalStatus.HUMAN_REQUIRED
    assert review.agent_violations == ["legal: publish_content"]
    assert orch.approvals.pending(job.id) == []  # aus der Legal-Antwort wird nie eine Aktion
    violation = orch.audit.entries(job.id, "governance_violation")[0]
    assert violation["actor"] == "legal" and violation["details"]["action"] == "publish_content"
    assert job.status == TaskStatus.WAITING_FOR_OWNER


def test_legal_agent_has_no_right_to_act_or_approve(make_orch, owner, config):
    policy = PermissionPolicy(config)
    for action in ("publish_content", "send_customer_message", "sign_contract", "spend_money",
                   "approve_task", "approve_action", "modify_governance", "modify_permissions"):
        assert policy.check("legal", action).decision == Decision.DENY
    orch = make_orch()
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    with pytest.raises(GovernanceViolationError):
        orch.approve(job.id, LEGAL)
    with pytest.raises(GovernanceViolationError):
        orch.submit("Neuer Auftrag vom Legal-Agenten", LEGAL)


def test_config_refuses_external_actions_for_legal_agent(config_dir):
    import yaml
    from agent_system.core.config import load_config
    path = config_dir / "agents.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["agents"]["legal"]["allowed_actions"].append("publish_content")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="Kontrollinstanz"):
        load_config(config_dir)
