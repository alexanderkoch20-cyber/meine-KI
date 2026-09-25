"""Erzeugt ein synthetisches Demo-Foto (Sonnenuntergang mit Bergsilhouette),
damit das Projekt ohne externe Bilddateien direkt getestet werden kann.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def make_sunset_scene(w: int = 1600, h: int = 1000) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.float32)

    horizon = int(h * 0.62)
    top_color = np.array([70, 40, 20], dtype=np.float32)     # dunkles Blau (BGR)
    mid_color = np.array([90, 110, 210], dtype=np.float32)   # warmes Orange-Rosa
    for y in range(horizon):
        t = y / horizon
        t_curved = t**1.6
        img[y, :] = top_color * (1 - t_curved) + mid_color * t_curved

    sun_center = (int(w * 0.68), int(horizon * 0.72))
    sun_radius = int(h * 0.11)
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - sun_center[0]) ** 2 + (yy - sun_center[1]) ** 2)
    glow = np.clip(1.0 - dist / (sun_radius * 4.2), 0, 1) ** 2
    sun_color = np.array([120, 200, 255], dtype=np.float32)
    for c in range(3):
        img[:, :, c] += glow * sun_color[c] * 0.55
    sun_mask = dist < sun_radius
    img[sun_mask] = np.array([210, 235, 255], dtype=np.float32)

    rng = np.random.default_rng(7)
    ridge_y = np.full(w, horizon, dtype=np.float32)
    freq = rng.uniform(0.002, 0.02, size=4)
    amp = rng.uniform(20, 90, size=4)
    phase = rng.uniform(0, 6.28, size=4)
    xs = np.arange(w, dtype=np.float32)
    for f, a, p in zip(freq, amp, phase):
        ridge_y -= np.abs(np.sin(xs * f + p)) * a
    ridge_y = cv2.GaussianBlur(ridge_y.reshape(1, -1), (0, 0), sigmaX=8).flatten()

    mountain_color = np.array([55, 35, 35], dtype=np.float32)
    for x in range(w):
        top = int(ridge_y[x])
        img[max(top, 0):horizon, x] = mountain_color

    ground_top = np.array([25, 55, 35], dtype=np.float32)
    ground_bottom = np.array([15, 30, 20], dtype=np.float32)
    for y in range(horizon, h):
        t = (y - horizon) / max(h - horizon, 1)
        img[y, :] = ground_top * (1 - t) + ground_bottom * t

    reflection_h = int((h - horizon) * 0.6)
    reflection = img[horizon:horizon + reflection_h, :].copy()
    reflection = reflection[::-1]
    blend = np.linspace(0.35, 0.05, reflection_h, dtype=np.float32)[:, None, None]
    img[horizon:horizon + reflection_h] = (
        img[horizon:horizon + reflection_h] * (1 - blend) + reflection * blend
    )

    noise = rng.normal(0, 3.5, size=img.shape).astype(np.float32)
    img += noise
    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0.6)
    return img


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent.parent / "samples"
    out_dir.mkdir(exist_ok=True)
    scene = make_sunset_scene()
    out_path = out_dir / "sample_input.jpg"
    cv2.imwrite(str(out_path), scene, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"Beispielbild geschrieben nach {out_path}")
