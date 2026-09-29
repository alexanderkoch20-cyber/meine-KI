from __future__ import annotations

import json
import logging

from agent_system.core.logging_setup import get_logger, setup_logging
from agent_system.core.secrets import REDACTED, contains_secret, redact

FAKE_KEY = "sk-ant-api03-" + "A" * 40


def test_known_key_patterns_are_redacted():
    text = f"key={FAKE_KEY} gh=ghp_{'b' * 36} aws=AKIAABCDEFGHIJKLMNOP"
    out = redact(text)
    assert FAKE_KEY not in out and "ghp_" not in out and "AKIA" not in out
    assert out.count(REDACTED) == 3


def test_assignment_style_secrets_are_redacted():
    assert redact("password: hunter2hunter2") == f"password: {REDACTED}"
    assert redact("API_KEY=abcdefgh12345") == f"API_KEY={REDACTED}"


def test_env_secret_values_are_redacted(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "plain-looking-value-123")
    assert redact("x plain-looking-value-123 y") == f"x {REDACTED} y"


def test_normal_text_is_untouched():
    text = "Eine Kampagne fuer Instagram mit 3 Reels."
    assert redact(text) == text and not contains_secret(text)


def test_log_file_never_contains_secrets(tmp_path):
    log_file = tmp_path / "log.jsonl"
    setup_logging(log_file=log_file)
    get_logger("test").info("Schluessel %s", FAKE_KEY, extra={"job_id": "job_x"})
    for h in logging.getLogger("agent_system").handlers:
        h.flush()
    line = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert FAKE_KEY not in line["msg"] and line["job_id"] == "job_x"
    setup_logging()
