from __future__ import annotations

import json

from agent_system.__main__ import main


def _job_id(tmp_path):
    return next((tmp_path / "jobs").glob("job_*.json")).stem


def test_cli_owner_workflow_across_processes(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path)]
    assert main(d + ["submit", "Schreibe einen Instagram-Post"]) == 0
    out = capsys.readouterr().out
    assert "waiting_for_owner" in out and "Nichts wird ausgefuehrt" in out
    job_id = _job_id(tmp_path)

    assert main(d + ["start", job_id]) == 2  # ohne Freigabe verweigert
    assert "owner_approval_required" in capsys.readouterr().err

    fp = json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))["plan"]["fingerprint"]
    assert main(d + ["approve", job_id, "--plan", fp]) == 0
    assert "approved" in capsys.readouterr().out
    assert main(d + ["start", job_id]) == 0
    out = capsys.readouterr().out
    assert "completed" in out and "Social-Agent" in out

    assert main(d + ["start", job_id]) == 2  # kein zweiter Lauf
    assert main(d + ["tasks"]) == 0
    assert job_id in capsys.readouterr().out
    assert main(d + ["audit"]) == 0
    out = capsys.readouterr().out
    assert "task_approved" in out and "Integritaet der Hash-Kette: OK" in out
    assert (tmp_path / "logs" / "agent_system.jsonl").exists()


def test_cli_unclear_task_and_clarify(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path)]
    main(d + ["submit", "Mach mal was"])
    job_id = _job_id(tmp_path)
    assert "RUECKFRAGE" in capsys.readouterr().out
    assert main(d + ["approve", job_id]) == 2
    assert main(d + ["clarify", job_id, "Einen Instagram-Post"]) == 0
    assert "social" in capsys.readouterr().out


def test_cli_cancel(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path)]
    main(d + ["submit", "Schreibe einen Instagram-Post"])
    job_id = _job_id(tmp_path)
    assert main(d + ["cancel", job_id, "--reason", "doch nicht"]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert main(d + ["approve", job_id]) == 2


def test_cli_agents_and_brand(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "agents"]) == 0
    out = capsys.readouterr().out
    assert "claude-opus-5-5" in out and "claude-haiku" in out and "Owner: Alex" in out
    assert main(["--data-dir", str(tmp_path), "brand", "check"]) == 0
    assert "Vollstaendigkeit: 0%" in capsys.readouterr().out


def test_cli_action_decisions_are_dry_run(tmp_path, capsys):
    from agent_system.core.models import ProposedAction
    from agent_system.core.permissions import ApprovalStore

    apr = ApprovalStore(tmp_path / "approvals.json").request(
        "job_x", "s", ProposedAction("publish_content", "Post"), "r")
    assert main(["--data-dir", str(tmp_path), "actions"]) == 0
    assert apr.id in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "approve-action", apr.id]) == 0
    assert "Dry-Run" in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "approve-action", apr.id]) == 2  # schon entschieden
