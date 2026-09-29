"""Gemeinsame Fixtures: alles offline, Mock-LLM, temporaere Datenablage."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from agent_system.core.brand import BrandKnowledge
from agent_system.core.brand_schema import load_schema
from agent_system.core.config import DEFAULT_BRAND_DIR, DEFAULT_CONFIG_DIR, load_config
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
def brand_schema():
    return load_schema(DEFAULT_BRAND_DIR / "schema.yaml")


@pytest.fixture
def empty_brand(brand_schema):
    return BrandKnowledge.empty(brand_schema)


def make_filled_brand_data(schema):
    """Fiktive Testmarke - NUR fuer Tests, nichts davon betrifft die echte Brand."""
    data = schema.empty_template()
    data["brand_identity"].update(brand_name="Testmarke", owner="Test-Owner", mission="Wir machen Tests einfach.",
                                  values=["Ehrlichkeit"])
    data["offer"].update(products=[{"name": "TestBox", "description": "Eine Box", "price": "49 EUR",
                                    "status": "current", "markets": ["DE"]}],
                         services="NOT_APPLICABLE", usps=["schnell"])
    data["target_audiences"].update(primary_audiences=[{"name": "Tester", "needs": ["Zeit sparen"]}], markets=["DE"])
    data["positioning"].update(market_position="Die einfachste Testbox", differentiation=["einfach"],
                               desired_perception="verlaesslich")
    data["brand_voice"].update(tonality="locker", writing_style="kurz", form_of_address="du",
                               words_to_avoid=["krass"])
    data["no_gos"].update(statements=["billig", "Garantie"], topics=["Politik"])
    data["legal_compliance"].update(jurisdictions=["DE"])
    data["strategic_goals"].update(short_term=[{"goal": "100 Kunden"}])
    return data


@pytest.fixture
def filled_brand(brand_schema) -> BrandKnowledge:
    return BrandKnowledge.from_data(make_filled_brand_data(brand_schema), brand_schema)


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
