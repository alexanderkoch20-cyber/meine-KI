"""Zentrale Brand-Knowledge: Laden, Pruefen, als Prompt-Kontext aufbereiten."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import BrandKnowledgeError

#: Pflichtbereiche der Brand-Struktur (Schluessel -> Anzeigename).
BRAND_SECTIONS: dict[str, str] = {
    "brand_name": "Brandname",
    "mission": "Mission",
    "products": "Produkte",
    "services": "Dienstleistungen",
    "target_audience": "Zielgruppe",
    "positioning": "Positionierung",
    "tonality": "Tonalitaet",
    "brand_values": "Markenwerte",
    "design_rules": "Designregeln",
    "no_go_rules": "No-Go-Regeln",
    "goals": "Ziele",
    "competitors": "Wettbewerber",
    "existing_content": "Bestehende Inhalte",
}


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_is_filled(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_is_filled(v) for v in value)
    return True


@dataclass
class BrandKnowledge:
    data: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None

    # -- Laden ---------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "BrandKnowledge":
        path = Path(path)
        if not path.exists():
            raise BrandKnowledgeError(f"Brand-Datei nicht gefunden: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise BrandKnowledgeError(f"Brand-Datei ist kein gueltiges YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise BrandKnowledgeError("Brand-Datei muss ein YAML-Mapping sein")
        unknown = set(data) - set(BRAND_SECTIONS)
        if unknown:
            raise BrandKnowledgeError(
                f"Unbekannte Bereiche in der Brand-Datei: {sorted(unknown)}. "
                f"Erlaubt: {sorted(BRAND_SECTIONS)}"
            )
        nogo = data.get("no_go_rules") or {}
        if not isinstance(nogo, dict):
            raise BrandKnowledgeError("no_go_rules muss 'forbidden_words' und 'rules' enthalten")
        return cls(data=data, source=path)

    # -- Abfragen ------------------------------------------------------------

    @property
    def name(self) -> str:
        return str(self.data.get("brand_name") or "").strip()

    @property
    def forbidden_words(self) -> list[str]:
        nogo = self.data.get("no_go_rules") or {}
        return [str(w).strip() for w in (nogo.get("forbidden_words") or []) if str(w).strip()]

    def missing_sections(self) -> list[str]:
        return [key for key in BRAND_SECTIONS if not _is_filled(self.data.get(key))]

    def completeness(self) -> float:
        total = len(BRAND_SECTIONS)
        return (total - len(self.missing_sections())) / total

    # -- Prompt-Kontext ------------------------------------------------------

    def to_prompt_context(self) -> str:
        """Kompakter, nur befuellte Bereiche enthaltender Kontext fuer Agenten."""
        filled = {k: self.data[k] for k in BRAND_SECTIONS if _is_filled(self.data.get(k))}
        if not filled:
            return (
                "## Brand-Kontext\n"
                "Es sind noch keine Brand-Informationen hinterlegt. Arbeite markenneutral "
                "und kennzeichne Annahmen."
            )
        lines = ["## Brand-Kontext"]
        for key, value in filled.items():
            lines.append(f"### {BRAND_SECTIONS[key]}")
            lines.append(yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip())
        missing = self.missing_sections()
        if missing:
            lines.append(
                "### Noch nicht hinterlegt\n" + ", ".join(BRAND_SECTIONS[m] for m in missing)
            )
        return "\n".join(lines)
