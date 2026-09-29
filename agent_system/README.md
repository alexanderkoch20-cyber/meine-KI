# Agentensystem (agent_system)

Eine echte Agenten-Orchestrierung statt neun einzelner Chatbots - unter der
**Owner-Regel**: Der Owner ist alleiniger Eigentuemer der Brand und der
gesamten AI-Workforce. Kein Agent beginnt, erweitert oder wiederholt einen
Auftrag ohne seine ausdrueckliche Freigabe.

```
OWNER → MASTER (plant) → LEGAL-Vorpruefung → OWNER-FREIGABE → SPEZIAL-AGENT
      → LEGAL & COMPLIANCE REVIEW → QA → OWNER-FREIGABE (Aktionen) → AUSFUEHRUNG (derzeit Dry-Run)
```

Der aktuelle Stand laeuft **komplett offline** (Mock-LLM): keine API-Kosten,
keine Netzwerkaufrufe, keine externen Aktionen, keine verbundenen Accounts.

## Schnellstart (Owner-Ablauf)

```bash
pip install -r requirements-agents.txt

python -m agent_system submit "Recherchiere Trends und plane eine Kampagne mit Instagram-Reels"
#   -> Master legt einen Plan vor. Status: waiting_for_owner. NICHTS laeuft.
python -m agent_system show JOB_ID                    # Plan + Fingerabdruck pruefen
python -m agent_system approve JOB_ID --plan FP       # Freigabe GENAU dieses Plans
python -m agent_system start JOB_ID                   # Ausfuehrung (nur wenn freigegeben)

python -m agent_system clarify JOB_ID "Antwort"       # Rueckfrage beantworten / entscheiden
python -m agent_system cancel JOB_ID                  # Auftrag abbrechen
python -m agent_system resubmit JOB_ID                # gescheiterten Auftrag NEU vorlegen (neue Freigabe)
python -m agent_system tasks                          # alle Auftraege

python -m agent_system actions                        # Aktionsvorschlaege der Agenten
python -m agent_system approve-action APR_ID          # entscheiden (Dry-Run, nichts wird ausgefuehrt)
python -m agent_system reject-action  APR_ID

python -m agent_system submit "..." -j DE -j AT       # Rechtsraeume angeben (sonst Rueckfrage)
python -m agent_system jurisdictions JOB_ID DE AT     # Rechtsraeume nachtragen
python -m agent_system legal JOB_ID                   # Legal-Bericht: Rechtsraum, Regel, Quelle, Daten
python -m agent_system legal-review-done JOB_ID --reviewer "RA Muster" --note "Ergebnis"
                                                      # menschliche Rechtspruefung dokumentieren

python -m agent_system audit [--job JOB_ID]           # Audit-Log + Integritaetspruefung
python -m agent_system agents                         # Agenten, Modelle, inaktive Agenten
python -m agent_system brand check [--agent social]   # fehlende Pflichtinformationen
python -m agent_system brand commit --note "..."      # neue Brand-Version freigeben (nur Owner)
python -m agent_system brand show|history|diff 1 2|onboarding

pytest tests/agent_system
```

Jobs, Freigaben, Audit-Log und Logs liegen in `data/agent_system/`
(per `--data-dir` oder `AGENT_SYSTEM_DATA_DIR` aenderbar, nicht versioniert).

## Owner-Regel: wie sie technisch durchgesetzt wird

### Task-Status

| Status | Bedeutung | Wer setzt ihn |
|---|---|---|
| `draft` | Auftrag angelegt, Master analysiert | System (kurz, innerhalb von `submit`) |
| `waiting_for_owner` | Plan / Rueckfrage / Entscheidung liegt beim Owner | System |
| `approved` | Owner hat genau diesen Plan freigegeben | **nur Owner** |
| `running` | wird ausgefuehrt | System, nur nach Gate-Pruefung |
| `completed` | fertig | System |
| `failed` | gestoppt nach Fehler - kein Auto-Neustart | System |
| `cancelled` | abgebrochen | **nur Owner** |

