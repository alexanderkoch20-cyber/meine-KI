"""Orchestrator: Laufzeit-Engine hinter dem Master-Agenten - mit Owner-Regel.

Drei strikt getrennte Phasen:

1. **Planen** (``submit``) - nur auf Anweisung des Owners.
       owner ──task_assignment──▶ master ──plan_proposal──▶ owner
   Ergebnis: Auftrag im Status WAITING_FOR_OWNER. Es wurde nichts ausgefuehrt.
   Unklarer Auftrag -> Rueckfragen, Freigabe erst nach ``clarify``.

2. **Freigeben** (``approve``) - ausschliesslich der Owner, ueber das
   OwnerApprovalGate. Die Freigabe gilt genau fuer diesen Plan.

3. **Ausfuehren** (``execute``) - nur nach gueltiger Owner-Freigabe.
       master ──task_assignment──▶ spezialist ──task_result──▶ master
       master ──legal_review_request──▶ legal ──legal_review_report──▶ master
       master ──qa_request──▶ qa ──qa_report──▶ master
       master ──approval_request──▶ owner   (Vorschlaege, nie ausgefuehrt)
       master ──final_result──▶ owner

Legal & Compliance (Pflicht, nicht umgehbar):
- Vorpruefung jedes Auftrags beim Planen (vor der Owner-Freigabe).
- Pruefung JEDES Agenten-Ergebnisses und jeder externen Aktion vor der QA.
  "Keine Pruefung noetig" wird ebenfalls dokumentiert.
- LEGAL_REVIEW_BLOCKED -> Schritt scheitert, Inhalt wird nicht ausgeliefert, Stopp.
- HUMAN_LEGAL_REVIEW_REQUIRED -> Pause; weiter erst nach dokumentierter
  menschlicher Rechtspruefung (Owner) UND erneuter Owner-Freigabe.
- Legal PASSED ersetzt nie die Owner-Freigabe.

STOPPEN statt selbststaendig handeln:
- Schritt schlaegt fehl / QA lehnt ab -> Auftrag stoppt (FAILED), keine
  weiteren Schritte, kein automatischer Neustart. Bericht + Empfehlung an den
  Owner; ein neuer Versuch ist ein neuer Auftrag (``resubmit``), der wieder
  eine Freigabe braucht.
- Agent meldet ``request_owner_decision`` -> Auftrag pausiert
  (WAITING_FOR_OWNER) und braucht eine neue Owner-Freigabe zum Weitermachen.
- Automatische Wiederholungen/Ueberarbeitungen gibt es nur, wenn der Owner
  sie in agents.yaml ausdruecklich erlaubt (Standard: 0).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .agents.base import AgentContext
from .agents.legal import LegalAgent
from .agents.master import MasterAgent, topological_order
from .agents.qa import QAAgent
from .agents.specialist import SPECIALIST_CLASSES, SpecialistAgent
from .core.brand import BrandKnowledge
from .core.brand_store import BrandContextLoader, BrandRepository
from .core.bus import MessageBus
from .core.config import SystemConfig, load_config
from .core.errors import (
    AgentSystemError,
    ExternalServiceNotApprovedError,
    GovernanceViolationError,
    OwnerApprovalRequiredError,
)
from .core.governance import SYSTEM, Actor, AuditLog, OwnerApprovalGate
from .core.jobs import JobStore, transition
from .core.legal import normalize_jurisdictions
from .core.llm import BudgetedLLMClient, LLMClient, create_llm_client
from .core.logging_setup import get_logger
from .core.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ApprovalRequest,
    Job,
    LegalReview,
    LegalStatus,
    MessageType,
    PlanStep,
    QAReport,
    QAVerdict,
    StepStatus,
    TaskStatus,
)
from .core.permissions import ApprovalStore
from .core.rules import AGENT_FORBIDDEN_ACTIONS

log = get_logger("orchestrator")


class _Team:
    """Pro Phase neu erzeugte Agenten-Instanzen (gemeinsames LLM-Budget je Job)."""

    def __init__(self, config: SystemConfig, llm: LLMClient, brand: BrandKnowledge):
        # EINE Brand-Momentaufnahme fuer alle Agenten dieses Teams.
        self.brand = brand
        ctx = AgentContext(config=config, llm=llm, brand=brand)
        self.bus = MessageBus()
        self.master: MasterAgent | None = None
        self.qa: QAAgent | None = None
        self.legal: LegalAgent | None = None
        self.specialists: dict[str, SpecialistAgent] = {}
        for agent_id, definition in config.agents.items():
            if definition.role == "master":
                self.master = MasterAgent(definition, ctx)
            elif definition.role == "qa":
                self.qa = QAAgent(definition, ctx)
            elif definition.role == "legal":
                self.legal = LegalAgent(definition, ctx)
            else:
                cls = SPECIALIST_CLASSES.get(agent_id, SpecialistAgent)
                self.specialists[agent_id] = cls(definition, ctx)

        master, qa, legal = self.master, self.qa, self.legal
        self.bus.register(master.id, lambda m: master.plan(m.payload["job"]))
        self.bus.register(qa.id, lambda m: qa.review(m.payload["request"], m.payload["response"]))

        def legal_handler(m: AgentMessage) -> LegalReview:
            if m.payload["scope"] == "task":
                return legal.review_task(m.payload["job"])
            return legal.review_step(m.payload["job"], m.payload["step"], m.payload["response"])

        self.bus.register(legal.id, legal_handler)
        for agent_id, agent in self.specialists.items():
            self.bus.register(agent_id, lambda m, a=agent: a.handle(AgentRequest(**m.payload["request"])),
                              requires_running_job=True)


class Orchestrator:
    def __init__(
        self,
        config: SystemConfig | None = None,
        llm: LLMClient | None = None,
        brand: BrandKnowledge | None = None,
        job_store: JobStore | None = None,
        approval_store: ApprovalStore | None = None,
        audit: AuditLog | None = None,
        data_dir: Path | str | None = None,
    ):
        self.config = config or load_config()
        self.llm = llm or create_llm_client(self.config)
        # Brand-Context-Loader: alle Agenten nutzen die neueste FREIGEGEBENE Version
        # der Brand Knowledge Base (oder eine fest vorgegebene, z.B. in Tests).
        self.brand_loader = (BrandContextLoader(fixed=brand) if brand is not None
                             else BrandContextLoader(BrandRepository(self.config.brand_dir)))
        data_dir = Path(data_dir) if data_dir else None
        self.jobs = job_store or JobStore(data_dir / "jobs" if data_dir else None)
        self.approvals = approval_store or ApprovalStore(data_dir / "approvals.json" if data_dir else None)
        self.audit = audit or AuditLog(data_dir / "audit.jsonl" if data_dir else None)
        self.gate = OwnerApprovalGate(self.config, self.audit,
                                      data_dir / "task_approvals.json" if data_dir else None)
        for agent_id in self.config.inactive_agents:
            log.warning("Agent '%s' ist definiert, aber nicht vom Owner freigegeben -> inaktiv", agent_id)

    # =================================================== Owner-Schnittstelle

    def submit(self, request: str, actor: Actor, jurisdictions: list[str] | None = None) -> Job:
        """Owner erteilt einen Auftrag. Der Master PLANT nur; nichts wird ausgefuehrt.

        ``jurisdictions``: Rechtsraeume (z.B. ["DE", "AT"]). Zusaetzlich werden im
        Text genannte Laender erkannt. Es wird nie ein Rechtsraum angenommen."""
        self.gate.require_owner(actor, "submit_task")
        request = (request or "").strip()
        if not request:
            raise AgentSystemError("Leerer Auftrag")
        job = Job(request=request, requested_by=actor.id, jurisdictions=normalize_jurisdictions(jurisdictions or []))
        self.jobs.add(job)
        self.audit.record("task_created", actor, job.id, request=request, jurisdictions=job.jurisdictions)
        self._plan(job)
        return job

    @property
    def brand(self) -> BrandKnowledge:
        """Aktuelle freigegebene Brand-Basis (bei jedem Zugriff frisch geladen)."""
        return self.brand_loader.load()

    def commit_brand(self, actor: Actor, note: str):
        """Owner gibt die Arbeitskopie der Brand Knowledge Base als neue Version frei."""
        self.gate.require_owner(actor, "commit_brand_knowledge")
        repo = self.brand_loader.repository or BrandRepository(self.config.brand_dir)
        info = repo.commit(actor, self.config.governance.owner_id, note)
        self.audit.record("brand_version_committed", actor, None, version=info.version,
                          content_hash=info.content_hash, note=note)
        return info

    def approve(self, job_id: str, actor: Actor, expected_plan: str | None = None) -> Job:
        job = self.jobs.get(job_id)
        self.gate.approve_task(job, actor, expected_plan, brand_hash=self.brand.content_hash)
        self.jobs.save(job)
        return job

    def cancel(self, job_id: str, actor: Actor, reason: str = "") -> Job:
        job = self.jobs.get(job_id)
        self.gate.cancel_task(job, actor, reason)
        self.jobs.save(job)
        return job

    def clarify(self, job_id: str, answer: str, actor: Actor) -> Job:
        """Owner beantwortet Rueckfragen / trifft eine Entscheidung.

        - Noch kein Schritt gelaufen: Die Antwort praezisiert den Auftrag und
          der Master plant neu (neuer Plan -> neue Freigabe noetig).
        - Auftrag war pausiert: Die Antwort geht als Owner-Notiz an die
          verbleibenden Schritte. Weiter geht es erst nach ``approve``.
        """
        self.gate.require_owner(actor, "clarify_task")
        job = self.jobs.get(job_id)
        answer = (answer or "").strip()
        if job.status != TaskStatus.WAITING_FOR_OWNER:
            raise OwnerApprovalRequiredError(f"Auftrag {job.id} wartet nicht auf den Owner ({job.status.value})")
        if not answer:
            raise AgentSystemError("Leere Antwort")
        job.owner_notes.append(answer)
        self.audit.record("owner_clarified", actor, job.id, answer=answer, questions=list(job.open_questions))
        started = job.plan and any(s.status != StepStatus.PENDING for s in job.plan.steps)
        job.open_questions = []
        if not started:
            job.request = f"{job.request}\n\nPraezisierung des Owners: {answer}"
            self._plan(job, replan=True)
        self.jobs.save(job)
        return job

    def set_jurisdictions(self, job_id: str, jurisdictions: list[str], actor: Actor) -> Job:
        """Owner legt die Rechtsraeume fest -> neue Planung inkl. Legal-Vorpruefung."""
        self.gate.require_owner(actor, "set_jurisdictions")
        job = self.jobs.get(job_id)
        if job.status != TaskStatus.WAITING_FOR_OWNER or (
                job.plan and any(s.status != StepStatus.PENDING for s in job.plan.steps)):
            raise OwnerApprovalRequiredError("Rechtsraeume koennen nur vor Beginn der Ausfuehrung gesetzt werden")
        job.jurisdictions = normalize_jurisdictions(jurisdictions)
        self.audit.record("jurisdictions_set", actor, job.id, jurisdictions=job.jurisdictions)
        self._plan(job, replan=True)
        return job

    def record_human_legal_review(self, job_id: str, reviewer: str, note: str, actor: Actor) -> Job:
        """Owner dokumentiert eine menschliche Rechtspruefung der offenen Legal-Punkte.
        Danach ist weiterhin die Owner-Freigabe noetig (approve / approve-action)."""
        job = self.jobs.get(job_id)
        self.gate.record_human_legal_review(job, reviewer, note, actor)
        self.jobs.save(job)
        return job

    def resubmit(self, job_id: str, actor: Actor) -> Job:
        """Owner entscheidet, einen gestoppten/abgebrochenen Auftrag neu vorzulegen.
        Ergebnis ist ein NEUER Auftrag, der wieder eine Freigabe braucht."""
        self.gate.require_owner(actor, "resubmit_task")
        old = self.jobs.get(job_id)
        if old.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise AgentSystemError(f"Nur gescheiterte/abgebrochene Auftraege koennen neu vorgelegt werden "
                                   f"({old.id} ist '{old.status.value}')")
        job = self.submit(old.request, actor, jurisdictions=old.jurisdictions)
        job.resubmitted_from = old.id
        self.audit.record("task_resubmitted", actor, job.id, from_job=old.id)
        self.jobs.save(job)
        return job

    def decide_action(self, approval_id: str, approve: bool, actor: Actor) -> ApprovalRequest:
        """Owner entscheidet ueber einen Aktionsvorschlag. Es wird NICHTS ausgefuehrt (Dry-Run).
        Freigeben setzt eine unbedenkliche bzw. menschlich gepruefte Legal-Pruefung voraus."""
        self.gate.require_owner(actor, "approve_action" if approve else "reject_action")
        apr = self.approvals.get(approval_id)
        review = None
        try:
            job = self.jobs.get(apr.job_id)
            step = next((s for s in job.plan.steps if s.id == apr.step_id), None) if job.plan else None
            review = step.legal_review if step else None
        except AgentSystemError:
            review = None
        return self.gate.decide_action(self.approvals, approval_id, approve, actor, legal_review=review)

    # ============================================================ Ausfuehrung

    def execute(self, job_id: str) -> Job:
        """Fuehrt einen vom Owner freigegebenen Auftrag aus. Ohne gueltige
        Freigabe: ``OwnerApprovalRequiredError`` - es passiert nichts."""
        job = self.jobs.get(job_id)
        try:
            brand = self.brand
            self.gate.assert_may_execute(job, brand_hash=brand.content_hash)  # wirft ohne gueltige Freigabe
        finally:
            self.jobs.save(job)  # z.B. Rueckfall auf WAITING_FOR_OWNER nach Konfig-Aenderung
        transition(job, TaskStatus.RUNNING, SYSTEM, "Ausfuehrung nach Owner-Freigabe")
        job.stop_reason = None  # Meldungen einer frueheren Pause sind mit der neuen Freigabe erledigt
        job.recommendations = []
        job.brand_version, job.brand_hash = brand.version, brand.content_hash
        self.audit.record("execution_started", SYSTEM, job.id, round=job.approval_round,
                          plan_fingerprint=job.plan.fingerprint(), brand_version=brand.version,
                          brand_hash=brand.content_hash)
        llm = BudgetedLLMClient(self.llm, self.config.max_llm_calls_per_job, already_used=job.llm_calls)
        team = _Team(self.config, llm, brand)
        try:
            self._run(job, team)
        except ExternalServiceNotApprovedError as exc:
            self._stop_failed(job, team, f"Externer Dienst nicht freigegeben: {exc}",
                              "Owner entscheidet, ob der Dienst freigegeben wird.")
        except AgentSystemError as exc:
            self._stop_failed(job, team, f"{exc.code}: {exc}", None)
        except Exception as exc:  # letzte Verteidigungslinie
            log.exception("Unerwarteter Fehler", extra={"job_id": job.id})
            self._stop_failed(job, team, f"internal_error: {type(exc).__name__}: {exc}", None)
        finally:
            job.llm_calls = llm.calls
            self.jobs.save(job)
        return job

    # ============================================================== Internals

    def _plan(self, job: Job, replan: bool = False) -> None:
        llm = BudgetedLLMClient(self.llm, self.config.max_llm_calls_per_job, already_used=job.llm_calls)
        brand = self.brand
        job.brand_version, job.brand_hash = brand.version, brand.content_hash
        team = _Team(self.config, llm, brand)
        try:
            plan = team.bus.send(job, AgentMessage(
                sender=job.requested_by, recipient=team.master.id, type=MessageType.TASK_ASSIGNMENT,
                job_id=job.id, payload={"job": job, "request": job.request},
            ))
        except AgentSystemError as exc:
            job.llm_calls = llm.calls
            job.error = f"{exc.code}: {exc}"
            if job.status == TaskStatus.DRAFT:
                transition(job, TaskStatus.FAILED, SYSTEM, "Planung fehlgeschlagen")
            self.audit.record("planning_failed", SYSTEM, job.id, error=job.error)
            self.jobs.save(job)
            if job.status != TaskStatus.FAILED:
                raise
            return
        job.plan = plan
        # Rechtsraeume: Owner-Angabe + im Text genannte Laender (nie ein angenommener).
        detected = self.config.legal.detect_jurisdictions("\n".join([job.request, *job.owner_notes]))
        job.jurisdictions = job.jurisdictions + [j for j in detected if j not in job.jurisdictions]
        review = team.bus.send(job, AgentMessage(
            sender=team.master.id, recipient=team.legal.id, type=MessageType.LEGAL_REVIEW_REQUEST,
            job_id=job.id, payload={"scope": "task", "job": job},
        ))
        job.llm_calls = llm.calls
        job.legal_precheck = review
        self._record_legal(job, team, review)
        job.open_questions = list(plan.questions) + [f"[Legal] {q}" for q in review.questions]
        team.bus.record(job, AgentMessage(
            sender=team.master.id, recipient=job.requested_by, type=MessageType.PLAN_PROPOSAL,
            job_id=job.id, payload={"plan": plan.to_dict()},
        ))
        if job.status == TaskStatus.DRAFT:
            transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM,
                       "Rueckfragen an den Owner" if plan.questions else "Plan wartet auf Owner-Freigabe")
        self.audit.record("plan_replanned" if replan else "plan_proposed", Actor.agent(team.master.id), job.id,
                          plan_fingerprint=plan.fingerprint(), source=plan.source,
                          steps=[f"{s.agent_id}: {s.instruction[:80]}" for s in plan.steps],
                          questions=plan.questions, brand_version=brand.version)
        self.jobs.save(job)

    def _run(self, job: Job, team: _Team) -> None:
        order = topological_order(job.plan)
        by_id = {s.id: s for s in job.plan.steps}

        for step in order:
            if step.status != StepStatus.PENDING:
                continue  # bereits in einer frueheren (freigegebenen) Runde erledigt
            upstream = {by_id[d].agent_id: by_id[d].result.content for d in step.depends_on}
            ok = self._execute_step(job, team, step, team.brand.to_prompt_context(step.agent_id), upstream)
            self.jobs.save(job)
            if not ok:
                if step.legal_review and step.legal_review.status == LegalStatus.BLOCKED:
                    recommendation = ("Inhalt anpassen und als neuen Auftrag erteilen; bei Bedarf eine "
                                      "menschliche Rechtspruefung einholen. Kein Agent kann eine Legal-"
                                      "Blockade aufheben.")
                else:
                    recommendation = ("Ursache pruefen; bei Bedarf den Auftrag mit 'resubmit' neu vorlegen "
                                      "(braucht erneut deine Freigabe).")
                self._stop_failed(job, team, f"Schritt '{step.agent_id}' fehlgeschlagen: {step.error}",
                                  recommendation)
                return
            legal_open = bool(self.gate.open_legal_reviews(job))
            if legal_open or (step.qa_report and step.qa_report.owner_decisions):
                self._pause_for_owner(job, team, step)
                return

        self._finish(job, team, TaskStatus.COMPLETED)

    def _execute_step(self, job: Job, team: _Team, step: PlanStep, brand_context: str,
                      upstream: dict[str, str]) -> bool:
        settings = self.config.orchestration
        step.status = StepStatus.RUNNING
        feedback: list[str] = []
        revisions = retries = 0

        while True:
            step.attempts += 1
            request = AgentRequest(
                job_id=job.id, step_id=step.id, agent_id=step.agent_id,
                instruction=step.instruction, original_request=job.request,
                brand_context=brand_context, upstream_results=upstream,
                revision_feedback=feedback, owner_notes=list(job.owner_notes), attempt=step.attempts,
            )
            try:
                response: AgentResponse = team.bus.send(job, AgentMessage(
                    sender=team.master.id, recipient=step.agent_id, type=MessageType.TASK_ASSIGNMENT,
                    job_id=job.id, step_id=step.id, payload={"request": asdict(request)},
                ))
            except ExternalServiceNotApprovedError:
                raise  # betrifft den ganzen Auftrag -> sofort stoppen
            except AgentSystemError as exc:
                if exc.retryable and retries < settings.max_retries:  # nur mit Owner-Erlaubnis (> 0)
                    retries += 1
                    self.audit.record("step_retry", SYSTEM, job.id, step=step.agent_id, error=str(exc),
                                      note="durch Owner-Einstellung max_retries erlaubt")
                    continue
                return self._step_failed(job, team, step, f"{exc.code}: {exc}")
            except Exception as exc:
                log.exception("Unerwarteter Agentenfehler",
                              extra={"job_id": job.id, "step_id": step.id, "agent": step.agent_id})
                return self._step_failed(job, team, step, f"internal_error: {type(exc).__name__}: {exc}")

            team.bus.record(job, AgentMessage(
                sender=step.agent_id, recipient=team.master.id, type=MessageType.TASK_RESULT,
                job_id=job.id, step_id=step.id,
                payload={"chars": len(response.content), "actions": [a.action for a in response.proposed_actions]},
            ))
            # --- Legal & Compliance (vor QA, Pflicht fuer jedes Ergebnis) ---
            legal_review: LegalReview = team.bus.send(job, AgentMessage(
                sender=team.master.id, recipient=team.legal.id, type=MessageType.LEGAL_REVIEW_REQUEST,
                job_id=job.id, step_id=step.id, payload={"scope": "step", "job": job, "step": step,
                                                         "response": response},
            ))
            step.legal_review = legal_review
            self._record_legal(job, team, legal_review, step)
            if legal_review.status == LegalStatus.BLOCKED:
                step.result = None  # blockierte Inhalte nie ausliefern
                return self._step_failed(job, team, step, "LEGAL_REVIEW_BLOCKED - " + "; ".join(
                    f"{f.label} ({f.evidence})" for f in legal_review.findings if f.status == LegalStatus.BLOCKED)
                    or "LEGAL_REVIEW_BLOCKED")

            report: QAReport = team.bus.send(job, AgentMessage(
                sender=team.master.id, recipient=team.qa.id, type=MessageType.QA_REQUEST,
                job_id=job.id, step_id=step.id, payload={"request": request, "response": response},
            ))
            team.bus.record(job, AgentMessage(
                sender=team.qa.id, recipient=team.master.id, type=MessageType.QA_REPORT,
                job_id=job.id, step_id=step.id, payload=report.to_dict(),
            ))
            step.result, step.qa_report = response, report
            for action in report.denied_actions:
                event = "governance_violation" if action.action in AGENT_FORBIDDEN_ACTIONS else "action_denied"
                self.audit.record(event, Actor.agent(step.agent_id), job.id,
                                  action=action.action, description=action.description)

            if report.verdict == QAVerdict.APPROVED:
                step.status = StepStatus.DONE
                self._request_action_approvals(job, team, step)
                return True
            if report.verdict == QAVerdict.NEEDS_REVISION and revisions < settings.max_revisions:
                revisions += 1  # nur mit Owner-Erlaubnis (max_revisions > 0)
                feedback = [i.message for i in report.issues if i.severity.value in ("error", "critical")]
                self.audit.record("step_revision", SYSTEM, job.id, step=step.agent_id, feedback=feedback,
                                  note="durch Owner-Einstellung max_revisions erlaubt")
                continue
            step.result = None  # nicht freigegebene Inhalte nie ausliefern
            return self._step_failed(job, team, step, f"QA: {report.verdict.value} - " + "; ".join(
                i.message for i in report.issues if i.severity.value in ("error", "critical")))

    def _request_action_approvals(self, job: Job, team: _Team, step: PlanStep) -> None:
        if step.qa_report.approval_required and step.legal_review is None:
            raise GovernanceViolationError(f"Schritt {step.id}: Aktionen ohne Legal-&-Compliance-Pruefung")
        for action in step.qa_report.approval_required:
            reason = self.config.action_descriptions.get(action.action) or action.action
            apr = self.approvals.request(job.id, step.id, action, reason,
                                         legal_status=step.legal_review.status.value,
                                         legal_review_id=step.legal_review.id)
            job.approvals.append(apr.id)
            self.audit.record("action_proposed", Actor.agent(step.agent_id), job.id, approval_id=apr.id,
                              action=action.action, description=action.description, executed=False,
                              legal_status=apr.legal_status)
            team.bus.record(job, AgentMessage(
                sender=team.master.id, recipient=job.requested_by, type=MessageType.APPROVAL_REQUEST,
                job_id=job.id, step_id=step.id, payload={"approval_id": apr.id, **action.to_dict()},
            ))

    def _record_legal(self, job: Job, team: _Team, review: LegalReview, step: PlanStep | None = None) -> None:
        """Legal-Ergebnis in Trace und Audit-Log - auch 'keine Pruefung noetig'."""
        team.bus.record(job, AgentMessage(
            sender=team.legal.id, recipient=team.master.id, type=MessageType.LEGAL_REVIEW_REPORT,
            job_id=job.id, step_id=step.id if step else None,
            payload={"scope": review.scope, "status": review.status.value, "findings": len(review.findings)},
        ))
        self.audit.record("legal_review", Actor.agent(team.legal.id), job.id, scope=review.scope,
                          subject=step.agent_id if step else "task", review_id=review.id,
                          status=review.status.value, jurisdictions=review.jurisdictions,
                          findings=[f"{f.label}/{f.jurisdiction}: {f.status.value}" for f in review.findings],
                          reasons=review.reasons)
        for violation in review.agent_violations:
            agent_id, _, action = violation.partition(": ")
            self.audit.record("governance_violation", Actor.agent(agent_id), job.id, action=action,
                              note="Versuch, die Legal-Pruefung zu beeinflussen oder selbst zu handeln - verworfen")

    def _step_failed(self, job: Job, team: _Team, step: PlanStep, error: str) -> bool:
        step.status = StepStatus.FAILED
        step.error = error
        team.bus.record(job, AgentMessage(
            sender=step.agent_id, recipient=team.master.id, type=MessageType.ERROR,
            job_id=job.id, step_id=step.id, payload={"error": error},
        ))
        self.audit.record("step_failed", SYSTEM, job.id, step=step.agent_id, error=error)
        log.error("Schritt fehlgeschlagen: %s", error,
                  extra={"job_id": job.id, "step_id": step.id, "agent": step.agent_id})
        return False

    def _pending_for(self, job: Job) -> list[tuple[str, str, str]]:
        return [(a.id, a.action.action, a.action.description) for a in self.approvals.pending(job.id)]

    def _stop_failed(self, job: Job, team: _Team, reason: str, recommendation: str | None) -> None:
        """STOPPEN und melden: keine weiteren Schritte, kein automatischer Neustart."""
        if job.status != TaskStatus.RUNNING:
            return
        job.stop_reason = reason
        job.error = reason
        if job.plan:
            for s in job.plan.steps:
                if s.status == StepStatus.RUNNING:  # mitten in der Arbeit abgebrochen
                    s.status = StepStatus.FAILED
                    s.error = s.error or reason
                elif s.status == StepStatus.PENDING:
                    s.status = StepStatus.SKIPPED
                    s.error = "nicht ausgefuehrt - Auftrag gestoppt"
        if recommendation:
            job.recommendations.append(recommendation)
        self._finish(job, team, TaskStatus.FAILED)

    def _pause_for_owner(self, job: Job, team: _Team, step: PlanStep) -> None:
        questions = [f"{step.agent_id}: {a.description}" for a in step.qa_report.owner_decisions]
        job.open_questions = questions
        reasons = []
        if questions:
            reasons.append("Ein Agent braucht deine Entscheidung: " + " | ".join(questions))
            job.recommendations.append("Mit 'clarify' antworten und danach mit 'approve' weitermachen - "
                                       "oder den Auftrag mit 'cancel' beenden.")
        open_legal = self.gate.open_legal_reviews(job)
        if open_legal:
            reasons.append("HUMAN_LEGAL_REVIEW_REQUIRED (Schritt '" + step.agent_id + "'): " + "; ".join(
                f"{f.label} [{f.jurisdiction or 'Rechtsraum unbekannt'}]" for r in open_legal
                for f in r.findings if f.status != LegalStatus.PASSED))
            job.recommendations.append("Menschliche Rechtspruefung einholen, mit 'legal-review-done' "
                                       "dokumentieren und danach mit 'approve' weitermachen - oder 'cancel'.")
        # Offene Rueckfragen sind nur Owner-Entscheidungen; Legal-Punkte klaert eine
        # menschliche Rechtspruefung (legal-review-done), nicht ein 'clarify'.
        job.stop_reason = " || ".join(reasons)
        job.final_output = team.master.synthesize(job, self._pending_for(job))
        transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM, "gestoppt: Owner-Entscheidung noetig")
        self.audit.record("task_paused_for_owner", SYSTEM, job.id, questions=questions)
        team.bus.record(job, AgentMessage(
            sender=team.master.id, recipient=job.requested_by, type=MessageType.STOP_REPORT,
            job_id=job.id, payload={"questions": questions},
        ))

    def _finish(self, job: Job, team: _Team, status: TaskStatus) -> None:
        job.final_output = team.master.synthesize(job, self._pending_for(job))
        team.bus.record(job, AgentMessage(
            sender=team.master.id, recipient=job.requested_by,
            type=MessageType.FINAL_RESULT if status == TaskStatus.COMPLETED else MessageType.STOP_REPORT,
            job_id=job.id, payload={"status": status.value, "chars": len(job.final_output)},
        ))
        transition(job, status, SYSTEM, job.stop_reason or "")
        self.audit.record("task_completed" if status == TaskStatus.COMPLETED else "task_failed",
                          SYSTEM, job.id, stop_reason=job.stop_reason,
                          pending_action_approvals=[p[0] for p in self._pending_for(job)])
