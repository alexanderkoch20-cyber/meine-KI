"""Legal & Compliance: Wissensbasis, Erkennung und deterministische Bewertung.

Grundsaetze (fest im Code):
- Es wird NIE ein Rechtsraum angenommen. Ohne Jurisdiktion -> Rueckfrage bzw.
  menschliche Pruefung. Deutsches Recht wird nicht auf andere Laender uebertragen.
- Jede Feststellung dokumentiert Rechtsraum, Regel, Quelle, Fassungsdatum,
  Verifikation der Quelle, Pruefdatum und Unsicherheiten.
- Fehlende oder unsichere Quellenlage -> HUMAN_LEGAL_REVIEW_REQUIRED.
- Kritische Risiken -> LEGAL_REVIEW_BLOCKED.
- Das Ergebnis ist NIE eine Garantie fuer Rechtmaessigkeit (siehe LEGAL_DISCLAIMER).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .errors import ConfigError
from .models import LEGAL_SEVERITY, LegalFinding, LegalReview, LegalStatus
from .rules import LEGAL_MANDATORY_ACTIONS
from .textmatch import keyword_matches, normalize

RISK_LEVELS = ("medium", "high", "critical")
_CODE = re.compile(r"^[A-Z]{2,5}$")
_CODES_IN_TEXT = re.compile(r"(?<![A-Za-z])(DE|AT|CH|EU|UK|GB|US|USA)(?![A-Za-z])")
_CODE_ALIASES = {"GB": "UK", "USA": "US"}

#: Formulierungen, mit denen Rechtssicherheit behauptet wuerde. Der Legal-Agent
#: darf so etwas nie ausgeben.
_GUARANTEE_PHRASES = (
    "garantiert legal", "garantiert rechtmaessig", "garantiert straffrei", "100% legal",
    "100 % legal", "rechtssicher", "straffrei", "voellig legal", "absolut legal",
    "keine rechtlichen risiken", "kein rechtliches risiko", "rechtlich unbedenklich",
    "abmahnsicher", "ist legal",
)


@dataclass(frozen=True)
class Topic:
    id: str
    area: str
    risk: str
    label: str
    triggers: tuple[str, ...]
    recommendation: str


@dataclass(frozen=True)
class LegalRule:
    id: str
    topic: str
    jurisdiction: str
    title: str
    reference: str
    url: str | None
    version_date: str | None
    verified_at: str | None
    verified_by: str | None
    notes: str | None = None

    @property
    def missing_documentation(self) -> list[str]:
        missing = []
        if not self.url:
            missing.append("URL der Quelle fehlt")
        if not self.version_date:
            missing.append("Veroeffentlichungs-/Fassungsdatum der Quelle nicht dokumentiert")
        if not self.verified_at:
            missing.append("Quelle nicht von einem Menschen verifiziert")
        return missing


@dataclass(frozen=True)
class LegalKnowledge:
    jurisdictions: Mapping[str, Mapping[str, Any]]
    aliases: Mapping[str, str]
    topics: Mapping[str, Topic]
    rules: tuple[LegalRule, ...]
    action_topics: Mapping[str, tuple[str, ...]]

    # -- Rechtsraeume ---------------------------------------------------------

    def is_known(self, code: str) -> bool:
        return code in self.jurisdictions

    def chain(self, code: str) -> list[str]:
        """Rechtsraum + uebergeordnete Rechtsraeume (DE -> [DE, EU])."""
        out, current = [], code
        while current and current in self.jurisdictions and current not in out:
            out.append(current)
            current = self.jurisdictions[current].get("parent")
        return out

    def detect_jurisdictions(self, text: str) -> list[str]:
        found = {_CODE_ALIASES.get(m, m) for m in _CODES_IN_TEXT.findall(text or "")}
        norm = normalize(text)
        for alias, code in self.aliases.items():
            if keyword_matches(alias, norm):
                found.add(code)
        return sorted(found)

    # -- Themen / Regeln ---------------------------------------------------------

    def detect_topics(self, text: str) -> dict[str, str]:
        """Thema -> Beleg (das ausloesende Stichwort)."""
        norm = normalize(text)
        hits: dict[str, str] = {}
        for topic in self.topics.values():
            for trig in topic.triggers:
                if keyword_matches(trig, norm):
                    hits[topic.id] = f"Stichwort '{trig.rstrip('*')}'"
                    break
        return hits

    def rules_for(self, topic: str, code: str) -> list[LegalRule]:
        """Regeln fuer ein Thema im Rechtsraum - die spezifischste (nationale) zuerst."""
        chain = self.chain(code)
        rules = [r for r in self.rules if r.topic == topic and r.jurisdiction in chain]
        return sorted(rules, key=lambda r: chain.index(r.jurisdiction))


def normalize_jurisdictions(codes: Iterable[str]) -> list[str]:
    out = []
    for raw in codes or []:
        code = _CODE_ALIASES.get(str(raw).strip().upper(), str(raw).strip().upper())
        if not _CODE.match(code):
            raise ConfigError(f"Ungueltiger Rechtsraum-Code '{raw}' (erwartet z.B. DE, AT, CH, EU, UK, US)")
        if code not in out:
            out.append(code)
    return out


def load_legal_knowledge(raw: dict[str, Any], known_actions: Iterable[str]) -> LegalKnowledge:
    jurisdictions = {}
    for code, spec in (raw.get("jurisdictions") or {}).items():
        if not _CODE.match(str(code)):
            raise ConfigError(f"legal.yaml: ungueltiger Rechtsraum-Code '{code}'")
        jurisdictions[str(code)] = MappingProxyType(dict(spec or {}))
    for code, spec in jurisdictions.items():
        parent = spec.get("parent")
        if parent and parent not in jurisdictions:
            raise ConfigError(f"legal.yaml: Rechtsraum '{code}' hat unbekannten parent '{parent}'")

    topics = {}
    for tid, t in (raw.get("topics") or {}).items():
        t = t or {}
        if t.get("risk") not in RISK_LEVELS:
            raise ConfigError(f"legal.yaml: Thema '{tid}' hat ungueltiges Risiko '{t.get('risk')}'")
        topics[tid] = Topic(id=tid, area=str(t.get("area", "")), risk=t["risk"], label=str(t.get("label", tid)),
                            triggers=tuple(str(x) for x in (t.get("triggers") or [])),
                            recommendation=str(t.get("recommendation", "")))

    rules = []
    for r in raw.get("rules") or []:
        if r.get("topic") not in topics:
            raise ConfigError(f"legal.yaml: Regel '{r.get('id')}' verweist auf unbekanntes Thema '{r.get('topic')}'")
        if r.get("jurisdiction") not in jurisdictions:
            raise ConfigError(f"legal.yaml: Regel '{r.get('id')}' hat unbekannten Rechtsraum '{r.get('jurisdiction')}'")
        rules.append(LegalRule(
            id=str(r["id"]), topic=r["topic"], jurisdiction=r["jurisdiction"], title=str(r.get("title", "")),
            reference=str(r.get("reference", "")), url=r.get("url"),
            version_date=str(r["version_date"]) if r.get("version_date") else None,
            verified_at=str(r["verified_at"]) if r.get("verified_at") else None,
            verified_by=r.get("verified_by"), notes=r.get("notes"),
        ))

    known_actions = set(known_actions)
    action_topics = {}
    for action, tids in (raw.get("action_topics") or {}).items():
        if action not in known_actions:
            raise ConfigError(f"legal.yaml: unbekannte Aktion '{action}' in action_topics")
        for tid in tids or []:
            if tid not in topics:
                raise ConfigError(f"legal.yaml: Aktion '{action}' verweist auf unbekanntes Thema '{tid}'")
        action_topics[action] = tuple(tids or ())
    missing = LEGAL_MANDATORY_ACTIONS - set(action_topics)
    if missing:
        raise ConfigError(f"legal.yaml: externe Aktionen ohne Legal-Pruefung: {sorted(missing)}")

    return LegalKnowledge(
        jurisdictions=MappingProxyType(jurisdictions),
        aliases=MappingProxyType({normalize(k): str(v).upper() for k, v in (raw.get("jurisdiction_aliases") or {}).items()}),
        topics=MappingProxyType(topics),
        rules=tuple(rules),
        action_topics=MappingProxyType(action_topics),
    )


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------


def _finding(topic: Topic, status: LegalStatus, reason: str, evidence: str, jurisdiction: str | None,
             rule: LegalRule | None, uncertainties: list[str]) -> LegalFinding:
    return LegalFinding(
        topic=topic.id, area=topic.area, label=topic.label, risk=topic.risk, status=status,
        reason_code=reason, jurisdiction=jurisdiction, evidence=evidence,
        rule_id=rule.id if rule else None, rule_title=rule.title if rule else None,
        reference=rule.reference if rule else None, url=rule.url if rule else None,
        version_date=rule.version_date if rule else None,
        source_verified_at=rule.verified_at if rule else None,
        source_verified_by=rule.verified_by if rule else None,
        uncertainties=uncertainties + ([f"Hinweis Quelle: {rule.notes}"] if rule and rule.notes else []),
        recommendation=topic.recommendation,
    )


def _evaluate_topic(kb: LegalKnowledge, topic: Topic, evidence: str, jurisdictions: list[str]) -> list[LegalFinding]:
    if topic.risk == "critical":
        rule = next((r for j in jurisdictions for r in kb.rules_for(topic.id, j)), None)
        return [_finding(topic, LegalStatus.BLOCKED, "critical_risk", evidence,
                         ",".join(jurisdictions) or None, rule,
                         ["Kritisches Risiko - Ausfuehrung blockiert bis zur menschlichen Rechtspruefung."])]
    if not jurisdictions:
        return [_finding(topic, LegalStatus.HUMAN_REQUIRED, "no_jurisdiction", evidence, None, None,
                         ["Rechtsraum unbekannt - es wird kein Rechtsraum (auch nicht Deutschland) angenommen."])]

    findings = []
    for code in jurisdictions:
        if not kb.is_known(code):
            findings.append(_finding(topic, LegalStatus.HUMAN_REQUIRED, "unknown_jurisdiction", evidence, code,
                                     None, [f"Rechtsraum '{code}' ist nicht in der Wissensbasis - keine "
                                            "Einschaetzung moeglich."]))
            continue
        rules = kb.rules_for(topic.id, code)
        complete = [r for r in rules if not r.missing_documentation]
        rule = complete[0] if complete else (rules[0] if rules else None)
        if topic.risk == "high":
            findings.append(_finding(topic, LegalStatus.HUMAN_REQUIRED, "high_risk", evidence, code, rule,
                                     ["Hohes Risiko - Einzelfallpruefung durch einen Menschen erforderlich."]
                                     + (rule.missing_documentation if rule else
                                        [f"Keine Quelle im Katalog fuer {code}."])))
        elif not rules:
            findings.append(_finding(topic, LegalStatus.HUMAN_REQUIRED, "no_source", evidence, code, None,
                                     [f"Keine Quelle im Katalog fuer {code} - Regeln anderer Rechtsraeume "
                                      "(z.B. deutsches Recht) werden nicht uebertragen."]))
        elif not complete:
            findings.append(_finding(topic, LegalStatus.HUMAN_REQUIRED, "unsafe_source", evidence, code, rule,
                                     rule.missing_documentation))
        else:
            findings.append(_finding(topic, LegalStatus.PASSED, "verified_source", evidence, code, rule,
                                     ["Automatische Einschaetzung ersetzt keine Einzelfallpruefung."]))
    return findings


def evaluate(kb: LegalKnowledge, *, scope: str, subject_id: str, text: str, jurisdictions: list[str],
             actions: Iterable[str] = ()) -> LegalReview:
    """Deterministische Legal-Pruefung eines Textes (+ vorgeschlagener Aktionen)."""
    topics = kb.detect_topics(text)
    for action in actions:
        for tid in kb.action_topics.get(action, ()):
            topics.setdefault(tid, f"externe Aktion '{action}'")

    areas = sorted({t.area for t in kb.topics.values()})
    review = LegalReview(scope=scope, subject_id=subject_id, status=LegalStatus.NOT_REQUIRED,
                         jurisdictions=list(jurisdictions), checked_areas=areas)
    unknown = [j for j in jurisdictions if not kb.is_known(j)]

    if not topics:
        review.reasons.append("Keine rechtlich relevanten Merkmale erkannt - keine Legal-Pruefung erforderlich. "
                              f"Gepruefte Bereiche: {', '.join(areas)}.")
        if unknown:
            review.reasons.append(f"Hinweis: unbekannte Rechtsraeume {unknown} (derzeit ohne Relevanz).")
        return review

    for tid, evidence in topics.items():
        review.findings.extend(_evaluate_topic(kb, kb.topics[tid], evidence, jurisdictions))

    review.status = max((f.status for f in review.findings), key=LEGAL_SEVERITY.__getitem__)
    review.reasons.append("Rechtlich relevante Themen: " + ", ".join(kb.topics[t].label for t in topics) + ".")
    if unknown:
        review.reasons.append(f"Unbekannte Rechtsraeume: {', '.join(unknown)} - menschliche Pruefung noetig.")

    if not jurisdictions:
        review.questions.append(
            "Fuer welche Laender/Rechtsraeume gilt dieser Auftrag (z.B. DE, AT, CH, EU, UK, US)? "
            "Ohne Angabe wird kein Recht - auch nicht deutsches - angenommen."
        )

    if scope == "task":
        # Vorpruefung: fehlende Jurisdiktion ist eine RUECKFRAGE (nicht schon ein Stopp)
        # und ein unauffaelliger, aber relevanter Auftrag wird als REQUIRED markiert.
        others = [f for f in review.findings if f.reason_code != "no_jurisdiction"]
        if review.status == LegalStatus.PASSED:
            review.status = LegalStatus.REQUIRED
        elif review.status == LegalStatus.HUMAN_REQUIRED and not any(
                f.status != LegalStatus.PASSED for f in others):
            review.status = LegalStatus.REQUIRED
        review.reasons.append("Auftrag ist rechtlich relevant: jeder Schritt wird vor QA und Owner-Freigabe "
                              "zusaetzlich rechtlich geprueft.")
    return review


def escalate(review: LegalReview, status: LegalStatus, reason: str) -> None:
    """Pruefstatus nur VERSCHAERFEN - nie abschwaechen."""
    if LEGAL_SEVERITY[status] > LEGAL_SEVERITY[review.status]:
        review.status = status
    review.reasons.append(reason)


def remove_legal_guarantees(text: str) -> tuple[str, list[str]]:
    """Entfernt Saetze, die Rechtssicherheit/Straffreiheit behaupten."""
    removed, kept = [], []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        norm = normalize(sentence)
        if any(p in norm for p in _GUARANTEE_PHRASES):
            removed.append(sentence.strip())
        elif sentence.strip():
            kept.append(sentence.strip())
    return " ".join(kept), removed


def format_review(review: LegalReview, title: str) -> str:
    lines = [f"### {title}: `{review.status.value}`"]
    lines.append(f"- Rechtsraeume: {', '.join(review.jurisdictions) or 'UNBEKANNT (nicht angenommen)'}")
    lines.append(f"- Pruefdatum: {review.reviewed_at}")
    for reason in review.reasons:
        lines.append(f"- {reason}")
    for f in review.findings:
        src = f"{f.reference} ({f.url})" if f.reference else "keine Quelle im Katalog"
        verified = (f"verifiziert {f.source_verified_at} von {f.source_verified_by}"
                    if f.source_verified_at else "NICHT verifiziert")
        lines.append(
            f"  - [{f.status.value}] {f.label} | Rechtsraum: {f.jurisdiction or 'unbekannt'} | Risiko: {f.risk} | "
            f"Beleg: {f.evidence}\n    Regel: {f.rule_title or '-'} | Quelle: {src} | "
            f"Fassung: {f.version_date or 'nicht dokumentiert'} | Quelle {verified}\n"
            f"    Unsicherheiten: {'; '.join(f.uncertainties) or '-'}\n    Empfehlung: {f.recommendation}"
        )
    for q in review.questions:
        lines.append(f"- RUECKFRAGE: {q}")
    if review.human_review:
        h = review.human_review
        lines.append(f"- Menschliche Rechtspruefung dokumentiert: {h['reviewer']} ({h['recorded_at']}): {h['note']}")
    lines.append(f"- _{review.disclaimer}_")
    return "\n".join(lines)