Erlaubte Uebergaenge (`core/jobs.py`):
`draft → waiting_for_owner → approved → running → completed | failed`,
`running → waiting_for_owner` (Agent braucht Entscheidung),
`approved → waiting_for_owner` (Konfiguration seit Freigabe geaendert),
`cancelled` aus draft/waiting/approved. Endzustaende sind endgueltig.
Agenten duerfen den Status **gar nicht** aendern.

### Die Schutzschichten

1. **OwnerApproval-Gate** (`core/governance.py`) - einzige Stelle fuer
   Freigaben. Prueft, dass der Akteur der in `governance.yaml` konfigurierte
   Owner ist, und speichert einen Freigabe-Datensatz, gebunden an
   Auftrag + Vorlagerunde + **Plan-Fingerabdruck** + **Konfigurations-Fingerabdruck**.
2. **Ausfuehrungs-Pruefung** - `execute()` laeuft nur, wenn Status `approved`
   UND ein passender Datensatz existiert. Ein manipulierter Status, eine
   veraenderte Job-Datei, ein nachtraeglich erweiterter Plan oder eine
   geaenderte Konfiguration -> Ausfuehrung verweigert und protokolliert.
3. **Message-Bus-Sperre** - Arbeitsauftraege an Spezial-Agenten werden nur
   zugestellt, wenn der Auftrag `running` ist.
4. **Unveraenderliche Regeln im Code** (`core/rules.py`) - Aktionen wie
   Geld, Veroeffentlichen, Kundenkontakt, Loeschen, Revert/Reset, neue
   Agenten, Modell-/Konfig-Wechsel, externe Dienste, API-Keys, neue/erweiterte/
   wiederholte Auftraege brauchen **immer** eine Owner-Freigabe; setzt
   `permissions.yaml` sie auf `allow`, startet das System nicht.
   Berechtigungen/Governance aendern, Freigaben erteilen und Secrets ausgeben
   ist fuer Agenten **immer verboten**.
5. **Unveraenderliche Konfiguration zur Laufzeit** - `SystemConfig` ist
   frozen, alle Mappings schreibgeschuetzt. Agenten erhalten nie ein
   Owner-Objekt.
6. **Agenten-Freigabe** - nur Agenten in `governance.yaml: approved_agents`
   sind aktiv. Ein neuer Agent in `agents.yaml` bleibt inaktiv, bis der Owner
   ihn eintraegt.
7. **Audit-Log** (`data/agent_system/audit.jsonl`) - append-only mit
   Hash-Kette: Auftrag erstellt, Plan vorgelegt, freigegeben, gestartet,
   Aktionen vorgeschlagen/entschieden, Stopps, Fehler, jeder
   Regelverstoss. `audit` prueft die Integritaet; nachtraegliche Aenderungen
   werden erkannt. Secrets sind geschwaerzt.

### Stoppen statt selbststaendig handeln

- **Unklarer Auftrag** -> keine Planung, Rueckfrage an den Owner. Freigabe
  erst nach `clarify` (dann neuer Plan -> neue Pruefung).
- **Fehler / QA lehnt ab** -> Auftrag stoppt sofort (`failed`), restliche
  Schritte werden nicht ausgefuehrt, Bericht mit Empfehlung. Kein
  automatischer Neustart; `resubmit` erzeugt auf Owner-Anweisung einen
  NEUEN Auftrag, der wieder freigegeben werden muss.
- **Agent braucht Entscheidung** (`request_owner_decision`) -> Auftrag
  pausiert (`waiting_for_owner`); weiter nur mit `clarify` + `approve`.
  Bereits erledigte Schritte werden nicht wiederholt.
