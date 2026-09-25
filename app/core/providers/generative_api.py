"""Externe Video-KI-API als Provider.

Dieser Provider ist bewusst NICHT aktiviert: ohne gesetztes API-Token
meldet ``check_capability()`` "nicht verfuegbar" und es wird niemals ein
Netzwerkaufruf ausgefuehrt - auch nicht zur Pruefung. Es werden hier keine
Zugangsdaten erfunden oder hart codiert; der Nutzer muss ``REPLICATE_API_TOKEN``
und ``REPLICATE_VIDEO_MODEL_VERSION`` selbst als Umgebungsvariablen setzen,
sobald er bewusst eine kostenpflichtige API nutzen moechte.

Als konkretes Beispiel wird die generische Prediction-API von Replicate
genutzt (https://replicate.com/docs/reference/http), weil sie viele
verschiedene offene Video-Modelle hinter einer einheitlichen REST-API
anbietet und damit einen realistischen, gut dokumentierten Referenzfall fuer
"externe Video-KI-API" darstellt. Eine andere API (Runway, Stability, Pika,
...) laesst sich als weitere Klasse mit demselben ``VideoProvider``-Interface
ergaenzen, ohne Orchestrator, CLI oder Web-App aendern zu muessen.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import time
from typing import Any

from .base import ProgressCallback, ProviderCapability, VideoProvider

logger = logging.getLogger("cinemotion.providers.generative_api")

API_TOKEN_ENV = "REPLICATE_API_TOKEN"
MODEL_VERSION_ENV = "REPLICATE_VIDEO_MODEL_VERSION"
API_BASE_URL = "https://api.replicate.com/v1/predictions"
POLL_INTERVAL_SECONDS = 3.0
POLL_TIMEOUT_SECONDS = 600.0


class ReplicateVideoProvider(VideoProvider):
    id = "generative_api_replicate"
    label = "Generative Video (externe API, Replicate)"
    description = (
        "Ruft ein gehostetes generatives Bild-zu-Video-Modell ueber die "
        "Replicate-API auf. Kostenpflichtig und standardmaessig deaktiviert - "
        "wird erst nutzbar, wenn REPLICATE_API_TOKEN und "
        "REPLICATE_VIDEO_MODEL_VERSION als Umgebungsvariablen gesetzt sind."
    )
    kind = "api"

    def check_capability(self) -> ProviderCapability:
        try:
            import requests  # noqa: F401
        except ImportError:
            return ProviderCapability(
                available=False,
                reason="Python-Paket 'requests' ist nicht installiert.",
            )

        token = os.environ.get(API_TOKEN_ENV)
        version = os.environ.get(MODEL_VERSION_ENV)

        if not token and not version:
            return ProviderCapability(
                available=False,
                reason=(
                    f"Nicht konfiguriert: {API_TOKEN_ENV} und {MODEL_VERSION_ENV} "
                    "sind nicht gesetzt. Diese kostenpflichtige API ist bewusst "
                    "deaktiviert, bis du eigene Zugangsdaten hinterlegst."
                ),
            )
        if not token:
            return ProviderCapability(
                available=False,
                reason=f"{API_TOKEN_ENV} ist nicht gesetzt.",
            )
        if not version:
            return ProviderCapability(
                available=False,
                reason=(
                    f"{MODEL_VERSION_ENV} ist nicht gesetzt (welches Modell auf "
                    "Replicate aufgerufen werden soll)."
                ),
            )

        # Bewusst KEIN Netzwerkaufruf hier - Verfuegbarkeit wird nur anhand
        # der Konfiguration beurteilt, damit dieser Check niemals unbeabsichtigt
        # eine kostenpflichtige Anfrage ausloest oder ohne Erlaubnis Traffic
        # zu einem externen Dienst erzeugt.
        return ProviderCapability(
            available=True,
            reason="API-Token und Modellversion konfiguriert.",
            details={"model_version": version},
        )

    def _image_to_data_uri(self, image_path: str) -> str:
        mime, _ = mimetypes.guess_type(image_path)
        mime = mime or "image/jpeg"
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def generate(
        self,
        image_path: str,
        out_path: str,
        config: Any,
        progress_cb: ProgressCallback = None,
    ) -> dict:  # pragma: no cover - braucht echtes API-Token, hier nie aufgerufen
        import requests

        cap = self.check_capability()
        if not cap.available:
            raise RuntimeError(f"Replicate-Provider nicht nutzbar: {cap.reason}")

        token = os.environ[API_TOKEN_ENV]
        version = os.environ[MODEL_VERSION_ENV]
        t0 = time.time()

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "version": version,
            "input": {
                "input_image": self._image_to_data_uri(image_path),
                "fps": int(getattr(config, "fps", 24)),
            },
        }

        resp = requests.post(API_BASE_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        prediction = resp.json()
        get_url = prediction["urls"]["get"]

        if progress_cb:
            progress_cb(0.05)

        deadline = time.time() + POLL_TIMEOUT_SECONDS
        while time.time() < deadline:
            poll_resp = requests.get(get_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            prediction = poll_resp.json()
            status = prediction.get("status")

            if status == "succeeded":
                if progress_cb:
                    progress_cb(0.9)
                break
            if status in ("failed", "canceled"):
                raise RuntimeError(f"Replicate-Generierung fehlgeschlagen: {prediction.get('error')}")

            if progress_cb:
                progress_cb(0.05 + 0.8 * min((time.time() - t0) / POLL_TIMEOUT_SECONDS, 1.0))
            time.sleep(POLL_INTERVAL_SECONDS)
        else:
            raise TimeoutError("Replicate-Generierung hat das Zeitlimit ueberschritten.")

        output = prediction.get("output")
        video_url = output[0] if isinstance(output, list) else output
        if not video_url:
            raise RuntimeError("Replicate-Antwort enthielt keine Video-URL.")

        video_resp = requests.get(video_url, stream=True, timeout=60)
        video_resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)

        if progress_cb:
            progress_cb(1.0)

        return {
            "provider": self.id,
            "style": "generative",
            "model_version": version,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
