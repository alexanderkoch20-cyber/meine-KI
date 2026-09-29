"""Task-/Job-System: Speicherung und Zustandsautomat mit Owner-Regel.

    DRAFT ──▶ WAITING_FOR_OWNER ──(nur Owner)──▶ APPROVED ──▶ RUNNING ──▶ COMPLETED
                    ▲                                              │  └──▶ FAILED
                    └───────────── Entscheidung noetig ◀───────────┘
    CANCELLED: nur durch den Owner (aus DRAFT, WAITING_FOR_OWNER, APPROVED)

Jeder Uebergang nennt den Akteur. Owner-Uebergaenge (APPROVED, CANCELLED)
lehnt der Automat fuer jeden anderen Akteur ab. Agenten duerfen den Status
eines Auftrags ueberhaupt nicht veraendern.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .errors import GovernanceViolationError, InvalidStateTransitionError, JobNotFoundError
from .governance import Actor
from .logging_setup import get_logger
from .models import Job, TaskStatus, utcnow

log = get_logger("jobs")

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.WAITING_FOR_OWNER, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.WAITING_FOR_OWNER: {TaskStatus.APPROVED, TaskStatus.CANCELLED},
    # APPROVED -> WAITING_FOR_OWNER: Freigabe ungueltig geworden (z.B. Konfiguration geaendert)
    TaskStatus.APPROVED: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.WAITING_FOR_OWNER},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.WAITING_FOR_OWNER},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

#: Diese Zielzustaende darf ausschliesslich der Owner setzen.
OWNER_ONLY_TARGETS = frozenset({TaskStatus.APPROVED, TaskStatus.CANCELLED})
TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


def transition(job: Job, new_status: TaskStatus, actor: Actor, note: str = "") -> None:
    if new_status not in TRANSITIONS[job.status]:
        raise InvalidStateTransitionError(
            f"Job {job.id}: Uebergang {job.status.value} -> {new_status.value} nicht erlaubt"
        )
    if new_status in OWNER_ONLY_TARGETS:
        if not actor.is_owner:
            raise GovernanceViolationError(
                f"'{actor.id}' ({actor.kind}) darf '{new_status.value}' nicht setzen - nur der Owner"
            )
    elif not actor.is_system:
        raise GovernanceViolationError(
            f"'{actor.id}' ({actor.kind}) darf den Auftragsstatus nicht auf '{new_status.value}' setzen"
        )
    if new_status == TaskStatus.WAITING_FOR_OWNER:
        job.approval_round += 1
    job.history.append({"from": job.status.value, "to": new_status.value, "at": utcnow(),
                        "by": actor.id, "note": note})
    job.status = new_status
    job.updated_at = utcnow()
    log.info("Job-Status %s durch %s%s", new_status.value, actor.id, f" ({note})" if note else "",
             extra={"job_id": job.id, "event": "job_status"})


class JobStore:
    """In-Memory-Store mit JSON-Persistenz (ein File pro Job).

    Jobs werden bei Bedarf von der Platte geladen - so kann der Owner einen
    Auftrag in einem CLI-Aufruf pruefen und in einem spaeteren freigeben.
    """

    def __init__(self, directory: Path | str | None = None):
        self._dir = Path(directory) if directory else None
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
        self.save(job)
        return job

    def get(self, job_id: str) -> Job:
        if job_id in self._jobs:
            return self._jobs[job_id]
        if self._dir:
            path = self._dir / f"{job_id}.json"
            if path.exists():
                job = Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self._jobs[job.id] = job
                return job
        raise JobNotFoundError(f"Job '{job_id}' nicht gefunden")

    def list(self) -> list[Job]:
        if self._dir and self._dir.exists():
            for path in self._dir.glob("job_*.json"):
                self.get(path.stem)
        return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def save(self, job: Job) -> None:
        if not self._dir:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{job.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