- **Zusaetzliche Arbeit sinnvoll** -> Agent schlaegt `start_new_task` /
  `extend_task` / `retry_task` vor. Das erzeugt nur eine Empfehlung - auch
  nach Freigabe wird kein Auftrag automatisch angelegt.
- **Automatische Wiederholungen/Ueberarbeitungen**: standardmaessig AUS
  (`max_retries: 0`, `max_revisions: 0`). Nur der Owner kann sie in
  `agents.yaml` einschalten; jede Wiederholung wird auditiert.
- **Aktionsfreigaben sind Dry-Run**: Es gibt noch keine Executor. Eine
  freigegebene Aktion wird nur protokolliert (`executed: false`).

Ehrliche Einordnung: Das Gate schuetzt vor Fehlverhalten der Agenten-Logik
und der Modell-Ausgaben (Agenten koennen nur Text liefern). Es ist keine
Sandbox gegen beliebigen Python-Code im selben Prozess.

## Chief Legal & Compliance Agent

Besonders geschuetzte Kontrollinstanz (`agents/legal.py`, `core/legal.py`,
Wissensbasis `config/legal.yaml`). **Keine Rechtsberatung** - er erkennt
Risiken frueh, dokumentiert Quellen und stoppt bei Unsicherheit.

**Wo er prueft (Pflicht, nicht umgehbar):**
1. *Vorpruefung* jedes Auftrags beim Planen - vor der Owner-Freigabe.
2. *Jedes Agenten-Ergebnis* und *jede externe Aktion* - nach dem
   Spezial-Agenten, **vor** der QA. Auch "nicht erforderlich" wird mit
   Begruendung dokumentiert (Job, Bericht, Audit-Log).

**Legal-Status** (eigene Dimension neben dem Task-Status):

| Status | Wirkung |
|---|---|
| `legal_review_not_required` | geprueft, nichts Relevantes gefunden (dokumentiert) |
| `legal_review_required` | Auftrag ist rechtlich relevant - jeder Schritt wird geprueft |
| `legal_review_passed` | keine blockierenden Risiken erkannt (keine Garantie!) - Owner-Freigabe trotzdem noetig |
| `human_legal_review_required` | Stopp: Freigaben gesperrt bis zur dokumentierten menschlichen Rechtspruefung |
| `legal_review_blocked` | Ausfuehrung blockiert; Inhalt wird nicht ausgeliefert |

**Bewertung** (`core/legal.py`, deterministisch; das Modell kann nur verschaerfen):
- Themen aus 6 Bereichen (Datenschutz, Werbung, Geistiges Eigentum,
  E-Commerce, International, Vertraege) per Stichwort; externe Aktionen
  (`publish_content`, `send_customer_message`, `spend_money`, `sign_contract`,
  `generate_video`, ...) loesen immer eine Pruefung aus.
- Risiko `critical` (z.B. Heilversprechen, gefaelschte Bewertungen, Nutzung
  ohne Lizenz) -> **BLOCKED**. Risiko `high` (z.B. Testsieger-Claims,
  Gesundheitsdaten, Drittlandtransfer, Vertraege, Marken) -> **HUMAN**.
  Risiko `medium` -> **PASSED nur**, wenn fuer JEDEN Rechtsraum eine
  vollstaendig dokumentierte und von einem Menschen verifizierte Quelle im
  Katalog steht - sonst **HUMAN**.
- **Jurisdiktion:** Owner-Angabe (`-j`) + im Text genannte Laender.
  Ohne Rechtsraum -> Rueckfrage; unbekannter Rechtsraum (z.B. BR) -> HUMAN.
  Deutsches Recht wird nie auf andere Laender uebertragen; AT/DE erben nur
  EU-Recht (`parent: EU`).
- Jede Feststellung dokumentiert: Rechtsraum, Regel, Quelle (Fundstelle +
  URL), Fassungsdatum, Verifikation (wer/wann), Pruefdatum, Unsicherheiten,
  Empfehlung. Jede Pruefung traegt den Hinweis "Keine Rechtsberatung ...
  garantiert nicht, dass eine Handlung legal oder straffrei ist".
