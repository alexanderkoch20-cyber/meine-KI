"""Master-Agent (Opus): analysiert Auftraege, legt dem Owner einen Plan vor,
fuehrt nach Freigabe die Ergebnisse zusammen.

Owner-Regel: Der Master PLANT nur. Er startet nichts - ausgefuehrt wird ein
Plan erst, nachdem der Owner ihn freigegeben hat (siehe OwnerApprovalGate).
Ist der Auftrag unklar, liefert er Rueckfragen statt eines Plans; es gibt
keinen "Fallback-Agenten", der unklare Auftraege trotzdem bearbeitet.

Planung in zwei Stufen:
1. **LLM-Planung**: Das Modell liefert einen JSON-Plan. Er wird streng
   validiert (nur bekannte Spezialisten, gueltige Abhaengigkeiten, keine
   Zyklen, maximale Schrittzahl).
2. **Regelbasiertes Fallback-Routing**: Ist die LLM-Antwort unbrauchbar (oder
   laeuft der Mock-Client), wird per Stichwort-Matching geplant. Trifft
   kein Stichwort, stellt der Master eine Rueckfrage. So ist das
   System auch komplett offline funktionsfaehig und deterministisch testbar.
"""

from __future__ import annotations

from ..core.errors import AgentSystemError, PlanningError
from ..core.legal import format_review
from ..core.models import Job, Plan, PlanStep, StepStatus, TaskDraft
from ..core.textmatch import keyword_matches, normalize
from .base import BaseAgent, parse_json_object

#: Reihenfolge fuer regelbasierte Plaene: Grundlagen zuerst, Umsetzung danach.
CANONICAL_ORDER = ["research", "marketing", "creative", "social", "video", "coding", "routine"]
#: Ergebnisse dieser Agenten sind Grundlage fuer spaetere Schritte.
FOUNDATION_AGENTS = {"research", "marketing", "creative"}

#: Entscheidungen, die im Auftrag anklingen koennen, aber AUSSCHLIESSLICH der Owner trifft.
#: Der Master markiert sie im Task-Draft - er trifft sie nie selbst.
OWNER_DECISION_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Preise festlegen oder aendern", ("preis*", "rabatt*", "streichpreis*")),
    ("Produkte/Produktlinien freigeben oder beschliessen",
     ("produkt*", "kollektion*", "produktlinie*", "launch*", "markteinfuehrung*", "first edition")),
    ("Drops starten/beenden, Limitierung und Stueckzahlen festlegen",
     ("drop*", "limitiert*", "limited", "stueckzahl*")),
    ("Kampagnen freigeben oder starten", ("kampagne*",)),
    ("Inhalte veroeffentlichen/posten",
     ("veroeffentlich*", "post", "posts", "posten", "reel*", "instagram*", "tiktok*", "facebook*", "live")),
    ("Werbebudget/Ausgaben freigeben", ("budget*", "werbebudget*", "ads", "anzeige*", "ausgabe*")),
    ("Kunden kontaktieren", ("kunde*", "newsletter*", "e-mail*", "mailing*")),
)

#: Feste Grenzen jedes Task-Drafts (spiegeln die bestehende Governance - aendern sie nicht).
DRAFT_CONSTRAINTS: tuple[str, ...] = (
    "Ergebnis sind ausschliesslich interne Entwuerfe/Konzepte zur Pruefung durch den Owner.",
    "Agenten veroeffentlichen, posten, kontaktieren keine Kunden, geben kein Geld aus und geben keine "
    "Preise, Produkte, Drops oder Kampagnen frei.",
    "Externe Aktionen nur als Vorschlag: Legal & Compliance, QA und Owner-Freigabe erforderlich "
    "(derzeit Dry-Run, nichts wird ausgefuehrt).",
    "Kein Start, keine Erweiterung und keine Wiederholung ohne ausdrueckliche Owner-Freigabe.",
    "Nicht angegebene Brand-Informationen werden nicht erfunden.",
)

UNCLEAR_QUESTION = (
    "Ich kann den Auftrag keinem Spezial-Agenten eindeutig zuordnen. Was genau soll "
    "entstehen (z.B. Marketingstrategie, Social-Media-Posts, Video-Storyboard, "
    "Bildkonzept, Recherche, Code, Datenformatierung)?"
)

