"""Owner-Schnittstelle (Kommandozeile) des Agentensystems.

Die CLI ist die Stelle, an der der Owner handelt. Agenten haben keinen Zugang
dazu. Typischer Ablauf:

    python -m agent_system submit "Plane eine Instagram-Kampagne fuer Produkt X"
        -> Master legt einen Plan vor (Status: waiting_for_owner). Nichts laeuft.
    python -m agent_system show JOB_ID                 # Plan + Fingerabdruck pruefen
    python -m agent_system approve JOB_ID --plan FP    # Owner-Freigabe genau dieses Plans
    python -m agent_system start JOB_ID                # Ausfuehrung (nur wenn freigegeben)

Legal & Compliance:
    python -m agent_system submit "..." -j DE -j AT     # Rechtsraeume angeben
    python -m agent_system jurisdictions JOB_ID DE AT   # Rechtsraeume nachtragen
    python -m agent_system legal JOB_ID                 # Legal-Bericht mit Quellen
    python -m agent_system legal-review-done JOB_ID --reviewer "RA Muster" --note "..."

Weitere Owner-Befehle: clarify, cancel, resubmit, actions, approve-action,
reject-action, audit. Info: agents, tasks.

Brand Knowledge Base (agent_system/brand/):
    python -m agent_system brand check [--agent social]  # fehlende Pflichtinformationen
    python -m agent_system brand show [--agent social] [--version N]
    python -m agent_system brand commit --note "..."     # neue Version freigeben (nur Owner)
    python -m agent_system brand history | diff 1 2 | onboarding [--write]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core.config import load_config
from .core.errors import AgentSystemError
from .core.governance import owner_session
from .core.brand_onboarding import render_onboarding
from .core.brand_store import BrandRepository
from .core.legal import format_review
from .core.logging_setup import setup_logging
from .core.models import Job
from .core.secrets import redact
from .orchestrator import Orchestrator

DEFAULT_DATA_DIR = Path(os.environ.get("AGENT_SYSTEM_DATA_DIR", "data/agent_system"))


def _print(text: str) -> None:
    print(redact(text))


def _print_job(orch: Orchestrator, job: Job, full: bool = False) -> None:
    _print(f"Auftrag {job.id}: {job.status.value}")
    _print(f"  Anweisung: {job.request}")
    if job.plan:
        _print(f"  Plan ({job.plan.source}), Fingerabdruck: {job.plan.fingerprint()}")
        if job.plan.rationale:
            _print(f"  Begruendung: {job.plan.rationale}")
        for i, s in enumerate(job.plan.steps, 1):
            verdict = f" QA={s.qa_report.verdict.value}" if s.qa_report else ""
            _print(f"   {i}. [{s.status.value}] {s.agent_id}: {s.instruction}{verdict}")
    _print(f"  Rechtsraeume: {', '.join(job.jurisdictions) or 'unbekannt (es wird keiner angenommen)'}")
    _print(f"  Legal & Compliance: {job.legal_status.value}")
    for q in job.open_questions:
        _print(f"  ? RUECKFRAGE: {q}")
    if job.stop_reason:
        _print(f"  ! GESTOPPT: {job.stop_reason}")
    for r in job.recommendations:
        _print(f"  > Empfehlung: {r}")
    if job.status.value == "waiting_for_owner" and orch.gate.open_legal_reviews(job):
        _print(f"\nLegal & Compliance verlangt eine MENSCHLICHE Rechtspruefung. Details:\n"
               f"  python -m agent_system legal {job.id}\n"
               f"Nach der Pruefung dokumentieren:\n"
               f"  python -m agent_system legal-review-done {job.id} --reviewer \"Name\" --note \"Ergebnis\"")
    if job.status.value == "waiting_for_owner":
        if job.open_questions:
            _print(f"\nNaechster Schritt: python -m agent_system clarify {job.id} \"Deine Antwort\"")
        else:
            _print(f"\nNichts wird ausgefuehrt, bis du freigibst:\n"
                   f"  python -m agent_system approve {job.id} --plan {job.plan.fingerprint()}\n"
                   f"  python -m agent_system cancel {job.id}")
    if job.status.value == "approved":
        _print(f"\nFreigegeben. Starten mit: python -m agent_system start {job.id}")
    if full and job.final_output:
        _print("\n" + job.final_output)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_system", description="KI-Agenten-Orchestrierung (Owner-CLI)")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Ablage fuer Jobs, Freigaben, Audit, Logs")
    p.add_argument("--config-dir", default=None)
    p.add_argument("--brand-dir", default=None, help="Ordner der Brand Knowledge Base")
    p.add_argument("--verbose", action="store_true", help="Logs zusaetzlich auf der Konsole")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit", help="Neuen Auftrag erteilen (Master plant nur, fuehrt nichts aus)")
    s.add_argument("request")
    s.add_argument("-j", "--jurisdiction", action="append", default=[],
                   help="Rechtsraum, mehrfach moeglich (z.B. -j DE -j AT)")

    ju = sub.add_parser("jurisdictions", help="Rechtsraeume eines Auftrags festlegen (vor der Ausfuehrung)")
    ju.add_argument("job_id")
    ju.add_argument("codes", nargs="+")

    lg = sub.add_parser("legal", help="Legal-&-Compliance-Bericht eines Auftrags anzeigen")
    lg.add_argument("job_id")

    lr = sub.add_parser("legal-review-done", help="Menschliche Rechtspruefung dokumentieren (nur Owner)")
    lr.add_argument("job_id")
    lr.add_argument("--reviewer", required=True, help="Wer hat geprueft (z.B. Kanzlei/Name)")
    lr.add_argument("--note", default="", help="Ergebnis/Auflagen der Pruefung")

    sh = sub.add_parser("show", help="Auftrag inkl. Plan und Ergebnis anzeigen")
    sh.add_argument("job_id")
    sh.add_argument("--json", action="store_true")

    sub.add_parser("tasks", help="Alle Auftraege auflisten")

    a = sub.add_parser("approve", help="Auftrag freigeben (nur Owner)")
    a.add_argument("job_id")
    a.add_argument("--plan", dest="expected_plan", default=None,
                   help="Fingerabdruck des geprueften Plans (empfohlen)")

    st = sub.add_parser("start", help="Freigegebenen Auftrag ausfuehren")
    st.add_argument("job_id")

    c = sub.add_parser("cancel", help="Auftrag abbrechen")
    c.add_argument("job_id")
    c.add_argument("--reason", default="")

    cl = sub.add_parser("clarify", help="Rueckfrage beantworten / Entscheidung treffen")
    cl.add_argument("job_id")
    cl.add_argument("answer")

    r = sub.add_parser("resubmit", help="Gescheiterten/abgebrochenen Auftrag als NEUEN Auftrag vorlegen")
    r.add_argument("job_id")

    ac = sub.add_parser("actions", help="Aktionsvorschlaege der Agenten anzeigen")
    ac.add_argument("--all", action="store_true")
    for name in ("approve-action", "reject-action"):
        d = sub.add_parser(name, help="Aktionsvorschlag entscheiden (Dry-Run, nichts wird ausgefuehrt)")
        d.add_argument("approval_id")

    au = sub.add_parser("audit", help="Audit-Log anzeigen und Integritaet pruefen")
    au.add_argument("--job", default=None)

    sub.add_parser("agents", help="Agenten und Modelle anzeigen")
    b = sub.add_parser("brand", help="Brand Knowledge Base: check, show, commit, history, diff, onboarding")
    b.add_argument("action", choices=["check", "show", "commit", "history", "diff", "onboarding"])
    b.add_argument("versions", nargs="*", type=int, help="bei diff: zwei Versionsnummern")
    b.add_argument("--agent", default=None, help="Sicht eines bestimmten Agenten")
    b.add_argument("--version", type=int, default=None, help="bei show: bestimmte Version")
    b.add_argument("--note", default="", help="bei commit: Aenderungsnotiz")
    b.add_argument("--write", action="store_true", help="bei onboarding: ONBOARDING.md neu schreiben")
    b.add_argument("--show-context", action="store_true", help="bei check: Agenten-Kontext mit ausgeben")
    return p


def run_brand(orch: Orchestrator, args, owner) -> int:
    repo = BrandRepository(orch.config.brand_dir)
    action = args.action
    if action == "check":
        from .core.brand import BrandKnowledge, read_brand_yaml

        validation = repo.validate_working()
        if not validation.ok:
            for err in validation.errors:
                _print(f"FEHLER: {err}")
            return 2
        working = BrandKnowledge(read_brand_yaml(repo.working_path), repo.schema, source=repo.working_path)
        agents = [args.agent] if args.agent else list(orch.config.agents)
        report = working.check(agents, validation, known_jurisdictions=orch.config.legal.jurisdictions)
        current = repo.current()
        _print(f"Arbeitskopie: {repo.working_path}")
        _print(f"Freigegeben (von Agenten genutzt): {current.info.label} "
               f"({current.info.committed_at or '-'}, {current.info.committed_by or '-'})")
        if repo.has_uncommitted_changes():
            _print("ACHTUNG: Die Arbeitskopie hat Aenderungen, die noch NICHT freigegeben sind "
                   "-> python -m agent_system brand commit --note \"...\"")
        _print("")
        _print(report.to_text(repo.schema, title="Pruefung der Arbeitskopie"))
        if args.show_context:
            _print("\n" + working.to_prompt_context(args.agent))
        return 0
    if action == "show":
        brand = repo.load_version(args.version) if args.version else repo.current()
        _print(brand.to_prompt_context(args.agent))
        return 0
    if action == "commit":
        info = orch.commit_brand(owner, args.note)
        _print(f"Brand Knowledge Base {info.label} freigegeben (Hash {info.content_hash[:12]}). "
               "Alle Agenten nutzen ab jetzt diese Version. Freigegebene, noch nicht gestartete Auftraege "
               "brauchen eine erneute Freigabe.")
        return 0
    if action == "history":
        for e in repo.history():
            _print(f"v{e['version']:<4} {e['committed_at']}  {e['committed_by']:<18} {e['content_hash'][:12]}  "
                   f"{e.get('note') or ''}")
        problems = repo.verify_index()
        _print("Integritaet: " + ("OK" if not problems else "VERLETZT - " + "; ".join(problems)))
        return 0 if not problems else 3
    if action == "diff":
        if len(args.versions) != 2:
            _print("Bitte zwei Versionen angeben, z.B.: brand diff 1 2")
            return 2
        changes = repo.diff(*args.versions)
        _print("\n".join(changes) if changes else "Keine Unterschiede.")
        return 0
    if action == "onboarding":
        text = render_onboarding(repo.schema)
        path = repo.dir / "ONBOARDING.md"
        if args.write:
            path.write_text(text, encoding="utf-8")
            _print(f"{path} aktualisiert.")
        else:
            _print(text)
        return 0
    return 1


def run_command(orch: Orchestrator, args) -> int:
    owner = owner_session(orch.config)
    cmd = args.command

    if cmd == "submit":
        _print_job(orch, orch.submit(args.request, owner, jurisdictions=args.jurisdiction))
        return 0
    if cmd == "jurisdictions":
        _print_job(orch, orch.set_jurisdictions(args.job_id, args.codes, owner))
        return 0
    if cmd == "legal":
        job = orch.jobs.get(args.job_id)
        _print(f"Legal & Compliance fuer {job.id}: {job.legal_status.value}\n")
        if job.legal_precheck:
            _print(format_review(job.legal_precheck, "Vorpruefung Auftrag") + "\n")
        for step in (job.plan.steps if job.plan else []):
            if step.legal_review:
                _print(format_review(step.legal_review, f"Schritt {step.agent_id}") + "\n")
        return 0
    if cmd == "legal-review-done":
        _print_job(orch, orch.record_human_legal_review(args.job_id, args.reviewer, args.note, owner))
        _print("\nMenschliche Rechtspruefung dokumentiert. Die Owner-Freigabe ist weiterhin erforderlich.")
        return 0
    if cmd == "show":
        job = orch.jobs.get(args.job_id)
        if args.json:
            _print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_job(orch, job, full=True)
        return 0
    if cmd == "tasks":
        for job in orch.jobs.list():
            _print(f"{job.id}  {job.status.value:<18} {job.legal_status.value:<28} {job.created_at}  "
                   f"{job.request[:50]}")
        return 0
    if cmd == "approve":
        _print_job(orch, orch.approve(args.job_id, owner, args.expected_plan))
        return 0
    if cmd == "start":
        job = orch.execute(args.job_id)
        _print_job(orch, job, full=True)
        return 0 if job.status.value in ("completed", "waiting_for_owner") else 1
    if cmd == "cancel":
        _print_job(orch, orch.cancel(args.job_id, owner, args.reason))
        return 0
    if cmd == "clarify":
        _print_job(orch, orch.clarify(args.job_id, args.answer, owner))
        return 0
    if cmd == "resubmit":
        _print_job(orch, orch.resubmit(args.job_id, owner))
        return 0
    if cmd == "actions":
        items = orch.approvals.all() if args.all else orch.approvals.pending()
        if not items:
            _print("Keine offenen Aktionsvorschlaege.")
        for a in items:
            _print(f"{a.id}  {a.status:<9} {a.action.action:<22} {a.legal_status:<28} job={a.job_id}  "
                   f"{a.action.description}")
        return 0
    if cmd in ("approve-action", "reject-action"):
        apr = orch.decide_action(args.approval_id, cmd == "approve-action", owner)
        _print(f"{apr.id}: {apr.status}. Hinweis: Es wurde nichts ausgefuehrt (Dry-Run, kein Executor).")
        return 0
    if cmd == "audit":
        for e in orch.audit.entries(job_id=args.job):
            _print(f"#{e['seq']:<4} {e['at']}  {e['event']:<22} {e['actor_kind']}:{e['actor']:<12} "
                   f"{e['job_id'] or '-'}  {json.dumps(e['details'], ensure_ascii=False)[:120]}")
        ok = orch.audit.verify()
        _print(f"\nIntegritaet der Hash-Kette: {'OK' if ok else 'VERLETZT - Log wurde veraendert!'}")
        return 0 if ok else 3
    if cmd == "agents":
        for a in orch.config.agents.values():
            tier = orch.config.tiers[a.model_tier]
            _print(f"{a.id:<10} {a.role:<10} {a.model_tier:<6} {tier.model_id:<28} {a.name}")
        for agent_id in orch.config.inactive_agents:
            _print(f"{agent_id:<10} INAKTIV - nicht in governance.yaml freigegeben")
        _print(f"\nLLM-Provider: {orch.config.provider}   Owner: {orch.config.governance.owner_name}")
        return 0
    if cmd == "brand":
        return run_brand(orch, args, owner)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    setup_logging(log_file=data_dir / "logs" / "agent_system.jsonl", console=args.verbose)
    try:
        config = load_config(config_dir=args.config_dir, brand_dir=args.brand_dir)
        return run_command(Orchestrator(config=config, data_dir=data_dir), args)
    except AgentSystemError as exc:
        print(f"Fehler ({exc.code}): {redact(str(exc))}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # z.B. Ausgabe an "head" weitergeleitet
        return 0


if __name__ == "__main__":
    sys.exit(main())
