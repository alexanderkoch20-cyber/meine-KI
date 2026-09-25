"""QA-Agent: prueft jedes Ergebnis, bevor es zum Master zurueckgeht.

Die Pruefung ist zweistufig:
1. **Deterministische Regeln** (laufen immer, auch offline):
   - leeres/zu kurzes Ergebnis
   - Secrets/API-Schluessel im Ergebnis  -> BLOCKED
   - verbotene Woerter aus den Brand-No-Go-Regeln -> NEEDS_REVISION
   - unlesbarer actions-Block
   - Berechtigungspruefung jeder vorgeschlagenen Aktion
     (deny -> blockiert, require_approval -> Freigabe durch Nutzer)
2. **LLM-Review** (optional): Das Modell kann zusaetzliche inhaltliche
   Probleme melden. Das Ergebnis kann das Urteil nur VERSCHAERFEN, nie
   entschaerfen - die Regeln aus Stufe 1 lassen sich nicht aushebeln.
"""

from __future__ import annotations

import re

from ..core.errors import AgentSystemError
from ..core.models import AgentRequest, AgentResponse, QAIssue, QAReport, QAVerdict, Severity
from ..core.permissions import Decision, PermissionPolicy
from ..core.secrets import contains_secret
from .base import BaseAgent, parse_json_object

_VERDICT_RANK = {QAVerdict.APPROVED: 0, QAVerdict.NEEDS_REVISION: 1, QAVerdict.BLOCKED: 2}
MIN_CONTENT_CHARS = 20


class QAAgent(BaseAgent):
    def __init__(self, definition, ctx):
        super().__init__(definition, ctx)
        self.policy = PermissionPolicy(ctx.config)

    def review(self, req: AgentRequest, resp: AgentResponse) -> QAReport:
        issues: list[QAIssue] = []
        report = QAReport(step_id=resp.step_id, verdict=QAVerdict.APPROVED, issues=issues)

        # --- Inhalt ---
        if len(resp.content.strip()) < MIN_CONTENT_CHARS:
            issues.append(QAIssue("content", "Ergebnis ist leer oder zu kurz", Severity.ERROR))

        if contains_secret(resp.content):
            issues.append(QAIssue("secrets", "Ergebnis enthaelt einen API-Schluessel oder ein Passwort",
                                  Severity.CRITICAL))

        lower = resp.content.lower()
        for word in self.ctx.brand.forbidden_words:
            if re.search(r"(?<!\w)" + re.escape(word.lower()) + r"(?!\w)", lower):
                issues.append(QAIssue("brand_no_go", f"Verbotenes Wort laut Brand-Regeln: '{word}'",
                                      Severity.ERROR))

        for warning in resp.metadata.get("parse_warnings", []):
            issues.append(QAIssue("format", warning, Severity.WARNING))

        # --- Aktionen ---
        for action in resp.proposed_actions:
            result = self.policy.check(resp.agent_id, action.action)
            if result.decision == Decision.DENY:
                report.denied_actions.append(action)
                severity = Severity.CRITICAL if action.action == "reveal_secret" else Severity.ERROR
                issues.append(QAIssue("permissions", f"Aktion blockiert: {result.reason}", severity))
            elif result.decision == Decision.REQUIRE_APPROVAL:
                report.approval_required.append(action)
                issues.append(QAIssue("permissions",
                                      f"Aktion '{action.action}' braucht deine Freigabe: {result.reason}",
                                      Severity.INFO))

        if self.ctx.brand.completeness() < 0.5:
            issues.append(QAIssue("brand_context",
                                  "Brand-Wissen ist noch unvollstaendig - Ergebnis ist eher allgemein",
                                  Severity.INFO))

        report.verdict = self._verdict_from(issues)
        # Aktionen blockieren den Inhalt nicht - ausser beim Versuch, Secrets auszugeben.
        report.verdict = self._merge(report.verdict, self._llm_review(req, resp, issues))
        return report

    # ------------------------------------------------------------------

    @staticmethod
    def _verdict_from(issues: list[QAIssue]) -> QAVerdict:
        severities = {i.severity for i in issues}
        # Aktions-Verbote allein fuehren nicht zur Ueberarbeitung des Inhalts
        content_errors = {i.severity for i in issues if i.check != "permissions"}
        if Severity.CRITICAL in severities:
            return QAVerdict.BLOCKED
        if Severity.ERROR in content_errors:
            return QAVerdict.NEEDS_REVISION
        return QAVerdict.APPROVED

    @staticmethod
    def _merge(a: QAVerdict, b: QAVerdict | None) -> QAVerdict:
        if b is None:
            return a
        return a if _VERDICT_RANK[a] >= _VERDICT_RANK[b] else b

    def _llm_review(self, req: AgentRequest, resp: AgentResponse, issues: list[QAIssue]) -> QAVerdict | None:
        prompt = (
            f"{req.brand_context}\n\n## Teilaufgabe\n{req.instruction}\n\n"
            f"## Ergebnis von {resp.agent_id}\n{resp.content}"
        )
        try:
            data = parse_json_object(self.call_llm(prompt, purpose="qa").text)
        except AgentSystemError as exc:
            issues.append(QAIssue("llm_review", f"LLM-Review nicht moeglich: {exc}", Severity.INFO))
            return None
        if not data:
            return None  # z.B. Mock-Modus -> nur Regelpruefung
        try:
            verdict = QAVerdict(data.get("verdict"))
        except ValueError:
            return None
        for text in data.get("issues") or []:
            sev = Severity.ERROR if verdict != QAVerdict.APPROVED else Severity.INFO
            issues.append(QAIssue("llm_review", str(text), sev))
        return verdict
