from __future__ import annotations

from dataclasses import replace

import pytest

from agent_system.core.errors import AgentSystemError, GovernanceViolationError
from agent_system.core.governance import Actor
from agent_system.core.models import ProposedAction
from agent_system.core.permissions import ApprovalStore, Decision, PermissionPolicy

RESTRICTED = ["spend_money", "publish_content", "send_customer_message", "delete_account",
              "delete_file_permanently", "sign_contract", "revert_commit", "reset_files", "reset_commits",
              "delete_data", "create_agent", "enable_agent", "disable_agent", "change_config",
              "change_model", "activate_external_service", "use_api_key", "connect_external_account"]


def test_all_restricted_actions_require_approval(config):
    for action in RESTRICTED:
        assert config.action_policies[action] == "require_approval"
    for action in ("reveal_secret", "modify_permissions", "modify_governance", "approve_task", "approve_action"):
        assert config.action_policies[action] == "deny"


def test_policy_decisions(config):
    policy = PermissionPolicy(config)
    assert policy.check("social", "create_draft").decision == Decision.ALLOW
    assert policy.check("social", "publish_content").decision == Decision.REQUIRE_APPROVAL
    assert policy.check("marketing", "spend_money").decision == Decision.REQUIRE_APPROVAL
    # Least privilege: Routine darf nichts veroeffentlichen
    assert policy.check("routine", "publish_content").decision == Decision.DENY
    # grundsaetzlich verboten - auch fuer den Master
    assert policy.check("master", "reveal_secret").decision == Decision.DENY
    assert policy.check("ghost", "create_draft").decision == Decision.DENY
    # Empfehlungen darf jeder Agent vorschlagen - aber nur mit Freigabe
    for rec in ("start_new_task", "extend_task", "retry_task", "request_owner_decision"):
        assert policy.check("routine", rec).decision == Decision.REQUIRE_APPROVAL
    # Governance ist tabu - fuer jeden Agenten
    for agent in config.agents:
        for forbidden in ("modify_permissions", "modify_governance", "approve_task", "approve_action"):
            assert policy.check(agent, forbidden).decision == Decision.DENY


def test_unknown_action_falls_back_to_default_policy(config):
    social = replace(config.agents["social"], allowed_actions=config.agents["social"].allowed_actions | {"teleport"})
    cfg = replace(config, agents={**config.agents, "social": social})
    assert PermissionPolicy(cfg).check("social", "teleport").decision == Decision.REQUIRE_APPROVAL


def test_approval_store_persists_and_decides_once(tmp_path):
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path)
    apr = store.request("job_1", "step_1", ProposedAction("publish_content", "Post"), "Veroeffentlichen")
    assert [a.id for a in store.pending()] == [apr.id]

    reloaded = ApprovalStore(path)
    assert reloaded.get(apr.id).status == "pending"
    owner = Actor("owner", "owner")
    with pytest.raises(GovernanceViolationError):
        reloaded.decide(apr.id, approve=True, actor=Actor.agent("social"))
    reloaded.decide(apr.id, approve=True, actor=owner)
    assert ApprovalStore(path).get(apr.id).status == "approved"
    with pytest.raises(AgentSystemError, match="bereits entschieden"):
        reloaded.decide(apr.id, approve=False, actor=owner)
    with pytest.raises(AgentSystemError, match="nicht gefunden"):
        reloaded.get("apr_missing")
