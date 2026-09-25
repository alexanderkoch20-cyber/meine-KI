"""Brand Knowledge Base: unveraenderliche Brand-Basis, Brand-Check, Agenten-Kontext.

- ``BrandKnowledge`` ist eine eingefrorene, versionierte Momentaufnahme. Alle
  Agenten eines Auftrags bekommen DIESELBE Instanz (siehe BrandContextLoader
  in ``brand_store.py``).
- Fehlende Informationen heissen ``NOT_PROVIDED`` bzw. ``UNKNOWN`` und werden
  den Agenten ausdruecklich als "nicht angegeben - nicht erfinden" genannt.
- ``check()`` erkennt fehlende Pflichtinformationen, auch je Agent.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .brand_schema import (
    NOT_APPLICABLE,
    NOT_PROVIDED,
    UNKNOWN,
    BrandSchema,
    FieldSpec,
    SectionSpec,
    ValidationResult,
    is_provided,
    is_sentinel,
    validate,
)
from .errors import BrandKnowledgeError


def normalize_brand_data(data: dict[str, Any], schema: BrandSchema) -> dict[str, Any]:
    """Alle Bereiche/Felder vorhanden; leere Werte -> NOT_PROVIDED. Meta entfernt."""
    out: dict[str, Any] = {}
    for section in schema.sections:
        sdata = data.get(section.name) or {}
        out[section.name] = {}
        for f in section.fields:
            value = sdata.get(f.name, NOT_PROVIDED)
            if value is None or value == [] or (isinstance(value, str) and not value.strip()):
                value = NOT_PROVIDED
            out[section.name][f.name] = value
    return out


def content_hash(normalized: dict[str, Any]) -> str:
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def field_status(value: Any) -> str:
    if is_sentinel(value):
        return value
    return "provided" if is_provided(value) else NOT_PROVIDED


@dataclass(frozen=True)
class BrandVersionInfo:
    version: int                  # 0 = noch keine freigegebene Version
    content_hash: str
    committed_at: str | None = None
    committed_by: str | None = None
    note: str | None = None

    @property
    def label(self) -> str:
        return f"v{self.version}" if self.version else "keine freigegebene Version"


@dataclass
class MissingItem:
    section: str
    field: str
    label: str
    status: str   # NOT_PROVIDED | UNKNOWN | INVALID

    def to_text(self) -> str:
        return f"{self.section}.{self.field} ({self.label}): {self.status}"


@dataclass
class BrandCheckReport:
    info: BrandVersionInfo
    validation: ValidationResult
    missing_required: list[MissingItem] = field(default_factory=list)
    missing_optional: list[MissingItem] = field(default_factory=list)
    sections: dict[str, tuple[int, int]] = field(default_factory=dict)   # name -> (beantwortet, gesamt)
    per_agent: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    required_completeness: float = 0.0
    overall_completeness: float = 0.0

    @property
    def ok(self) -> bool:
        return self.validation.ok and not self.missing_required

    def to_text(self, schema: BrandSchema, title: str | None = None) -> str:
        lines = [f"{title or 'Brand Knowledge Base ' + self.info.label} (Hash {self.info.content_hash[:12]})",
                 f"Pflichtinformationen: {self.required_completeness:.0%} vollstaendig | "
                 f"alle Felder: {self.overall_completeness:.0%}"]
        for err in self.validation.errors:
            lines.append(f"  FEHLER: {err}")
        for w in self.validation.warnings + self.warnings:
            lines.append(f"  Hinweis: {w}")
        lines.append("\nBereiche (beantwortet/gesamt):")
        for s in schema.sections:
            done, total = self.sections[s.name]
            lines.append(f"  [{'x' if done == total else ' '}] {s.title:<22} {done}/{total}")
        if self.missing_required:
            lines.append("\nFehlende PFLICHTinformationen:")
            lines.extend(f"  - {m.to_text()}" for m in self.missing_required)
        if self.per_agent:
            lines.append("\nFehlt je Agent (Pflichtangaben in seinen Bereichen):")
            for agent, items in self.per_agent.items():
                lines.append(f"  {agent:<10} {', '.join(items)}")
        lines.append("\nStatus: " + ("OK - alle Pflichtinformationen vorhanden" if self.ok else
                                     "UNVOLLSTAENDIG - siehe agent_system/brand/ONBOARDING.md"))
        return "\n".join(lines)


class BrandKnowledge:
    """Eingefrorene Momentaufnahme der Brand-Basis. Werte werden nur als Kopie herausgegeben."""

    def __init__(self, data: dict[str, Any], schema: BrandSchema, info: BrandVersionInfo | None = None,
                 source: Path | None = None):
        self.schema = schema
        self._data = normalize_brand_data(data, schema)
        self.content_hash = content_hash(self._data)
        self.info = info or BrandVersionInfo(version=0, content_hash=self.content_hash)
        if self.info.content_hash != self.content_hash:
            raise BrandKnowledgeError(f"Brand-Version {self.info.label}: Inhalt passt nicht zum Hash "
                                      "(Datei wurde nachtraeglich veraendert)")
        self.source = source

    # -- Erzeugen -------------------------------------------------------------

    @classmethod
    def from_data(cls, data: dict[str, Any], schema: BrandSchema, info: BrandVersionInfo | None = None,
                  source: Path | None = None) -> "BrandKnowledge":
        result = validate(data, schema)
        if not result.ok:
            raise BrandKnowledgeError("Brand-Daten ungueltig:\n  - " + "\n  - ".join(result.errors))
        return cls(data, schema, info, source)

    @classmethod
    def from_file(cls, path: Path | str, schema: BrandSchema, info: BrandVersionInfo | None = None) -> "BrandKnowledge":
        return cls.from_data(read_brand_yaml(path), schema, info, Path(path))

    @classmethod
    def empty(cls, schema: BrandSchema) -> "BrandKnowledge":
        return cls(schema.empty_template(), schema)

    # -- Lesen ------------------------------------------------------------------

    @property
    def version(self) -> int:
        return self.info.version

    def get(self, section: str, name: str) -> Any:
        return copy.deepcopy(self._data[section][name])

    def status(self, section: str, name: str) -> str:
        return field_status(self._data[section][name])

    def is_provided(self, section: str, name: str) -> bool:
        return self.status(section, name) == "provided"

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def _list(self, section: str, name: str) -> list[str]:
        value = self._data[section][name]
        return [str(v).strip() for v in value if str(v).strip()] if is_provided(value) else []

    @property
    def name(self) -> str:
        return self._data["brand_identity"]["brand_name"] if self.is_provided("brand_identity", "brand_name") else ""

    @property
    def forbidden_phrases(self) -> list[str]:
        """No-Go-Aussagen - die QA blockiert Ergebnisse, die sie enthalten."""
        return self._list("no_gos", "statements")

    #: Rueckwaertskompatibler Name
    forbidden_words = forbidden_phrases

    @property
    def avoid_phrases(self) -> list[str]:
        """Zu vermeidende Woerter der Brand Voice - die QA warnt."""
        return self._list("brand_voice", "words_to_avoid")

    @property
    def jurisdictions(self) -> list[str]:
        return self._list("legal_compliance", "jurisdictions")

    @property
    def markets(self) -> list[str]:
        return self._list("target_audiences", "markets")

    # -- Brand-Check --------------------------------------------------------------

    def _satisfied(self, f: FieldSpec, value: Any) -> bool:
        return is_provided(value) or (value == NOT_APPLICABLE and f.allow_na)

    def check(self, agent_ids: Iterable[str] = (), validation: ValidationResult | None = None,
              known_jurisdictions: Iterable[str] | None = None) -> BrandCheckReport:
        report = BrandCheckReport(info=self.info, validation=validation or ValidationResult())
        req_total = req_done = all_total = all_done = 0
        missing_by_section: dict[str, list[MissingItem]] = {}

        for s in self.schema.sections:
            sdata = self._data[s.name]
            done = 0
            for f in s.fields:
                value = sdata[f.name]
                ok = self._satisfied(f, value)
                done += ok
                if ok:
                    continue
                item = MissingItem(s.name, f.name, f.label, field_status(value))
                if f.required:
                    report.missing_required.append(item)
                    missing_by_section.setdefault(s.name, []).append(item)
                else:
                    report.missing_optional.append(item)
            report.sections[s.name] = (done, len(s.fields))
            all_total += len(s.fields)
            all_done += done
            req_fields = [f for f in s.fields if f.required]
            req_total += len(req_fields)
            req_done += sum(self._satisfied(f, sdata[f.name]) for f in req_fields)
            if s.required_any:
                req_total += 1
                if any(is_provided(sdata[n]) for n in s.required_any):
                    req_done += 1
                else:
                    labels = " oder ".join(s.field(n).label for n in s.required_any)
                    item = MissingItem(s.name, "|".join(s.required_any), f"mindestens eins: {labels}", NOT_PROVIDED)
                    report.missing_required.append(item)
                    missing_by_section.setdefault(s.name, []).append(item)

        report.required_completeness = req_done / req_total if req_total else 1.0
        report.overall_completeness = all_done / all_total if all_total else 1.0

        for agent in agent_ids:
            items = [m.label for s in self.schema.sections if s.is_relevant_for(agent)
                     for m in missing_by_section.get(s.name, [])]
            if items:
                report.per_agent[agent] = items

        # Querpruefungen
        if known_jurisdictions is not None:
            known = set(known_jurisdictions)
            for code in sorted(set(self.jurisdictions) | set(self.markets)):
                if code not in known:
                    report.warnings.append(f"Rechtsraum/Markt '{code}' ist der Legal-Wissensbasis unbekannt - "
                                           "Legal verlangt dafuer menschliche Pruefung")
        uncovered = sorted(set(self.markets) - set(self.jurisdictions))
        if uncovered and self.jurisdictions:
            report.warnings.append(f"Maerkte ohne Eintrag unter legal_compliance.jurisdictions: {uncovered}")
        return report

    def completeness(self) -> float:
        """Anteil erfuellter Pflichtinformationen (0..1)."""
        return self.check().required_completeness

    # -- Kontext fuer Agenten --------------------------------------------------------

    def to_prompt_context(self, agent_id: str | None = None) -> str:
        """Brand-Kontext fuer einen Agenten: nur seine Bereiche, Fehlendes ausdruecklich markiert."""
        header = (f"## Brand-Kontext (Brand Knowledge Base {self.info.label}, Hash {self.content_hash[:12]})\n"
                  "Nutze ausschliesslich diese Angaben. Alles, was als NICHT ANGEGEBEN markiert ist, darfst du "
                  "NICHT erfinden: arbeite markenneutral, kennzeichne Annahmen als 'Annahme' oder stoppe mit "
                  "request_owner_decision, wenn die Information fuer die Aufgabe wesentlich ist.")
        parts = [header]
        missing: list[str] = []
        for s in self.schema.sections:
            if not s.is_relevant_for(agent_id):
                continue
            lines = []
            for f in s.fields:
                value = self._data[s.name][f.name]
                status = field_status(value)
                if status == "provided":
                    lines.append(_render_field(f, value))
                elif status == NOT_APPLICABLE:
                    lines.append(f"- {f.label}: trifft nicht zu")
                else:
                    missing.append(f"{s.title} > {f.label}" + (" (Owner: unbekannt)" if status == UNKNOWN else ""))
            if lines:
                parts.append(f"### {s.title}\n" + "\n".join(lines))
        if len(parts) == 1:
            parts.append("Es sind noch keine Brand-Informationen hinterlegt.")
        if missing:
            parts.append("### NICHT ANGEGEBEN (nicht erfinden)\n" + "\n".join(f"- {m}" for m in missing))
        return "\n\n".join(parts)


def _render_field(f: FieldSpec, value: Any) -> str:
    if isinstance(value, str):
        return f"- {f.label}: {value}"
    if f.type in ("text_list", "country_list"):
        return f"- {f.label}: " + "; ".join(str(v) for v in value)
    dumped = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip()
    return f"- {f.label}:\n" + "\n".join("    " + line for line in dumped.splitlines())


def read_brand_yaml(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise BrandKnowledgeError(f"Brand-Datei nicht gefunden: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BrandKnowledgeError(f"Brand-Datei ist kein gueltiges YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BrandKnowledgeError("Brand-Datei muss ein YAML-Mapping sein")
    return data


__all__ = [
    "BrandKnowledge", "BrandCheckReport", "BrandVersionInfo", "MissingItem", "SectionSpec",
    "normalize_brand_data", "content_hash", "read_brand_yaml",
]
