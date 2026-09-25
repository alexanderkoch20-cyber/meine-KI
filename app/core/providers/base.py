"""Provider-Schnittstelle fuer Bild-zu-Video-Engines.

Ein "Provider" kapselt eine austauschbare Video-Erzeugungs-Engine - egal ob
lokale 2.5D-Parallaxe, ein lokales generatives Diffusionsmodell oder eine
externe Video-KI-API. Alle Provider implementieren dasselbe Interface,
sodass CLI, Web-App und der Orchestrator (``app/core/orchestrator.py``)
nicht wissen muessen, wie eine konkrete Engine intern arbeitet.

``check_capability()`` MUSS immer schnell und ohne Nebenwirkungen sein
(keine Downloads, keine Netzwerkaufrufe außer optionalen, sehr kurzen
Erreichbarkeits-Checks) - der Orchestrator ruft sie vor jeder Generierung
auf, um bei Nichtverfuegbarkeit automatisch auf den Fallback-Provider
(Parallax Dolly) auszuweichen.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

ProgressCallback = Optional[Callable[[float], None]]


@dataclass
class ProviderCapability:
    """Ergebnis einer Verfuegbarkeits-Pruefung fuer einen Provider."""

    available: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class VideoProvider(ABC):
    """Basisklasse fuer alle Bild-zu-Video-Engines."""

    id: str = "base"
    label: str = "Basis-Provider"
    description: str = ""
    # "local" (immer lokal, keine Zusatz-Hardware) | "local-generative"
    # (lokales KI-Modell, braucht i.d.R. GPU) | "api" (externer Dienst)
    kind: str = "local"

    @abstractmethod
    def check_capability(self) -> ProviderCapability:
        """Prueft schnell, ob dieser Provider aktuell nutzbar ist."""

    @abstractmethod
    def generate(
        self,
        image_path: str,
        out_path: str,
        config: Any,
        progress_cb: ProgressCallback = None,
    ) -> dict:
        """Erzeugt das Video und gibt Metadaten zurueck (mind. {"provider": self.id})."""

    def describe(self) -> dict:
        cap = self.check_capability()
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "available": cap.available,
            "status": cap.reason,
            "details": cap.details,
        }
