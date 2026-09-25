# Agentensystem (agent_system)

Eine echte Agenten-Orchestrierung statt neun einzelner Chatbots:

```
User ──▶ Master-Agent ──▶ Spezial-Agenten ──▶ QA-Agent ──▶ Master-Agent ──▶ User
            (Opus)          (Sonnet/Haiku)      (Regeln + LLM)   (Synthese)
```

Der aktuelle Stand laeuft **komplett offline** (Mock-LLM): keine API-Kosten,
keine Netzwerkaufrufe, keine externen Aktionen. Die Architektur ist aber
vollstaendig - fuer den Produktivbetrieb wird nur der LLM-Client getauscht.

## Schnellstart

```bash
pip install -r requirements-agents.txt
python -m agent_system agents                 # Agenten + Modelle
python -m agent_system brand check            # Was fehlt im Brand-Wissen?
python -m agent_system run "Recherchiere Trends und plane eine Kampagne mit Instagram-Reels"
python -m agent_system jobs                   # alle Jobs
python -m agent_system jobs JOB_ID            # kompletter Job inkl. Plan, QA, Trace
python -m agent_system approvals              # offene Freigaben
python -m agent_system approve APR_ID         # Freigabe erteilen (Dry-Run!)
python -m agent_system reject  APR_ID
pytest tests/agent_system
```

Jobs, Freigaben und Logs landen in `data/agent_system/` (per `--data-dir`
oder `AGENT_SYSTEM_DATA_DIR` aenderbar, nicht versioniert).

## Agenten

| ID | Rolle | Modell-Stufe | Aufgaben |
|---|---|---|---|
| `master` | Orchestrator / CEO | opus | zerlegen, delegieren, zusammenfuehren |
| `marketing` | Spezialist | sonnet | Strategie, Kampagnen, Angebote, Positionierung, Conversion-Texte |
| `social` | Spezialist | sonnet | Content-Ideen, Posts, Reels, Content-Kalender, Plattform-Varianten |
| `video` | Spezialist | sonnet | Konzepte, Storyboards, Prompts, Produktion, spaeter CineMotion |
| `creative` | Spezialist | sonnet | Konzepte, Bildideen, Kampagnenvisuals, Design-Richtlinien |
| `research` | Spezialist | sonnet | Markt, Wettbewerb, Trends, Zielgruppe, Quellen |
| `coding` | Spezialist | sonnet | Software, Bugfixes, Tests, Infrastruktur |
| `routine` | Spezialist | haiku | Formatierung, Klassifizierung, kurze Zusammenfassungen |
| `qa` | Pruefinstanz | sonnet | Pruefen, Brand-Regeln, riskante Aktionen blockieren, Freigaben |

Modell-IDs stehen nur an einer Stelle: `config/models.yaml`.

## Projektstruktur

```
agent_system/
  __main__.py          CLI
  orchestrator.py      Laufzeit-Engine: Plan ausfuehren, Retries, QA-Schleife, Freigaben
  config/
    agents.yaml        Agenten-Definitionen (Rolle, Modell, Keywords, erlaubte Aktionen)
    models.yaml        Modell-Stufen, LLM-Provider (mock|anthropic), Budget
    permissions.yaml   Aktionen + Policy (allow / require_approval / deny)
  brand/
    brand_knowledge.yaml   zentrale Brand-Wissensbasis (hier traegst du ein)
  prompts/             System-Prompts je Agent + _common.md (Sicherheitsregeln fuer alle)
  agents/
    base.py            BaseAgent, actions-Block-Parser, JSON-Parser
    master.py          Planung (LLM-JSON-Plan + regelbasiertes Fallback), Synthese
    specialist.py      Spezial-Agenten (gemeinsame Schnittstelle), VideoAgent
    qa.py              QA: Regelpruefung + optionaler LLM-Review
  core/
    models.py          gemeinsames Datenmodell (Job, Plan, AgentRequest/Response, QAReport, ...)
    config.py          Laden + Validieren der Konfiguration
    brand.py           Brand-Knowledge laden, Vollstaendigkeit, Prompt-Kontext
    bus.py             MessageBus - einziger Kommunikationsweg, schreibt den Trace
    jobs.py            Job-Store + Zustandsautomat
    permissions.py     Berechtigungen (Least Privilege) + Freigabe-Store
    llm.py             LLM-Schnittstelle: MockLLMClient, AnthropicLLMClient (gesperrt), Budget
    secrets.py         Schwaerzung von API-Schluesseln
    logging_setup.py   JSON-Logging mit job_id/step_id/agent + Schwaerzung
    errors.py          Fehlerhierarchie (retryable vs. nicht retryable)
tests/agent_system/    72 Tests, Netzwerk in Tests hart blockiert
```

## Wie die Agenten kommunizieren

1. **Nur ueber den MessageBus.** Kein Agent ruft einen anderen direkt auf.
   Jede Nachricht ist ein `AgentMessage` (sender, recipient, type, job_id,
   step_id, payload) und wird im `trace` des Jobs protokolliert (Secrets
   geschwaerzt).
2. **Feste Schnittstellen:**
   - Master: `plan(Job) -> Plan` und `synthesize(Job, approvals) -> str`
   - Spezialist: `handle(AgentRequest) -> AgentResponse`
   - QA: `review(AgentRequest, AgentResponse) -> QAReport`
