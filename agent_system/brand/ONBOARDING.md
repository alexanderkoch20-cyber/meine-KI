# Brand Knowledge Base - Onboarding fuer den Owner

<!-- Automatisch erzeugt aus agent_system/brand/schema.yaml - nicht von Hand bearbeiten.
     Neu erzeugen: python -m agent_system brand onboarding --write -->

Mit diesen Fragen fuellst du die zentrale Brand-Wissensbasis deiner AI-Workforce.
Alle Agenten arbeiten ausschliesslich mit dem, was du hier freigibst - **fehlende Angaben
werden nie erfunden**, sondern als `NOT_PROVIDED` gespeichert und den Agenten als
"nicht angegeben" gemeldet.

## So gehst du vor

1. Beantworte die Fragen - hier in dieser Datei als Notiz oder direkt in
   `agent_system/brand/brand_knowledge.yaml` (dort steht jedes Feld schon mit `NOT_PROVIDED`).
2. Du musst nicht alles auf einmal beantworten. Beginne mit den **Pflicht**-Fragen.
3. Platzhalter, wenn du keine Antwort hast:
   - `NOT_PROVIDED` - noch nicht beantwortet (Standard)
   - `UNKNOWN` - weiss ich (noch) nicht
   - `NOT_APPLICABLE` - trifft auf meine Brand nicht zu (z.B. keine Dienstleistungen)
4. Pruefen: `python -m agent_system brand check`
5. Freigeben (erst dann nutzen die Agenten die neuen Angaben):
   `python -m agent_system brand commit --note "Was wurde ergaenzt"`

Bitte **keine Kundendaten** (Namen, E-Mail-Adressen, Listen) eintragen - die Wissensbasis
beschreibt die Brand, nicht einzelne Personen.

## Uebersicht

| # | Bereich | Pflichtfragen | Nutzen fuer |
|---|---|---|---|
| 1 | Brand Identity | 4 von 8 | alle Agenten |
| 2 | Angebot | 2 von 6 | master, marketing, social, video, creative, research, legal, qa |
| 3 | Zielgruppen | 2 von 3 | master, marketing, social, video, creative, research, legal |
| 4 | Positionierung | 3 von 5 | master, marketing, social, video, creative, research |
| 5 | Brand Voice | 3 von 8 | master, marketing, social, video, creative, routine, qa |
| 6 | Visuelle Identitaet | 1 von 6 | creative, video, social, marketing |
| 7 | Content | 2 von 5 | master, marketing, social, video, creative, research |
| 8 | No-Gos | 2 von 5 | alle Agenten |
| 9 | Recht & Compliance | 1 von 5 | master, legal, qa, marketing, social |
| 10 | Strategische Ziele | 1 von 3 | master, marketing, social, research |

## 1. Brand Identity

_Wer ist die Marke, woher kommt sie, wofuer steht sie?_  
<sub>Wird genutzt von: alle Agenten</sub>

**1.1 Brandname** **(Pflicht)**  
Wie lautet der offizielle Name deiner Brand (genaue Schreibweise)?  
<sub>Feld: `brand_identity.brand_name` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**1.2 Owner** **(Pflicht)**  
Wer ist Owner der Brand (Person oder Firma, so wie sie genannt werden soll)?  
<sub>Feld: `brand_identity.owner` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**1.3 Gruender**  
Wer hat die Brand gegruendet (falls abweichend vom Owner)?  
<sub>Feld: `brand_identity.founders` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**1.4 Geschichte**  
Wie ist die Brand entstanden? Welche Meilensteine gab es?  
<sub>Feld: `brand_identity.history` · Format: Freitext</sub>

> Antwort:

**1.5 Mission** **(Pflicht)**  
Was ist die Mission der Brand - welches Problem loest sie fuer wen?  
<sub>Feld: `brand_identity.mission` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**1.6 Vision**  
Wo soll die Brand in 5-10 Jahren stehen?  
<sub>Feld: `brand_identity.vision` · Format: Freitext</sub>

