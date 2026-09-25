"""Spezial-Agenten (Marketing, Social, Video, Creative, Research, Coding, Routine).

Alle Spezialisten teilen dieselbe Schnittstelle: ``handle(AgentRequest) ->
AgentResponse``. Ihr Verhalten unterscheidet sich ueber Konfiguration
(``config/agents.yaml``), System-Prompt (``prompts/<id>.md``) und Modellstufe.
Fuer spezielles Verhalten kann eine Unterklasse ``build_prompt`` oder
``postprocess`` ueberschreiben und in ``SPECIALIST_CLASSES`` registriert werden.
"""

from __future__ import annotations

from ..core.errors import AgentExecutionError
from ..core.models import AgentRequest, AgentResponse
from .base import BaseAgent, extract_actions

MIN_CONTENT_CHARS = 20


class SpecialistAgent(BaseAgent):
    def build_prompt(self, req: AgentRequest) -> str:
        parts = [req.brand_context, f"## Urspruenglicher Nutzer-Auftrag\n{req.original_request}"]
        if req.upstream_results:
            parts.append("## Ergebnisse anderer Agenten (als Grundlage nutzen)")
            for agent_id, content in req.upstream_results.items():
                parts.append(f"### {agent_id}\n{content}")
        if req.revision_feedback:
            parts.append("## QA-Rueckmeldung - bitte ueberarbeiten\n"
                         + "\n".join(f"- {f}" for f in req.revision_feedback))
        parts.append(f"## Auftrag\n{req.instruction}")
        return "\n\n".join(p for p in parts if p)

    def postprocess(self, response: AgentResponse) -> AgentResponse:
        return response

    def handle(self, req: AgentRequest) -> AgentResponse:
        self.log.info("Bearbeite Teilaufgabe (Versuch %d)", req.attempt,
                      extra={"job_id": req.job_id, "step_id": req.step_id, "agent": self.id,
                             "event": "agent_start"})
        llm_response = self.call_llm(self.build_prompt(req), purpose="work")
        content, actions, warnings = extract_actions(llm_response.text, proposed_by=self.id)
        if len(content.strip()) < MIN_CONTENT_CHARS:
            raise AgentExecutionError(f"{self.id}: Antwort leer oder zu kurz")
        return self.postprocess(AgentResponse(
            agent_id=self.id,
            step_id=req.step_id,
            content=content,
            model=llm_response.model,
            proposed_actions=actions,
            metadata={"usage": llm_response.usage, "parse_warnings": warnings, "attempt": req.attempt},
        ))


class VideoAgent(SpecialistAgent):
    """Video-Agent. Spaeterer Andockpunkt fuer die Bild-zu-Video-KI (CineMotion,
    ``app/core/orchestrator.generate_scene``). Rendering wird nur als Aktion
    ``generate_video`` vorgeschlagen und braucht eine Freigabe."""

    SUPPORTED_CAMERA_STYLES = ("parallax_dolly", "parallax_orbit", "ken_burns", "vertigo", "drift")

    def postprocess(self, response: AgentResponse) -> AgentResponse:
        for action in response.proposed_actions:
            if action.action == "generate_video":
                style = action.params.get("style")
                if style and style not in self.SUPPORTED_CAMERA_STYLES:
                    response.metadata.setdefault("parse_warnings", []).append(
                        f"Unbekannter Kamera-Stil '{style}'"
                    )
        return response


SPECIALIST_CLASSES: dict[str, type[SpecialistAgent]] = {
    "video": VideoAgent,
}
