"""Kommandozeilen-Tool: Bild -> filmische Video-Szene.

Beispiel:
    python -m app.cli --image foto.jpg --out szene.mp4 --style parallax_dolly --duration 6
"""

from __future__ import annotations

import argparse
import sys

from .core.camera import PRESET_INFO, PRESETS
from .core.effects import COLOR_GRADES
from .core.pipeline import GenerationConfig, generate_video


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cinemotion",
        description="Verwandelt ein Standbild in eine filmische Kamerafahrt (2.5D-Parallaxe + Cinematic Grading).",
    )
    p.add_argument("--image", help="Pfad zum Eingabebild (jpg/png/...)")
    p.add_argument("--out", default="szene.mp4", help="Pfad der Ausgabe-MP4 (Standard: szene.mp4)")
    p.add_argument(
        "--style",
        choices=sorted(PRESETS.keys()),
        default="parallax_dolly",
        help="Kamerafahrt-Preset",
    )
    p.add_argument("--duration", type=float, default=5.0, help="Laenge des Clips in Sekunden")
    p.add_argument("--fps", type=int, default=24, help="Bildrate")
    p.add_argument("--resolution", type=int, default=1280, help="Maximale Kantenlaenge in Pixel")
    p.add_argument("--intensity", type=float, default=1.0, help="Staerke von Zoom/Parallaxe (0.5-2.0)")
    p.add_argument(
        "--color-grade",
        choices=sorted(COLOR_GRADES.keys()),
        default="cinematic_teal_orange",
        help="Farbgrading-Preset",
    )
    p.add_argument("--ai-depth", action="store_true", help="Echtes AI-Tiefenmodell (MiDaS) statt schneller Heuristik verwenden")
    p.add_argument("--no-vignette", action="store_true")
    p.add_argument("--no-grain", action="store_true")
    p.add_argument("--no-bloom", action="store_true")
    p.add_argument("--no-chromatic-aberration", action="store_true")
    p.add_argument("--no-letterbox", action="store_true")
    p.add_argument("--no-motion-blur", action="store_true")
    p.add_argument("--list-styles", action="store_true", help="Alle Kamera-Presets mit Beschreibung anzeigen und beenden")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_styles:
        for key, info in PRESET_INFO.items():
            print(f"{key:16s} {info['label']:16s} {info['description']}")
        return 0

    if not args.image:
        parser.error("--image ist erforderlich (oder --list-styles verwenden)")

    config = GenerationConfig(
        style=args.style,
        duration=args.duration,
        fps=args.fps,
        max_dim=args.resolution,
        depth_backend="ai" if args.ai_depth else "fast",
        color_grade=args.color_grade,
        intensity=args.intensity,
        vignette=not args.no_vignette,
        grain=not args.no_grain,
        bloom=not args.no_bloom,
        chromatic_aberration=not args.no_chromatic_aberration,
        letterbox=not args.no_letterbox,
        motion_blur=not args.no_motion_blur,
    )

    def progress(p: float) -> None:
        bar_len = 30
        filled = int(bar_len * p)
        bar = "#" * filled + "-" * (bar_len - filled)
        sys.stdout.write(f"\r[{bar}] {p * 100:5.1f}%")
        sys.stdout.flush()

    print(f"Erzeuge cinematische Szene aus '{args.image}' (Stil: {args.style}) ...")
    meta = generate_video(args.image, args.out, config, progress_cb=progress)
    print()
    print(f"Fertig in {meta['elapsed_seconds']}s -> {args.out}")
    print(f"  Aufloesung: {meta['resolution'][0]}x{meta['resolution'][1]}, {meta['frames']} Frames @ {meta['fps']}fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
