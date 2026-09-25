"""Schutz vor dem Ausgeben von API-Schluesseln und Zugangsdaten.

- ``redact()`` schwaerzt bekannte Schluessel-Muster und die Werte aller
  Umgebungsvariablen, deren Name nach einem Secret klingt.
- ``contains_secret()`` wird von der QA genutzt, um Ergebnisse zu blockieren.
- Logging nutzt ``redact()`` fuer jede Zeile (siehe logging_setup.py).
"""

from __future__ import annotations

import os
import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),          # Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),            # OpenAI & Co.
    re.compile(r"\br8_[A-Za-z0-9]{20,}"),               # Replicate
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),        # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                # AWS Access Key
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwort)\b(\s*[:=]\s*)['\"]?([^\s'\"]{8,})"
    ),
]

_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")


def _env_secret_values() -> list[str]:
    values = []
    for name, value in os.environ.items():
        if value and len(value) >= 8 and any(h in name.upper() for h in _SECRET_ENV_HINTS):
            values.append(value)
    # laengste zuerst, damit Teilstrings nicht zuerst ersetzt werden
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    if not text:
        return text
    for value in _env_secret_values():
        text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def contains_secret(text: str) -> bool:
    return bool(text) and redact(text) != text
