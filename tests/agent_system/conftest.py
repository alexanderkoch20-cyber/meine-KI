"""Gemeinsame Fixtures: alles offline, Mock-LLM, temporaere Datenablage."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from agent_system.core.brand import BrandKnowledge
from agent_system.core.config import DEFAULT_BRAND_FILE, DEFAULT_CONFIG_DIR, load_config
from agent_system.core.llm import MockLLMClient
from agent_system.orchestrator import Orchestrator


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def config_dir(tmp_path) -> Path:
    """Beschreibbare Kopie der Konfiguration fuer Tests, die sie veraendern."""
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    return target


@pytest.fixture
def empty_brand():
    return BrandKnowledge.load(DEFAULT_BRAND_FILE)


@pytest.fixture
def filled_brand(tmp_path) -> BrandKnowledge:
    data = yaml.safe_load(DEFAULT_BRAND_FILE.read_text(encoding="utf-8"))
    data.update({
        "brand_name": "Testmarke",
        "mission": "Wir machen Tests einfach.",
        "products": [{"name": "TestBox", "description": "Eine Box", "price": "49 EUR", "usp": "schnell"}],
        "positioning": "Die einfachste Testbox",
        "tonality": {"description": "locker", "address": "du", "do": [], "dont": []},
        "brand_values": ["Ehrlichkeit"],
        "goals": ["100 Kunden"],
        "no_go_rules": {"forbidden_words": ["billig", "Garantie"], "rules": ["Keine Heilversprechen"]},
    })
    path = tmp_path / "brand.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return BrandKnowledge.load(path)


@pytest.fixture
def verified_config(config_dir):
    """Konfiguration, in der ein Mensch (fiktiv, nur fuer Tests) alle Quellen des
    Legal-Katalogs verifiziert und datiert hat. Der ausgelieferte Katalog ist
    bewusst UNverifiziert."""
    path = config_dir / "legal.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        rule["verified_at"] = "2026-09-01"
        rule["verified_by"] = "Test-Kanzlei (fiktiv)"
        rule["version_date"] = rule.get("version_date") or "2024-01-01"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return load_config(config_dir)


@pytest.fixture
def owner(config):
    from agent_system.core.governance import owner_session
    return owner_session(config)


@pytest.fixture
def run_approved(owner):
    """Owner-Ablauf: Auftrag erteilen -> Plan freigeben -> ausfuehren."""
    def _run(orch, request, jurisdictions=None):
        job = orch.submit(request, owner, jurisdictions=jurisdictions)
        orch.approve(job.id, owner, job.plan.fingerprint())
        return orch.execute(job.id)
    return _run


@pytest.fixture
def make_orch(config, empty_brand, tmp_path):
    def _make(llm=None, brand=None, cfg=None, data_dir=None):
        return Orchestrator(
            config=cfg or config,
            llm=llm or MockLLMClient(),
            brand=brand or empty_brand,
            data_dir=data_dir or tmp_path / "data",
        )
    return _make


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Garantiert: kein Test des Agentensystems baut eine Netzwerkverbindung auf."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("Netzwerkzugriff in Tests des Agentensystems ist verboten")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
