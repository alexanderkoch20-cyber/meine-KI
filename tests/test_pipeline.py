"""Smoke-Test: prueft, dass die komplette Pipeline auf einem synthetischen
Bild ohne Fehler ein abspielbares MP4 mit der erwarteten Framezahl erzeugt.
Braucht keine Modell-Downloads (nutzt das 'fast'-Tiefenmodell).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core.camera import PRESETS
from app.core.pipeline import GenerationConfig, compute_output_size, generate_video


@pytest.fixture
def synthetic_image(tmp_path):
    h, w = 240, 320
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[: h // 2, :] = (160, 110, 60)
    img[h // 2 :, :] = (30, 90, 30)
    cv2.circle(img, (w // 2, h // 2), 40, (20, 20, 200), -1)
    path = tmp_path / "in.jpg"
    cv2.imwrite(str(path), img)
    return str(path)


def test_compute_output_size_keeps_aspect_and_even_dims():
    w, h = compute_output_size(4000, 3000, 1000)
    assert max(w, h) <= 1000
    assert w % 2 == 0 and h % 2 == 0
    assert abs((w / h) - (4000 / 3000)) < 1e-2


@pytest.mark.parametrize("style", sorted(PRESETS.keys()))
def test_generate_video_all_presets(synthetic_image, tmp_path, style):
    out_path = tmp_path / f"{style}.mp4"
    config = GenerationConfig(style=style, duration=0.5, fps=6, max_dim=160)
    meta = generate_video(synthetic_image, str(out_path), config)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert meta["frames"] == 3
    assert meta["style"] == style

    cap = cv2.VideoCapture(str(out_path))
    assert cap.isOpened()
    count = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        count += 1
    cap.release()
    assert count == meta["frames"]


def test_generate_video_respects_toggles(synthetic_image, tmp_path):
    out_path = tmp_path / "no_effects.mp4"
    config = GenerationConfig(
        style="drift",
        duration=0.3,
        fps=5,
        max_dim=160,
        vignette=False,
        grain=False,
        bloom=False,
        chromatic_aberration=False,
        letterbox=False,
        motion_blur=False,
        color_grade="none",
    )
    meta = generate_video(synthetic_image, str(out_path), config)
    assert out_path.exists() and out_path.stat().st_size > 0
    assert meta["frames"] >= 1
