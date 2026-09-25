"""Erzeugt ONBOARDING.md (Fragenkatalog fuer den Owner) aus dem Brand-Schema.

Die Datei wird generiert, damit Fragen und Schema nie auseinanderlaufen:
    python -m agent_system brand onboarding --write
Ein Test stellt sicher, dass die eingecheckte Datei aktuell ist.
"""

from __future__ import annotations

from .brand_schema import BrandSchema, FieldSpec

_TYPE_HINT = {
    "text": "Freitext",
    "text_list": "Liste (ein Eintrag pro Zeile mit `- `)",
    "object_list": "Liste von Eintraegen",
    "enum": "genau ein Wert",
    "country_list": "Liste von Codes, z.B. `[DE, AT]`",
}


def _agents(relevant_for: tuple[str, ...]) -> str:
    return "alle Agenten" if "all" in relevant_for else ", ".join(relevant_for)


def _field_block(section: str, f: FieldSpec, number: str) -> list[str]:
    tag = " **(Pflicht)**" if f.required else ""
    lines = [f"**{number} {f.label}**{tag}  ", f"{f.question}  ",
             f"<sub>Feld: `{section}.{f.name}` · Format: {_TYPE_HINT[f.type]}"]
    if f.type == "enum":
        lines[-1] += f" (`{'` / `'.join(f.options)}`)"
    if f.item_fields:
        lines[-1] += " · je Eintrag: " + ", ".join(
            f"`{i.name}`{'*' if i.required else ''}" for i in f.item_fields)
    if not f.allow_na:
        lines[-1] += " · `NOT_APPLICABLE` nicht erlaubt"
    lines[-1] += "</sub>"
    lines += ["", "> Antwort:", ""]
    return lines


def render_onboarding(schema: BrandSchema) -> str:
    out = [
        "# Brand Knowledge Base - Onboarding fuer den Owner",
        "",
        "<!-- Automatisch erzeugt aus agent_system/brand/schema.yaml - nicht von Hand bearbeiten.",
        "     Neu erzeugen: python -m agent_system brand onboarding --write -->",
        "",
        "Mit diesen Fragen fuellst du die zentrale Brand-Wissensbasis deiner AI-Workforce.",
        "Alle Agenten arbeiten ausschliesslich mit dem, was du hier freigibst - **fehlende Angaben",
        "werden nie erfunden**, sondern als `NOT_PROVIDED` gespeichert und den Agenten als",
        "\"nicht angegeben\" gemeldet.",
        "",
        "## So gehst du vor",
        "",
        "1. Beantworte die Fragen - hier in dieser Datei als Notiz oder direkt in",
        "   `agent_system/brand/brand_knowledge.yaml` (dort steht jedes Feld schon mit `NOT_PROVIDED`).",
        "2. Du musst nicht alles auf einmal beantworten. Beginne mit den **Pflicht**-Fragen.",
        "3. Platzhalter, wenn du keine Antwort hast:",
        "   - `NOT_PROVIDED` - noch nicht beantwortet (Standard)",
        "   - `UNKNOWN` - weiss ich (noch) nicht",
        "   - `NOT_APPLICABLE` - trifft auf meine Brand nicht zu (z.B. keine Dienstleistungen)",
        "4. Pruefen: `python -m agent_system brand check`",
        "5. Freigeben (erst dann nutzen die Agenten die neuen Angaben):",
        "   `python -m agent_system brand commit --note \"Was wurde ergaenzt\"`",
        "",
        "Bitte **keine Kundendaten** (Namen, E-Mail-Adressen, Listen) eintragen - die Wissensbasis",
        "beschreibt die Brand, nicht einzelne Personen.",
        "",
        "## Uebersicht",
        "",
        "| # | Bereich | Pflichtfragen | Nutzen fuer |",
        "|---|---|---|---|",
    ]
    for i, s in enumerate(schema.sections, 1):
        req = sum(f.required for f in s.fields) + (1 if s.required_any else 0)
        out.append(f"| {i} | {s.title} | {req} von {len(s.fields)} | {_agents(s.relevant_for)} |")
    out.append("")

    for i, s in enumerate(schema.sections, 1):
        out += [f"## {i}. {s.title}", "", f"_{s.intro}_  ", f"<sub>Wird genutzt von: {_agents(s.relevant_for)}</sub>", ""]
        if s.required_any:
            labels = " oder ".join(s.field(n).label for n in s.required_any)
            out += [f"> Pflicht: mindestens eins von **{labels}**.", ""]
        for j, f in enumerate(s.fields, 1):
            out += _field_block(s.name, f, f"{i}.{j}")

    out += [
        "## Beispiel fuer das Eintragen in brand_knowledge.yaml",
        "",
        "Nur das FORMAT ist ein Beispiel - die Inhalte sind Platzhalter, keine Angaben zu deiner Brand:",
        "",
        "```yaml",
        "brand_identity:",
        "  brand_name: \"<Name deiner Brand>\"",
        "  values:",
        "    - \"<Wert 1>: <was er konkret bedeutet>\"",
        "  history: UNKNOWN",
        "offer:",
        "  products:",
        "    - name: \"<Produktname>\"",
        "      description: \"<Beschreibung>\"",
        "      price: NOT_PROVIDED",
        "      status: current",
        "      markets: [DE]",
        "  services: NOT_APPLICABLE",
        "```",
        "",
    ]
    return "\n".join(out)
