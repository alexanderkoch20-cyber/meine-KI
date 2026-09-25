"""Filmische Postprocessing-Effekte.

Alle Funktionen arbeiten auf BGR uint8 Frames (OpenCV-Konvention) und sind so
geschrieben, dass teure Vorberechnungen (LUTs, Masken, Verschiebungsfelder)
einmal pro Aufloesung erstellt und dann pro Frame wiederverwendet werden.
"""

from __future__ import annotations

import numpy as np
import cv2


# ---------------------------------------------------------------------------
# Farbgrading: Teal & Orange (der klassische Kino-Look)
# ---------------------------------------------------------------------------

def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def build_teal_orange_lut(strength: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Baut je eine 256er-LUT fuer B, G, R fuer einen Teal&Orange-Grade."""
    x = np.arange(256, dtype=np.float32)
    shadow_w = 1.0 - _smoothstep(0, 170, x)
    highlight_w = _smoothstep(85, 255, x)

    # sanfter S-Kurven-Kontrast um den Mittelwert
    contrast = (x - 127.5) * 1.06 + 127.5

    lut_b = contrast + strength * (40 * shadow_w - 22 * highlight_w)
    lut_r = contrast + strength * (-18 * shadow_w + 34 * highlight_w)
    lut_g = contrast + strength * (6 * shadow_w - 4 * highlight_w)

    lut_b = np.clip(lut_b, 0, 255).astype(np.uint8)
    lut_g = np.clip(lut_g, 0, 255).astype(np.uint8)
    lut_r = np.clip(lut_r, 0, 255).astype(np.uint8)
    return lut_b, lut_g, lut_r


COLOR_GRADES = {
    "cinematic_teal_orange": dict(strength=1.0),
    "warm_film": dict(strength=0.55),
    "cold_thriller": dict(strength=1.4),
    "none": dict(strength=0.0),
}


def apply_color_grade(frame: np.ndarray, luts: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    lut_b, lut_g, lut_r = luts
    b, g, r = cv2.split(frame)
    b = cv2.LUT(b, lut_b)
    g = cv2.LUT(g, lut_g)
    r = cv2.LUT(r, lut_r)
    return cv2.merge((b, g, r))


# ---------------------------------------------------------------------------
# Vignette
# ---------------------------------------------------------------------------

def build_vignette_mask(h: int, w: int, strength: float = 0.35, power: float = 2.1) -> np.ndarray:
    xx, yy = np.meshgrid(
        np.linspace(-1, 1, w, dtype=np.float32),
        np.linspace(-1, 1, h, dtype=np.float32),
    )
    radial = np.sqrt(xx**2 + yy**2) / np.sqrt(2)
    falloff = 1.0 - strength * np.clip(radial, 0, 1) ** power
    return np.clip(falloff, 0.0, 1.0)[:, :, None].astype(np.float32)


def apply_vignette(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = frame.astype(np.float32) * mask
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Bloom / Glow fuer Lichter
# ---------------------------------------------------------------------------

def apply_bloom(frame: np.ndarray, threshold: int = 195, sigma: float = 14.0, strength: float = 0.4) -> np.ndarray:
    if strength <= 0:
        return frame
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_TOZERO)
    bright = cv2.bitwise_and(frame, frame, mask=mask)
    glow = cv2.GaussianBlur(bright, (0, 0), sigmaX=sigma)
    out = cv2.addWeighted(frame, 1.0, glow, strength, 0)
    return out


# ---------------------------------------------------------------------------
# Filmkorn
# ---------------------------------------------------------------------------

def apply_grain(frame: np.ndarray, amount: float, rng: np.random.Generator) -> np.ndarray:
    if amount <= 0:
        return frame
    h, w = frame.shape[:2]
    noise = rng.normal(0, amount, size=(h, w, 1)).astype(np.float32)
    out = frame.astype(np.float32) + noise
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Chromatische Aberration
# ---------------------------------------------------------------------------

def build_chromatic_aberration_maps(h: int, w: int, strength_px: float = 1.6):
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    cx, cy = w / 2.0, h / 2.0
    dx, dy = xx - cx, yy - cy
    dist = np.sqrt(dx**2 + dy**2)
    max_dist = np.sqrt(cx**2 + cy**2)
    factor = (dist / max_dist) * strength_px
    norm = dist + 1e-6
    ux, uy = dx / norm, dy / norm

    r_map_x = (xx + ux * factor).astype(np.float32)
    r_map_y = (yy + uy * factor).astype(np.float32)
    b_map_x = (xx - ux * factor).astype(np.float32)
    b_map_y = (yy - uy * factor).astype(np.float32)
    return (r_map_x, r_map_y), (b_map_x, b_map_y)


def apply_chromatic_aberration(frame: np.ndarray, r_map, b_map) -> np.ndarray:
    b, g, r = cv2.split(frame)
    r = cv2.remap(r, r_map[0], r_map[1], interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    b = cv2.remap(b, b_map[0], b_map[1], interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.merge((b, g, r))


# ---------------------------------------------------------------------------
# Letterbox (Kino-Balken)
# ---------------------------------------------------------------------------

def apply_letterbox(frame: np.ndarray, bar_ratio: float = 0.09) -> np.ndarray:
    if bar_ratio <= 0:
        return frame
    h, w = frame.shape[:2]
    bar_h = int(round(h * bar_ratio))
    if bar_h <= 0:
        return frame
    out = frame.copy()
    fade = min(bar_h, 24)
    out[:bar_h] = 0
    out[h - bar_h:] = 0
    if fade > 0:
        grad = np.linspace(0, 1, fade, dtype=np.float32)[:, None, None]
        out[bar_h:bar_h + fade] = (out[bar_h:bar_h + fade].astype(np.float32) * grad).astype(np.uint8)
        out[h - bar_h - fade:h - bar_h] = (
            out[h - bar_h - fade:h - bar_h].astype(np.float32) * grad[::-1]
        ).astype(np.uint8)
    return out


# ---------------------------------------------------------------------------
# Motion Blur (temporales Blending zwischen Frames)
# ---------------------------------------------------------------------------

def blend_motion_trail(current: np.ndarray, previous: np.ndarray | None, strength: float = 0.25) -> np.ndarray:
    if previous is None or strength <= 0:
        return current
    return cv2.addWeighted(current, 1.0 - strength, previous, strength, 0)
