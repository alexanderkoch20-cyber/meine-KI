"""CineMotion AI - Web-Anwendung.

Startet ueber:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .core.camera import PRESET_INFO
from .core.effects import COLOR_GRADES
from .core.pipeline import GenerationConfig, generate_video

logger = logging.getLogger("cinemotion.web")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
MEDIA_DIR = DATA_DIR / "media"
for d in (UPLOAD_DIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_DURATION = 10.0
MAX_RESOLUTION = 1920

app = FastAPI(title="CineMotion AI", version="1.0.0")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/styles")
def styles() -> JSONResponse:
    data = [{"id": key, **info} for key, info in PRESET_INFO.items()]
    grades = list(COLOR_GRADES.keys())
    return JSONResponse({"styles": data, "color_grades": grades})


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@app.post("/api/generate")
async def api_generate(
    file: UploadFile = File(...),
    style: str = Form("parallax_dolly"),
    duration: float = Form(5.0),
    fps: int = Form(24),
    resolution: int = Form(1280),
    intensity: float = Form(1.0),
    color_grade: str = Form("cinematic_teal_orange"),
    ai_depth: bool = Form(False),
    vignette: bool = Form(True),
    grain: bool = Form(True),
    bloom: bool = Form(True),
    chromatic_aberration: bool = Form(True),
    letterbox: bool = Form(True),
    motion_blur: bool = Form(True),
) -> JSONResponse:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"Nicht unterstuetztes Bildformat: {file.content_type}")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Bild ist zu gross (max. 20 MB).")
    if not contents:
        raise HTTPException(400, "Leere Datei.")

    if style not in PRESET_INFO:
        raise HTTPException(400, f"Unbekannter Stil: {style}")
    if color_grade not in COLOR_GRADES:
        raise HTTPException(400, f"Unbekanntes Farbgrading: {color_grade}")

    job_id = uuid.uuid4().hex
    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    input_path = UPLOAD_DIR / f"{job_id}{suffix}"
    output_path = MEDIA_DIR / f"{job_id}.mp4"
    input_path.write_bytes(contents)

    config = GenerationConfig(
        style=style,
        duration=_clamp(duration, 1.0, MAX_DURATION),
        fps=int(_clamp(fps, 12, 30)),
        max_dim=int(_clamp(resolution, 320, MAX_RESOLUTION)),
        depth_backend="ai" if ai_depth else "fast",
        color_grade=color_grade,
        intensity=_clamp(intensity, 0.4, 2.0),
        vignette=vignette,
        grain=grain,
        bloom=bloom,
        chromatic_aberration=chromatic_aberration,
        letterbox=letterbox,
        motion_blur=motion_blur,
    )

    try:
        meta = await run_in_threadpool(generate_video, str(input_path), str(output_path), config)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Videoerzeugung fehlgeschlagen")
        raise HTTPException(500, f"Videoerzeugung fehlgeschlagen: {exc}") from exc
    finally:
        input_path.unlink(missing_ok=True)

    return JSONResponse({"video_url": f"/media/{output_path.name}", "meta": meta})
