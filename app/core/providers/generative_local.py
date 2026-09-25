"""Lokales generatives Bild-zu-Video-Modell (Stable Video Diffusion).

WICHTIG - Ehrlicher Status dieses Providers:
Dieser Code ist vollstaendig implementiert, konnte in der Entwicklungsumgebung
aber NICHT end-to-end getestet werden, weil dort weder eine GPU noch eine
Internetverbindung zum Modell-Hub (huggingface.co) verfuegbar war (siehe
``app/diagnostics.py`` fuer den Hardware-Befund). ``check_capability()``
prueft genau diese Voraussetzungen zur Laufzeit und meldet ehrlich
"nicht verfuegbar", statt es einfach zu versuchen und mit einem kryptischen
Fehler abzustuerzen - der Orchestrator faellt dann automatisch auf die
Parallax-Dolly-Engine zurueck.

Voraussetzungen fuer echten Betrieb (z.B. auf einer eigenen Maschine mit
NVIDIA-GPU):
    pip install -r requirements-generative.txt
    (laedt beim ersten Lauf ~9-10 GB Modellgewichte von huggingface.co)

Empfohlen: GPU mit >= 12-16 GB VRAM. Auf der CPU ist Stable Video Diffusion
technisch lauffaehig, aber mit Minuten bis Stunden pro Clip praktisch
unbrauchbar - das wird in ``check_capability()`` explizit ausgeschlossen.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from .base import ProgressCallback, ProviderCapability, VideoProvider

logger = logging.getLogger("cinemotion.providers.generative_local")

MIN_VRAM_GB = 10.0
MODEL_ID = "stabilityai/stable-video-diffusion-img2vid-xt"


class LocalGenerativeProvider(VideoProvider):
    id = "generative_local_svd"
    label = "Generative Video (lokal, Stable Video Diffusion)"
    description = (
        "Echtes generatives Diffusionsmodell (Stable Video Diffusion), das neue "
        "Bildinhalte und Bewegung synthetisiert statt nur die Kamera zu warpen. "
        "Braucht eine lokale NVIDIA-GPU mit >= 10 GB VRAM und ~9-10 GB Modell-"
        "Download. Auf reiner CPU nicht praktikabel."
    )
    kind = "local-generative"

    def check_capability(self) -> ProviderCapability:
        try:
            import torch  # noqa: F401
        except ImportError:
            return ProviderCapability(
                available=False,
                reason=(
                    "PyTorch ist nicht installiert. Installiere die optionalen "
                    "Abhaengigkeiten mit: pip install -r requirements-generative.txt"
                ),
            )

        import torch

        if not torch.cuda.is_available():
            return ProviderCapability(
                available=False,
                reason=(
                    "Keine CUDA-faehige GPU gefunden. Stable Video Diffusion "
                    "braucht eine NVIDIA-GPU mit mindestens ~10 GB VRAM - auf "
                    "der CPU dauert ein Clip Minuten bis Stunden statt Sekunden."
                ),
                details={"cuda_available": False},
            )

        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / 1e9
        if vram_gb < MIN_VRAM_GB:
            return ProviderCapability(
                available=False,
                reason=(
                    f"GPU '{props.name}' gefunden, aber nur {vram_gb:.1f} GB VRAM "
                    f"(empfohlen: >= {MIN_VRAM_GB:.0f} GB fuer {MODEL_ID})."
                ),
                details={"cuda_available": True, "vram_gb": round(vram_gb, 1)},
            )

        try:
            import diffusers  # noqa: F401
        except ImportError:
            return ProviderCapability(
                available=False,
                reason=(
                    "GPU ausreichend, aber 'diffusers' ist nicht installiert. "
                    "pip install -r requirements-generative.txt"
                ),
                details={"cuda_available": True, "vram_gb": round(vram_gb, 1)},
            )

        return ProviderCapability(
            available=True,
            reason=f"GPU '{props.name}' mit {vram_gb:.1f} GB VRAM erkannt - bereit.",
            details={"cuda_available": True, "vram_gb": round(vram_gb, 1)},
        )

    @functools.lru_cache(maxsize=1)
    def _load_pipeline(self):  # pragma: no cover - braucht GPU, in Sandbox ungetestet
        import torch
        from diffusers import StableVideoDiffusionPipeline

        pipe = StableVideoDiffusionPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            variant="fp16",
        )
        pipe.to("cuda")
        pipe.enable_model_cpu_offload()
        return pipe

    def generate(
        self,
        image_path: str,
        out_path: str,
        config: Any,
        progress_cb: ProgressCallback = None,
    ) -> dict:  # pragma: no cover - braucht GPU, in Sandbox ungetestet
        from PIL import Image

        from ..video import write_video

        t0 = time.time()
        pipe = self._load_pipeline()

        image = Image.open(image_path).convert("RGB")
        image = image.resize((1024, 576))

        num_frames = 25
        fps = min(max(int(config.fps), 6), 30)

        def _diffusers_progress(step, timestep, latents):  # noqa: ARG001
            if progress_cb:
                progress_cb(min(step / 25.0, 0.95))

        result = pipe(
            image,
            decode_chunk_size=8,
            num_frames=num_frames,
            motion_bucket_id=127,
            noise_aug_strength=0.02,
            callback=_diffusers_progress,
        )
        frames_rgb = result.frames[0]

        import numpy as np
        import cv2

        frames_bgr = (cv2.cvtColor(np.array(f), cv2.COLOR_RGB2BGR) for f in frames_rgb)
        write_video(frames_bgr, out_path, fps=fps)

        if progress_cb:
            progress_cb(1.0)

        return {
            "provider": self.id,
            "style": "generative",
            "frames": num_frames,
            "fps": fps,
            "model": MODEL_ID,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
