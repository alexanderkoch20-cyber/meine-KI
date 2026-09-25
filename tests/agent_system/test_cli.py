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
    out = capsys.readouterr().out
    assert "Pflichtinformationen:" in out and "Pruefung der Arbeitskopie" in out
    assert "Freigegeben (von Agenten genutzt): v1" in out


def test_cli_action_decisions_are_dry_run(tmp_path, capsys):
    from agent_system.core.models import ProposedAction
    from agent_system.core.permissions import ApprovalStore

    # Eine "untergeschobene" Aktion ohne Legal-Pruefung kann nie freigegeben werden ...
    apr = ApprovalStore(tmp_path / "approvals.json").request(
        "job_x", "s", ProposedAction("publish_content", "Post"), "r")
    assert main(["--data-dir", str(tmp_path), "actions"]) == 0
    assert apr.id in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "approve-action", apr.id]) == 2
    assert "legal_review_required" in capsys.readouterr().err
    # ... ablehnen geht immer - und fuehrt nichts aus.
    assert main(["--data-dir", str(tmp_path), "reject-action", apr.id]) == 0
    assert "Dry-Run" in capsys.readouterr().out
    assert main(["--data-dir", str(tmp_path), "reject-action", apr.id]) == 2  # schon entschieden


def test_cli_legal_workflow(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path)]
    assert main(d + ["submit", "Sende einen Newsletter an unsere Kundenliste", "-j", "DE"]) == 0
    out = capsys.readouterr().out
    assert "human_legal_review_required" in out and "legal-review-done" in out
    job_id = _job_id(tmp_path)

    assert main(d + ["approve", job_id]) == 2
    assert "legal_review_required" in capsys.readouterr().err
    assert main(d + ["legal", job_id]) == 0
    out = capsys.readouterr().out
    assert "Verordnung (EU) 2016/679" in out and "NICHT verifiziert" in out and "Keine Rechtsberatung" in out

    assert main(d + ["legal-review-done", job_id, "--reviewer", "RA Muster (fiktiv)", "--note", "ok mit Auflagen"]) == 0
    assert "Owner-Freigabe ist weiterhin erforderlich" in capsys.readouterr().out
    assert main(d + ["approve", job_id]) == 0
    assert main(d + ["start", job_id]) in (0, 1)
    assert main(d + ["audit"]) == 0
    out = capsys.readouterr().out
    assert "human_legal_review_recorded" in out and "Hash-Kette: OK" in out


def test_cli_jurisdictions_command(tmp_path, capsys):
    d = ["--data-dir", str(tmp_path)]
    main(d + ["submit", "Sende einen Newsletter an unsere Kundenliste"])
    job_id = _job_id(tmp_path)
    assert "[Legal]" in capsys.readouterr().out
    assert main(d + ["jurisdictions", job_id, "DE", "AT"]) == 0
    assert "Rechtsraeume: DE, AT" in capsys.readouterr().out


def test_cli_brand_workflow(tmp_path, capsys):
    import shutil

    import yaml

    from agent_system.core.config import DEFAULT_BRAND_DIR

    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    for name in ("schema.yaml", "brand_knowledge.yaml", "ONBOARDING.md"):
        shutil.copy(DEFAULT_BRAND_DIR / name, brand_dir / name)
    d = ["--data-dir", str(tmp_path / "data"), "--brand-dir", str(brand_dir)]

    # Arbeitskopie bearbeiten (fiktive Test-Angaben)
    path = brand_dir / "brand_knowledge.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["brand_identity"]["brand_name"] = "CLI-Testmarke"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    assert main(d + ["brand", "check", "--agent", "social"]) == 0
    out = capsys.readouterr().out
    assert "noch NICHT freigegeben" in out and "keine freigegebene Version" in out

    assert main(d + ["brand", "commit"]) == 2                       # Notiz fehlt
    capsys.readouterr()
    assert main(d + ["brand", "commit", "--note", "Name eingetragen"]) == 0
    assert "v1 freigegeben" in capsys.readouterr().out
    assert main(d + ["brand", "show", "--agent", "social"]) == 0
    assert "CLI-Testmarke" in capsys.readouterr().out

    # Wert, der sich garantiert von der (echten) Arbeitskopie unterscheidet - nur in der Temp-Kopie
    data["brand_voice"]["form_of_address"] = "situationsabhaengig"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert main(d + ["brand", "commit", "--note", "Anrede"]) == 0
    capsys.readouterr()
    assert main(d + ["brand", "diff", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "brand_voice.form_of_address:" in out and "-> situationsabhaengig" in out
    assert main(d + ["brand", "history"]) == 0
    out = capsys.readouterr().out
    assert "Name eingetragen" in out and "Integritaet: OK" in out
    assert main(d + ["audit"]) == 0
    assert "brand_version_committed" in capsys.readouterr().out
    assert main(d + ["brand", "onboarding"]) == 0
    assert "Onboarding fuer den Owner" in capsys.readouterr().out
