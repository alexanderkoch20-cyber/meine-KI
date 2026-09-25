"""LLM-Clients hinter einer gemeinsamen Schnittstelle.

- ``MockLLMClient``: komplett offline, deterministisch, kostenlos. Standard.
- ``AnthropicLLMClient``: Platzhalter fuer die echte Claude-API. Er ist
  bewusst NICHT implementiert und wirft ``ExternalServiceNotApprovedError``,
  bis der Nutzer die Anbindung ausdruecklich freigibt.

Jeder Agent spricht nur mit ``LLMClient.complete()`` - ein Wechsel des
Providers aendert keinen Agenten-Code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .config import ModelTier, SystemConfig
from .errors import BudgetExceededError, ConfigError, ExternalServiceNotApprovedError


@dataclass
class LLMRequest:
    agent_id: str
    tier: ModelTier
    system: str
    prompt: str
    #: z.B. "plan", "work", "qa", "synthesize" - hilft Mock/Tests beim Antworten
    purpose: str = "work"


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


Responder = Callable[[LLMRequest], str]


class MockLLMClient:
    """Deterministischer Offline-Client.

    Standardverhalten: liefert einen strukturierten Platzhalter-Entwurf.
    Fuer Planungs- und QA-Anfragen liefert er bewusst KEIN JSON, damit der
    Master auf das regelbasierte Routing und die QA auf ihre regelbasierten
    Pruefungen zurueckfaellt. Tests koennen per ``responder`` oder
    ``scripted`` gezielt Antworten vorgeben.
    """

    name = "mock"

    def __init__(self, responder: Responder | None = None, scripted: dict[str, list[str]] | None = None):
        self._responder = responder
        self._scripted = {k: list(v) for k, v in (scripted or {}).items()}
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        key = f"{request.agent_id}:{request.purpose}"
        if self._scripted.get(key):
            text = self._scripted[key].pop(0)
        elif self._responder is not None:
            text = self._responder(request)
        else:
            text = self._default(request)
        return LLMResponse(text=text, model=request.tier.model_id,
                           usage={"input_tokens": len(request.prompt) // 4, "output_tokens": len(text) // 4})

    @staticmethod
    def _default(request: LLMRequest) -> str:
        if request.purpose in ("plan", "qa"):
            return "MOCK: keine strukturierte Antwort"
        if request.purpose == "synthesize":
            return ("Mock-Modus: Die Teilergebnisse unten sind Platzhalter. Naechster Schritt: "
                    "Brand-Wissen eintragen und einen echten LLM-Provider freigeben.")
        digest = hashlib.sha1(request.prompt.encode("utf-8")).hexdigest()[:8]
        first_line = request.prompt.rsplit("## Auftrag", 1)[-1].strip().splitlines()
        task = first_line[0] if first_line else ""
        return (
            f"# Entwurf von {request.agent_id} ({request.tier.name}/{request.tier.model_id})\n\n"
            f"**Teilaufgabe:** {task}\n\n"
            "- Punkt 1: Offline-Platzhalter (Mock-Modus, kein echtes Modell aufgerufen)\n"
            "- Punkt 2: Sobald ein echter LLM-Provider freigegeben ist, steht hier das Ergebnis\n"
            f"\n_ref:{digest}_"
        )


class AnthropicLLMClient:
    """Platzhalter fuer die Claude-API (kostenpflichtig, extern).

    Wird erst nach ausdruecklicher Freigabe durch den Nutzer implementiert.
    Bis dahin wird jeder Aufruf mit einem nicht wiederholbaren Fehler
    abgelehnt - es entsteht garantiert kein Netzwerkverkehr und keine Kosten.
    """

    name = "anthropic"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ExternalServiceNotApprovedError(
            "Die Anthropic-API ist noch nicht freigegeben. Bitte zuerst ausdruecklich "
            "zustimmen - dann wird der Client implementiert (siehe README, Abschnitt "
            "'Ersten Agenten produktiv machen')."
        )


class BudgetedLLMClient:
    """Wrapper, der die Anzahl der LLM-Aufrufe pro Job begrenzt."""

    def __init__(self, inner: LLMClient, max_calls: int):
        self.inner = inner
        self.name = inner.name
        self.max_calls = max_calls
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.calls >= self.max_calls:
            raise BudgetExceededError(f"LLM-Budget erschoepft ({self.max_calls} Aufrufe pro Job)")
        self.calls += 1
        return self.inner.complete(request)


def create_llm_client(config: SystemConfig) -> LLMClient:
    if config.provider == "mock":
        return MockLLMClient()
    if config.provider == "anthropic":
        return AnthropicLLMClient()
    raise ConfigError(f"Unbekannter LLM-Provider '{config.provider}'")
