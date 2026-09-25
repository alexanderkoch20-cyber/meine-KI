"""Orchestriert die komplette Bild-zu-Video-Erzeugung.

Ablauf: Bild laden -> Tiefenkarte schaetzen -> Arbeits-Canvas aufbauen ->
pro Frame virtuelle Kamera bewegen & 2.5D warpen -> filmische Effekte
anwenden -> als H.264-MP4 encodieren.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import depth as depth_mod
from . import effects
from .camera import CameraFrame, get_preset
from .video import write_video
from .warp import ParallaxRenderer, build_workspace

DEPTH_ESTIMATION_MAX_DIM = 800


@dataclass
class GenerationConfig:
    style: str = "parallax_dolly"
    duration: float = 5.0
    fps: int = 24
    max_dim: int = 1280
    depth_backend: str = "fast"       # "fast" (offline) oder "ai" (MiDaS)
    color_grade: str = "cinematic_teal_orange"
    intensity: float = 1.0            # globaler Multiplikator fuer Zoom/Parallaxe
    vignette: bool = True
    grain: bool = True
    bloom: bool = True
    chromatic_aberration: bool = True
    letterbox: bool = True
    motion_blur: bool = True
    seed: int = 12345


ProgressCallback = Optional[Callable[[float], None]]


def load_image_bgr(path: str) -> np.ndarray:
    pil_img = Image.open(path)
    pil_img = ImageOps.exif_transpose(pil_img)
    pil_img = pil_img.convert("RGB")
    rgb = np.array(pil_img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def compute_output_size(w: int, h: int, max_dim: int) -> tuple[int, int]:
    scale = max_dim / float(max(w, h))
    if scale < 1.0:
        w, h = int(round(w * scale)), int(round(h * scale))
    # gerade Aufloesungen sind fuer H.264 (yuv420p) erforderlich
    w -= w % 2
    h -= h % 2
    return max(w, 2), max(h, 2)


def _downscaled_for_depth(image_bgr: np.ndarray) -> np.ndarray:
    h, w = image_bgr.shape[:2]
    scale = min(1.0, DEPTH_ESTIMATION_MAX_DIM / float(max(w, h)))
    if scale >= 1.0:
        return image_bgr
    return cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def generate_video(image_path: str, out_path: str, config: GenerationConfig, progress_cb: ProgressCallback = None) -> dict:
    t0 = time.time()

    image = load_image_bgr(image_path)
    h0, w0 = image.shape[:2]
    out_w, out_h = compute_output_size(w0, h0, config.max_dim)

    depth_input = _downscaled_for_depth(image)
    depth_small = depth_mod.estimate_depth(depth_input, backend=config.depth_backend)
    depth_full = cv2.resize(depth_small, (w0, h0), interpolation=cv2.INTER_LINEAR)

    workspace = build_workspace(image, depth_full, out_w, out_h, margin=1.32)
    renderer = ParallaxRenderer(workspace)
    cam_fn = get_preset(config.style)

    num_frames = max(int(round(config.duration * config.fps)), 2)

    grade_cfg = effects.COLOR_GRADES.get(config.color_grade, {"strength": 1.0})
    luts = effects.build_teal_orange_lut(grade_cfg["strength"]) if grade_cfg["strength"] > 0 else None
    vignette_mask = effects.build_vignette_mask(out_h, out_w, strength=0.32) if config.vignette else None
    if config.chromatic_aberration:
        r_map, b_map = effects.build_chromatic_aberration_maps(out_h, out_w, strength_px=1.6)
    else:
        r_map = b_map = None
    rng = np.random.default_rng(config.seed)

    state = {"prev": None}

    def frame_generator():
        for i in range(num_frames):
            t = i / (num_frames - 1) if num_frames > 1 else 0.0
            cam: CameraFrame = cam_fn(t)
            cam.parallax *= config.intensity
            cam.zoom = 1.0 + (cam.zoom - 1.0) * config.intensity

            frame = renderer.render(cam)

            if config.motion_blur:
                frame = effects.blend_motion_trail(frame, state["prev"], strength=0.18)
            state["prev"] = frame.copy()

            if r_map is not None:
                frame = effects.apply_chromatic_aberration(frame, r_map, b_map)
            if luts is not None:
                frame = effects.apply_color_grade(frame, luts)
            if config.bloom:
                frame = effects.apply_bloom(frame)
            if vignette_mask is not None:
                frame = effects.apply_vignette(frame, vignette_mask)
            if config.grain:
                frame = effects.apply_grain(frame, amount=4.5, rng=rng)
            if config.letterbox:
                frame = effects.apply_letterbox(frame, bar_ratio=0.09)

            if progress_cb:
                progress_cb((i + 1) / num_frames)

            yield frame

    write_video(frame_generator(), out_path, fps=config.fps)

    return {
        "style": config.style,
        "duration_sec": config.duration,
        "fps": config.fps,
        "frames": num_frames,
        "resolution": [out_w, out_h],
        "depth_backend": config.depth_backend,
        "elapsed_seconds": round(time.time() - t0, 2),
    }
