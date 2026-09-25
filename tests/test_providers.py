"""Tests fuer die Provider-Architektur: Registry, Capability-Checks und die
automatische Fallback-Logik im Orchestrator. Laufen komplett offline und
ohne GPU (so wie die Zielumgebung dieser Tests).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core.orchestrator import generate_scene
from app.core.pipeline import GenerationConfig
from app.core.providers import DEFAULT_PROVIDER_ID, get_provider, list_providers
from app.core.providers.generative_api import ReplicateVideoProvider
from app.core.providers.generative_local import LocalGenerativeProvider
from app.core.providers.parallax_provider import ParallaxDollyProvider


@pytest.fixture
def synthetic_image(tmp_path):
    h, w = 200, 260
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[: h // 2, :] = (150, 100, 60)
    img[h // 2 :, :] = (30, 90, 30)
    cv2.circle(img, (w // 2, h // 2), 30, (20, 20, 200), -1)
    path = tmp_path / "in.jpg"
    cv2.imwrite(str(path), img)
    return str(path)


def test_default_provider_is_parallax_dolly():
    assert DEFAULT_PROVIDER_ID == "parallax_2_5d"
    provider = get_provider(None)
    assert isinstance(provider, ParallaxDollyProvider)


def test_list_providers_contains_all_three():
    ids = {p["id"] for p in list_providers()}
    assert ids == {"parallax_2_5d", "generative_local_svd", "generative_api_replicate"}


def test_parallax_provider_always_available():
    cap = ParallaxDollyProvider().check_capability()
    assert cap.available is True


def test_generative_local_unavailable_without_torch():
    cap = LocalGenerativeProvider().check_capability()
    # In dieser Testumgebung ist kein Torch/keine GPU vorhanden.
    assert cap.available is False
    assert cap.reason


def test_generative_api_unavailable_without_env(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_VIDEO_MODEL_VERSION", raising=False)
    cap = ReplicateVideoProvider().check_capability()
    assert cap.available is False
    assert "nicht gesetzt" in cap.reason or "Nicht konfiguriert" in cap.reason


def test_generative_api_check_capability_makes_no_network_call(monkeypatch):
    """check_capability() darf NIE einen Netzwerkaufruf ausloesen - auch nicht,
    wenn (versehentlich) nur eine der beiden Umgebungsvariablen gesetzt ist."""
    monkeypatch.setenv("REPLICATE_API_TOKEN", "dummy-token-fuer-test")
    monkeypatch.delenv("REPLICATE_VIDEO_MODEL_VERSION", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("check_capability() darf keine Netzwerkaufrufe machen")

    import requests

    monkeypatch.setattr(requests, "get", _forbidden)
    monkeypatch.setattr(requests, "post", _forbidden)

    cap = ReplicateVideoProvider().check_capability()
    assert cap.available is False


def test_get_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider("does_not_exist")


def test_orchestrator_uses_requested_provider_when_available(synthetic_image, tmp_path):
    out_path = tmp_path / "out.mp4"
    config = GenerationConfig(duration=0.4, fps=5, max_dim=160)
    meta = generate_scene(str(synthetic_image), str(out_path), config, provider_id="parallax_2_5d")

    assert out_path.exists() and out_path.stat().st_size > 0
    assert meta["requested_provider"] == "parallax_2_5d"
    assert meta["used_provider"] == "parallax_2_5d"
    assert meta["fallback_reason"] is None


@pytest.mark.parametrize("unavailable_id", ["generative_local_svd", "generative_api_replicate"])
def test_orchestrator_falls_back_to_parallax_dolly(synthetic_image, tmp_path, unavailable_id):
    out_path = tmp_path / "out.mp4"
    config = GenerationConfig(duration=0.4, fps=5, max_dim=160)
    meta = generate_scene(str(synthetic_image), str(out_path), config, provider_id=unavailable_id)

    assert out_path.exists() and out_path.stat().st_size > 0
    assert meta["requested_provider"] == unavailable_id
    assert meta["used_provider"] == DEFAULT_PROVIDER_ID
    assert meta["fallback_reason"]  # nicht leer/None

    cap = cv2.VideoCapture(str(out_path))
    assert cap.isOpened()
    cap.release()


def test_orchestrator_default_style_is_parallax_dolly(synthetic_image, tmp_path):
    """Regression: 'Parallax Dolly' muss weiterhin das Standard-Kamera-Preset
    der 2.5D-Engine sein, unabhaengig von der neuen Provider-Ebene."""
    config = GenerationConfig()
    assert config.style == "parallax_dolly"
