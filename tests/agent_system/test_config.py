from __future__ import annotations

import pytest
import yaml

from agent_system.core.config import load_config
from agent_system.core.errors import ConfigError
from agent_system.core.llm import AnthropicLLMClient, MockLLMClient, create_llm_client


def _edit(path, fn):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_default_config_has_all_nine_agents_with_expected_models(config):
    expected = {
        "master": ("master", "opus"),
        "marketing": ("specialist", "sonnet"),
        "social": ("specialist", "sonnet"),
        "video": ("specialist", "sonnet"),
        "creative": ("specialist", "sonnet"),
        "research": ("specialist", "sonnet"),
        "coding": ("specialist", "sonnet"),
        "routine": ("specialist", "haiku"),
        "qa": ("qa", "sonnet"),
    }
    assert {a: (d.role, d.model_tier) for a, d in config.agents.items()} == expected
    assert config.provider == "mock"


def test_every_agent_prompt_loads_with_common_safety_rules(config):
    for agent_id in config.agents:
        prompt = config.load_prompt(agent_id)
        assert "Gib niemals API-Schluessel" in prompt


def test_unknown_model_tier_is_rejected(config_dir):
    _edit(config_dir / "agents.yaml", lambda d: d["agents"]["social"].update(model_tier="gpt"))
    with pytest.raises(ConfigError, match="Modell-Stufe"):
        load_config(config_dir)


def test_second_master_is_rejected(config_dir):
    _edit(config_dir / "agents.yaml", lambda d: d["agents"]["social"].update(role="master"))
    with pytest.raises(ConfigError, match="Master"):
        load_config(config_dir)


def test_unknown_allowed_action_is_rejected(config_dir):
    _edit(config_dir / "agents.yaml",
          lambda d: d["agents"]["routine"]["allowed_actions"].append("launch_rocket"))
    with pytest.raises(ConfigError, match="launch_rocket"):
        load_config(config_dir)


def test_invalid_policy_is_rejected(config_dir):
    _edit(config_dir / "permissions.yaml", lambda d: d["actions"]["spend_money"].update(policy="yolo"))
    with pytest.raises(ConfigError, match="spend_money"):
        load_config(config_dir)


def test_missing_file_is_reported(config_dir):
    (config_dir / "models.yaml").unlink()
    with pytest.raises(ConfigError, match="fehlt"):
        load_config(config_dir)


def test_llm_client_factory(config_dir):
    assert isinstance(create_llm_client(load_config(config_dir)), MockLLMClient)
    _edit(config_dir / "models.yaml", lambda d: d.update(provider="anthropic"))
    assert isinstance(create_llm_client(load_config(config_dir)), AnthropicLLMClient)
    _edit(config_dir / "models.yaml", lambda d: d.update(provider="other"))
    with pytest.raises(ConfigError):
        create_llm_client(load_config(config_dir))
