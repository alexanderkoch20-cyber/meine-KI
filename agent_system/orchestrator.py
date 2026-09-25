"""Orchestrator: die Laufzeit-Engine hinter dem Master-Agenten.

Ablauf eines Auftrags (jede Station laeuft ueber den MessageBus und
erscheint im Job-Trace):

    user ──task_assignment──▶ master            (Planung)
    master ──task_assignment──▶ <spezialist>    (je Planschritt, in Abhaengigkeitsreihenfolge)
    <spezialist> ──task_result──▶ master
    master ──qa_request──▶ qa
    qa ──qa_report──▶ master                    (ggf. Ueberarbeitungsrunde)
    master ──approval_request──▶ user           (nur fuer Aktionen mit externer Wirkung)
    master ──final_result──▶ user

Fehlerbehandlung:
- technische Fehler mit ``retryable=True`` -> bis zu ``max_retries`` Wiederholungen
- nicht wiederholbare Fehler (z.B. nicht freigegebener externer Dienst,
  Budget erschoepft) -> Schritt schlaegt sofort fehl
- Schritte, deren Abhaengigkeit fehlschlug -> ``skipped``
- unerwartete Exceptions werden abgefangen, geloggt und dem Schritt zugeordnet;
  ein einzelner Fehler bringt nie den ganzen Prozess zum Absturz.
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
from .core.errors import AgentSystemError, ExternalServiceNotApprovedError
from .core.jobs import JobStore, transition
from .core.llm import BudgetedLLMClient, LLMClient, create_llm_client
from .core.logging_setup import get_logger
from .core.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    Job,
    JobStatus,
    MessageType,
    PlanStep,
    QAReport,
    QAVerdict,
    StepStatus,
)
from .core.permissions import ApprovalStore

log = get_logger("orchestrator")

USER = "user"


class _Team:
    """Pro Job neu erzeugte Agenten-Instanzen (eigenes LLM-Budget je Job)."""

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
            self.bus.register(agent_id, lambda m, a=agent: a.handle(AgentRequest(**m.payload["request"])))


class Orchestrator:
    def __init__(
        self,
        config: SystemConfig | None = None,
        llm: LLMClient | None = None,
        brand: BrandKnowledge | None = None,
        job_store: JobStore | None = None,
        approval_store: ApprovalStore | None = None,
        data_dir: Path | str | None = None,
    ):
        self.config = config or load_config()
        self.llm = llm or create_llm_client(self.config)
        self.brand = brand or BrandKnowledge.load(self.config.brand_file)
        data_dir = Path(data_dir) if data_dir else None
        self.jobs = job_store or JobStore(data_dir / "jobs" if data_dir else None)
        self.approvals = approval_store or ApprovalStore(data_dir / "approvals.json" if data_dir else None)

    # ----------------------------------------------------------- Public API

    def submit(self, request: str, requested_by: str = USER) -> Job:
        request = (request or "").strip()
        if not request:
            raise AgentSystemError("Leerer Auftrag")
        return self.jobs.add(Job(request=request, requested_by=requested_by))

    def run(self, job: Job) -> Job:
        llm = BudgetedLLMClient(self.llm, self.config.max_llm_calls_per_job)
        team = _Team(self.config, llm, self.brand)
        try:
            self._run(job, team)
        except AgentSystemError as exc:
            self._fail(job, f"{exc.code}: {exc}")
        except Exception as exc:  # letzte Verteidigungslinie
            log.exception("Unerwarteter Fehler", extra={"job_id": job.id})
            self._fail(job, f"internal_error: {type(exc).__name__}: {exc}")
        finally:
            job.trace.append({"llm_calls": llm.calls})
            self.jobs.save(job)
        return job

    def handle(self, request: str) -> Job:
        """Bequemer Einstieg: Auftrag anlegen und sofort ausfuehren."""
        return self.run(self.submit(request))

    def decide_approval(self, approval_id: str, approve: bool, decided_by: str = USER):
        """Freigabe erteilen/ablehnen. Fuehrt NICHTS aus (Dry-Run)."""
        apr = self.approvals.decide(approval_id, approve, decided_by)
        if not self.approvals.pending(apr.job_id):
            note = "alle Freigaben entschieden (Dry-Run, keine externe Ausfuehrung)"
            try:
                job = self.jobs.get(apr.job_id)
            except AgentSystemError:
                self.jobs.complete_snapshot(apr.job_id, note)  # Job aus frueherem Prozess
                return apr
            if job.status == JobStatus.AWAITING_APPROVAL:
                transition(job, JobStatus.COMPLETED, note)
                self.jobs.save(job)
        return apr

    # ------------------------------------------------------------ Internals

    def _fail(self, job: Job, error: str) -> None:
        job.error = error
        if job.status not in (JobStatus.FAILED, JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED,
                              JobStatus.CANCELLED, JobStatus.AWAITING_APPROVAL):
            transition(job, JobStatus.FAILED, error)

    def _run(self, job: Job, team: _Team) -> None:
        transition(job, JobStatus.PLANNING)
        job.plan = team.bus.send(job, AgentMessage(
            sender=USER, recipient=team.master.id, type=MessageType.TASK_ASSIGNMENT,
            job_id=job.id, payload={"job": job, "request": job.request},
        ))
        team.bus.record(job, AgentMessage(
            sender=team.master.id, recipient=team.master.id, type=MessageType.TASK_RESULT,
            job_id=job.id, payload={"plan": job.plan.to_dict()},
        ))
        self.jobs.save(job)

        transition(job, JobStatus.RUNNING, f"{len(job.plan.steps)} Schritte ({job.plan.source})")
        order = topological_order(job.plan)
        by_id = {s.id: s for s in job.plan.steps}
        brand_context = self.brand.to_prompt_context()

        for step in order:
            failed_deps = [d for d in step.depends_on if by_id[d].status != StepStatus.DONE]
            if failed_deps:
                step.status = StepStatus.SKIPPED
                step.error = "Abhaengigkeit fehlgeschlagen: " + ", ".join(by_id[d].agent_id for d in failed_deps)
                continue
            upstream = {by_id[d].agent_id: by_id[d].result.content for d in step.depends_on}
            self._execute_step(job, team, step, brand_context, upstream)
            self.jobs.save(job)

        transition(job, JobStatus.QA_REVIEW)
        pending = []
        for step in job.plan.steps:
            if step.status == StepStatus.DONE and step.qa_report:
                for action in step.qa_report.approval_required:
                    reason = self.config.action_descriptions.get(action.action) or action.action
                    apr = self.approvals.request(job.id, step.id, action, reason)
                    job.approvals.append(apr.id)
                    pending.append((apr.id, action.action, action.description))
                    team.bus.record(job, AgentMessage(
                        sender=team.master.id, recipient=USER, type=MessageType.APPROVAL_REQUEST,
                        job_id=job.id, step_id=step.id, payload={"approval_id": apr.id, **action.to_dict()},
                    ))

        done = [s for s in job.plan.steps if s.status == StepStatus.DONE]
        if not done:
            errors = "; ".join(f"{s.agent_id}: {s.error}" for s in job.plan.steps)
            job.error = errors
            transition(job, JobStatus.FAILED, "kein Schritt erfolgreich")
            return

        job.final_output = team.master.synthesize(job, pending)
        team.bus.record(job, AgentMessage(
            sender=team.master.id, recipient=USER, type=MessageType.FINAL_RESULT,
            job_id=job.id, payload={"chars": len(job.final_output)},
        ))
        if len(done) < len(job.plan.steps):
            transition(job, JobStatus.PARTIALLY_COMPLETED)
        elif pending:
            transition(job, JobStatus.AWAITING_APPROVAL, f"{len(pending)} Freigabe(n) offen")
        else:
            transition(job, JobStatus.COMPLETED)

    def _execute_step(self, job: Job, team: _Team, step: PlanStep, brand_context: str,
                      upstream: dict[str, str]) -> None:
        settings = self.config.orchestration
        step.status = StepStatus.RUNNING
        feedback: list[str] = []
        revisions = 0
        retries = 0

        while True:
            step.attempts += 1
            request = AgentRequest(
                job_id=job.id, step_id=step.id, agent_id=step.agent_id,
                instruction=step.instruction, original_request=job.request,
                brand_context=brand_context, upstream_results=upstream,
                revision_feedback=feedback, attempt=step.attempts,
            )
            try:
                response: AgentResponse = team.bus.send(job, AgentMessage(
                    sender=team.master.id, recipient=step.agent_id, type=MessageType.TASK_ASSIGNMENT,
                    job_id=job.id, step_id=step.id, payload={"request": asdict(request)},
                ))
            except ExternalServiceNotApprovedError:
                raise  # betrifft den ganzen Job -> sofort abbrechen
            except AgentSystemError as exc:
                if exc.retryable and retries < settings.max_retries:
                    retries += 1
                    log.warning("Schritt fehlgeschlagen, Wiederholung %d: %s", retries, exc,
                                extra={"job_id": job.id, "step_id": step.id, "agent": step.agent_id})
                    continue
                self._step_failed(job, team, step, f"{exc.code}: {exc}")
                return
            except Exception as exc:
                log.exception("Unerwarteter Agentenfehler",
                              extra={"job_id": job.id, "step_id": step.id, "agent": step.agent_id})
                self._step_failed(job, team, step, f"internal_error: {type(exc).__name__}: {exc}")
                return

            team.bus.record(job, AgentMessage(
                sender=step.agent_id, recipient=team.master.id, type=MessageType.TASK_RESULT,
                job_id=job.id, step_id=step.id, payload={"chars": len(response.content),
                                                         "actions": [a.action for a in response.proposed_actions]},
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

            if report.verdict == QAVerdict.APPROVED:
                step.status = StepStatus.DONE
                return
            if report.verdict == QAVerdict.NEEDS_REVISION and revisions < settings.max_revisions:
                revisions += 1
                feedback = [i.message for i in report.issues if i.severity.value in ("error", "critical")]
                continue
            step.result = None  # nicht freigegebene Inhalte nie ausliefern
            self._step_failed(job, team, step, f"QA: {report.verdict.value} - "
                              + "; ".join(i.message for i in report.issues
                                          if i.severity.value in ("error", "critical")))
            return

    @staticmethod
    def _step_failed(job: Job, team: _Team, step: PlanStep, error: str) -> None:
        step.status = StepStatus.FAILED
        step.error = error
        team.bus.record(job, AgentMessage(
            sender=step.agent_id, recipient=team.master.id, type=MessageType.ERROR,
            job_id=job.id, step_id=step.id, payload={"error": error},
        ))
        log.error("Schritt fehlgeschlagen: %s", error,
                  extra={"job_id": job.id, "step_id": step.id, "agent": step.agent_id})
