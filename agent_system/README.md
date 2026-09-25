# Agentensystem (agent_system)

Eine echte Agenten-Orchestrierung statt neun einzelner Chatbots - unter der
**Owner-Regel**: Der Owner ist alleiniger Eigentuemer der Brand und der
gesamten AI-Workforce. Kein Agent beginnt, erweitert oder wiederholt einen
Auftrag ohne seine ausdrueckliche Freigabe.

```
Owner ──▶ Master-Agent ──▶ PLAN ──▶ Owner-Freigabe ──▶ Spezial-Agenten ──▶ QA ──▶ Master ──▶ Owner
           (analysiert,     (waiting_     (OwnerApproval-      (arbeiten)       (prueft)  (Bericht)
            plant nur)       for_owner)    Gate)
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

python -m agent_system audit [--job JOB_ID]           # Audit-Log + Integritaetspruefung
python -m agent_system agents                         # Agenten, Modelle, inaktive Agenten
python -m agent_system brand check                    # Was fehlt im Brand-Wissen?

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
| `qa` | Pruefinstanz | sonnet | Pruefen, Brand-Regeln, riskante Aktionen blockieren, Freigaben verlangen |

## Projektstruktur

```
agent_system/
  __main__.py          Owner-CLI
  orchestrator.py      Phasen Planen / Freigeben / Ausfuehren, Stopp-und-Melden
  config/
    governance.yaml    Owner-ID, freigegebene Agenten
    agents.yaml        Agenten-Definitionen, Orchestrierung (Retries standardmaessig 0)
    models.yaml        Modell-Stufen, LLM-Provider (mock|anthropic), Budget
    permissions.yaml   Aktionen + Policy (allow / require_approval / deny)
  brand/brand_knowledge.yaml   zentrale Brand-Wissensbasis
  prompts/             System-Prompts je Agent + _common.md (Owner-Regel fuer alle)
  agents/
    base.py            BaseAgent, actions-Block-Parser, JSON-Parser
    master.py          Planung (LLM-JSON-Plan / Stichworte / Rueckfragen), Bericht
    specialist.py      Spezial-Agenten, VideoAgent
    qa.py              QA: Regelpruefung, Berechtigungen, Entscheidungs-Signale, LLM-Review
  core/
    rules.py           UNVERAENDERLICHE Owner-Regeln (geschuetzte/verbotene Aktionen)
    governance.py      Akteure, OwnerApprovalGate, AuditLog (Hash-Kette)
    jobs.py            Task-Zustandsautomat (mit Akteur-Pruefung) + Job-Store
    models.py          gemeinsames Datenmodell (TaskStatus, Job, Plan, ...)
    bus.py             MessageBus (einziger Kommunikationsweg, Ausfuehrungssperre)
    config.py          Laden + Validieren + Fingerabdruck, frozen
    permissions.py     Berechtigungen (Least Privilege) + Aktions-Freigabe-Store
    llm.py             MockLLMClient, AnthropicLLMClient (gesperrt), Budget
    brand.py, secrets.py, logging_setup.py, errors.py
tests/agent_system/    145 Tests (davon test_governance.py fuer die Owner-Regel),
                       Netzwerk in Tests hart blockiert
```

## Wie die Agenten kommunizieren

Nur ueber den MessageBus, jede Nachricht landet im Trace des Auftrags:

```
owner  ─task_assignment─▶ master          (Planung)
master ─plan_proposal───▶ owner           (Plan / Rueckfragen)
          ... Owner-Freigabe ueber das Gate ...
master ─task_assignment─▶ spezialist      (nur wenn Auftrag running)
spezialist ─task_result─▶ master
master ─qa_request──────▶ qa ─qa_report─▶ master
master ─approval_request▶ owner           (Vorschlaege, nie ausgefuehrt)
master ─final_result / stop_report──▶ owner
```

Feste Schnittstellen: `MasterAgent.plan(Job) -> Plan`,
`SpecialistAgent.handle(AgentRequest) -> AgentResponse`,
`QAAgent.review(AgentRequest, AgentResponse) -> QAReport`. Agenten schlagen
Aktionen in einem ```` ```actions ```` -JSON-Block vor; die QA prueft jede
einzelne gegen Agenten-Rechte und Owner-Regeln.

## Brand-Informationen eintragen

1. `agent_system/brand/brand_knowledge.yaml` ausfuellen (Brandname, Mission,
   Produkte, Dienstleistungen, Zielgruppe, Positionierung, Tonalitaet,
   Markenwerte, Designregeln, No-Go-Regeln, Ziele, Wettbewerber, bestehende
   Inhalte). Leere Felder sind erlaubt.
2. `no_go_rules.forbidden_words` erzwingt die QA automatisch.
3. `python -m agent_system brand check --show-context` zeigt Luecken und den
   Kontext, den jeder Agent bekommt.

## Ersten Agenten produktiv machen (erst nach deiner Freigabe)

1. Brand-Wissen eintragen.
2. Freigabe fuer die Claude-API erteilen (kostenpflichtig). Erst dann:
   API-Key nur als Umgebungsvariable `ANTHROPIC_API_KEY`.
3. `AnthropicLLMClient.complete()` in `core/llm.py` implementieren
   (Messages-API; Timeouts/Rate-Limits als `LLMError`).
4. Pilot mit einem Agenten (z.B. Social), `provider: anthropic`,
   `max_llm_calls_per_job: 10`. Owner-Gate, QA und Audit bleiben unveraendert aktiv.
5. Erst viel spaeter und einzeln: echte Executor fuer freigegebene Aktionen.
