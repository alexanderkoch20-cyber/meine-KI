"""Chief Legal & Compliance Agent - besonders geschuetzte Kontrollinstanz.

Er sitzt in der Pipeline zwischen Spezial-Agent und QA:

    Owner -> Master -> Spezial-Agent -> LEGAL & COMPLIANCE -> QA -> Owner-Freigabe -> Ausfuehrung

Zwei Pruefungen:
- ``review_task``: Vorpruefung des Owner-Auftrags (vor der Owner-Freigabe).
  Markiert rechtlich relevante Auftraege als LEGAL_REVIEW_REQUIRED, fragt nach
  fehlenden Rechtsraeumen, blockiert oder verlangt menschliche Pruefung.
- ``review_step``: Pruefung jedes Agenten-Ergebnisses inkl. vorgeschlagener
  externer Aktionen. Auch "keine Pruefung noetig" wird dokumentiert.

Er darf: markieren, blockieren, Risiken melden, Quellen/Begruendungen liefern,
menschliche Rechtspruefung verlangen. Er darf NICHT: freigeben, handeln,
Vertraege schliessen, rechtsverbindlich entscheiden oder Rechtssicherheit
behaupten. Technisch: Sein Ergebnis ist ausschliesslich ein ``LegalReview``;
jeder Versuch, ueber seine Modell-Antwort Aktionen auszuloesen, wird verworfen,
als Regelverstoss gemeldet und fuehrt zu menschlicher Pruefung.

Das Modell (LLM) kann die regelbasierte Einschaetzung nur VERSCHAERFEN.
"""

from __future__ import annotations

from ..core.errors import AgentSystemError
from ..core.legal import escalate, evaluate, remove_legal_guarantees
from ..core.models import AgentResponse, Job, LegalReview, LegalStatus, PlanStep
from .base import BaseAgent, extract_actions, parse_json_object

_LLM_STATUS = {
    "legal_review_passed": LegalStatus.PASSED,
    "human_legal_review_required": LegalStatus.HUMAN_REQUIRED,
    "legal_review_blocked": LegalStatus.BLOCKED,
}
_REQUIRED_FINDING_FIELDS = ("jurisdiction", "rule", "source", "source_date")


class LegalAgent(BaseAgent):
    # ------------------------------------------------------------ Pruefungen

    def review_task(self, job: Job) -> LegalReview:
        text = "\n".join([job.request, *job.owner_notes])
        review = evaluate(self.ctx.config.legal, scope="task", subject_id=job.id, text=text,
                          jurisdictions=list(job.jurisdictions))
        self._model_review(review, f"## Auftrag des Owners\n{text}")
        return review

    def review_step(self, job: Job, step: PlanStep, response: AgentResponse) -> LegalReview:
        actions = [a.action for a in response.proposed_actions]
        text = "\n".join([response.content, *(f"{a.action}: {a.description}" for a in response.proposed_actions)])
        review = evaluate(self.ctx.config.legal, scope="step", subject_id=step.id, text=text,
                          jurisdictions=list(job.jurisdictions), actions=actions)
        # Behauptungen des Spezialisten ueber einen Legal-Status werden ignoriert.
        if any(k in response.metadata for k in ("legal_status", "legal_review")):
            escalate(review, LegalStatus.HUMAN_REQUIRED,
                     f"Agent '{response.agent_id}' hat versucht, selbst einen Legal-Status zu setzen - ignoriert.")
            review.agent_violations.append(f"{response.agent_id}: set_legal_status")
        self._model_review(review, f"## Ergebnis von {response.agent_id}\n{text}")
        return review

    # ------------------------------------------------------------ Modell

    def _model_review(self, review: LegalReview, material: str) -> None:
        """Optionale Modell-Pruefung. Kann nur verschaerfen; Fehler -> Hinweis, keine Lockerung."""
        prompt = (
            f"## Rechtsraeume\n{', '.join(review.jurisdictions) or 'UNBEKANNT - nicht annehmen'}\n\n"
            f"## Regelbasierte Vorpruefung\nStatus: {review.status.value}\n"
            + "\n".join(f"- {f.label} ({f.jurisdiction}): {f.status.value}" for f in review.findings)
            + f"\n\n{material}"
        )
        try:
            raw = self.call_llm(prompt, purpose="legal").text
        except AgentSystemError as exc:
            review.reasons.append(f"Modell-Pruefung nicht moeglich ({exc.code}) - nur regelbasierte Pruefung.")
            if review.status not in (LegalStatus.NOT_REQUIRED,):
                escalate(review, LegalStatus.HUMAN_REQUIRED,
                         "Unvollstaendige Pruefung bei rechtlich relevantem Inhalt -> menschliche Pruefung.")
            return

        # 1) Der Legal-Agent darf keine Aktionen ausloesen - auch nicht ueber seine Antwort.
        _, actions, _ = extract_actions(raw, proposed_by=self.id)
        for action in actions:
            review.agent_violations.append(f"{self.id}: {action.action}")
        if actions:
            escalate(review, LegalStatus.HUMAN_REQUIRED,
                     "Legal-Agent hat versucht, Aktionen auszuloesen - verworfen, menschliche Pruefung noetig.")

        data = parse_json_object(raw)
        if not data:
            return  # z.B. Mock-Modus: nur regelbasierte Pruefung

        # 2) Keine Garantie-Aussagen.
        summary, removed = remove_legal_guarantees(str(data.get("summary", "")))
        if removed:
            escalate(review, LegalStatus.HUMAN_REQUIRED,
                     "Unzulaessige Aussage zur Rechtssicherheit aus der Modell-Antwort entfernt.")
        if summary:
            review.reasons.append(f"Modell-Einschaetzung: {summary}")

        # 3) Status nur verschaerfen.
        status = _LLM_STATUS.get(str(data.get("status")))
        if status:
            escalate(review, status, f"Modell-Pruefung: {status.value}")

        # 4) Modell-Feststellungen ohne vollstaendige Quelle -> menschliche Pruefung.
        for f in data.get("findings") or []:
            if not isinstance(f, dict):
                continue
            missing = [k for k in _REQUIRED_FINDING_FIELDS if not f.get(k)]
            text = f"Modell-Hinweis ({f.get('area', '?')}, {f.get('jurisdiction', '?')}): {f.get('rule', '')}"
            if missing:
                escalate(review, LegalStatus.HUMAN_REQUIRED,
                         f"{text} - Quellenlage unvollstaendig ({', '.join(missing)}).")
            else:
                review.reasons.append(f"{text} | Quelle: {f['source']} ({f['source_date']})"
                                      + (f" | Unsicherheit: {f['uncertainty']}" if f.get("uncertainty") else ""))
