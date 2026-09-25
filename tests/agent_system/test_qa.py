from __future__ import annotations

from agent_system.agents.base import AgentContext, extract_actions
from agent_system.agents.qa import QAAgent
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import AgentRequest, AgentResponse, ProposedAction, QAVerdict

GOOD = "Ein ausfuehrlicher, sauberer Entwurf fuer die Kampagne mit klarer Botschaft."


def _qa(config, brand, qa_responses=None):
    llm = MockLLMClient(scripted={"qa:qa": qa_responses or []})
    return QAAgent(config.agents["qa"], AgentContext(config, llm, brand))


def _review(qa, content=GOOD, agent="social", actions=(), warnings=()):
    req = AgentRequest(job_id="j", step_id="s", agent_id=agent, instruction="i", original_request="o")
    resp = AgentResponse(agent_id=agent, step_id="s", content=content, model="m",
                         proposed_actions=list(actions), metadata={"parse_warnings": list(warnings)})
    return qa.review(req, resp)


def test_clean_result_is_approved(config, filled_brand):
    report = _review(_qa(config, filled_brand))
    assert report.verdict == QAVerdict.APPROVED
    assert not report.approval_required and not report.denied_actions


def test_forbidden_brand_word_requires_revision(config, filled_brand):
    report = _review(_qa(config, filled_brand), content=GOOD + " Jetzt super BILLIG kaufen!")
    assert report.verdict == QAVerdict.NEEDS_REVISION
    assert any(i.check == "brand_no_go" for i in report.issues)


def test_forbidden_word_only_matches_whole_words(config, filled_brand):
    # "Garantiert" ist nicht "Garantie"
    assert _review(_qa(config, filled_brand), content=GOOD + " Garantiert gut.").verdict == QAVerdict.APPROVED


def test_secret_in_result_is_blocked(config, filled_brand):
    report = _review(_qa(config, filled_brand), content=GOOD + " sk-ant-api03-" + "x" * 40)
    assert report.verdict == QAVerdict.BLOCKED


def test_external_action_requires_user_approval(config, filled_brand):
    act = ProposedAction("publish_content", "Post auf Instagram", proposed_by="social")
    report = _review(_qa(config, filled_brand), actions=[act])
    assert report.verdict == QAVerdict.APPROVED  # Inhalt ok ...
    assert report.approval_required == [act]      # ... Aktion braucht Freigabe


def test_unauthorized_and_forbidden_actions_are_blocked(config, filled_brand):
    qa = _qa(config, filled_brand)
    spend = ProposedAction("spend_money", "Ads buchen", proposed_by="social")  # social darf das nicht
    report = _review(qa, actions=[spend])
    assert report.denied_actions == [spend] and report.verdict == QAVerdict.APPROVED
    leak = ProposedAction("reveal_secret", "Key zeigen", proposed_by="social")
    assert _review(qa, actions=[leak]).verdict == QAVerdict.BLOCKED


def test_llm_review_can_only_tighten(config, filled_brand):
    qa = _qa(config, filled_brand, ['{"verdict": "needs_revision", "issues": ["CTA fehlt"]}'])
    report = _review(qa)
    assert report.verdict == QAVerdict.NEEDS_REVISION
    assert any("CTA fehlt" in i.message for i in report.issues)

    qa = _qa(config, filled_brand, ['{"verdict": "approved", "issues": []}'])
    assert _review(qa, content=GOOD + " billig").verdict == QAVerdict.NEEDS_REVISION


def test_extract_actions_block():
    text = 'Entwurf\n```actions\n[{"action": "publish_content", "description": "Post", "params": {"p": 1}}]\n```'
    content, actions, warnings = extract_actions(text, "social")
    assert content == "Entwurf" and not warnings
    assert actions[0].action == "publish_content" and actions[0].proposed_by == "social"

    _, actions, warnings = extract_actions("x\n```actions\nkein json\n```", "social")
    assert not actions and warnings
