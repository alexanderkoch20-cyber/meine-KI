"""Wrapper um die bestehende, produktiv laufende 2.5D-Parallax-Engine.

Dieser Provider ist absichtlich trivial: er ruft exakt dieselbe
``generate_video``-Funktion auf, die vorher direkt von CLI/Web-App genutzt
wurde. Dadurch bleibt das bisherige, getestete Verhalten unveraendert - der
Provider ist nur eine duenne Huelle darum, damit die Engine ueber dieselbe
Schnittstelle wie neue (generative) Engines ansprechbar ist.

Dieser Provider ist IMMER verfuegbar (reine CPU-Bildverarbeitung, keine
GPU/Modell-Downloads noetig) und dient als garantierter Fallback fuer alle
anderen Provider.
"""

from __future__ import annotations

from typing import Any

from ..pipeline import generate_video
from .base import ProgressCallback, ProviderCapability, VideoProvider


class ParallaxDollyProvider(VideoProvider):
    id = "parallax_2_5d"
    label = "Parallax Dolly (2.5D-Engine)"
    description = (
        "Schnelle, komplett lokale 2.5D-Kamerafahrt-Engine (Tiefenschaetzung + "
        "Warp + Cinematic Grading). Laeuft auf jeder CPU in Sekunden, ohne "
        "Modell-Download. Standard-Preset: Parallax Dolly. Dient als "
        "garantierter Fallback fuer alle anderen Provider."
    )
    kind = "local"

    def check_capability(self) -> ProviderCapability:
        return ProviderCapability(
            available=True,
            reason="Laeuft immer lokal auf der CPU, keine zusaetzliche Hardware noetig.",
        )

    def generate(
        self,
        image_path: str,
        out_path: str,
        config: Any,
        progress_cb: ProgressCallback = None,
    ) -> dict:
        meta = generate_video(image_path, out_path, config, progress_cb=progress_cb)
        meta["provider"] = self.id
        return meta
