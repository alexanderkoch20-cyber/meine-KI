"""Message-Bus: der einzige Kommunikationsweg zwischen Agenten.

Agenten rufen sich nie gegenseitig direkt auf. Der Orchestrator stellt jede
Nachricht ueber ``MessageBus.send()`` zu; der Bus
  1. prueft, ob der Empfaenger registriert ist,
  2. schwaerzt Secrets im Payload,
  3. protokolliert die Nachricht im Trace des Jobs (Nachvollziehbarkeit),
  4. ruft den Handler des Empfaengers auf.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .errors import UnknownAgentError
from .logging_setup import get_logger
from .models import AgentMessage, Job
from .secrets import redact

log = get_logger("bus")

Handler = Callable[[AgentMessage], Any]


def _summarize(payload: dict[str, Any], limit: int = 300) -> str:
    text = redact(json.dumps(payload, ensure_ascii=False, default=str))
    return text if len(text) <= limit else text[:limit] + "..."


class MessageBus:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, agent_id: str, handler: Handler) -> None:
        self._handlers[agent_id] = handler

    @property
    def recipients(self) -> list[str]:
        return sorted(self._handlers)

    def record(self, job: Job, message: AgentMessage) -> None:
        """Nachricht nur protokollieren (z.B. Antworten, finale Ergebnisse)."""
        job.trace.append({
            "id": message.id,
            "at": message.created_at,
            "from": message.sender,
            "to": message.recipient,
            "type": message.type.value,
            "step_id": message.step_id,
            "payload": _summarize(message.payload),
        })
        log.debug("%s -> %s [%s]", message.sender, message.recipient, message.type.value,
                  extra={"job_id": job.id, "step_id": message.step_id, "event": "message"})

    def send(self, job: Job, message: AgentMessage) -> Any:
        handler = self._handlers.get(message.recipient)
        if handler is None:
            raise UnknownAgentError(f"Kein Agent '{message.recipient}' am Bus registriert")
        self.record(job, message)
        return handler(message)
