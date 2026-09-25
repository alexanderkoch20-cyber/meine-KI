"""2.5D-Parallax-Warp-Engine.

Idee: Aus einem Einzelbild + Tiefenkarte wird eine "virtuelle Szene" gebaut,
durch die eine virtuelle Kamera fliegt. Nahe Bildbereiche (hohe Tiefe)
verschieben sich beim Schwenk/Zoom staerker als ferne Bereiche - genau der
Effekt, der ein Standbild "dreidimensional" und filmisch wirken laesst.

Technisch nutzen wir *backward warping*: fuer jedes Ausgabepixel wird eine
Quellkoordinate im hochskalierten Arbeitsbild berechnet (abhaengig von Zoom,
Pan, Tiefenwert und leichter Rotation) und per ``cv2.remap`` gesampelt. Das
ist robust, GPU-frei sehr schnell und braucht - anders als forward warping -
kein Inpainting fuer Disocclusion-Loecher.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .camera import CameraFrame

# Wie stark der Zoom je nach Tiefe variiert (Vordergrund "kommt naeher")
ZOOM_PARALLAX_CONST = 0.45
# Wie stark der Kameraschwenk je nach Tiefe variiert
PAN_PARALLAX_CONST = 1.1
# Grenzen fuer den depth-abhaengigen Skalierungsfaktor (verhindert Extremverzerrung)
DEPTH_FACTOR_CLIP = (0.25, 2.2)


@dataclass
class Workspace:
    image: np.ndarray      # hochskaliertes BGR-Arbeitsbild (H_w, W_w, 3) uint8
    depth: np.ndarray      # Tiefenkarte auf Arbeitsbild-Aufloesung, float32 [0,1]
    out_w: int
    out_h: int


def build_workspace(
    image_bgr: np.ndarray,
    depth: np.ndarray,
    out_w: int,
    out_h: int,
    margin: float = 1.32,
) -> Workspace:
    """Skaliert Bild + Tiefenkarte auf ein groesseres Arbeitscanvas.

    Das Arbeitscanvas ist ``margin``-mal groesser als die Zielaufloesung und
    dient als Sicherheitszone, damit Kamerabewegungen nie ueber den
    Bildrand hinaus "greifen" muessen.
    """
    ws_w, ws_h = int(round(out_w * margin)), int(round(out_h * margin))
    ws_image = cv2.resize(image_bgr, (ws_w, ws_h), interpolation=cv2.INTER_LINEAR)
    ws_depth = cv2.resize(depth, (ws_w, ws_h), interpolation=cv2.INTER_LINEAR)
    return Workspace(image=ws_image, depth=ws_depth, out_w=out_w, out_h=out_h)


class ParallaxRenderer:
    """Rendert Frames aus einem Workspace fuer beliebige CameraFrame-Zustaende.

    Die (teuren) Koordinatenraster werden einmal pro Aufloesung vorbereitet
    und fuer jeden Frame wiederverwendet - dadurch bleibt das Rendering auch
    auf der CPU schnell genug fuer mehrsekuendige Clips.
    """

    def __init__(self, workspace: Workspace, depth_pivot: float | None = None):
        self.ws = workspace
        h_w, w_w = workspace.image.shape[:2]
        out_w, out_h = workspace.out_w, workspace.out_h

        self.cx, self.cy = w_w / 2.0, h_w / 2.0
        out_cx, out_cy = out_w / 2.0, out_h / 2.0

        xs = np.arange(out_w, dtype=np.float32) - out_cx
        ys = np.arange(out_h, dtype=np.float32) - out_cy
        self.nx, self.ny = np.meshgrid(xs, ys)  # (out_h, out_w)

        # Basis-Tiefenwert je Ausgabepixel: Tiefe an der ungeschobenen
        # Zentrumsposition im Workspace (gute Naeherung, da Verschiebungen
        # durch die Sicherheitsmarge begrenzt sind).
        base_x = np.clip(self.nx + self.cx, 0, w_w - 1).astype(np.int32)
        base_y = np.clip(self.ny + self.cy, 0, h_w - 1).astype(np.int32)
        self.base_depth = workspace.depth[base_y, base_x]

        self.pivot = depth_pivot if depth_pivot is not None else float(np.median(workspace.depth))

        self.max_pan_x = max((w_w - out_w) / 2.0, 1.0)
        self.max_pan_y = max((h_w - out_h) / 2.0, 1.0)

        self.prev_map: tuple[np.ndarray, np.ndarray] | None = None

    def render(self, cam: CameraFrame) -> np.ndarray:
        depth_delta = self.base_depth - self.pivot

        zoom_factor = cam.zoom * (
            1.0 + depth_delta * ZOOM_PARALLAX_CONST * cam.parallax * 0.5
        )
        zoom_factor = np.clip(zoom_factor, DEPTH_FACTOR_CLIP[0] * cam.zoom, DEPTH_FACTOR_CLIP[1] * cam.zoom)

        local_x = self.nx / zoom_factor
        local_y = self.ny / zoom_factor

        pan_depth_factor = np.clip(
            1.0 + depth_delta * PAN_PARALLAX_CONST * cam.parallax,
            DEPTH_FACTOR_CLIP[0],
            DEPTH_FACTOR_CLIP[1],
        )
        pan_x = (cam.dx + cam.shake_x) * self.max_pan_x * pan_depth_factor
        pan_y = (cam.dy + cam.shake_y) * self.max_pan_y * pan_depth_factor

        src_x = self.cx + local_x - pan_x
        src_y = self.cy + local_y - pan_y

        if abs(cam.tilt_deg) > 1e-4:
            theta = np.deg2rad(-cam.tilt_deg)
            rel_x, rel_y = src_x - self.cx, src_y - self.cy
            cos_t, sin_t = np.cos(theta), np.sin(theta)
            rot_x = rel_x * cos_t - rel_y * sin_t
            rot_y = rel_x * sin_t + rel_y * cos_t
            src_x, src_y = self.cx + rot_x, self.cy + rot_y

        map_x = src_x.astype(np.float32)
        map_y = src_y.astype(np.float32)
        self.prev_map = (map_x, map_y)

        frame = cv2.remap(
            self.ws.image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT101,
        )
        return frame

    def motion_magnitude(self, prev_maps, cur_maps) -> np.ndarray:
        """Pixelweise Verschiebung zwischen zwei Frames (fuer Motion Blur)."""
        pmx, pmy = prev_maps
        cmx, cmy = cur_maps
        return np.sqrt((cmx - pmx) ** 2 + (cmy - pmy) ** 2)
