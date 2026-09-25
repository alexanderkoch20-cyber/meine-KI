from __future__ import annotations

import json

import pytest

from agent_system.agents.base import AgentContext
from agent_system.agents.master import MasterAgent, topological_order
from agent_system.core.errors import PlanningError
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import Job, Plan, PlanStep


def _master(config, brand, plan_responses=None):
    llm = MockLLMClient(scripted={"master:plan": plan_responses or []})
    return MasterAgent(config.agents["master"], AgentContext(config, llm, brand)), llm


@pytest.mark.parametrize("request_text, expected", [
    ("Schreibe 5 Instagram-Posts mit Hashtags", ["social"]),
    ("Recherchiere unsere Wettbewerber", ["research"]),
    ("Erstelle ein Storyboard fuer ein Video", ["video"]),
    ("Behebe den Bug in unserem Python-Skript", ["coding"]),
    ("Formatiere diese Liste als Tabelle", ["routine"]),
    ("Entwickle ein Moodboard und Bildideen", ["creative"]),
    ("Recherchiere Trends und plane eine Kampagne mit Reels",
     ["research", "marketing", "social"]),
])
def test_rule_based_routing(config, empty_brand, request_text, expected):
    master, _ = _master(config, empty_brand)
    plan = master.plan(Job(request=request_text))
    assert plan.source == "rules"
    assert [s.agent_id for s in plan.steps] == expected


def test_rule_based_plan_dependencies_follow_foundation_agents(config, empty_brand):
    master, _ = _master(config, empty_brand)
    plan = master.rule_based_plan("Recherchiere den Markt, plane eine Kampagne und Instagram-Posts")
    research, marketing, social = plan.steps
    assert research.depends_on == []
    assert marketing.depends_on == [research.id]
    assert social.depends_on == [research.id, marketing.id]


def test_unroutable_request_goes_to_fallback_agent(config, empty_brand):
    master, _ = _master(config, empty_brand)
    plan = master.plan(Job(request="Hallo, wie geht's?"))
    assert [s.agent_id for s in plan.steps] == [config.orchestration.fallback_agent]


def test_valid_llm_plan_is_used(config, empty_brand):
    raw = json.dumps({"rationale": "erst Recherche", "steps": [
        {"key": "a", "agent": "research", "instruction": "Markt analysieren", "depends_on": []},
        {"key": "b", "agent": "creative", "instruction": "Visuals", "depends_on": ["a"]},
    ]})
    master, llm = _master(config, empty_brand, [f"Hier der Plan:\n```json\n{raw}\n```"])
    plan = master.plan(Job(request="egal"))
    assert plan.source == "llm"
    a, b = plan.steps
    assert (a.agent_id, b.agent_id) == ("research", "creative")
    assert b.depends_on == [a.id]
    call = llm.calls[0]
    assert call.tier.name == "opus" and "research (Research-Agent)" in call.system


@pytest.mark.parametrize("bad_plan", [
    {"steps": [{"key": "a", "agent": "hacker", "instruction": "x"}]},
    {"steps": [{"key": "a", "agent": "qa", "instruction": "x"}]},           # QA ist kein Spezialist
    {"steps": [{"key": "a", "agent": "social", "instruction": ""}]},
    {"steps": [{"key": "a", "agent": "social", "instruction": "x", "depends_on": ["zz"]}]},
    {"steps": [{"key": "a", "agent": "social", "instruction": "x", "depends_on": ["b"]},
               {"key": "b", "agent": "video", "instruction": "y", "depends_on": ["a"]}]},
    {"steps": [{"key": f"s{i}", "agent": "social", "instruction": "x"} for i in range(20)]},
    {"steps": []},
])
def test_invalid_llm_plans_fall_back_to_rules(config, empty_brand, bad_plan):
    master, _ = _master(config, empty_brand, [json.dumps(bad_plan)])
    plan = master.plan(Job(request="Schreibe einen Instagram-Post"))
    assert plan.source == "rules"
    assert [s.agent_id for s in plan.steps] == ["social"]


def test_topological_order_and_cycle_detection():
    a = PlanStep(agent_id="research", instruction="a")
    b = PlanStep(agent_id="marketing", instruction="b", depends_on=[a.id])
    c = PlanStep(agent_id="social", instruction="c", depends_on=[b.id])
    assert topological_order(Plan(steps=[c, b, a])) == [a, b, c]
    a.depends_on = [c.id]
    with pytest.raises(PlanningError):
        topological_order(Plan(steps=[a, b, c]))


def test_keyword_matching_rules():
    from agent_system.agents.master import keyword_matches
    assert keyword_matches("kampagne*", "drei kampagnen planen")
    assert keyword_matches("story", "eine story posten")
    assert not keyword_matches("story", "ein storyboard")
    assert not keyword_matches("ads", "downloads")
