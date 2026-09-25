"""Provider-Registry: zentrale Anlaufstelle, um verfuegbare Bild-zu-Video-
Engines nachzuschlagen. CLI, Web-App und Orchestrator nutzen ausschliesslich
diese Funktionen - nie eine konkrete Provider-Klasse direkt - damit ein
neuer Provider (weiteres lokales Modell, andere API) nur hier registriert
werden muss.
"""

from __future__ import annotations

from .base import ProviderCapability, VideoProvider
from .generative_api import ReplicateVideoProvider
from .generative_local import LocalGenerativeProvider
from .parallax_provider import ParallaxDollyProvider

DEFAULT_PROVIDER_ID = "parallax_2_5d"

_PROVIDER_CLASSES = [
    ParallaxDollyProvider,
    LocalGenerativeProvider,
    ReplicateVideoProvider,
]

_INSTANCES: dict[str, VideoProvider] = {cls().id: cls() for cls in _PROVIDER_CLASSES}

assert DEFAULT_PROVIDER_ID in _INSTANCES, "Default-Provider muss registriert sein"


def get_provider(provider_id: str | None) -> VideoProvider:
    """Liefert einen registrierten Provider. ``None``/leer -> Standard-Provider."""
    pid = provider_id or DEFAULT_PROVIDER_ID
    if pid not in _INSTANCES:
        available = ", ".join(sorted(_INSTANCES))
        raise ValueError(f"Unbekannter Provider '{pid}'. Verfuegbar: {available}")
    return _INSTANCES[pid]


def list_providers() -> list[dict]:
    """Beschreibung + Live-Verfuegbarkeit aller registrierten Provider."""
    return [p.describe() for p in _INSTANCES.values()]


__all__ = [
    "DEFAULT_PROVIDER_ID",
    "ProviderCapability",
    "VideoProvider",
    "get_provider",
    "list_providers",
]
