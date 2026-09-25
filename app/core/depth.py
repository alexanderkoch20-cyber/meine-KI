"""Monokulare Tiefenschaetzung.

Zwei Backends:

- ``fast`` (Standard): rein lokale, heuristische Tiefenkarte aus Schaerfe-,
  Kontrast- und Positionsmerkmalen. Braucht keine Modell-Downloads, laeuft
  auf jeder CPU in Millisekunden und liefert erstaunlich brauchbare
  Tiefenschichten fuer Foto-Kompositionen (Motiv scharf vor unscharfem
  Hintergrund, Motiv mittig/unten = nah).
- ``ai``: echtes monokulares Tiefen-Netz (MiDaS, kleine Variante) ueber
  ``torch``. Wird nur bei Bedarf nachgeladen; falls Torch/Gewichte nicht
  verfuegbar sind, faellt automatisch auf ``fast`` zurueck.

Die Rueckgabe ist in beiden Faellen eine float32-Karte in [0, 1], wobei
1.0 = nah an der Kamera und 0.0 = weit entfernt.
"""

from __future__ import annotations

import functools
import logging

import cv2
import numpy as np

logger = logging.getLogger("cinemotion.depth")

_AI_MODEL_CACHE: dict = {}


def _normalize(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    lo, hi = np.percentile(x, 1), np.percentile(x, 99)
    if hi - lo < 1e-6:
        return np.zeros_like(x)
    x = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return x


def estimate_depth_fast(image_bgr: np.ndarray) -> np.ndarray:
    """Heuristische Tiefenkarte ohne Modell-Download.

    Kombiniert drei klassische Bildschaerfe-/Kompositions-Priors, die in
    den meisten Fotos (Portrait, Landschaft, Produktfoto) gut funktionieren:

    1. Lokale Schaerfe (Laplacian-Energie) -> scharfe Bereiche sind meist
       das Motiv im Vordergrund, unscharfe (Bokeh) sind Hintergrund.
    2. Vertikale Position -> in den meisten Kompositionen ist "unten" naeher
       an der Kamera (Boden/Vordergrund) und "oben" der Himmel/Horizont.
    3. Zentrums-Prior -> das Hauptmotiv steht meist im Zentrum des Bildes.
    """
    h, w = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # 1) lokale Schaerfe ueber Laplacian-Energie in Bloecken
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    sharpness = cv2.GaussianBlur(np.abs(lap), (0, 0), sigmaX=max(w, h) * 0.01 + 1)
    sharpness = _normalize(sharpness)

    # 2) vertikale Position: unten = naeher
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(h, 1)
    vertical_prior = np.repeat(yy, w, axis=1)

    # 3) Zentrums-Prior (radialer Abstand zur Bildmitte, invertiert)
    xx, yy2 = np.meshgrid(
        np.linspace(-1, 1, w, dtype=np.float32),
        np.linspace(-1, 1, h, dtype=np.float32),
    )
    radial = np.sqrt(xx**2 + (yy2 * 1.2) ** 2)
    center_prior = 1.0 - _normalize(radial)

    depth = 0.55 * sharpness + 0.25 * vertical_prior + 0.20 * center_prior
    depth = _normalize(depth)

    # Kantenerhaltende Glaettung, damit Warps keine harten Spruenge zeigen
    depth = cv2.bilateralFilter(depth.astype(np.float32), d=9, sigmaColor=0.15, sigmaSpace=15)
    depth = _normalize(depth)
    return depth


@functools.lru_cache(maxsize=1)
def _load_midas():
    """Laedt MiDaS-small ueber torch.hub (nur bei explizitem AI-Modus)."""
    import torch  # noqa: F401  (lazy import, optionale Abhaengigkeit)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
    model.to(device).eval()
    transforms = torch.hub.load("intel-isl/MiDaS", "transforms", trust_repo=True)
    transform = transforms.small_transform
    return model, transform, device


def estimate_depth_ai(image_bgr: np.ndarray) -> np.ndarray:
    """Echte AI-Tiefenschaetzung via MiDaS. Wirft Exception wenn nicht verfuegbar."""
    import torch

    model, transform, device = _load_midas()
    img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    inp = transform(img_rgb).to(device)
    with torch.no_grad():
        pred = model(inp)
        pred = torch.nn.functional.interpolate(
            pred.unsqueeze(1),
            size=img_rgb.shape[:2],
            mode="bicubic",
            align_corners=False,
        ).squeeze()
    depth = pred.cpu().numpy()
    depth = _normalize(depth)
    depth = cv2.bilateralFilter(depth.astype(np.float32), d=9, sigmaColor=0.15, sigmaSpace=15)
    return _normalize(depth)


def estimate_depth(image_bgr: np.ndarray, backend: str = "fast") -> np.ndarray:
    """Oeffentliche Schnittstelle: liefert normalisierte Tiefenkarte [0,1]."""
    if backend == "ai":
        try:
            return estimate_depth_ai(image_bgr)
        except Exception as exc:  # pragma: no cover - Netz/GPU abhaengig
            logger.warning("AI-Tiefenmodell nicht verfuegbar (%s) - nutze Fallback 'fast'.", exc)
            return estimate_depth_fast(image_bgr)
    return estimate_depth_fast(image_bgr)
