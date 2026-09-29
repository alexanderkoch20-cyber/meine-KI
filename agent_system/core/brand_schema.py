"""Schema der Brand Knowledge Base: Laden, Validieren, leere Vorlage erzeugen.

Keine externe Abhaengigkeit (kein jsonschema) - das Schema steht in
``agent_system/brand/schema.yaml`` und wird hier interpretiert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import BrandKnowledgeError

NOT_PROVIDED = "NOT_PROVIDED"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
SENTINELS = (NOT_PROVIDED, UNKNOWN, NOT_APPLICABLE)

FIELD_TYPES = {"text", "text_list", "object_list", "enum", "country_list"}
_CODE = re.compile(r"^[A-Z]{2,5}$")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    label: str
    required: bool = False
    allow_na: bool = True
    question: str = ""
    options: tuple[str, ...] = ()
    item_fields: tuple["FieldSpec", ...] = ()


@dataclass(frozen=True)
class SectionSpec:
    name: str
    title: str
    intro: str
    relevant_for: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    required_any: tuple[str, ...] = ()

    def is_relevant_for(self, agent_id: str | None) -> bool:
        return agent_id is None or "all" in self.relevant_for or agent_id in self.relevant_for

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)


@dataclass(frozen=True)
class BrandSchema:
    version: int
    sections: tuple[SectionSpec, ...]

    def section(self, name: str) -> SectionSpec:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(name)

    def empty_template(self) -> dict[str, Any]:
        """Alle Felder auf NOT_PROVIDED - es wird nichts erfunden."""
        return {s.name: {f.name: NOT_PROVIDED for f in s.fields} for s in self.sections}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _field_spec(name: str, raw: dict[str, Any], where: str) -> FieldSpec:
    ftype = raw.get("type")
    if ftype not in FIELD_TYPES:
        raise BrandKnowledgeError(f"schema.yaml: {where}.{name} hat unbekannten Typ '{ftype}'")
    if ftype == "enum" and not raw.get("options"):
        raise BrandKnowledgeError(f"schema.yaml: {where}.{name} (enum) braucht 'options'")
    items = tuple(_field_spec(n, r or {}, f"{where}.{name}") for n, r in (raw.get("item_fields") or {}).items())
    if ftype == "object_list" and not items:
        raise BrandKnowledgeError(f"schema.yaml: {where}.{name} (object_list) braucht 'item_fields'")
    return FieldSpec(
        name=name, type=ftype, label=str(raw.get("label", name)), required=bool(raw.get("required", False)),
        allow_na=bool(raw.get("allow_na", True)), question=str(raw.get("question", "")),
        options=tuple(str(o) for o in raw.get("options") or ()), item_fields=items,
    )


def load_schema(path: Path | str) -> BrandSchema:
    path = Path(path)
    if not path.exists():
        raise BrandKnowledgeError(f"Brand-Schema nicht gefunden: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sections = []
    for sname, s in (raw.get("sections") or {}).items():
        s = s or {}
        fields = tuple(_field_spec(fn, fr or {}, sname) for fn, fr in (s.get("fields") or {}).items())
        if not fields:
            raise BrandKnowledgeError(f"schema.yaml: Bereich '{sname}' ohne Felder")
        for f in fields:
            if not f.question:
                raise BrandKnowledgeError(f"schema.yaml: {sname}.{f.name} ohne Onboarding-Frage")
        req_any = tuple(s.get("required_any") or ())
        unknown = set(req_any) - {f.name for f in fields}
        if unknown:
            raise BrandKnowledgeError(f"schema.yaml: required_any in '{sname}' nennt unbekannte Felder {unknown}")
        sections.append(SectionSpec(name=sname, title=str(s.get("title", sname)), intro=str(s.get("intro", "")),
                                    relevant_for=tuple(s.get("relevant_for") or ("all",)), fields=fields,
                                    required_any=req_any))
    if not sections:
        raise BrandKnowledgeError("schema.yaml: keine Bereiche definiert")
    return BrandSchema(version=int(raw.get("schema_version", 1)), sections=tuple(sections))


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------


def is_sentinel(value: Any) -> bool:
    return isinstance(value, str) and value in SENTINELS


def is_provided(value: Any) -> bool:
    """True, wenn echter Inhalt vorliegt (kein Platzhalter, nicht leer)."""
    if value is None or is_sentinel(value):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _check_value(spec: FieldSpec, value: Any, path: str, res: ValidationResult) -> None:
    if value is None:
        res.warnings.append(f"{path}: leer - wird als {NOT_PROVIDED} behandelt")
        return
    if is_sentinel(value):
        if value == NOT_APPLICABLE and not spec.allow_na:
            res.errors.append(f"{path}: {NOT_APPLICABLE} ist hier nicht erlaubt (Pflichtangabe der Brand)")
        return
    if isinstance(value, str) and value.strip().upper() in SENTINELS and value not in SENTINELS:
        res.errors.append(f"{path}: Platzhalter bitte exakt schreiben ({', '.join(SENTINELS)})")
        return

    if spec.type == "text":
        if not isinstance(value, str) or not value.strip():
            res.errors.append(f"{path}: Text erwartet")
    elif spec.type == "enum":
        if value not in spec.options:
            res.errors.append(f"{path}: '{value}' ist nicht erlaubt (erlaubt: {', '.join(spec.options)})")
    elif spec.type in ("text_list", "country_list", "object_list"):
        if not isinstance(value, list):
            res.errors.append(f"{path}: Liste erwartet")
            return
        if not value:
            res.warnings.append(f"{path}: leere Liste - bitte {NOT_PROVIDED} oder {NOT_APPLICABLE} eintragen")
            return
        for i, item in enumerate(value):
            ipath = f"{path}[{i}]"
            if spec.type == "text_list":
                if not isinstance(item, str) or not item.strip():
                    res.errors.append(f"{ipath}: Text erwartet")
            elif spec.type == "country_list":
                if not isinstance(item, str) or not _CODE.match(item):
                    res.errors.append(f"{ipath}: Laendercode wie DE, AT, CH, EU, UK, US erwartet (war: {item!r})")
            else:
                _check_object(spec, item, ipath, res)


def _check_object(spec: FieldSpec, item: Any, path: str, res: ValidationResult) -> None:
    if not isinstance(item, dict):
        res.errors.append(f"{path}: Objekt erwartet")
        return
    allowed = {f.name: f for f in spec.item_fields}
    for key in item:
        if key not in allowed:
            res.errors.append(f"{path}.{key}: unbekanntes Feld (erlaubt: {', '.join(allowed)})")
    for name, fspec in allowed.items():
        value = item.get(name, NOT_PROVIDED)
        if fspec.required and not is_provided(value):
            res.errors.append(f"{path}.{name}: Pflichtfeld des Eintrags fehlt")
        elif name in item:
            _check_value(fspec, value, f"{path}.{name}", res)


def validate(data: Any, schema: BrandSchema) -> ValidationResult:
    """Prueft Struktur und Typen. Fehlende Inhalte sind KEIN Fehler (dafuer gibt
    es den Brand-Check) - erfundene Felder, falsche Typen und Tippfehler schon."""
    res = ValidationResult()
    if not isinstance(data, dict):
        res.errors.append("Brand-Datei muss ein YAML-Mapping sein")
        return res
    known = {s.name for s in schema.sections} | {"meta"}
    for key in data:
        if key not in known:
            res.errors.append(f"{key}: unbekannter Bereich (erlaubt: {', '.join(sorted(known))})")
    meta = data.get("meta") or {}
    if isinstance(meta, dict) and meta.get("schema_version") not in (None, schema.version):
        res.errors.append(f"meta.schema_version {meta.get('schema_version')} passt nicht zum Schema {schema.version}")
    for section in schema.sections:
        sdata = data.get(section.name)
        if sdata is None:
            res.warnings.append(f"{section.name}: Bereich fehlt - alle Felder gelten als {NOT_PROVIDED}")
            continue
        if not isinstance(sdata, dict):
            res.errors.append(f"{section.name}: Mapping erwartet")
            continue
        names = {f.name for f in section.fields}
        for key in sdata:
            if key not in names:
                res.errors.append(f"{section.name}.{key}: unbekanntes Feld (Tippfehler?)")
        for f in section.fields:
            if f.name not in sdata:
                res.warnings.append(f"{section.name}.{f.name}: Feld fehlt - gilt als {NOT_PROVIDED}")
                continue
            _check_value(f, sdata[f.name], f"{section.name}.{f.name}", res)
    return res
