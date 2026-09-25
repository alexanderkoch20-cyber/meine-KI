"""Task-/Job-System: Speicherung und Zustandsautomat fuer Auftraege."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .errors import InvalidStateTransitionError, JobNotFoundError
from .logging_setup import get_logger
from .models import Job, JobStatus, utcnow

log = get_logger("jobs")

#: Erlaubte Zustandsuebergaenge. Alles andere ist ein Programmierfehler.
TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.PLANNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.PLANNING: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.QA_REVIEW, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.QA_REVIEW: {
        JobStatus.RUNNING,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.COMPLETED,
        JobStatus.PARTIALLY_COMPLETED,
        JobStatus.FAILED,
    },
    JobStatus.AWAITING_APPROVAL: {JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),
    JobStatus.PARTIALLY_COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

TERMINAL = {s for s, nxt in TRANSITIONS.items() if not nxt}


def transition(job: Job, new_status: JobStatus, note: str = "") -> None:
    if new_status not in TRANSITIONS[job.status]:
        raise InvalidStateTransitionError(
            f"Job {job.id}: Uebergang {job.status.value} -> {new_status.value} nicht erlaubt"
        )
    job.history.append({"from": job.status.value, "to": new_status.value, "at": utcnow(), "note": note})
    job.status = new_status
    job.updated_at = utcnow()
    log.info("Job-Status %s%s", new_status.value, f" ({note})" if note else "",
             extra={"job_id": job.id, "event": "job_status"})


class JobStore:
    """In-Memory-Store mit optionaler JSON-Persistenz (ein File pro Job)."""

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
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(f"Job '{job_id}' nicht gefunden") from None

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def save(self, job: Job) -> None:
        if not self._dir:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{job.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load_snapshot(self, job_id: str) -> dict:
        """Liest einen gespeicherten Job (z.B. aus einem frueheren CLI-Lauf)."""
        if job_id in self._jobs:
            return self._jobs[job_id].to_dict()
        if self._dir:
            path = self._dir / f"{job_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise JobNotFoundError(f"Job '{job_id}' nicht gefunden")

    def complete_snapshot(self, job_id: str, note: str) -> bool:
        """Schliesst einen gespeicherten Job aus einem frueheren Prozess ab
        (awaiting_approval -> completed). Gibt False zurueck, wenn nicht moeglich."""
        if not self._dir:
            return False
        path = self._dir / f"{job_id}.json"
        if not path.exists():
            return False
        snap = json.loads(path.read_text(encoding="utf-8"))
        if snap["status"] != JobStatus.AWAITING_APPROVAL.value:
            return False
        now = utcnow()
        snap["history"].append({"from": snap["status"], "to": JobStatus.COMPLETED.value, "at": now, "note": note})
        snap["status"] = JobStatus.COMPLETED.value
        snap["updated_at"] = now
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True

    def list_snapshots(self) -> list[dict]:
        if not self._dir or not self._dir.exists():
            return [j.to_dict() for j in self.list()]
        snaps = [json.loads(p.read_text(encoding="utf-8")) for p in self._dir.glob("job_*.json")]
        return sorted(snaps, key=lambda d: d["created_at"])
