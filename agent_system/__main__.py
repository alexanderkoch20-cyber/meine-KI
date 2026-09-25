"""Kommandozeile fuer das Agentensystem.

    python -m agent_system run "Plane eine Instagram-Kampagne fuer unser neues Produkt"
    python -m agent_system agents
    python -m agent_system brand check
    python -m agent_system jobs [JOB_ID]
    python -m agent_system approvals
    python -m agent_system approve APR_ID   /   reject APR_ID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core.brand import BRAND_SECTIONS, BrandKnowledge
from .core.config import load_config
from .core.errors import AgentSystemError
from .core.logging_setup import setup_logging
from .core.secrets import redact
from .orchestrator import Orchestrator

DEFAULT_DATA_DIR = Path(os.environ.get("AGENT_SYSTEM_DATA_DIR", "data/agent_system"))


def _print(text: str) -> None:
    print(redact(text))


def cmd_run(orch: Orchestrator, args) -> int:
    job = orch.handle(args.request)
    if args.json:
        _print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print(f"Job {job.id}: {job.status.value}")
        if job.plan:
            _print(f"Plan ({job.plan.source}): {job.plan.rationale}")
            for s in job.plan.steps:
                verdict = s.qa_report.verdict.value if s.qa_report else "-"
                _print(f"  - {s.agent_id:<10} {s.status.value:<8} QA={verdict}")
        if job.error:
            _print(f"Fehler: {job.error}")
        if job.final_output:
            _print("\n" + job.final_output)
    return 0 if job.status.value in ("completed", "awaiting_approval", "partially_completed") else 1


def cmd_agents(orch: Orchestrator, args) -> int:
    for a in orch.config.agents.values():
        tier = orch.config.tiers[a.model_tier]
        _print(f"{a.id:<10} {a.role:<10} {a.model_tier:<6} {tier.model_id:<28} {a.name}")
    _print(f"\nLLM-Provider: {orch.config.provider}")
    return 0


def cmd_brand(orch: Orchestrator, args) -> int:
    brand: BrandKnowledge = orch.brand
    missing = brand.missing_sections()
    _print(f"Brand-Datei: {brand.source}")
    _print(f"Vollstaendigkeit: {brand.completeness():.0%}")
    for key, label in BRAND_SECTIONS.items():
        _print(f"  [{' ' if key in missing else 'x'}] {label} ({key})")
    if args.show_context:
        _print("\n" + brand.to_prompt_context())
    return 0


def cmd_jobs(orch: Orchestrator, args) -> int:
    if args.job_id:
        _print(json.dumps(orch.jobs.load_snapshot(args.job_id), indent=2, ensure_ascii=False))
        return 0
    for snap in orch.jobs.list_snapshots():
        _print(f"{snap['id']}  {snap['status']:<20} {snap['created_at']}  {snap['request'][:60]}")
    return 0


def cmd_approvals(orch: Orchestrator, args) -> int:
    items = orch.approvals.all() if args.all else orch.approvals.pending()
    if not items:
        _print("Keine offenen Freigaben.")
    for a in items:
        _print(f"{a.id}  {a.status:<9} {a.action.action:<22} job={a.job_id}  {a.action.description}")
    return 0


def cmd_decide(orch: Orchestrator, args, approve: bool) -> int:
    apr = orch.decide_approval(args.approval_id, approve)
    _print(f"{apr.id}: {apr.status}. Hinweis: Es wurde nichts extern ausgefuehrt (Dry-Run).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_system", description="KI-Agenten-Orchestrierung")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Ablage fuer Jobs, Freigaben, Logs")
    p.add_argument("--config-dir", default=None)
    p.add_argument("--brand-file", default=None)
    p.add_argument("--verbose", action="store_true", help="Logs zusaetzlich auf der Konsole")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Auftrag an den Master-Agenten geben")
    r.add_argument("request")
    r.add_argument("--json", action="store_true")

    sub.add_parser("agents", help="Agenten und Modelle anzeigen")

    b = sub.add_parser("brand", help="Brand-Wissen pruefen")
    b.add_argument("action", choices=["check"])
    b.add_argument("--show-context", action="store_true")

    j = sub.add_parser("jobs", help="Jobs auflisten / anzeigen")
    j.add_argument("job_id", nargs="?")

    a = sub.add_parser("approvals", help="Offene Freigaben anzeigen")
    a.add_argument("--all", action="store_true")

    for name in ("approve", "reject"):
        d = sub.add_parser(name, help=f"Freigabe {'erteilen' if name == 'approve' else 'ablehnen'}")
        d.add_argument("approval_id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    setup_logging(log_file=data_dir / "logs" / "agent_system.jsonl", console=args.verbose)
    try:
        config = load_config(config_dir=args.config_dir, brand_file=args.brand_file)
        orch = Orchestrator(config=config, data_dir=data_dir)
        if args.command == "run":
            return cmd_run(orch, args)
        if args.command == "agents":
            return cmd_agents(orch, args)
        if args.command == "brand":
            return cmd_brand(orch, args)
        if args.command == "jobs":
            return cmd_jobs(orch, args)
        if args.command == "approvals":
            return cmd_approvals(orch, args)
        if args.command in ("approve", "reject"):
            return cmd_decide(orch, args, args.command == "approve")
    except AgentSystemError as exc:
        print(f"Fehler ({exc.code}): {redact(str(exc))}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
