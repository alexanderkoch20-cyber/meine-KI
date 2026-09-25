"""Berechtigungssystem und Freigabe-Verwaltung.

Zwei Ebenen:
1. **Agenten-Ebene (Least Privilege):** Ein Agent darf nur Aktionen
   vorschlagen, die in seinen ``allowed_actions`` stehen.
2. **Aktions-Ebene:** Jede Aktion hat eine Policy (allow / require_approval /
   deny). Unbekannte Aktionen -> ``default_policy`` (require_approval).

Freigaben werden ausschliesslich ueber ``ApprovalStore.decide()`` erteilt -
das ist die einzige Stelle, an der der Nutzer eingreift. Es gibt in dieser
Ausbaustufe KEINE Executor fuer externe Aktionen: Auch freigegebene Aktionen
werden nur protokolliert (Dry-Run).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import SystemConfig
from .errors import AgentSystemError
from .logging_setup import get_logger
from .models import ApprovalRequest, ProposedAction, utcnow

log = get_logger("permissions")


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionResult:
    decision: Decision
    reason: str


class PermissionPolicy:
    def __init__(self, config: SystemConfig):
        self._config = config

    def check(self, agent_id: str, action: str) -> PermissionResult:
        agent = self._config.agents.get(agent_id)
        if agent is None:
            return PermissionResult(Decision.DENY, f"Unbekannter Agent '{agent_id}'")

        policy = self._config.action_policies.get(action)
        if policy == Decision.DENY.value:
            return PermissionResult(Decision.DENY, f"Aktion '{action}' ist grundsaetzlich verboten")

        if action not in agent.allowed_actions:
            return PermissionResult(
                Decision.DENY,
                f"Agent '{agent_id}' ist nicht berechtigt, '{action}' vorzuschlagen",
            )

        if policy is None:
            policy = self._config.default_policy
            reason = f"Unbekannte Aktion '{action}' -> Standard-Policy '{policy}'"
        else:
            reason = self._config.action_descriptions.get(action) or action
        return PermissionResult(Decision(policy), reason)


class ApprovalStore:
    """Speichert Freigabe-Anfragen; optional persistent als JSON-Datei."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items: dict[str, ApprovalRequest] = {}
        if self._path and self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._items = {d["id"]: ApprovalRequest.from_dict(d) for d in raw}

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([a.to_dict() for a in self._items.values()], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def request(self, job_id: str, step_id: str, action: ProposedAction, reason: str) -> ApprovalRequest:
        with self._lock:
            apr = ApprovalRequest(job_id=job_id, step_id=step_id, action=action, reason=reason)
            self._items[apr.id] = apr
            self._save()
        log.info("Freigabe angefordert: %s (%s)", action.action, apr.id,
                 extra={"job_id": job_id, "step_id": step_id, "event": "approval_requested"})
        return apr

    def get(self, approval_id: str) -> ApprovalRequest:
        try:
            return self._items[approval_id]
        except KeyError:
            raise AgentSystemError(f"Freigabe '{approval_id}' nicht gefunden") from None

    def pending(self, job_id: str | None = None) -> list[ApprovalRequest]:
        return [a for a in self._items.values()
                if a.status == "pending" and (job_id is None or a.job_id == job_id)]

    def all(self) -> list[ApprovalRequest]:
        return list(self._items.values())

    def decide(self, approval_id: str, approve: bool, decided_by: str = "user") -> ApprovalRequest:
        """Einzige Stelle, an der eine Freigabe erteilt/abgelehnt wird."""
        with self._lock:
            apr = self.get(approval_id)
            if apr.status != "pending":
                raise AgentSystemError(f"Freigabe '{approval_id}' wurde bereits entschieden ({apr.status})")
            apr.status = "approved" if approve else "rejected"
            apr.decided_by = decided_by
            apr.decided_at = utcnow()
            self._save()
        log.info("Freigabe %s: %s -> %s (Dry-Run, keine Ausfuehrung)", apr.id, apr.action.action, apr.status,
                 extra={"job_id": apr.job_id, "event": "approval_decided"})
        return apr