> Antwort:

**1.7 Werte** **(Pflicht)**  
Welche 3-6 Werte leiten die Brand? (je Wert gern ein Satz, was er konkret bedeutet)  
<sub>Feld: `brand_identity.values` · Format: Liste (ein Eintrag pro Zeile mit `- `) · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**1.8 Langfristige Ziele**  
Welche langfristigen Ziele verfolgt die Brand?  
<sub>Feld: `brand_identity.long_term_goals` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

## 2. Angebot

_Was verkauft bzw. bietet die Brand an?_  
<sub>Wird genutzt von: master, marketing, social, video, creative, research, legal, qa</sub>

> Pflicht: mindestens eins von **Produkte oder Dienstleistungen**.

**2.1 Produkte**  
Welche Produkte bietet die Brand an? (je Produkt Name, Beschreibung, Preis, USP, Status, Maerkte)  
<sub>Feld: `offer.products` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `price`, `usp`, `status`, `markets`</sub>

> Antwort:

**2.2 Dienstleistungen**  
Welche Dienstleistungen bietet die Brand an? (je Leistung Name, Beschreibung, Preis, USP, Status, Maerkte)  
<sub>Feld: `offer.services` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `price`, `usp`, `status`, `markets`</sub>

> Antwort:

**2.3 Preis-Hinweise**  
Gibt es Regeln zur Kommunikation von Preisen (z.B. nie Rabatte, immer Bruttopreise)?  
<sub>Feld: `offer.pricing_notes` · Format: Freitext</sub>

> Antwort:

**2.4 USPs** **(Pflicht)**  
Was unterscheidet das Angebot konkret von Alternativen (Unique Selling Points)?  
<sub>Feld: `offer.usps` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**2.5 Aktuelle Angebote/Aktionen**  
Welche Angebote oder Aktionen laufen aktuell?  
<sub>Feld: `offer.current_offers` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `period`, `markets`</sub>

> Antwort:

**2.6 Geplante Angebote**  
Welche Angebote oder Produkte sind geplant?  
<sub>Feld: `offer.planned_offers` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `planned_for`, `markets`</sub>

> Antwort:

## 3. Zielgruppen

_Fuer wen ist die Brand da - und was bewegt diese Menschen?_  
<sub>Wird genutzt von: master, marketing, social, video, creative, research, legal</sub>

**3.1 Hauptzielgruppen** **(Pflicht)**  
Wer sind deine Hauptzielgruppen? (je Gruppe Beschreibung, Beduerfnisse, Probleme, Wuensche, Kaufmotive, Maerkte)  
<sub>Feld: `target_audiences.primary_audiences` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `needs`, `problems`, `desires`, `buying_motives`, `markets` · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**3.2 Weitere Zielgruppen**  
Gibt es weitere Zielgruppen? (gleiche Angaben wie oben)  
<sub>Feld: `target_audiences.secondary_audiences` · Format: Liste von Eintraegen · je Eintrag: `name`*, `description`, `needs`, `problems`, `desires`, `buying_motives`, `markets`</sub>

> Antwort:

**3.3 Relevante Maerkte/Laender** **(Pflicht)**  
In welchen Laendern/Maerkten ist die Brand aktiv oder will aktiv werden? (Codes wie DE, AT, CH, EU, UK, US)  
<sub>Feld: `target_audiences.markets` · Format: Liste von Codes, z.B. `[DE, AT]` · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

## 4. Positionierung

_Wo steht die Brand im Markt und wie soll sie wahrgenommen werden?_  
<sub>Wird genutzt von: master, marketing, social, video, creative, research</sub>

