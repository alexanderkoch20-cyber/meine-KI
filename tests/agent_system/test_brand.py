from __future__ import annotations

import pytest

from agent_system.core.brand import BRAND_SECTIONS, BrandKnowledge
from agent_system.core.config import DEFAULT_BRAND_FILE
from agent_system.core.errors import BrandKnowledgeError


def test_template_contains_every_required_section():
    import yaml
    data = yaml.safe_load(DEFAULT_BRAND_FILE.read_text(encoding="utf-8"))
    assert set(data) == set(BRAND_SECTIONS)


def test_empty_template_is_zero_percent_complete(empty_brand):
    assert empty_brand.completeness() == 0
    assert set(empty_brand.missing_sections()) == set(BRAND_SECTIONS)
    assert "noch keine Brand-Informationen" in empty_brand.to_prompt_context()


def test_filled_brand_context_and_rules(filled_brand):
    ctx = filled_brand.to_prompt_context()
    assert "Testmarke" in ctx and "TestBox" in ctx
    assert "Noch nicht hinterlegt" in ctx  # z.B. Wettbewerber fehlen
    assert filled_brand.forbidden_words == ["billig", "Garantie"]
    assert 0.5 < filled_brand.completeness() < 1


def test_unknown_section_is_rejected(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("brand_name: X\nslogan: Y\n", encoding="utf-8")
    with pytest.raises(BrandKnowledgeError, match="slogan"):
        BrandKnowledge.load(p)


def test_invalid_yaml_and_missing_file(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("brand_name: [unclosed\n", encoding="utf-8")
    with pytest.raises(BrandKnowledgeError):
        BrandKnowledge.load(p)
    with pytest.raises(BrandKnowledgeError):
        BrandKnowledge.load(tmp_path / "missing.yaml")
