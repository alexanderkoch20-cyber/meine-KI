"""Master-Agent (Opus): zerlegt Auftraege, verteilt sie, fuehrt Ergebnisse zusammen.

Planung in zwei Stufen:
1. **LLM-Planung**: Das Modell liefert einen JSON-Plan. Er wird streng
   validiert (nur bekannte Spezialisten, gueltige Abhaengigkeiten, keine
   Zyklen, maximale Schrittzahl).
2. **Regelbasiertes Fallback-Routing**: Ist die LLM-Antwort unbrauchbar (oder
   laeuft der Mock-Client), wird per Stichwort-Matching geplant. So ist das
   System auch komplett offline funktionsfaehig und deterministisch testbar.
"""

from __future__ import annotations

import re

from ..core.errors import AgentSystemError, PlanningError
from ..core.models import Job, Plan, PlanStep, StepStatus
from .base import BaseAgent, parse_json_object

#: Reihenfolge fuer regelbasierte Plaene: Grundlagen zuerst, Umsetzung danach.
CANONICAL_ORDER = ["research", "marketing", "creative", "social", "video", "coding", "routine"]
#: Ergebnisse dieser Agenten sind Grundlage fuer spaetere Schritte.
FOUNDATION_AGENTS = {"research", "marketing", "creative"}

PLAN_FORMAT = """Antworte ausschliesslich mit JSON in diesem Format:
{
  "rationale": "kurze Begruendung",
  "steps": [
    {"key": "s1", "agent": "<agent_id>", "instruction": "konkrete Teilaufgabe", "depends_on": []},
    {"key": "s2", "agent": "<agent_id>", "instruction": "...", "depends_on": ["s1"]}
  ]
}"""


