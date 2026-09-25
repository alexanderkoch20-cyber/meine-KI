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

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    QA_REVIEW = "qa_review"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    APPROVAL_REQUEST = "approval_request"
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
    #: Rueckmeldung der QA bei einer Ueberarbeitungsrunde.
    revision_feedback: list[str] = field(default_factory=list)
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


@dataclass
class QAReport:
    step_id: str
    verdict: QAVerdict
    issues: list[QAIssue] = field(default_factory=list)
    #: Aktionen, die nur mit ausdruecklicher Freigabe des Nutzers laufen duerfen.
    approval_required: list[ProposedAction] = field(default_factory=list)
    #: Aktionen, die grundsaetzlich verboten sind.
    denied_actions: list[ProposedAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "verdict": self.verdict.value,
            "issues": [i.to_dict() for i in self.issues],
            "approval_required": [a.to_dict() for a in self.approval_required],
            "denied_actions": [a.to_dict() for a in self.denied_actions],
        }


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
            "error": self.error,
        }


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str = ""
    source: str = "rules"  # "llm" oder "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rationale": self.rationale,
            "source": self.source,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class ApprovalRequest:
    job_id: str
    step_id: str
    action: ProposedAction
    reason: str
    id: str = field(default_factory=lambda: new_id("apr"))
    status: str = "pending"  # pending | approved | rejected
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
        d["action"] = ProposedAction(**d["action"])
        return cls(**d)


@dataclass
class Job:
    request: str
    requested_by: str = "user"
    id: str = field(default_factory=lambda: new_id("job"))
    status: JobStatus = JobStatus.CREATED
    plan: Plan | None = None
    final_output: str | None = None
    error: str | None = None
    approvals: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request,
            "requested_by": self.requested_by,
            "status": self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "final_output": self.final_output,
            "error": self.error,
            "approvals": list(self.approvals),
            "trace": list(self.trace),
            "history": list(self.history),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
