from __future__ import annotations

from dataclasses import replace

import pytest

from agent_system.core.errors import AgentSystemError
from agent_system.core.models import ProposedAction
from agent_system.core.permissions import ApprovalStore, Decision, PermissionPolicy

RESTRICTED = ["spend_money", "publish_content", "send_customer_message", "delete_account",
              "delete_file_permanently", "sign_contract"]


def test_all_restricted_actions_require_approval(config):
    for action in RESTRICTED:
        assert config.action_policies[action] == "require_approval"
    assert config.action_policies["reveal_secret"] == "deny"


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


def test_unknown_action_falls_back_to_default_policy(config):
    social = config.agents["social"]
    config.agents["social"] = replace(social, allowed_actions=social.allowed_actions | {"teleport"})
    assert PermissionPolicy(config).check("social", "teleport").decision == Decision.REQUIRE_APPROVAL


def test_approval_store_persists_and_decides_once(tmp_path):
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path)
    apr = store.request("job_1", "step_1", ProposedAction("publish_content", "Post"), "Veroeffentlichen")
    assert [a.id for a in store.pending()] == [apr.id]

    reloaded = ApprovalStore(path)
    assert reloaded.get(apr.id).status == "pending"
    reloaded.decide(apr.id, approve=True)
    assert ApprovalStore(path).get(apr.id).status == "approved"
    with pytest.raises(AgentSystemError, match="bereits entschieden"):
        reloaded.decide(apr.id, approve=False)
    with pytest.raises(AgentSystemError, match="nicht gefunden"):
        reloaded.get("apr_missing")