- Aussagen wie "garantiert legal", "rechtssicher", "straffrei" werden aus
  Modell-Antworten entfernt und fuehren zu HUMAN.

**Grenzen (technisch erzwungen):** Der Legal-Agent kann nicht freigeben
(Gate), keine Auftraege erteilen, nicht als Arbeitsschritt geplant werden,
keine externen Aktionen vorschlagen (Konfig-Fehler) und aus seiner Antwort
entsteht nie eine Aktion (Versuch -> verworfen, Audit
`governance_violation`, HUMAN). Er kann nicht deaktiviert werden (Pflicht-Agent).
Kein Agent kann Legal umgehen: `bypass_legal_review`, `override_legal_review`,
`set_legal_status`, `modify_legal_knowledge` sind immer verboten; eine Aktion
ohne Legal-Pruefung ist nicht freigebbar; ein behaupteter Legal-Status im
Agenten-Ergebnis wird ignoriert.

**BLOCKED / HUMAN aufheben** kann ausschliesslich der Owner, indem er eine
**menschliche Rechtspruefung dokumentiert** (`legal-review-done`, mit Name der
pruefenden Person). Das wird im Gate (nicht in der Job-Datei) gespeichert und
auditiert; danach ist die normale Owner-Freigabe trotzdem noch noetig.

**Quellenkatalog:** `config/legal.yaml` wird mit **unverifizierten** Quellen
ausgeliefert (DSGVO, ePrivacy, KI-Verordnung, UCPD, Verbraucherrechte-RL,
UWG, PAngV, TDDDG, UrhG, MarkenG, BGB). Solange niemand sie prueft
(`verified_at`, `verified_by`, `version_date` eintragen), fuehrt jeder
relevante Treffer zu HUMAN. Fuer CH, UK, US gibt es noch keine Eintraege.

## Agenten

| ID | Rolle | Modell-Stufe | Aufgaben |
|---|---|---|---|
| `master` | Orchestrator / CEO | opus | analysieren, planen, vorlegen, zusammenfuehren |
| `marketing` | Spezialist | sonnet | Strategie, Kampagnen, Angebote, Positionierung, Conversion-Texte |
| `social` | Spezialist | sonnet | Content-Ideen, Posts, Reels, Content-Kalender, Plattform-Varianten |
| `video` | Spezialist | sonnet | Konzepte, Storyboards, Prompts, Produktion, spaeter CineMotion |
| `creative` | Spezialist | sonnet | Konzepte, Bildideen, Kampagnenvisuals, Design-Richtlinien |
| `research` | Spezialist | sonnet | Markt, Wettbewerb, Trends, Zielgruppe, Quellen |
| `coding` | Spezialist | sonnet | Software, Bugfixes, Tests, Infrastruktur |
| `routine` | Spezialist | haiku | Formatierung, Klassifizierung, kurze Zusammenfassungen |
| `legal` | Kontrollinstanz | opus | Recht, Datenschutz, Compliance: markieren, blockieren, Quellen, menschliche Pruefung verlangen |
| `qa` | Pruefinstanz | sonnet | Pruefen, Brand-Regeln, riskante Aktionen blockieren, Freigaben verlangen |

## Projektstruktur

