"""Orchestriert Provider-Auswahl inkl. automatischem Fallback.

Regel (vom Nutzer vorgegeben): "Parallax Dolly" (die bestehende 2.5D-Engine)
ist und bleibt der garantierte Fallback. Wenn ein angeforderter Provider
(z.B. ein generatives Modell) nicht verfuegbar ist - fehlende GPU, fehlendes
API-Token, fehlende Abhaengigkeiten - wird automatisch und transparent auf
den Standard-Provider ausgewichen, statt mit einem Fehler abzubrechen.
"""

from __future__ import annotations

from typing import Any

from .providers import DEFAULT_PROVIDER_ID, get_provider
from .providers.base import ProgressCallback


def generate_scene(
    image_path: str,
    out_path: str,
    config: Any,
    provider_id: str | None = None,
    progress_cb: ProgressCallback = None,
    allow_fallback: bool = True,
) -> dict:
    """Erzeugt die Videoszene mit dem gewuenschten Provider.

    Gibt immer ein Metadaten-Dict zurueck mit zusaetzlich:
      - ``requested_provider``: urspruenglich angefragter Provider
      - ``used_provider``: tatsaechlich verwendeter Provider
      - ``fallback_reason``: gesetzt, wenn auf den Fallback ausgewichen wurde
    """
    requested = get_provider(provider_id)
    cap = requested.check_capability()

    if cap.available:
        meta = requested.generate(image_path, out_path, config, progress_cb=progress_cb)
        meta["requested_provider"] = requested.id
        meta["used_provider"] = requested.id
        meta["fallback_reason"] = None
        return meta

    if requested.id == DEFAULT_PROVIDER_ID or not allow_fallback:
        raise RuntimeError(f"Provider '{requested.id}' nicht verfuegbar: {cap.reason}")

    fallback = get_provider(DEFAULT_PROVIDER_ID)
    meta = fallback.generate(image_path, out_path, config, progress_cb=progress_cb)
    meta["requested_provider"] = requested.id
    meta["used_provider"] = fallback.id
    meta["fallback_reason"] = cap.reason
    return meta
