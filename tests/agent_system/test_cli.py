from __future__ import annotations

from agent_system.__main__ import main
from agent_system.core.permissions import ApprovalStore
from agent_system.core.models import ProposedAction


def test_cli_run_and_inspect(tmp_path, capsys):
    d = str(tmp_path)
    assert main(["--data-dir", d, "run", "Schreibe einen Instagram-Post"]) == 0
    out = capsys.readouterr().out
    assert "completed" in out and "Social-Agent" in out

    assert main(["--data-dir", d, "jobs"]) == 0
    assert "Instagram" in capsys.readouterr().out
    assert (tmp_path / "logs" / "agent_system.jsonl").exists()


def test_cli_agents_and_brand(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "agents"]) == 0
    out = capsys.readouterr().out
    assert "claude-opus-5-5" in out and "claude-haiku" in out
    assert main(["--data-dir", str(tmp_path), "brand", "check"]) == 0
    assert "Vollstaendigkeit: 0%" in capsys.readouterr().out


def test_cli_approvals(tmp_path, capsys):
    store = ApprovalStore(tmp_path / "approvals.json")
    apr = store.request("job_x", "s", ProposedAction("publish_content", "Post"), "r")
    assert main(["--data-dir", str(tmp_path), "approvals"]) == 0
    assert apr.id in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "approve", apr.id]) == 0
    assert "Dry-Run" in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "approve", apr.id]) == 2  # schon entschieden


def test_cli_approval_completes_job_from_earlier_run(tmp_path, capsys, monkeypatch):
    import json

    from agent_system.core.llm import MockLLMClient
    from agent_system.orchestrator import Orchestrator

    post = ("Ausfuehrlicher Entwurf fuer einen Instagram-Post mit klarer Botschaft.\n"
            '```actions\n[{"action": "publish_content", "description": "Post live"}]\n```')
    orch = Orchestrator(llm=MockLLMClient(scripted={"social:work": [post]}), data_dir=tmp_path)
    job = orch.handle("Schreibe einen Instagram-Post")
    assert job.status.value == "awaiting_approval"
    apr_id = job.approvals[0]

    # neuer Prozess (CLI) entscheidet die Freigabe
    assert main(["--data-dir", str(tmp_path), "approve", apr_id]) == 0
    snap = json.loads((tmp_path / "jobs" / f"{job.id}.json").read_text(encoding="utf-8"))
    assert snap["status"] == "completed"