**4.1 Marktpositionierung** **(Pflicht)**  
Wie wuerdest du die Position der Brand im Markt in 1-3 Saetzen beschreiben (z.B. Premium, Preis-Leistung, Nische)?  
<sub>Feld: `positioning.market_position` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**4.2 Differenzierung** **(Pflicht)**  
Worin unterscheidet sich die Brand klar von Wettbewerbern?  
<sub>Feld: `positioning.differentiation` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**4.3 Wettbewerbsumfeld**  
Wie sieht das Wettbewerbsumfeld aus (Marktgroesse, Art der Anbieter, Dynamik)?  
<sub>Feld: `positioning.competitive_landscape` · Format: Freitext</sub>

> Antwort:

**4.4 Wettbewerber**  
Welche konkreten Wettbewerber kennst du? (Name, Website, Staerken, Schwaechen)  
<sub>Feld: `positioning.competitors` · Format: Liste von Eintraegen · je Eintrag: `name`*, `website`, `strengths`, `weaknesses`, `notes`</sub>

> Antwort:

**4.5 Gewuenschte Wahrnehmung** **(Pflicht)**  
Was sollen Menschen denken oder fuehlen, wenn sie die Brand sehen?  
<sub>Feld: `positioning.desired_perception` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

## 5. Brand Voice

_Wie spricht und schreibt die Brand?_  
<sub>Wird genutzt von: master, marketing, social, video, creative, routine, qa</sub>

**5.1 Tonalitaet** **(Pflicht)**  
Wie klingt die Brand (z.B. warm, direkt, humorvoll, sachlich)?  
<sub>Feld: `brand_voice.tonality` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**5.2 Schreibstil** **(Pflicht)**  
Wie soll geschrieben werden (Satzlaenge, Emojis ja/nein, Fachbegriffe, Struktur)?  
<sub>Feld: `brand_voice.writing_style` · Format: Freitext · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**5.3 Anrede** **(Pflicht)**  
Wie spricht die Brand ihre Zielgruppe an - du, Sie oder situationsabhaengig?  
<sub>Feld: `brand_voice.form_of_address` · Format: genau ein Wert (`du` / `Sie` / `situationsabhaengig`) · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**5.4 Sprachen**  
In welchen Sprachen kommuniziert die Brand?  
<sub>Feld: `brand_voice.languages` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**5.5 Gewuenschte Woerter/Phrasen**  
Welche Woerter oder Phrasen gehoeren typischerweise zur Brand?  
<sub>Feld: `brand_voice.words_to_use` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**5.6 Zu vermeidende Woerter/Phrasen**  
Welche Woerter oder Phrasen sollen vermieden werden? (Die QA markiert sie als Warnung.)  
<sub>Feld: `brand_voice.words_to_avoid` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**5.7 Beispiele guter Brand-Kommunikation**  
Hast du Beispiele fuer Texte, die genau nach deiner Brand klingen? (Kontext, Text, warum gut)  
<sub>Feld: `brand_voice.good_examples` · Format: Liste von Eintraegen · je Eintrag: `context`, `text`*, `why_good`</sub>

> Antwort:

**5.8 Negativbeispiele**  
Hast du Beispiele, wie die Brand NICHT klingen soll? (Kontext, Text, warum schlecht)  
<sub>Feld: `brand_voice.bad_examples` · Format: Liste von Eintraegen · je Eintrag: `context`, `text`*, `why_bad`</sub>

> Antwort:

## 6. Visuelle Identitaet

_Wie sieht die Brand aus?_  
<sub>Wird genutzt von: creative, video, social, marketing</sub>

**6.1 Farben** **(Pflicht)**  
Welche Markenfarben gibt es? (Name, Farbcode z.B. als HEX, Verwendung)  
<sub>Feld: `visual_identity.colors` · Format: Liste von Eintraegen · je Eintrag: `name`*, `value`, `usage` · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**6.2 Schriften**  
Welche Schriften werden verwendet? (Name, Verwendung, Lizenz)  
<sub>Feld: `visual_identity.fonts` · Format: Liste von Eintraegen · je Eintrag: `name`*, `usage`, `license`</sub>

> Antwort:

