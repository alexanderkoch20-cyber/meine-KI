"""Basisklasse und gemeinsame Hilfen fuer alle Agenten."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..core.brand import BrandKnowledge
from ..core.config import AgentDefinition, SystemConfig
from ..core.llm import LLMClient, LLMRequest, LLMResponse
from ..core.logging_setup import get_logger
from ..core.models import ProposedAction

_ACTIONS_BLOCK = re.compile(r"```actions\s*\n(.*?)```", re.DOTALL)
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class AgentContext:
    """Alles, was ein Agent zur Laufzeit braucht - von aussen injiziert."""

    config: SystemConfig
    llm: LLMClient
    brand: BrandKnowledge


class BaseAgent:
    def __init__(self, definition: AgentDefinition, ctx: AgentContext):
        self.definition = definition
        self.ctx = ctx
        self.log = get_logger(f"agent.{definition.id}")
        self._system_prompt: str | None = None

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = self.ctx.config.load_prompt(self.id)
        return self._system_prompt

    def call_llm(self, prompt: str, purpose: str = "work", system: str | None = None) -> LLMResponse:
        tier = self.ctx.config.tier_for(self.id)
        return self.ctx.llm.complete(LLMRequest(
            agent_id=self.id,
            tier=tier,
            system=system if system is not None else self.system_prompt,
            prompt=prompt,
            purpose=purpose,
        ))


def extract_actions(text: str, proposed_by: str) -> tuple[str, list[ProposedAction], list[str]]:
    """Trennt den ```actions```-Block vom Inhalt.

    Rueckgabe: (bereinigter Inhalt, Aktionen, Parse-Warnungen).
    """
    actions: list[ProposedAction] = []
    warnings: list[str] = []
    for block in _ACTIONS_BLOCK.findall(text):
        try:
            items = json.loads(block)
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                raise ValueError("actions-Block muss eine JSON-Liste sein")
            for item in items:
                if not isinstance(item, dict) or not item.get("action"):
                    raise ValueError(f"ungueltiger Aktionseintrag: {item!r}")
                actions.append(ProposedAction(
                    action=str(item["action"]),
                    description=str(item.get("description", "")),
                    params=item.get("params") or {},
                    proposed_by=proposed_by,
                ))
        except (ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"actions-Block nicht lesbar: {exc}")
    clean = _ACTIONS_BLOCK.sub("", text).strip()
    return clean, actions, warnings


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Findet das erste JSON-Objekt in einer LLM-Antwort (tolerant ggue. Prosa/Codefences)."""
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