```
agent_system/
  __main__.py          Owner-CLI
  orchestrator.py      Phasen Planen / Freigeben / Ausfuehren, Stopp-und-Melden
  config/
    governance.yaml    Owner-ID, freigegebene Agenten
    legal.yaml         Legal-Wissensbasis: Rechtsraeume, Risikothemen, Quellenkatalog
    agents.yaml        Agenten-Definitionen, Orchestrierung (Retries standardmaessig 0)
    models.yaml        Modell-Stufen, LLM-Provider (mock|anthropic), Budget
    permissions.yaml   Aktionen + Policy (allow / require_approval / deny)
  brand/
    brand_knowledge.yaml   Arbeitskopie der Brand Knowledge Base (Owner traegt hier ein)
    schema.yaml            Schema: 10 Bereiche, Felder, Pflicht, Agenten-Relevanz, Onboarding-Fragen
    ONBOARDING.md          Fragenkatalog fuer den Owner (aus dem Schema erzeugt)
    versions/              freigegebene, unveraenderliche Versionen + index.json (Hash-Kette)
  prompts/             System-Prompts je Agent + _common.md (Owner-Regel fuer alle)
  agents/
    base.py            BaseAgent, actions-Block-Parser, JSON-Parser
    master.py          Planung (LLM-JSON-Plan / Stichworte / Rueckfragen), Bericht
    specialist.py      Spezial-Agenten, VideoAgent
    qa.py              QA: Regelpruefung, Berechtigungen, Entscheidungs-Signale, LLM-Review
    legal.py           Chief Legal & Compliance Agent (Vorpruefung + Schrittpruefung)
  core/
    rules.py           UNVERAENDERLICHE Owner-Regeln (geschuetzte/verbotene Aktionen)
    governance.py      Akteure, OwnerApprovalGate (inkl. Legal-Sperren), AuditLog (Hash-Kette)
    legal.py           Legal-Wissensbasis, Themen-/Rechtsraum-Erkennung, Bewertung
    textmatch.py       Stichwortsuche mit Umlaut-Normalisierung
    jobs.py            Task-Zustandsautomat (mit Akteur-Pruefung) + Job-Store
    models.py          gemeinsames Datenmodell (TaskStatus, Job, Plan, ...)
    bus.py             MessageBus (einziger Kommunikationsweg, Ausfuehrungssperre)
    config.py          Laden + Validieren + Fingerabdruck, frozen
    permissions.py     Berechtigungen (Least Privilege) + Aktions-Freigabe-Store
    llm.py             MockLLMClient, AnthropicLLMClient (gesperrt), Budget
    brand.py           BrandKnowledge (eingefrorene Version), Brand-Check, Agenten-Kontext
    brand_schema.py    Schema laden + Validierung (NOT_PROVIDED / UNKNOWN / NOT_APPLICABLE)
    brand_store.py     Versionierung (commit/history/diff/Integritaet) + BrandContextLoader
    brand_onboarding.py  erzeugt ONBOARDING.md aus dem Schema
    secrets.py, logging_setup.py, errors.py
tests/agent_system/    223 Tests (test_governance.py: Owner-Regel, test_legal.py: Legal & Compliance,
                       test_brand.py: Brand Knowledge Base),
                       Netzwerk in Tests hart blockiert
```

## Wie die Agenten kommunizieren

Nur ueber den MessageBus, jede Nachricht landet im Trace des Auftrags:

```
owner  ─task_assignment─▶ master          (Planung)
master ─plan_proposal───▶ owner           (Plan / Rueckfragen)
          ... Owner-Freigabe ueber das Gate ...
master ─legal_review_request─▶ legal      (Vorpruefung des Auftrags)
master ─task_assignment─▶ spezialist      (nur wenn Auftrag running)
spezialist ─task_result─▶ master
master ─legal_review_request─▶ legal ─legal_review_report─▶ master   (vor QA)
master ─qa_request──────▶ qa ─qa_report─▶ master
master ─approval_request▶ owner           (Vorschlaege, nie ausgefuehrt)
master ─final_result / stop_report──▶ owner
```