3. **Ablauf eines Jobs** (`created -> planning -> running -> qa_review ->
   completed | awaiting_approval | partially_completed | failed`):
   - `user -> master` (task_assignment): Master plant. Zuerst per LLM als
     JSON-Plan (streng validiert: nur bekannte Spezialisten, gueltige
     Abhaengigkeiten, keine Zyklen, max. Schrittzahl), bei unbrauchbarer
     Antwort per Stichwort-Routing.
   - `master -> spezialist` (task_assignment) je Schritt in
     Abhaengigkeitsreihenfolge. Ergebnisse vorheriger Schritte werden als
     `upstream_results` mitgegeben (z.B. Research -> Marketing -> Social).
   - `spezialist -> master` (task_result), dann `master -> qa`
     (qa_request) und `qa -> master` (qa_report).
   - QA-Urteil `needs_revision` -> eine Ueberarbeitungsrunde mit
     QA-Feedback; bleibt es schlecht oder ist es `blocked`, wird der Inhalt
     **nicht** ausgeliefert.
   - `master -> user` (approval_request) fuer jede Aktion mit externer Wirkung,
     dann `master -> user` (final_result).
4. **Aktionen:** Agenten fuehren nie etwas aus. Sie schlagen Aktionen in
   einem ```` ```actions ```` -JSON-Block vor. Die QA prueft jede Aktion:
   - Darf dieser Agent die Aktion ueberhaupt vorschlagen? (`allowed_actions`)
   - Policy in `permissions.yaml`: `allow`, `require_approval`, `deny`.
     Unbekannte Aktionen -> `require_approval`.

## Sicherheit

- Geld ausgeben, veroeffentlichen, Kunden anschreiben, Accounts loeschen,
  Dateien endgueltig loeschen, Vertraege, externe APIs, Video-Rendering:
  **`require_approval`**. Selbst nach Freigabe wird nichts ausgefuehrt
  (es existieren noch keine Executor - Dry-Run, nur protokolliert).
- API-Schluessel ausgeben: **`deny`** (auch mit Freigabe nicht). Zusaetzlich
  schwaerzt `secrets.py` Schluessel in Logs, Trace, CLI-Ausgabe; die QA
  blockiert Ergebnisse, die Schluessel enthalten.
- `AnthropicLLMClient` ist bewusst gesperrt und wirft
  `ExternalServiceNotApprovedError` - es kann kein kostenpflichtiger Aufruf
  "aus Versehen" passieren.
- LLM-Budget pro Job (`models.yaml: budget.max_llm_calls_per_job`).
- Fehler: technische Fehler werden bis zu `max_retries` wiederholt, nicht
  wiederholbare (Freigabe fehlt, Budget) brechen sofort ab; Folgeschritte
  werden `skipped`; unerwartete Exceptions bringen nie den Prozess zum Absturz.

## Brand-Informationen eintragen

1. `agent_system/brand/brand_knowledge.yaml` oeffnen und die Felder
   ausfuellen (Brandname, Mission, Produkte, Dienstleistungen, Zielgruppe,
   Positionierung, Tonalitaet, Markenwerte, Designregeln, No-Go-Regeln,
   Ziele, Wettbewerber, bestehende Inhalte). Leere Felder sind erlaubt.
2. `no_go_rules.forbidden_words` wird von der QA **automatisch erzwungen**:
   Ergebnisse mit diesen Woertern werden zur Ueberarbeitung zurueckgeschickt
   und notfalls nicht ausgeliefert.
3. `python -m agent_system brand check --show-context` zeigt, was fehlt, und
   genau den Kontext, den jeder Agent bekommt.
4. Alternativ eigene Datei: `python -m agent_system --brand-file meine_marke.yaml ...`

## Ersten Agenten produktiv machen (naechste Schritte)

Empfohlene Reihenfolge - jeder Schritt braucht deine ausdrueckliche Freigabe,
weil er die kostenpflichtige Claude-API nutzt:

1. **Brand-Wissen eintragen** (siehe oben) - ohne das bleiben Ergebnisse generisch.
2. **API-Key bereitstellen** - nur als Umgebungsvariable `ANTHROPIC_API_KEY`,
   nie in Dateien/Git.
3. **`AnthropicLLMClient.complete()` implementieren** (`core/llm.py`) mit dem
   offiziellen `anthropic`-SDK: `request.system`, `request.prompt`,
   `request.tier.model_id/max_tokens/temperature` auf die Messages-API
   abbilden; Timeouts/Rate-Limits als `LLMError` (retryable) melden.
4. **Pilot mit einem Agenten**, z.B. dem Social-Agenten: in `models.yaml`
   `provider: anthropic` setzen, Budget klein halten
   (`max_llm_calls_per_job: 10`), und nur Auftraege geben, die auf `social`
   geroutet werden. Master-Planung und QA-Review nutzen dann automatisch
   ebenfalls das echte Modell (inkl. JSON-Plan) - die Regelpruefungen der
   QA und die Freigabepflicht bleiben unveraendert aktiv.
5. **Ergebnisse bewerten**, Prompts in `prompts/social.md` nachschaerfen,
   dann schrittweise weitere Agenten.
6. **Erst danach** echte Executor fuer freigegebene Aktionen bauen (z.B.
   Instagram-Entwurf anlegen, CineMotion-Rendering fuer `generate_video`) -
   jeweils einzeln, mit eigener Freigabe.
