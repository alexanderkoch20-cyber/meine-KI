"""Versionierung der Brand Knowledge Base + Brand-Context-Loader.

Aufbau von ``agent_system/brand/``::

    brand_knowledge.yaml     Arbeitskopie - hier traegt der Owner ein
    schema.yaml              Schema (Felder, Pflichtangaben, Onboarding-Fragen)
    ONBOARDING.md            Fragenkatalog fuer den Owner (aus dem Schema erzeugt)
    versions/index.json      Versionsliste mit Hash-Kette
    versions/v0001.yaml ...  freigegebene, unveraenderliche Versionen

Regeln:
- Agenten arbeiten IMMER mit der neuesten FREIGEGEBENEN Version - nie mit der
  Arbeitskopie. Aenderungen wirken erst nach ``commit`` (nur Owner).
- Eine Version ist unveraenderlich: Ihr Inhalt ist per SHA-256 im Index
  festgehalten, der Index ist verkettet (parent_hash). Manipulationen fuehren
  beim Laden zu einem Fehler statt zu stillschweigend falschem Kontext.
- Der System-"bootstrap" darf nur die leere Vorlage (alles NOT_PROVIDED)
  als v1 anlegen - Inhalte kann ausschliesslich der Owner freigeben.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import yaml

from .brand import BrandKnowledge, BrandVersionInfo, content_hash, normalize_brand_data, read_brand_yaml
from .brand_schema import BrandSchema, ValidationResult, load_schema, validate
from .errors import BrandKnowledgeError, GovernanceViolationError
from .governance import Actor
from .models import utcnow

WORKING_FILE = "brand_knowledge.yaml"
SCHEMA_FILE = "schema.yaml"
VERSIONS_DIR = "versions"
INDEX_FILE = "index.json"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BrandRepository:
    def __init__(self, brand_dir: Path | str):
        self.dir = Path(brand_dir)
        self.schema: BrandSchema = load_schema(self.dir / SCHEMA_FILE)
        self._lock = threading.Lock()

    # -- Pfade / Index ------------------------------------------------------------

    @property
    def working_path(self) -> Path:
        return self.dir / WORKING_FILE

    @property
    def versions_dir(self) -> Path:
        return self.dir / VERSIONS_DIR

    def _index(self) -> list[dict[str, Any]]:
        path = self.versions_dir / INDEX_FILE
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("versions", [])

    def _write_index(self, versions: list[dict[str, Any]]) -> None:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        path = self.versions_dir / INDEX_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": self.schema.version, "versions": versions},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def history(self) -> list[dict[str, Any]]:
        return list(self._index())

    def verify_index(self) -> list[str]:
        """Prueft Hash-Kette und Dateihashes aller Versionen. Rueckgabe: gefundene Probleme."""
        problems, parent = [], None
        for i, entry in enumerate(self._index(), start=1):
            if entry["version"] != i:
                problems.append(f"v{entry['version']}: Versionsnummer nicht fortlaufend")
            if entry.get("parent_hash") != parent:
                problems.append(f"v{entry['version']}: Hash-Kette unterbrochen")
            path = self.versions_dir / entry["file"]
            if not path.exists():
                problems.append(f"v{entry['version']}: Datei fehlt")
            elif _file_hash(path) != entry["file_hash"]:
                problems.append(f"v{entry['version']}: Datei wurde nach der Freigabe veraendert")
            parent = entry["content_hash"]
        return problems

    # -- Arbeitskopie ------------------------------------------------------------------

    def validate_working(self) -> ValidationResult:
        return validate(read_brand_yaml(self.working_path), self.schema)

    def working_hash(self) -> str:
        return content_hash(normalize_brand_data(read_brand_yaml(self.working_path), self.schema))

    def has_uncommitted_changes(self) -> bool:
        index = self._index()
        return not index or index[-1]["content_hash"] != self.working_hash()

    # -- Versionen ---------------------------------------------------------------------

    def load_version(self, version: int) -> BrandKnowledge:
        index = self._index()
        entry = next((e for e in index if e["version"] == version), None)
        if entry is None:
            raise BrandKnowledgeError(f"Brand-Version v{version} existiert nicht")
        path = self.versions_dir / entry["file"]
        if not path.exists() or _file_hash(path) != entry["file_hash"]:
            raise BrandKnowledgeError(f"Brand-Version v{version} ist beschaedigt oder wurde veraendert - "
                                      "wird nicht verwendet")
        data = read_brand_yaml(path)
        info = BrandVersionInfo(version=entry["version"], content_hash=entry["content_hash"],
                                committed_at=entry["committed_at"], committed_by=entry["committed_by"],
                                note=entry.get("note"))
        return BrandKnowledge.from_data(data, self.schema, info, path)

    def current(self) -> BrandKnowledge:
        """Neueste freigegebene Version. Ohne Freigabe: leere Basis (nichts erfunden)."""
        index = self._index()
        if not index:
            return BrandKnowledge.empty(self.schema)
        problems = self.verify_index()
        if problems:
            raise BrandKnowledgeError("Brand-Versionen nicht vertrauenswuerdig: " + "; ".join(problems))
        return self.load_version(index[-1]["version"])

    def _write_version(self, normalized: dict[str, Any], committed_by: str, note: str) -> BrandVersionInfo:
        with self._lock:
            index = self._index()
            chash = content_hash(normalized)
            if index and index[-1]["content_hash"] == chash:
                raise BrandKnowledgeError(f"Keine Aenderungen gegenueber v{index[-1]['version']}")
            version = len(index) + 1
            now = utcnow()
            meta = {"schema_version": self.schema.version, "version": version, "content_hash": chash,
                    "committed_at": now, "committed_by": committed_by, "change_note": note}
            filename = f"v{version:04d}.yaml"
            self.versions_dir.mkdir(parents=True, exist_ok=True)
            path = self.versions_dir / filename
            header = (f"# Brand Knowledge Base - freigegebene Version {version} (UNVERAENDERLICH).\n"
                      "# Aenderungen nur in ../brand_knowledge.yaml und danach: python -m agent_system brand commit\n")
            path.write_text(header + yaml.safe_dump({"meta": meta, **normalized}, allow_unicode=True,
                                                    sort_keys=False), encoding="utf-8")
            index.append({"version": version, "file": filename, "content_hash": chash,
                          "file_hash": _file_hash(path),
                          "parent_hash": index[-1]["content_hash"] if index else None,
                          "committed_at": now, "committed_by": committed_by, "note": note})
            self._write_index(index)
        return BrandVersionInfo(version=version, content_hash=chash, committed_at=now,
                                committed_by=committed_by, note=note)

    def commit(self, actor: Actor, owner_id: str, note: str) -> BrandVersionInfo:
        """Owner gibt die Arbeitskopie als neue Version frei."""
        if not (actor.is_owner and actor.id == owner_id):
            raise GovernanceViolationError(f"'{actor.id}' darf die Brand Knowledge Base nicht aendern - nur der Owner")
        note = (note or "").strip()
        if not note:
            raise BrandKnowledgeError("Bitte eine Aenderungsnotiz angeben (--note)")
        data = read_brand_yaml(self.working_path)
        result = validate(data, self.schema)
        if not result.ok:
            raise BrandKnowledgeError("Arbeitskopie ungueltig - nicht freigegeben:\n  - " + "\n  - ".join(result.errors))
        return self._write_version(normalize_brand_data(data, self.schema), actor.id, note)

    def bootstrap(self) -> BrandVersionInfo:
        """Legt v1 als LEERE Vorlage an (nur wenn noch keine Version existiert)."""
        if self._index():
            raise BrandKnowledgeError("Es existieren bereits Versionen - bootstrap nicht moeglich")
        return self._write_version(self.schema.empty_template(), "system:bootstrap",
                                   "Initiale leere Vorlage - alle Felder NOT_PROVIDED")

    # -- Vergleich ----------------------------------------------------------------------

    def diff(self, old: int, new: int) -> list[str]:
        a, b = self.load_version(old).as_dict(), self.load_version(new).as_dict()
        changes = []
        for s in self.schema.sections:
            for f in s.fields:
                va, vb = a[s.name][f.name], b[s.name][f.name]
                if va != vb:
                    changes.append(f"{s.name}.{f.name}: {_short(va)} -> {_short(vb)}")
        return changes


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


class BrandContextLoader:
    """Liefert allen Agenten dieselbe, aktuelle freigegebene Brand-Basis.

    Pro Planung/Ausfuehrung wird genau EINE Momentaufnahme geladen und an alle
    Agenten des Teams uebergeben. Wurde die Brand-Basis zwischen Freigabe und
    Ausfuehrung geaendert, erkennt das Gate das am Hash.
    """

    def __init__(self, repository: BrandRepository | None = None, fixed: BrandKnowledge | None = None):
        if repository is None and fixed is None:
            raise BrandKnowledgeError("BrandContextLoader braucht ein Repository oder eine feste Brand-Basis")
        self.repository = repository
        self.fixed = fixed

    def load(self) -> BrandKnowledge:
        return self.fixed if self.fixed is not None else self.repository.current()