PLAN_FORMAT = """Plane NUR, was ausdruecklich beauftragt ist - keine Zusatzschritte.
Ist der Auftrag unklar, plane NICHTS und antworte mit {"questions": ["..."]}.
Sonst antworte ausschliesslich mit JSON in diesem Format:
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
            f"{self.ctx.brand.to_prompt_context(self.id)}\n\n"
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
        questions = [str(q) for q in (data or {}).get("questions") or [] if str(q).strip()]
        if questions:
            return Plan(steps=[], rationale=str(data.get("rationale", "Auftrag unklar")),
                        source="llm", questions=questions)
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
        text = normalize(request)
        scores: dict[str, int] = {}
        for spec in self.ctx.config.specialists().values():
            score = sum(1 for kw in spec.keywords if keyword_matches(kw, text))
            if score:
                scores[spec.id] = score

        if not scores:
            return Plan(steps=[], rationale="Auftrag unklar - Rueckfrage an den Owner",
                        source="rules", questions=[UNCLEAR_QUESTION])

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

    # ------------------------------------------------------------ Task-Draft

    def draft_task(self, job: Job) -> TaskDraft:
        """Strukturierter Auftragsentwurf fuer den Owner - deterministisch, ohne Modell.

        Der Draft startet nichts und gibt nichts frei; er macht sichtbar, was nach
        einer Owner-Freigabe an welche Spezialagenten ginge und was nur der Owner entscheidet.
        """
        assert job.plan is not None
        agents = self.ctx.config.agents
        by_id = {s.id: s for s in job.plan.steps}
        specialists = [{
            "step_id": s.id,
            "agent_id": s.agent_id,
            "agent_name": agents[s.agent_id].name,
            "model_tier": agents[s.agent_id].model_tier,
            "instruction": s.instruction,
            "depends_on": [by_id[d].agent_id for d in s.depends_on],
        } for s in job.plan.steps]

        text = normalize("\n".join([job.request, *job.owner_notes, *(s.instruction for s in job.plan.steps)]))
        decisions = [f"{label} - entscheidet ausschliesslich der Owner"
                     for label, keywords in OWNER_DECISION_TOPICS
                     if any(keyword_matches(k, text) for k in keywords)]

        brand = self.ctx.brand
        constraints = list(DRAFT_CONSTRAINTS)
        if brand.version:
            constraints.append(f"Brand-Kontext: {brand.name or 'Brand'} Brand Knowledge Base {brand.info.label} "
                               f"(Hash {brand.content_hash[:12]}) - nur lesend.")
            constraints.append(f"Brand-No-Gos beachten ({len(brand.forbidden_phrases)} verbotene Aussagen und "
                               "weitere Regeln laut Brand Knowledge Base).")
        else:
            constraints.append("Keine freigegebene Brand-Version - markenneutral arbeiten.")

        names = ", ".join(sp["agent_name"] for sp in specialists) or "noch keine (Rueckfragen offen)"
        return TaskDraft(
            job_id=job.id,
            owner_request=job.request,
            objective=job.request,
            specialists=specialists,
            deliverable=f"Interne Entwuerfe zur Owner-Pruefung von: {names}. Keine Veroeffentlichung.",
            brand_version=brand.version or None,
            brand_hash=brand.content_hash if brand.version else None,
            jurisdictions=list(job.jurisdictions),
            legal_status=(job.legal_precheck.status.value if job.legal_precheck else "legal_review_required"),
            constraints=constraints,
            owner_decisions_required=decisions,
            open_questions=list(job.open_questions),
            plan_fingerprint=job.plan.fingerprint(),
        )

    # ------------------------------------------------------------ Bericht

    def synthesize(self, job: Job, pending_approvals: list[tuple[str, str, str]]) -> str:
        """Bericht an den Owner: gepruefte Ergebnisse, Stopp-Grund, offene Vorschlaege.

        ``pending_approvals``: Liste aus (approval_id, action, description).
        Eine KI-Zusammenfassung gibt es nur fuer vollstaendig erledigte
        Auftraege - nach einem Stopp wird keine weitere Arbeit angestossen.
        """
        assert job.plan is not None
        done = [s for s in job.plan.steps if s.status == StepStatus.DONE and s.result]
        not_done = [s for s in job.plan.steps if s.status != StepStatus.DONE]

        summary = ""
        if done and not job.stop_reason:
            material = "\n\n".join(f"### {s.agent_id}\n{s.result.content}" for s in done)
            try:
                resp = self.call_llm(
                    f"## Auftrag\n{job.request}\n\n## Gepruefte Teilergebnisse\n{material}\n\n"
                    "Schreibe eine kurze Management-Zusammenfassung (max. 5 Saetze). "
                    "Naechste Schritte nur als Empfehlung formulieren.",
                    purpose="synthesize",
                )
                summary = resp.text.strip()
            except AgentSystemError as exc:
                self.log.warning("Zusammenfassung nicht moeglich: %s", exc,
                                 extra={"job_id": job.id, "agent": self.id})

        out = [f"# Ergebnis zu: {job.request}"]
        if job.stop_reason:
            out.append(f"## GESTOPPT - Meldung an den Owner\n{job.stop_reason}")
        if summary:
            out.append(f"## Zusammenfassung\n{summary}")
        for s in done:
            name = self.ctx.config.agents[s.agent_id].name
            out.append(f"## {name}\n{s.result.content}")
        if not_done:
            out.append("## Nicht erledigt")
            for s in not_done:
                out.append(f"- {s.agent_id}: {s.status.value} - {s.error or 'nicht ausgefuehrt'}")
        reviews = [("Vorpruefung Auftrag", job.legal_precheck)] if job.legal_precheck else []
        reviews += [(f"Schritt {s.agent_id}", s.legal_review) for s in job.plan.steps if s.legal_review]
        if reviews:
            out.append(f"## Legal & Compliance (Gesamtstatus: `{job.legal_status.value}`)")
            out.extend(format_review(review, title) for title, review in reviews)
        if job.recommendations:
            out.append("## Empfehlungen (nichts davon wurde ausgefuehrt)")
            out.extend(f"- {r}" for r in job.recommendations)
        if pending_approvals:
            out.append("## Vorschlaege der Agenten - warten auf deine Freigabe (nichts ausgefuehrt)")
            for apr_id, action, desc in pending_approvals:
                out.append(f"- `{apr_id}` **{action}**: {desc}")
        return "\n\n".join(out)


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