class MasterAgent(BaseAgent):
    # ------------------------------------------------------------------ Plan

    def agent_catalog(self) -> str:
        lines = []
        for spec in self.ctx.config.specialists().values():
            lines.append(f"- {spec.id} ({spec.name}): {', '.join(spec.responsibilities)}")
        return "\n".join(lines)

    @property
    def system_prompt(self) -> str:
        return super().system_prompt.replace("{agent_catalog}", self.agent_catalog())

    def plan(self, job: Job) -> Plan:
        prompt = (
            f"{self.ctx.brand.to_prompt_context()}\n\n"
            f"## Auftrag des Nutzers\n{job.request}\n\n{PLAN_FORMAT}"
        )
        try:
            response = self.call_llm(prompt, purpose="plan")
            plan = self._parse_llm_plan(response.text)
            self.log.info("LLM-Plan mit %d Schritten", len(plan.steps),
                          extra={"job_id": job.id, "agent": self.id, "event": "plan"})
            return plan
        except PlanningError as exc:
            self.log.info("LLM-Plan unbrauchbar (%s) -> regelbasiertes Routing", exc,
                          extra={"job_id": job.id, "agent": self.id, "event": "plan_fallback"})
        return self.rule_based_plan(job.request)

    def _parse_llm_plan(self, text: str) -> Plan:
        data = parse_json_object(text)
        if not data or not isinstance(data.get("steps"), list) or not data["steps"]:
            raise PlanningError("keine gueltige JSON-Planstruktur")

        specialists = self.ctx.config.specialists()
        max_steps = self.ctx.config.orchestration.max_steps
        if len(data["steps"]) > max_steps:
            raise PlanningError(f"zu viele Schritte ({len(data['steps'])} > {max_steps})")

        key_to_step: dict[str, PlanStep] = {}
        raw_deps: dict[str, list[str]] = {}
        for i, raw in enumerate(data["steps"]):
            if not isinstance(raw, dict):
                raise PlanningError(f"Schritt {i} ist kein Objekt")
            agent_id = raw.get("agent")
            instruction = str(raw.get("instruction") or "").strip()
            key = str(raw.get("key") or f"s{i + 1}")
            if agent_id not in specialists:
                raise PlanningError(f"unbekannter Spezial-Agent '{agent_id}'")
            if not instruction:
                raise PlanningError(f"Schritt '{key}' ohne Anweisung")
            if key in key_to_step:
                raise PlanningError(f"doppelter Schritt-Schluessel '{key}'")
            key_to_step[key] = PlanStep(agent_id=agent_id, instruction=instruction)
            raw_deps[key] = [str(d) for d in (raw.get("depends_on") or [])]

        for key, deps in raw_deps.items():
            for dep in deps:
                if dep not in key_to_step:
                    raise PlanningError(f"Schritt '{key}' haengt von unbekanntem '{dep}' ab")
            key_to_step[key].depends_on = [key_to_step[d].id for d in deps]

        plan = Plan(steps=list(key_to_step.values()), rationale=str(data.get("rationale", "")), source="llm")
        topological_order(plan)  # wirft PlanningError bei Zyklen
        return plan

    def rule_based_plan(self, request: str) -> Plan:
        text = request.lower()
        scores: dict[str, int] = {}
        for spec in self.ctx.config.specialists().values():
            score = sum(1 for kw in spec.keywords if keyword_matches(kw, text))
            if score:
                scores[spec.id] = score

        if not scores:
            fallback = self.ctx.config.orchestration.fallback_agent
            return Plan(
                steps=[PlanStep(agent_id=fallback, instruction=request)],
                rationale=f"Keine eindeutige Zuordnung -> Fallback-Agent '{fallback}'",
                source="rules",
            )

        order = CANONICAL_ORDER + sorted(set(scores) - set(CANONICAL_ORDER))
        chosen = [a for a in order if a in scores][: self.ctx.config.orchestration.max_steps]
        steps: list[PlanStep] = []
        for agent_id in chosen:
            spec = self.ctx.config.agents[agent_id]
            deps = [s.id for s in steps if s.agent_id in FOUNDATION_AGENTS]
            steps.append(PlanStep(
                agent_id=agent_id,
                instruction=f"Bearbeite aus Sicht '{spec.name}' folgenden Auftrag: {request}",
                depends_on=deps,
            ))
        return Plan(
            steps=steps,
            rationale="Stichwort-Routing: " + ", ".join(f"{a}={scores[a]}" for a in chosen),
            source="rules",
        )

    # ------------------------------------------------------------ Synthese

    def synthesize(self, job: Job, pending_approvals: list[tuple[str, str, str]]) -> str:
        """Fuehrt alle QA-geprueften Ergebnisse zu einer Antwort fuer den Nutzer zusammen.

        ``pending_approvals``: Liste aus (approval_id, action, description).
        """
        assert job.plan is not None
        done = [s for s in job.plan.steps if s.status == StepStatus.DONE and s.result]
        problems = [s for s in job.plan.steps if s.status in (StepStatus.FAILED, StepStatus.SKIPPED)]

        summary = ""
        if done:
            material = "\n\n".join(f"### {s.agent_id}\n{s.result.content}" for s in done)
            try:
                resp = self.call_llm(
                    f"## Auftrag\n{job.request}\n\n## Gepruefte Teilergebnisse\n{material}\n\n"
                    "Schreibe eine kurze Management-Zusammenfassung (max. 5 Saetze) "
                    "und die naechsten Schritte.",
                    purpose="synthesize",
                )
                summary = resp.text.strip()
            except AgentSystemError as exc:
                self.log.warning("Zusammenfassung nicht moeglich: %s", exc,
                                 extra={"job_id": job.id, "agent": self.id})

        out = [f"# Ergebnis zu: {job.request}"]
        if summary:
            out.append(f"## Zusammenfassung\n{summary}")
        for s in done:
            name = self.ctx.config.agents[s.agent_id].name
            out.append(f"## {name}\n{s.result.content}")
        if problems:
            out.append("## Nicht erledigt")
            for s in problems:
                out.append(f"- {s.agent_id}: {s.status.value} - {s.error or 'ohne Angabe'}")
        if pending_approvals:
            out.append("## Wartet auf deine Freigabe (nichts wurde ausgefuehrt)")
            for apr_id, action, desc in pending_approvals:
                out.append(f"- `{apr_id}` **{action}**: {desc}")
        return "\n\n".join(out)


def keyword_matches(keyword: str, text: str) -> bool:
    """Ganzwort-Treffer; ``wort*`` trifft jeden Wortanfang (z.B. Plural, Komposita)."""
    if keyword.endswith("*"):
        pattern = r"(?<!\w)" + re.escape(keyword[:-1])
    else:
        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
    return re.search(pattern, text) is not None


def topological_order(plan: Plan) -> list[PlanStep]:
    by_id = {s.id: s for s in plan.steps}
    visited: dict[str, int] = {}  # 1 = in Bearbeitung, 2 = fertig
    order: list[PlanStep] = []

    def visit(step: PlanStep) -> None:
        state = visited.get(step.id)
        if state == 2:
            return
        if state == 1:
            raise PlanningError("Zyklische Abhaengigkeit im Plan")
        visited[step.id] = 1
        for dep in step.depends_on:
            if dep not in by_id:
                raise PlanningError(f"Unbekannte Abhaengigkeit '{dep}'")
            visit(by_id[dep])
        visited[step.id] = 2
        order.append(step)

    for s in plan.steps:
        visit(s)
    return order
