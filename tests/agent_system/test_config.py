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


def test_config_is_immutable_at_runtime(config):
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.provider = "anthropic"
    with pytest.raises(TypeError):
        config.action_policies["publish_content"] = "allow"
    with pytest.raises(TypeError):
        config.agents["evil"] = config.agents["social"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.governance.owner_id = "social"


@pytest.mark.parametrize("action", ["publish_content", "spend_money", "revert_commit", "reset_files",
                                    "delete_data", "create_agent", "change_model", "start_new_task"])
def test_protected_actions_can_never_be_set_to_allow(config_dir, action):
    _edit(config_dir / "permissions.yaml", lambda d: d["actions"].setdefault(action, {}).update(policy="allow"))
    with pytest.raises(ConfigError, match="Owner-Regel"):
        load_config(config_dir)


def test_forbidden_actions_must_stay_denied(config_dir):
    _edit(config_dir / "permissions.yaml",
          lambda d: d["actions"]["modify_permissions"].update(policy="require_approval"))
    with pytest.raises(ConfigError, match="immer verboten"):
        load_config(config_dir)


def test_default_policy_allow_is_forbidden(config_dir):
    _edit(config_dir / "permissions.yaml", lambda d: d.update(default_policy="allow"))
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_new_agent_without_owner_approval_is_inactive(config_dir):
    def add_agent(d):
        d["agents"]["seo"] = {"name": "SEO-Agent", "role": "specialist", "model_tier": "sonnet",
                              "prompt_file": "marketing.md", "keywords": ["seo"],
                              "allowed_actions": ["create_draft"]}
    _edit(config_dir / "agents.yaml", add_agent)
    cfg = load_config(config_dir)
    assert "seo" not in cfg.agents and cfg.inactive_agents == ("seo",)

    _edit(config_dir / "governance.yaml", lambda d: d["approved_agents"].append("seo"))
    assert "seo" in load_config(config_dir).agents  # erst nach Owner-Eintrag aktiv


def test_removing_agent_from_governance_deactivates_it(config_dir):
    _edit(config_dir / "governance.yaml", lambda d: d["approved_agents"].remove("coding"))
    cfg = load_config(config_dir)
    assert "coding" not in cfg.agents and "coding" in cfg.inactive_agents


def test_master_and_qa_must_be_approved(config_dir):
    _edit(config_dir / "governance.yaml", lambda d: d["approved_agents"].remove("qa"))
    with pytest.raises(ConfigError, match="Pflicht-Agenten"):
        load_config(config_dir)


def test_owner_id_cannot_be_an_agent(config_dir):
    _edit(config_dir / "governance.yaml", lambda d: d["owner"].update(id="master"))
    with pytest.raises(ConfigError, match="Owner-ID"):
        load_config(config_dir)


def test_fingerprint_changes_when_config_changes(config_dir):
    before = load_config(config_dir).fingerprint
    _edit(config_dir / "models.yaml", lambda d: d["tiers"]["sonnet"].update(temperature=0.1))
    assert load_config(config_dir).fingerprint != before
