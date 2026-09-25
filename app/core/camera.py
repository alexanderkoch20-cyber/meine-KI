"""Virtuelle Kamerapfade fuer die 2.5D-Parallaxe.

Jedes Preset ist eine reine Funktion ``t in [0, 1] -> CameraFrame`` und
beschreibt, wie sich eine virtuelle Kamera durch die Tiefenschichten eines
Standbilds bewegt (Dolly, Pan, Orbit, Zoom, Handheld-Shake). Die eigentliche
Bildverzerrung passiert in ``warp.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

Ease = Callable[[float], float]


def ease_in_out_cubic(t: float) -> float:
    return 4 * t**3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def ease_out_quad(t: float) -> float:
    return 1 - (1 - t) ** 2


def ease_in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1) / 2


@dataclass
class CameraFrame:
    """Zustand der virtuellen Kamera zu einem Zeitpunkt t."""

    dx: float = 0.0          # horizontale Kamera-Verschiebung, Anteil der Sicherheitsmarge
    dy: float = 0.0          # vertikale Kamera-Verschiebung
    zoom: float = 1.0        # globaler Zoomfaktor (>1 = hineinzoomen)
    parallax: float = 1.2    # Staerke der Tiefen-Parallaxe (0 = flach, >1 = starkes 3D-Gefuehl)
    tilt_deg: float = 0.0    # leichte Rotation fuer organische Kamerafahrten
    shake_x: float = 0.0
    shake_y: float = 0.0


def _handheld_shake(t: float, amplitude: float) -> tuple[float, float]:
    """Weiches Pseudo-Handheld-Wackeln aus ueberlagerten Sinuswellen."""
    if amplitude <= 0:
        return 0.0, 0.0
    sx = (
        math.sin(t * 2 * math.pi * 2.7) * 0.6
        + math.sin(t * 2 * math.pi * 5.3 + 1.3) * 0.4
    )
    sy = (
        math.sin(t * 2 * math.pi * 3.1 + 0.7) * 0.6
        + math.sin(t * 2 * math.pi * 6.1 + 2.1) * 0.4
    )
    return sx * amplitude, sy * amplitude


def preset_ken_burns(t: float) -> CameraFrame:
    e = ease_in_out_sine(t)
    return CameraFrame(
        dx=-0.22 + 0.44 * e,
        dy=-0.10 + 0.16 * e,
        zoom=1.0 + 0.14 * e,
        parallax=0.7,
    )


def preset_parallax_dolly(t: float) -> CameraFrame:
    e = ease_in_out_cubic(t)
    shake_x, shake_y = _handheld_shake(t, 0.004)
    return CameraFrame(
        dx=0.0,
        dy=-0.06 * e,
        zoom=1.0 + 0.22 * e,
        parallax=1.5,
        tilt_deg=0.0,
        shake_x=shake_x,
        shake_y=shake_y,
    )


def preset_parallax_orbit(t: float) -> CameraFrame:
    e = ease_in_out_sine(t)
    arc = math.sin((e - 0.5) * math.pi)  # -1 .. 1 weicher Bogen
    shake_x, shake_y = _handheld_shake(t, 0.003)
    return CameraFrame(
        dx=0.20 * arc,
        dy=-0.05 * e,
        zoom=1.0 + 0.08 * e,
        parallax=1.7,
        tilt_deg=1.1 * arc,
        shake_x=shake_x,
        shake_y=shake_y,
    )


def preset_vertigo(t: float) -> CameraFrame:
    e = ease_in_out_cubic(t)
    return CameraFrame(
        dx=0.0,
        dy=0.02 * e,
        zoom=1.0 + 0.05 * e,
        parallax=1.2 + 1.4 * e,
        tilt_deg=0.0,
    )


def preset_drift(t: float) -> CameraFrame:
    e = ease_in_out_sine(t)
    shake_x, shake_y = _handheld_shake(t, 0.0025)
    return CameraFrame(
        dx=-0.09 + 0.18 * e,
        dy=0.05 * math.sin(e * math.pi),
        zoom=1.0 + 0.045 * e,
        parallax=1.05,
        tilt_deg=-0.4 + 0.8 * e,
        shake_x=shake_x,
        shake_y=shake_y,
    )


PRESETS: dict[str, Callable[[float], CameraFrame]] = {
    "ken_burns": preset_ken_burns,
    "parallax_dolly": preset_parallax_dolly,
    "parallax_orbit": preset_parallax_orbit,
    "vertigo": preset_vertigo,
    "drift": preset_drift,
}

PRESET_INFO = {
    "parallax_dolly": {
        "label": "Parallax Dolly",
        "description": "Kraftvoller Push-in mit starker Tiefenstaffelung - der Klassiker fuer epische Reveals.",
    },
    "parallax_orbit": {
        "label": "Orbit Drift",
        "description": "Sanfter Bogen um das Motiv wie eine kleine Kranfahrt, mit organischem Handheld-Feel.",
    },
    "ken_burns": {
        "label": "Ken Burns+",
        "description": "Der zeitlose Zoom/Pan-Klassiker, verfeinert mit dezenter 3D-Tiefe.",
    },
    "vertigo": {
        "label": "Vertigo Zoom",
        "description": "Der beruehmte Hitchcock-'Dolly Zoom' - Hintergrund und Vordergrund driften dramatisch auseinander.",
    },
    "drift": {
        "label": "Cinematic Drift",
        "description": "Ruhiges, schwebendes Treiben - ideal fuer Mood- und Stimmungsbilder.",
    },
}


def get_preset(name: str) -> Callable[[float], CameraFrame]:
    if name not in PRESETS:
        raise ValueError(
            f"Unbekanntes Kamera-Preset '{name}'. Verfuegbar: {', '.join(PRESETS)}"
        )
    return PRESETS[name]
