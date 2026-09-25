"""Owner-Schnittstelle (Kommandozeile) des Agentensystems.

Die CLI ist die Stelle, an der der Owner handelt. Agenten haben keinen Zugang
dazu. Typischer Ablauf:

    python -m agent_system submit "Plane eine Instagram-Kampagne fuer Produkt X"
        -> Master legt einen Plan vor (Status: waiting_for_owner). Nichts laeuft.
    python -m agent_system show JOB_ID                 # Plan + Fingerabdruck pruefen
    python -m agent_system approve JOB_ID --plan FP    # Owner-Freigabe genau dieses Plans
    python -m agent_system start JOB_ID                # Ausfuehrung (nur wenn freigegeben)

Weitere Owner-Befehle: clarify, cancel, resubmit, actions, approve-action,
reject-action, audit. Info: agents, brand check, tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core.brand import BRAND_SECTIONS
from .core.config import load_config
from .core.errors import AgentSystemError
from .core.governance import owner_session
from .core.logging_setup import setup_logging
from .core.models import Job
from .core.secrets import redact
from .orchestrator import Orchestrator

DEFAULT_DATA_DIR = Path(os.environ.get("AGENT_SYSTEM_DATA_DIR", "data/agent_system"))


def _print(text: str) -> None:
    print(redact(text))


def _print_job(job: Job, full: bool = False) -> None:
    _print(f"Auftrag {job.id}: {job.status.value}")
    _print(f"  Anweisung: {job.request}")
    if job.plan:
        _print(f"  Plan ({job.plan.source}), Fingerabdruck: {job.plan.fingerprint()}")
        if job.plan.rationale:
            _print(f"  Begruendung: {job.plan.rationale}")
        for i, s in enumerate(job.plan.steps, 1):
            verdict = f" QA={s.qa_report.verdict.value}" if s.qa_report else ""
            _print(f"   {i}. [{s.status.value}] {s.agent_id}: {s.instruction}{verdict}")
    for q in job.open_questions:
        _print(f"  ? RUECKFRAGE: {q}")
    if job.stop_reason:
        _print(f"  ! GESTOPPT: {job.stop_reason}")
    for r in job.recommendations:
        _print(f"  > Empfehlung: {r}")
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
    p.add_argument("--brand-file", default=None)
    p.add_argument("--verbose", action="store_true", help="Logs zusaetzlich auf der Konsole")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit", help="Neuen Auftrag erteilen (Master plant nur, fuehrt nichts aus)")
    s.add_argument("request")

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
    b = sub.add_parser("brand", help="Brand-Wissen pruefen")
    b.add_argument("action", choices=["check"])
    b.add_argument("--show-context", action="store_true")
    return p


def run_command(orch: Orchestrator, args) -> int:
    owner = owner_session(orch.config)
    cmd = args.command

    if cmd == "submit":
        _print_job(orch.submit(args.request, owner))
        return 0
    if cmd == "show":
        job = orch.jobs.get(args.job_id)
        if args.json:
            _print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_job(job, full=True)
        return 0
    if cmd == "tasks":
        for job in orch.jobs.list():
            _print(f"{job.id}  {job.status.value:<18} {job.created_at}  {job.request[:60]}")
        return 0
    if cmd == "approve":
        _print_job(orch.approve(args.job_id, owner, args.expected_plan))
        return 0
    if cmd == "start":
        job = orch.execute(args.job_id)
        _print_job(job, full=True)
        return 0 if job.status.value in ("completed", "waiting_for_owner") else 1
    if cmd == "cancel":
        _print_job(orch.cancel(args.job_id, owner, args.reason))
        return 0
    if cmd == "clarify":
        _print_job(orch.clarify(args.job_id, args.answer, owner))
        return 0
    if cmd == "resubmit":
        _print_job(orch.resubmit(args.job_id, owner))
        return 0
    if cmd == "actions":
        items = orch.approvals.all() if args.all else orch.approvals.pending()
        if not items:
            _print("Keine offenen Aktionsvorschlaege.")
        for a in items:
            _print(f"{a.id}  {a.status:<9} {a.action.action:<22} job={a.job_id}  {a.action.description}")
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
        brand = orch.brand
        missing = brand.missing_sections()
        _print(f"Brand-Datei: {brand.source}")
        _print(f"Vollstaendigkeit: {brand.completeness():.0%}")
        for key, label in BRAND_SECTIONS.items():
            _print(f"  [{' ' if key in missing else 'x'}] {label} ({key})")
        if args.show_context:
            _print("\n" + brand.to_prompt_context())
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    setup_logging(log_file=data_dir / "logs" / "agent_system.jsonl", console=args.verbose)
    try:
        config = load_config(config_dir=args.config_dir, brand_file=args.brand_file)
        return run_command(Orchestrator(config=config, data_dir=data_dir), args)
    except AgentSystemError as exc:
        print(f"Fehler ({exc.code}): {redact(str(exc))}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # z.B. Ausgabe an "head" weitergeleitet
        return 0


if __name__ == "__main__":
    sys.exit(main())