**6.3 Logo-Regeln**  
Welche Regeln gelten fuer das Logo (Schutzzone, Mindestgroesse, Varianten, Verbote)?  
<sub>Feld: `visual_identity.logo_rules` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**6.4 Bildsprache**  
Wie sieht die Bildsprache aus (Motive, Licht, Farben, Stimmung, echte Menschen vs. Produkt)?  
<sub>Feld: `visual_identity.imagery_style` · Format: Freitext</sub>

> Antwort:

**6.5 Video-Stil**  
Wie sehen Videos der Brand aus (Tempo, Schnitt, Musik, Kamerafuehrung, Laenge)?  
<sub>Feld: `visual_identity.video_style` · Format: Freitext</sub>

> Antwort:

**6.6 Designregeln**  
Welche weiteren Designregeln gelten (Layouts, Weissraum, Icons, Formen)?  
<sub>Feld: `visual_identity.design_rules` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

## 7. Content

_Welche Inhalte gibt es schon und wo wird kommuniziert?_  
<sub>Wird genutzt von: master, marketing, social, video, creative, research</sub>

**7.1 Bisherige Inhalte**  
Welche Inhalte gab es bisher (Titel, Typ, Plattform, Link, Datum, was gut/schlecht lief)?  
<sub>Feld: `content.past_content` · Format: Liste von Eintraegen · je Eintrag: `title`*, `type`, `platform`, `url`, `date`, `notes`</sub>

> Antwort:

**7.2 Bevorzugte Content-Formate**  
Welche Formate bevorzugt die Brand (z.B. Reels, Karussells, Blogartikel, Newsletter)?  
<sub>Feld: `content.preferred_formats` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**7.3 Social-Media-Plattformen** **(Pflicht)**  
Auf welchen Plattformen ist die Brand aktiv oder plant es? (Name, Handle, Ziel, Status)  
<sub>Feld: `content.platforms` · Format: Liste von Eintraegen · je Eintrag: `name`*, `handle`, `goal`, `status`</sub>

> Antwort:

**7.4 Content-Saeulen** **(Pflicht)**  
Welche 3-5 Themen-Saeulen soll der Content abdecken?  
<sub>Feld: `content.content_pillars` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**7.5 Kampagnen**  
Welche Kampagnen gab es oder sind geplant? (Name, Ziel, Status, Zeitraum, Maerkte)  
<sub>Feld: `content.campaigns` · Format: Liste von Eintraegen · je Eintrag: `name`*, `goal`, `status`, `period`, `markets`</sub>

> Antwort:

## 8. No-Gos

_Was die Brand niemals sagt, zeigt oder tut. Die QA setzt "statements" automatisch durch._  
<sub>Wird genutzt von: alle Agenten</sub>

**8.1 Verbotene Aussagen/Woerter** **(Pflicht)**  
Welche Aussagen, Woerter oder Versprechen darf die Brand NIE verwenden? (Die QA blockiert Ergebnisse damit.)  
<sub>Feld: `no_gos.statements` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**8.2 Verbotene Themen** **(Pflicht)**  
Ueber welche Themen spricht die Brand nie (z.B. Politik, Religion, Konkurrenz)?  
<sub>Feld: `no_gos.topics` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**8.3 Verbotene Designs**  
Welche Designs, Stile, Motive oder Farben sind tabu?  
<sub>Feld: `no_gos.designs` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**8.4 Verbotene Marketingmethoden**  
Welche Marketingmethoden lehnt die Brand ab (z.B. Fake-Verknappung, Clickbait, Kaltakquise)?  
<sub>Feld: `no_gos.marketing_methods` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**8.5 Verbotene Aktionen**  
Welche Aktionen soll die Brand niemals durchfuehren?  
<sub>Feld: `no_gos.actions` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

## 9. Recht & Compliance

_Bekannte rechtliche Rahmenbedingungen. Das ist Wissen fuer die Agenten - keine Rechtsberatung._  
<sub>Wird genutzt von: master, legal, qa, marketing, social</sub>