Feste Schnittstellen: `MasterAgent.plan(Job) -> Plan`,
`SpecialistAgent.handle(AgentRequest) -> AgentResponse`,
`QAAgent.review(AgentRequest, AgentResponse) -> QAReport`. Agenten schlagen
Aktionen in einem ```` ```actions ```` -JSON-Block vor; die QA prueft jede
einzelne gegen Agenten-Rechte und Owner-Regeln.

## Brand Knowledge Base

Zentrale, versionierte Wissensbasis fuer die gesamte Workforce
(`agent_system/brand/`). **Es wird nichts erfunden:** Jedes Feld steht auf
`NOT_PROVIDED`, bis der Owner es beantwortet. Weitere Platzhalter: `UNKNOWN`
(Owner weiss es nicht) und `NOT_APPLICABLE` (trifft nicht zu - bei Kernangaben
wie Brandname nicht erlaubt).

**10 Bereiche** (Details und Fragen: `brand/ONBOARDING.md`): Brand Identity,
Angebot, Zielgruppen, Positionierung, Brand Voice, Visuelle Identitaet,
Content, No-Gos, Recht & Compliance, Strategische Ziele.

**Ablauf fuer den Owner:**
1. Fragen in `brand/ONBOARDING.md` lesen, Antworten in
   `brand/brand_knowledge.yaml` eintragen (keine Kundendaten!).
2. `python -m agent_system brand check` - validiert (Tippfehler, falsche
   Typen, ungueltige Laendercodes, unbekannte Felder) und listet fehlende
   Pflichtinformationen, auch je Agent.
3. `python -m agent_system brand commit --note "..."` - gibt eine neue,
   unveraenderliche Version frei (nur Owner, auditiert).

**Versionierung:** Jede Freigabe erzeugt `versions/vNNNN.yaml` und einen
Eintrag in `versions/index.json` mit SHA-256-Hash und Verkettung zur
Vorversion. Veraenderte Versionsdateien oder eine gebrochene Kette werden
beim Laden erkannt - die Agenten arbeiten dann nicht mit manipulierten Daten.
`brand history`, `brand diff 1 2`, `brand show --version 1`.

**Wie die Agenten zugreifen (Brand-Context-Loader):**
- Agenten nutzen immer die **neueste freigegebene Version** - nie die
  Arbeitskopie. v1 ist die leere Vorlage.
- Pro Planung/Ausfuehrung wird genau **eine** Momentaufnahme geladen und
  allen Agenten des Teams uebergeben (gleiche Version, gleicher Hash).
- Jeder Agent bekommt nur seine relevanten Bereiche (`relevant_for` im
  Schema) plus eine Liste "NICHT ANGEGEBEN (nicht erfinden)".
- Auftrag und Audit-Log halten fest, mit welcher Brand-Version gearbeitet
  wurde. Wird die Brand-Basis nach einer Auftragsfreigabe geaendert, braucht
  der Auftrag eine erneute Freigabe.
- QA setzt `no_gos.statements` durch (Ueberarbeitung/Stopp) und warnt bei
  `brand_voice.words_to_avoid`. Legal nennt `legal_compliance.jurisdictions`
  als Hinweis, uebernimmt sie aber nie automatisch fuer einen Auftrag.
- Agenten koennen die Brand-Basis nicht aendern (`modify_brand_knowledge`
  ist verboten, `commit` nur fuer den Owner, Daten nur als Kopie).

## Ersten Agenten produktiv machen (erst nach deiner Freigabe)

1. Brand Knowledge Base ausfuellen und freigeben (siehe oben).
2. Freigabe fuer die Claude-API erteilen (kostenpflichtig). Erst dann:
   API-Key nur als Umgebungsvariable `ANTHROPIC_API_KEY`.
3. `AnthropicLLMClient.complete()` in `core/llm.py` implementieren
   (Messages-API; Timeouts/Rate-Limits als `LLMError`).
4. Pilot mit einem Agenten (z.B. Social), `provider: anthropic`,
   `max_llm_calls_per_job: 10`. Owner-Gate, QA und Audit bleiben unveraendert aktiv.
5. Erst viel spaeter und einzeln: echte Executor fuer freigegebene Aktionen.
