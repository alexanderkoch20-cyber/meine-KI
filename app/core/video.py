"""H.264-Encoding via imageio/ffmpeg (Binary wird durch imageio-ffmpeg mitgeliefert,
kein System-ffmpeg noetig)."""

from __future__ import annotations

from typing import Iterable

import cv2
import imageio.v2 as imageio
import numpy as np


def write_video(frames: Iterable[np.ndarray], out_path: str, fps: int, crf: int = 18) -> None:
    """Schreibt eine Sequenz von BGR-uint8-Frames als H.264-MP4.

    ``macro_block_size=1`` verhindert, dass imageio die Aufloesung heimlich
    auf ein Vielfaches von 16 aufrundet - wir wollen exakt die Ausgabegroesse
    behalten, die die Pipeline berechnet hat.
    """
    writer = imageio.get_writer(
        out_path,
        fps=fps,
        codec="libx264",
        quality=None,
        macro_block_size=1,
        ffmpeg_params=["-crf", str(crf), "-preset", "medium"],
    )
    try:
        for frame_bgr in frames:
            writer.append_data(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    finally:
        writer.close()