**9.1 Laender/Jurisdiktionen** **(Pflicht)**  
In welchen Rechtsraeumen ist die Brand rechtlich taetig (Sitz, Verkauf, Werbung)? (Codes wie DE, AT, CH, EU, UK, US)  
<sub>Feld: `legal_compliance.jurisdictions` · Format: Liste von Codes, z.B. `[DE, AT]` · `NOT_APPLICABLE` nicht erlaubt</sub>

> Antwort:

**9.2 Bekannte rechtliche Vorgaben**  
Welche rechtlichen Vorgaben sind dir bekannt (z.B. Branchenregeln, Auflagen)? (Titel, Rechtsraum, Beschreibung, Quelle)  
<sub>Feld: `legal_compliance.known_requirements` · Format: Liste von Eintraegen · je Eintrag: `title`*, `jurisdiction`, `description`, `source`</sub>

> Antwort:

**9.3 Besondere Datenschutzanforderungen**  
Gibt es besondere Datenschutzanforderungen (z.B. sensible Kundendaten, Auftragsverarbeiter, Loeschfristen)?  
<sub>Feld: `legal_compliance.privacy_requirements` · Format: Liste (ein Eintrag pro Zeile mit `- `)</sub>

> Antwort:

**9.4 Bekannte Lizenzen**  
Welche Lizenzen besitzt die Brand (Bilder, Musik, Fonts, Software)? (Asset, Lizenz, Lizenzgeber, Umfang, gueltig bis, Nachweis)  
<sub>Feld: `legal_compliance.licenses` · Format: Liste von Eintraegen · je Eintrag: `asset`*, `license`, `licensor`, `scope`, `valid_until`, `proof_location`</sub>

> Antwort:

**9.5 Marken-/Urheberrechte**  
Welche Marken, Urheberrechte oder Domains gehoeren der Brand bzw. sind zu beachten? (Name, Art, Inhaber, Registrierung, Rechtsraeume)  
<sub>Feld: `legal_compliance.trademarks_copyrights` · Format: Liste von Eintraegen · je Eintrag: `name`*, `kind`, `owner`, `registration`, `jurisdictions`, `notes`</sub>

> Antwort:

## 10. Strategische Ziele

_Wohin will die Brand - kurz-, mittel- und langfristig?_  
<sub>Wird genutzt von: master, marketing, social, research</sub>

**10.1 Kurzfristig (bis 12 Monate)** **(Pflicht)**  
Welche Ziele gelten fuer die naechsten 12 Monate? (Ziel, Messgroesse, Zieldatum)  
<sub>Feld: `strategic_goals.short_term` · Format: Liste von Eintraegen · je Eintrag: `goal`*, `metric`, `target_date`</sub>

> Antwort:

**10.2 Mittelfristig (1-3 Jahre)**  
Welche Ziele gelten fuer 1-3 Jahre?  
<sub>Feld: `strategic_goals.mid_term` · Format: Liste von Eintraegen · je Eintrag: `goal`*, `metric`, `target_date`</sub>

> Antwort:

**10.3 Langfristig (3+ Jahre)**  
Welche Ziele gelten langfristig (3+ Jahre)?  
<sub>Feld: `strategic_goals.long_term` · Format: Liste von Eintraegen · je Eintrag: `goal`*, `metric`, `target_date`</sub>

> Antwort:

## Beispiel fuer das Eintragen in brand_knowledge.yaml

Nur das FORMAT ist ein Beispiel - die Inhalte sind Platzhalter, keine Angaben zu deiner Brand:

```yaml
brand_identity:
  brand_name: "<Name deiner Brand>"
  values:
    - "<Wert 1>: <was er konkret bedeutet>"
  history: UNKNOWN
offer:
  products:
    - name: "<Produktname>"
      description: "<Beschreibung>"
      price: NOT_PROVIDED
      status: current
      markets: [DE]
  services: NOT_APPLICABLE
```
