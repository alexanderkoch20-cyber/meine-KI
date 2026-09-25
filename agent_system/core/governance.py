"""Owner-Governance: Akteure, OwnerApproval-Gate und Audit-Log.

Kernidee
--------
- Es gibt drei Arten von Akteuren: den **Owner** (Mensch), das **System**
  (Orchestrator-Laufzeit) und **Agenten**. Agenten erhalten nie ein
  Owner-Objekt; sie liefern ausschliesslich Text/Vorschlaege.
- Das ``OwnerApprovalGate`` ist die EINZIGE Stelle, an der Auftraege und
  Aktionen freigegeben werden. Es prueft, dass der Akteur der konfigurierte
  Owner ist, und speichert jede Freigabe als eigenen Datensatz - gebunden an
  Auftrag, Vorlagerunde, Plan-Fingerabdruck und Konfigurations-Fingerabdruck.
- Vor jeder Ausfuehrung fragt der Orchestrator das Gate
  (``assert_may_execute``). Ein Auftrag, dessen Status nur "APPROVED"
  behauptet (z.B. manipulierte Job-Datei), aber keinen passenden
  Freigabe-Datensatz hat, wird NICHT ausgefuehrt.
- Jede Freigabe, Ablehnung, jeder Start/Stopp und jeder Regelverstoss landet
  im ``AuditLog`` (append-only JSONL mit Hash-Kette: nachtraegliche
  Aenderungen sind mit ``verify()`` erkennbar).

Bedrohungsmodell (ehrlich): Das Gate schuetzt vor Fehlverhalten der
Agenten-Logik und der LLM-Ausgaben (Agenten koennen nur Text liefern). Es
ist keine Sandbox gegen beliebigen Python-Code im selben Prozess.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import GovernanceViolationError, OwnerApprovalRequiredError
from .logging_setup import get_logger
from .models import ApprovalRequest, Job, TaskStatus, utcnow
from .secrets import redact

if TYPE_CHECKING:  # pragma: no cover
    from .config import SystemConfig
    from .permissions import ApprovalStore

log = get_logger("governance")


# ---------------------------------------------------------------------------
# Akteure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    id: str
    kind: str  # "owner" | "system" | "agent"

    @property
    def is_owner(self) -> bool:
        return self.kind == "owner"

    @property
    def is_system(self) -> bool:
        return self.kind == "system"

    @classmethod
    def agent(cls, agent_id: str) -> "Actor":
        return cls(agent_id, "agent")


SYSTEM = Actor("orchestrator", "system")


def owner_session(config: "SystemConfig") -> Actor:
    """Owner-Akteur. Wird nur von der Owner-Schnittstelle (CLI) erzeugt und
    nie an Agenten weitergegeben."""
    return Actor(config.governance.owner_id, "owner")


# ---------------------------------------------------------------------------
# Audit-Log
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only Audit-Log mit Hash-Kette (tamper-evident)."""

    GENESIS = "0" * 64

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._entries.append(json.loads(line))

    @staticmethod
    def _hash(entry: dict[str, Any]) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def record(self, event: str, actor: Actor, job_id: str | None = None, **details: Any) -> dict[str, Any]:
        with self._lock:
            prev = self._entries[-1]["hash"] if self._entries else self.GENESIS
            entry = {
                "seq": len(self._entries) + 1,
                "at": utcnow(),
                "event": event,
                "actor": actor.id,
                "actor_kind": actor.kind,
                "job_id": job_id,
                "details": json.loads(redact(json.dumps(details, ensure_ascii=False, default=str))),
                "prev_hash": prev,
            }
            entry["hash"] = self._hash(entry)
            self._entries.append(entry)
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("AUDIT %s by %s", event, actor.id, extra={"job_id": job_id, "event": f"audit:{event}"})
        return entry

    def entries(self, job_id: str | None = None, event: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self._entries
                if (job_id is None or e["job_id"] == job_id) and (event is None or e["event"] == event)]

    def verify(self) -> bool:
        """Prueft die Hash-Kette (bei Persistenz direkt aus der Datei)."""
        entries = self._entries
        if self._path and self._path.exists():
            entries = [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
        prev = self.GENESIS
        for e in entries:
            if e.get("prev_hash") != prev or self._hash(e) != e.get("hash"):
                return False
            prev = e["hash"]
        return True


# ---------------------------------------------------------------------------
# OwnerApproval-Gate
# ---------------------------------------------------------------------------


class OwnerApprovalGate:
    def __init__(self, config: "SystemConfig", audit: AuditLog, path: Path | str | None = None):
        self.config = config
        self.audit = audit
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        if self._path and self._path.exists():
            self._records = json.loads(self._path.read_text(encoding="utf-8"))

    # -- intern ---------------------------------------------------------------

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _key(job: Job) -> str:
        return f"{job.id}#{job.approval_round}"

    def require_owner(self, actor: Actor, attempted: str, job_id: str | None = None) -> None:
        """Wirft und protokolliert, wenn ``actor`` nicht der Owner ist."""
        if actor.is_owner and actor.id == self.config.governance.owner_id:
            return
        self.audit.record("governance_violation", actor, job_id, attempted=attempted)
        raise GovernanceViolationError(
            f"'{actor.id}' ({actor.kind}) darf '{attempted}' nicht ausfuehren - nur der Owner"
        )

    # -- Auftraege --------------------------------------------------------------

    def approve_task(self, job: Job, actor: Actor, expected_plan: str | None = None) -> None:
        from .jobs import transition  # zirkulaeren Import vermeiden

        self.require_owner(actor, "approve_task", job.id)
        if job.status != TaskStatus.WAITING_FOR_OWNER:
            raise OwnerApprovalRequiredError(
                f"Auftrag {job.id} steht auf '{job.status.value}' - freigeben geht nur aus 'waiting_for_owner'"
            )
        if job.open_questions:
            raise OwnerApprovalRequiredError(
                "Offene Rueckfragen - bitte zuerst beantworten (clarify): " + " | ".join(job.open_questions)
            )
        if not job.plan or not job.plan.steps:
            raise OwnerApprovalRequiredError(f"Auftrag {job.id} hat keinen ausfuehrbaren Plan")
        plan_fp = job.plan.fingerprint()
        if expected_plan and expected_plan != plan_fp:
            raise OwnerApprovalRequiredError(
                f"Plan hat sich geaendert (erwartet {expected_plan}, aktuell {plan_fp}) - bitte neu pruefen"
            )
        with self._lock:
            self._records[self._key(job)] = {
                "job_id": job.id,
                "round": job.approval_round,
                "plan_fingerprint": plan_fp,
                "config_fingerprint": self.config.fingerprint,
                "approved_by": actor.id,
                "approved_at": utcnow(),
                "steps": [s.id for s in job.plan.steps],
            }
            self._save()
        transition(job, TaskStatus.APPROVED, actor, f"Owner-Freigabe fuer Plan {plan_fp}")
        self.audit.record("task_approved", actor, job.id, round=job.approval_round, plan_fingerprint=plan_fp,
                          steps=[f"{s.agent_id}: {s.instruction[:80]}" for s in job.plan.steps])

    def cancel_task(self, job: Job, actor: Actor, reason: str = "") -> None:
        from .jobs import transition

        self.require_owner(actor, "cancel_task", job.id)
        transition(job, TaskStatus.CANCELLED, actor, reason or "vom Owner abgebrochen")
        self.audit.record("task_cancelled", actor, job.id, reason=reason)

    def assert_may_execute(self, job: Job) -> None:
        """Letzte Kontrolle vor jeder Ausfuehrung. Nur APPROVED + gueltiger
        Freigabe-Datensatz des Owners fuer genau diesen Plan & diese Konfiguration."""
        from .jobs import transition

        def deny(reason: str) -> None:
            self.audit.record("execution_denied", SYSTEM, job.id, reason=reason, status=job.status.value)
            raise OwnerApprovalRequiredError(f"Ausfuehrung von {job.id} verweigert: {reason}")

        if job.status != TaskStatus.APPROVED:
            deny(f"Status ist '{job.status.value}', nicht 'approved'")
        record = self._records.get(self._key(job))
        if not record or record.get("approved_by") != self.config.governance.owner_id:
            deny("kein Freigabe-Datensatz des Owners fuer diese Runde")
        if not job.plan or record["plan_fingerprint"] != job.plan.fingerprint():
            deny("Plan weicht vom freigegebenen Plan ab")
        if record["config_fingerprint"] != self.config.fingerprint:
            transition(job, TaskStatus.WAITING_FOR_OWNER, SYSTEM,
                       "Konfiguration seit der Freigabe geaendert - erneute Freigabe noetig")
            deny("Konfiguration seit der Freigabe geaendert")

    # -- Aktionen ---------------------------------------------------------------

    def decide_action(self, store: "ApprovalStore", approval_id: str, approve: bool,
                      actor: Actor) -> ApprovalRequest:
        self.require_owner(actor, "approve_action" if approve else "reject_action")
        apr = store.decide(approval_id, approve, actor)
        self.audit.record("action_approved" if approve else "action_rejected", actor, apr.job_id,
                          approval_id=apr.id, action=apr.action.action, description=apr.action.description,
                          executed=False, note="Dry-Run: es existiert kein Executor, nichts wurde ausgefuehrt")
        return apr
