"""Gemeinsames Datenmodell aller Agenten.

Alle Agenten kommunizieren ausschliesslich ueber diese Strukturen - nie ueber
freie Strings oder direkte Methodenaufrufe untereinander. Damit sind die
Schnittstellen stabil, serialisierbar (JSON) und testbar.

Fluss:
    User -> Job -> MasterAgent.plan() -> Plan(Steps)
         -> AgentRequest -> Spezial-Agent -> AgentResponse
         -> QAAgent.review() -> QAReport
         -> MasterAgent.synthesize() -> Job.final_output -> User
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStatus(str, Enum):
    """Lebenszyklus eines Auftrags (Owner-Regel).

    DRAFT -> WAITING_FOR_OWNER -> APPROVED -> RUNNING -> COMPLETED | FAILED
    Nur der Owner setzt APPROVED bzw. CANCELLED. Ein laufender Auftrag, der
    eine Entscheidung braucht, geht zurueck auf WAITING_FOR_OWNER.
    """

    DRAFT = "draft"
    WAITING_FOR_OWNER = "waiting_for_owner"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LegalStatus(str, Enum):
    """Rechtlicher Pruefstatus - eine eigene Dimension NEBEN dem Task-Status.

    NOT_REQUIRED          geprueft, keine rechtlich relevanten Merkmale (dokumentiert)
    REQUIRED              Auftrag ist rechtlich relevant -> jeder Schritt wird geprueft
    PASSED                keine blockierenden Risiken erkannt (keine Garantie!)
    BLOCKED               Ausfuehrung blockiert (kritisches Risiko)
    HUMAN_REQUIRED        menschliche Rechtspruefung zwingend (Unsicherheit/hohes Risiko)
    """

    NOT_REQUIRED = "legal_review_not_required"
    REQUIRED = "legal_review_required"
    PASSED = "legal_review_passed"
    BLOCKED = "legal_review_blocked"
    HUMAN_REQUIRED = "human_legal_review_required"


#: Rangfolge fuer die Zusammenfassung mehrerer Pruefungen (hoeher = strenger).
LEGAL_SEVERITY = {
    LegalStatus.NOT_REQUIRED: 0,
    LegalStatus.PASSED: 1,
    LegalStatus.REQUIRED: 2,
    LegalStatus.HUMAN_REQUIRED: 3,
    LegalStatus.BLOCKED: 4,
}
#: Status, die ohne menschliche Rechtspruefung weiterlaufen duerfen.
LEGAL_CLEARED = frozenset({LegalStatus.NOT_REQUIRED, LegalStatus.REQUIRED, LegalStatus.PASSED})


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class QAVerdict(str, Enum):
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_RESULT = "task_result"
    QA_REQUEST = "qa_request"
    QA_REPORT = "qa_report"
    PLAN_PROPOSAL = "plan_proposal"
    LEGAL_REVIEW_REQUEST = "legal_review_request"
    LEGAL_REVIEW_REPORT = "legal_review_report"
    OWNER_DECISION = "owner_decision"
    APPROVAL_REQUEST = "approval_request"
    STOP_REPORT = "stop_report"
    ERROR = "error"
    FINAL_RESULT = "final_result"


# ---------------------------------------------------------------------------
# Nachrichten zwischen Agenten
# ---------------------------------------------------------------------------


@dataclass
class AgentMessage:
    """Umschlag fuer jede Kommunikation zwischen zwei Agenten."""

    sender: str
    recipient: str
    type: MessageType
    job_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    id: str = field(default_factory=lambda: new_id("msg"))
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


# ---------------------------------------------------------------------------
# Aktionen mit (potenziell) externer Wirkung
# ---------------------------------------------------------------------------


@dataclass
class ProposedAction:
    """Eine Aktion, die ein Agent VORSCHLAEGT, aber nie selbst ausfuehrt.

    ``action`` ist ein Schluessel aus ``config/permissions.yaml``
    (z.B. ``publish_content``, ``spend_money``).
    """

    action: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    proposed_by: str = ""
    id: str = field(default_factory=lambda: new_id("act"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProposedAction":
        return cls(**d)


# ---------------------------------------------------------------------------
# Auftrag / Ergebnis eines Spezial-Agenten
# ---------------------------------------------------------------------------


@dataclass
class AgentRequest:
    job_id: str
    step_id: str
    agent_id: str
    instruction: str
    original_request: str
    brand_context: str = ""
    #: Ergebnisse vorheriger Schritte, von denen dieser Schritt abhaengt.
    upstream_results: dict[str, str] = field(default_factory=dict)
    #: Rueckmeldung der QA bei einer (vom Owner erlaubten) Ueberarbeitungsrunde.
    revision_feedback: list[str] = field(default_factory=list)
    #: Antworten/Entscheidungen des Owners zu diesem Auftrag.
    owner_notes: list[str] = field(default_factory=list)
    attempt: int = 1


@dataclass
class AgentResponse:
    agent_id: str
    step_id: str
    content: str
    model: str
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["proposed_actions"] = [a.to_dict() for a in self.proposed_actions]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentResponse":
        d = dict(d)
        d["proposed_actions"] = [ProposedAction.from_dict(a) for a in d.get("proposed_actions", [])]
        return cls(**d)


# ---------------------------------------------------------------------------
# Qualitaetssicherung
# ---------------------------------------------------------------------------


@dataclass
class QAIssue:
    check: str
    message: str
    severity: Severity

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "message": self.message, "severity": self.severity.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QAIssue":
        return cls(check=d["check"], message=d["message"], severity=Severity(d["severity"]))


@dataclass
class QAReport:
    step_id: str
    verdict: QAVerdict
    issues: list[QAIssue] = field(default_factory=list)
    #: Aktionen, die nur mit ausdruecklicher Freigabe des Owners laufen duerfen.
    approval_required: list[ProposedAction] = field(default_factory=list)
    #: Aktionen, die grundsaetzlich verboten sind.
    denied_actions: list[ProposedAction] = field(default_factory=list)
    #: Der Agent braucht eine Entscheidung des Owners -> Auftrag stoppt.
    owner_decisions: list[ProposedAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "verdict": self.verdict.value,
            "issues": [i.to_dict() for i in self.issues],
            "approval_required": [a.to_dict() for a in self.approval_required],
            "denied_actions": [a.to_dict() for a in self.denied_actions],
            "owner_decisions": [a.to_dict() for a in self.owner_decisions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QAReport":
        acts = lambda key: [ProposedAction.from_dict(a) for a in d.get(key, [])]  # noqa: E731
        return cls(
            step_id=d["step_id"],
            verdict=QAVerdict(d["verdict"]),
            issues=[QAIssue.from_dict(i) for i in d.get("issues", [])],
            approval_required=acts("approval_required"),
            denied_actions=acts("denied_actions"),
            owner_decisions=acts("owner_decisions"),
        )


# ---------------------------------------------------------------------------
# Legal & Compliance
# ---------------------------------------------------------------------------

LEGAL_DISCLAIMER = (
    "Keine Rechtsberatung und keine verbindliche Rechtsauskunft. Diese automatische "
    "Einschaetzung dient der Risiko-Frueherkennung, ersetzt keine Pruefung durch eine "
    "qualifizierte Rechtsperson und garantiert nicht, dass eine Handlung legal oder "
    "straffrei ist. Entscheidungen trifft ausschliesslich der Owner."
)


@dataclass
class LegalFinding:
    """Ein dokumentiertes Rechtsrisiko - mit Rechtsraum, Regel, Quelle, Daten, Unsicherheiten."""

    topic: str
    area: str
    label: str
    risk: str                      # medium | high | critical
    status: LegalStatus            # PASSED | HUMAN_REQUIRED | BLOCKED
    reason_code: str               # z.B. verified_source, no_jurisdiction, unverified_source
    jurisdiction: str | None = None
    evidence: str = ""
    rule_id: str | None = None
    rule_title: str | None = None
    reference: str | None = None
    url: str | None = None
    version_date: str | None = None        # Veroeffentlichungs-/Fassungsdatum laut Katalog
    source_verified_at: str | None = None  # wann ein Mensch die Quelle geprueft hat
    source_verified_by: str | None = None
    reviewed_at: str = field(default_factory=utcnow)  # Pruefdatum dieser Einschaetzung
    uncertainties: list[str] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LegalFinding":
        d = dict(d)
        d["status"] = LegalStatus(d["status"])
        return cls(**d)


@dataclass
class LegalReview:
    scope: str                     # "task" (Vorpruefung) | "step" (Ergebnis eines Agenten)
    subject_id: str
    status: LegalStatus
    jurisdictions: list[str] = field(default_factory=list)
    findings: list[LegalFinding] = field(default_factory=list)
    #: Begruendung - auch (gerade) wenn keine Pruefung noetig war.
    reasons: list[str] = field(default_factory=list)
    checked_areas: list[str] = field(default_factory=list)
    #: Rueckfragen an den Owner (z.B. fehlende Jurisdiktion).
    questions: list[str] = field(default_factory=list)
    #: Versuche des Legal-Agenten selbst, Aktionen auszuloesen (werden nie ausgefuehrt).
    agent_violations: list[str] = field(default_factory=list)
    reviewed_by: str = "legal"
    reviewed_at: str = field(default_factory=utcnow)
    disclaimer: str = LEGAL_DISCLAIMER
    #: Vom Owner dokumentierte menschliche Rechtspruefung (Kopie; massgeblich ist das Gate).
    human_review: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: new_id("lgl"))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["findings"] = [f.to_dict() for f in self.findings]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LegalReview":
        d = dict(d)
        d["status"] = LegalStatus(d["status"])
        d["findings"] = [LegalFinding.from_dict(f) for f in d.get("findings", [])]
        return cls(**d)


# ---------------------------------------------------------------------------
# Plan & Job
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    agent_id: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("step"))
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    result: AgentResponse | None = None
    qa_report: QAReport | None = None
    legal_review: LegalReview | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "instruction": self.instruction,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "attempts": self.attempts,
            "result": self.result.to_dict() if self.result else None,
            "qa_report": self.qa_report.to_dict() if self.qa_report else None,
            "legal_review": self.legal_review.to_dict() if self.legal_review else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanStep":
        return cls(
            agent_id=d["agent_id"],
            instruction=d["instruction"],
            depends_on=list(d.get("depends_on", [])),
            id=d["id"],
            status=StepStatus(d["status"]),
            attempts=d.get("attempts", 0),
            result=AgentResponse.from_dict(d["result"]) if d.get("result") else None,
            qa_report=QAReport.from_dict(d["qa_report"]) if d.get("qa_report") else None,
            legal_review=LegalReview.from_dict(d["legal_review"]) if d.get("legal_review") else None,
            error=d.get("error"),
        )


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str = ""
    source: str = "rules"  # "llm" oder "rules"
    #: Rueckfragen des Masters bei unklarem Auftrag (dann gibt es keine Schritte).
    questions: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        """Fingerabdruck des Plans. Eine Owner-Freigabe gilt nur fuer genau
        diesen Plan - jede Aenderung (neuer Schritt, andere Anweisung) macht
        sie ungueltig. So kann ein Auftrag nicht unbemerkt erweitert werden."""
        canonical = [
            {"id": s.id, "agent": s.agent_id, "instruction": s.instruction, "depends_on": s.depends_on}
            for s in self.steps
        ]
        raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rationale": self.rationale,
            "source": self.source,
            "fingerprint": self.fingerprint(),
            "questions": list(self.questions),
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        return cls(steps=[PlanStep.from_dict(s) for s in d.get("steps", [])],
                   rationale=d.get("rationale", ""), source=d.get("source", "rules"),
                   questions=list(d.get("questions", [])))


@dataclass
class TaskDraft:
    """Strukturierter Auftragsentwurf des Master-Agenten fuer den Owner.

    Der Draft beschreibt, WAS nach einer Owner-Freigabe an welchen Spezialagenten
    gehen wuerde - er startet nichts und gibt nichts frei. Massgeblich fuer die
    Ausfuehrung bleibt der Plan, dessen Fingerabdruck der Owner freigibt.
    """

    job_id: str
    owner_request: str
    objective: str
    specialists: list[dict[str, Any]]
    deliverable: str
    brand_version: int | None
    brand_hash: str | None
    jurisdictions: list[str]
    legal_status: str
    constraints: list[str]
    #: Entscheidungen, die im Auftrag anklingen und ausschliesslich der Owner trifft.
    owner_decisions_required: list[str]
    open_questions: list[str]
    plan_fingerprint: str
    requires_owner_approval: bool = True
    created_by: str = "master"
    created_at: str = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("draft"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskDraft":
        return cls(**d)


@dataclass
class ApprovalRequest:
    """Owner-Freigabe fuer eine einzelne vorgeschlagene AKTION."""

    job_id: str
    step_id: str
    action: ProposedAction
    reason: str
    id: str = field(default_factory=lambda: new_id("apr"))
    status: str = "pending"  # pending | approved | rejected
    #: Legal-Status der Pruefung, die diese Aktion abdeckt. Ohne Legal-Pruefung
    #: (Default) kann die Aktion nicht freigegeben werden.
    legal_status: str = LegalStatus.REQUIRED.value
    legal_review_id: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalRequest":
        d = dict(d)
        d["action"] = ProposedAction.from_dict(d["action"])
        return cls(**d)


@dataclass
class Job:
    """Ein Auftrag des Owners (Task)."""

    request: str
    requested_by: str = "owner"
    id: str = field(default_factory=lambda: new_id("job"))
    status: TaskStatus = TaskStatus.DRAFT
    plan: Plan | None = None
    #: Zaehlt, wie oft der Auftrag dem Owner vorgelegt wurde. Jede Freigabe
    #: gilt nur fuer genau eine Runde.
    approval_round: int = 0
    #: Rueckfragen an den Owner - solange offen, ist keine Freigabe moeglich.
    open_questions: list[str] = field(default_factory=list)
    owner_notes: list[str] = field(default_factory=list)
    #: Warum der Auftrag gestoppt hat (Fehler, Entscheidung noetig, ...).
    stop_reason: str | None = None
    recommendations: list[str] = field(default_factory=list)
    final_output: str | None = None
    error: str | None = None
    approvals: list[str] = field(default_factory=list)
    llm_calls: int = 0
    #: Rechtsraeume, fuer die der Auftrag gilt (Owner-Angabe + Erkennung im Text).
    #: Leer = unbekannt. Es wird NIE automatisch deutsches Recht angenommen.
    jurisdictions: list[str] = field(default_factory=list)
    #: Legal-Vorpruefung des Auftrags (vor der Owner-Freigabe).
    legal_precheck: LegalReview | None = None
    #: Strukturierter Auftragsentwurf des Masters (fuer die Owner-Entscheidung).
    task_draft: TaskDraft | None = None
    #: Brand-Basis, mit der geplant bzw. zuletzt gearbeitet wurde (Version + Hash).
    brand_version: int | None = None
    brand_hash: str | None = None
    #: ID des Auftrags, aus dem dieser (per Owner-Entscheidung) neu erstellt wurde.
    resubmitted_from: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def legal_reviews(self) -> list[LegalReview]:
        reviews = [self.legal_precheck] if self.legal_precheck else []
        if self.plan:
            reviews += [s.legal_review for s in self.plan.steps if s.legal_review]
        return reviews

    @property
    def legal_status(self) -> LegalStatus:
        """Strengster Legal-Status aller Pruefungen dieses Auftrags."""
        reviews = self.legal_reviews()
        if not reviews:
            return LegalStatus.REQUIRED  # noch nicht geprueft
        return max((r.status for r in reviews), key=LEGAL_SEVERITY.__getitem__)

    def to_dict(self) -> dict[str, Any]:
        d = {f: getattr(self, f) for f in self.__dataclass_fields__}
        d["status"] = self.status.value
        d["plan"] = self.plan.to_dict() if self.plan else None
        d["legal_precheck"] = self.legal_precheck.to_dict() if self.legal_precheck else None
        d["task_draft"] = self.task_draft.to_dict() if self.task_draft else None
        d["legal_status"] = self.legal_status.value
        for key in ("open_questions", "owner_notes", "recommendations", "approvals", "trace", "history",
                    "jurisdictions"):
            d[key] = list(d[key])
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        d["status"] = TaskStatus(d["status"])
        d["plan"] = Plan.from_dict(d["plan"]) if d.get("plan") else None
        d["legal_precheck"] = LegalReview.from_dict(d["legal_precheck"]) if d.get("legal_precheck") else None
        d["task_draft"] = TaskDraft.from_dict(d["task_draft"]) if d.get("task_draft") else None
        return cls(**d)
