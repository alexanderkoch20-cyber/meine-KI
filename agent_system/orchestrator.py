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
       master ──qa_request──▶ qa ──qa_report──▶ master
       master ──approval_request──▶ owner   (Vorschlaege, nie ausgefuehrt)
       master ──final_result──▶ owner

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
from .agents.master import MasterAgent, topological_order
from .agents.qa import QAAgent
from .agents.specialist import SPECIALIST_CLASSES, SpecialistAgent
from .core.brand import BrandKnowledge
from .core.bus import MessageBus
from .core.config import SystemConfig, load_config
from .core.errors import AgentSystemError, ExternalServiceNotApprovedError, OwnerApprovalRequiredError
from .core.governance import SYSTEM, Actor, AuditLog, OwnerApprovalGate
from .core.jobs import JobStore, transition
from .core.llm import BudgetedLLMClient, LLMClient, create_llm_client
from .core.logging_setup import get_logger
from .core.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ApprovalRequest,
    Job,
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
        ctx = AgentContext(config=config, llm=llm, brand=brand)
        self.bus = MessageBus()
        self.master: MasterAgent | None = None
        self.qa: QAAgent | None = None
        self.specialists: dict[str, SpecialistAgent] = {}
        for agent_id, definition in config.agents.items():
            if definition.role == "master":
                self.master = MasterAgent(definition, ctx)
            elif definition.role == "qa":
                self.qa = QAAgent(definition, ctx)
            else:
                cls = SPECIALIST_CLASSES.get(agent_id, SpecialistAgent)
                self.specialists[agent_id] = cls(definition, ctx)

        master, qa = self.master, self.qa
        self.bus.register(master.id, lambda m: master.plan(m.payload["job"]))
        self.bus.register(qa.id, lambda m: qa.review(m.payload["request"], m.payload["response"]))
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
        self.brand = brand or BrandKnowledge.load(self.config.brand_file)
        data_dir = Path(data_dir) if data_dir else None
        self.jobs = job_store or JobStore(data_dir / "jobs" if data_dir else None)
        self.approvals = approval_store or ApprovalStore(data_dir / "approvals.json" if data_dir else None)
        self.audit = audit or AuditLog(data_dir / "audit.jsonl" if data_dir else None)
        self.gate = OwnerApprovalGate(self.config, self.audit,
                                      data_dir / "task_approvals.json" if data_dir else None)
        for agent_id in self.config.inactive_agents:
            log.warning("Agent '%s' ist definiert, aber nicht vom Owner freigegeben -> inaktiv", agent_id)

    # =================================================== Owner-Schnittstelle

    def submit(self, request: str, actor: Actor) -> Job:
        """Owner erteilt einen Auftrag. Der Master PLANT nur; nichts wird ausgefuehrt."""
        self.gate.require_owner(actor, "submit_task")
        request = (request or "").strip()
        if not request:
            raise AgentSystemError("Leerer Auftrag")
        job = self.jobs.add(Job(request=request, requested_by=actor.id))
        self.audit.record("task_created", actor, job.id, request=request)
        self._plan(job)
        return job

    def approve(self, job_id: str, actor: Actor, expected_plan: str | None = None) -> Job:
        job = self.jobs.get(job_id)
        self.gate.approve_task(job, actor, expected_plan)
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

    def resubmit(self, job_id: str, actor: Actor) -> Job:
        """Owner entscheidet, einen gestoppten/abgebrochenen Auftrag neu vorzulegen.
        Ergebnis ist ein NEUER Auftrag, der wieder eine Freigabe braucht."""
        self.gate.require_owner(actor, "resubmit_task")
        old = self.jobs.get(job_id)
        if old.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise AgentSystemError(f"Nur gescheiterte/abgebrochene Auftraege koennen neu vorgelegt werden "
                                   f"({old.id} ist '{old.status.value}')")
        job = self.submit(old.request, actor)
        job.resubmitted_from = old.id
        self.audit.record("task_resubmitted", actor, job.id, from_job=old.id)
        self.jobs.save(job)
        return job

    def decide_action(self, approval_id: str, approve: bool, actor: Actor) -> ApprovalRequest:
        """Owner entscheidet ueber einen Aktionsvorschlag. Es wird NICHTS ausgefuehrt (Dry-Run)."""
        return self.gate.decide_action(self.approvals, approval_id, approve, actor)

    # ============================================================ Ausfuehrung

    def execute(self, job_id: str) -> Job:
        """Fuehrt einen vom Owner freigegebenen Auftrag aus. Ohne gueltige
        Freigabe: ``OwnerApprovalRequiredError`` - es passiert nichts."""
        job = self.jobs.get(job_id)
        try:
            self.gate.assert_may_execute(job)  # wirft bei fehlender/ungueltiger Freigabe
        finally:
            self.jobs.save(job)  # z.B. Rueckfall auf WAITING_FOR_OWNER nach Konfig-Aenderung
        transition(job, TaskStatus.RUNNING, SYSTEM, "Ausfuehrung nach Owner-Freigabe")
        job.stop_reason = None  # Meldungen einer frueheren Pause sind mit der neuen Freigabe erledigt
        job.recommendations = []
        self.audit.record("execution_started", SYSTEM, job.id, round=job.approval_round,
                          plan_fingerprint=job.plan.fingerprint())
        llm = BudgetedLLMClient(self.llm, self.config.max_llm_calls_per_job, already_used=job.llm_calls)
        team = _Team(self.config, llm, self.brand)
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
        team = _Team(self.config, llm, self.brand)
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
        job.llm_calls = llm.calls
        job.plan = plan
        job.open_questions = list(plan.questions)
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
                          questions=plan.questions)
        self.jobs.save(job)

    def _run(self, job: Job, team: _Team) -> None:
        order = topological_order(job.plan)
        by_id = {s.id: s for s in job.plan.steps}
        brand_context = self.brand.to_prompt_context()

        for step in order:
            if step.status != StepStatus.PENDING:
                continue  # bereits in einer frueheren (freigegebenen) Runde erledigt
            upstream = {by_id[d].agent_id: by_id[d].result.content for d in step.depends_on}
            ok = self._execute_step(job, team, step, brand_context, upstream)
            self.jobs.save(job)
            if not ok:
                self._stop_failed(job, team, f"Schritt '{step.agent_id}' fehlgeschlagen: {step.error}",
                                  "Ursache pruefen; bei Bedarf den Auftrag mit 'resubmit' neu vorlegen "
                                  "(braucht erneut deine Freigabe).")
                return
            if step.qa_report and step.qa_report.owner_decisions:
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
        for action in step.qa_report.approval_required:
            reason = self.config.action_descriptions.get(action.action) or action.action
            apr = self.approvals.request(job.id, step.id, action, reason)
            job.approvals.append(apr.id)
            self.audit.record("action_proposed", Actor.agent(step.agent_id), job.id, approval_id=apr.id,
                              action=action.action, description=action.description, executed=False)
            team.bus.record(job, AgentMessage(
                sender=team.master.id, recipient=job.requested_by, type=MessageType.APPROVAL_REQUEST,
                job_id=job.id, step_id=step.id, payload={"approval_id": apr.id, **action.to_dict()},
            ))

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
        job.stop_reason = "Ein Agent braucht deine Entscheidung: " + " | ".join(questions)
        job.recommendations.append("Mit 'clarify' antworten und danach mit 'approve' weitermachen - "
                                   "oder den Auftrag mit 'cancel' beenden.")
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
