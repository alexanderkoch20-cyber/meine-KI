"""Tests der zentralen Brand Knowledge Base: Schema, Validierung, Versionierung,
Brand-Context-Loader, Brand-Check, Onboarding und Integration in die Workforce."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from agent_system.agents.base import AgentContext
from agent_system.agents.qa import QAAgent
from agent_system.core.brand import BrandKnowledge, read_brand_yaml
from agent_system.core.brand_onboarding import render_onboarding
from agent_system.core.brand_schema import NOT_APPLICABLE, NOT_PROVIDED, UNKNOWN, validate
from agent_system.core.brand_store import BrandContextLoader, BrandRepository
from agent_system.core.config import DEFAULT_BRAND_DIR
from agent_system.core.errors import BrandKnowledgeError, GovernanceViolationError, OwnerApprovalRequiredError
from agent_system.core.governance import Actor
from agent_system.core.llm import MockLLMClient
from agent_system.core.models import AgentRequest, AgentResponse, QAVerdict, Severity, TaskStatus
from agent_system.core.permissions import Decision, PermissionPolicy

from .conftest import make_filled_brand_data

OWNER = Actor("owner", "owner")

# Anforderung des Owners: Bereich -> Felder, die es mindestens geben muss.
REQUIRED_STRUCTURE = {
    "brand_identity": ["brand_name", "owner", "founders", "history", "mission", "vision", "values",
                       "long_term_goals"],
    "offer": ["products", "services", "usps", "current_offers", "planned_offers", "pricing_notes"],
    "target_audiences": ["primary_audiences", "secondary_audiences", "markets"],
    "positioning": ["market_position", "differentiation", "competitive_landscape", "competitors",
                    "desired_perception"],
    "brand_voice": ["tonality", "writing_style", "words_to_use", "words_to_avoid", "good_examples"],
    "visual_identity": ["colors", "fonts", "logo_rules", "imagery_style", "video_style", "design_rules"],
    "content": ["past_content", "preferred_formats", "platforms", "content_pillars", "campaigns"],
    "no_gos": ["statements", "topics", "designs", "marketing_methods", "actions"],
    "legal_compliance": ["known_requirements", "jurisdictions", "privacy_requirements", "licenses",
                         "trademarks_copyrights"],
    "strategic_goals": ["short_term", "mid_term", "long_term"],
}


@pytest.fixture
def repo_dir(tmp_path):
    """Frische Brand-Ablage ohne Versionen (Kopie von Schema + Arbeitskopie)."""
    d = tmp_path / "brand"
    d.mkdir()
    for name in ("schema.yaml", "brand_knowledge.yaml"):
        shutil.copy(DEFAULT_BRAND_DIR / name, d / name)
    return d


def _write_working(repo_dir, data):
    (repo_dir / "brand_knowledge.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


# ================================================================ Schema


def test_schema_has_all_ten_sections_and_requested_fields(brand_schema):
    assert [s.name for s in brand_schema.sections] == list(REQUIRED_STRUCTURE)
    for section, fields in REQUIRED_STRUCTURE.items():
        names = {f.name for f in brand_schema.section(section).fields}
        assert set(fields) <= names, section


def test_audiences_capture_needs_problems_desires_motives(brand_schema):
    item = {f.name for f in brand_schema.section("target_audiences").field("primary_audiences").item_fields}
    assert {"needs", "problems", "desires", "buying_motives", "markets"} <= item
    offer_item = {f.name for f in brand_schema.section("offer").field("products").item_fields}
    assert {"name", "description", "price", "usp", "status"} <= offer_item


def test_every_field_has_an_onboarding_question(brand_schema):
    for s in brand_schema.sections:
        for f in s.fields:
            assert f.question.endswith("?") or "?" in f.question, f"{s.name}.{f.name}"


# ================================================================ Nichts erfinden


def test_shipped_working_copy_contains_no_invented_information(brand_schema):
    data = read_brand_yaml(DEFAULT_BRAND_DIR / "brand_knowledge.yaml")
    assert validate(data, brand_schema).ok
    for s in brand_schema.sections:
        for f in s.fields:
            assert data[s.name][f.name] == NOT_PROVIDED, f"{s.name}.{f.name} ist vorausgefuellt"


def test_shipped_version_1_is_the_empty_template(brand_schema):
    repo = BrandRepository(DEFAULT_BRAND_DIR)
    assert repo.verify_index() == []
    current = repo.current()
    assert current.version >= 1
    v1 = repo.load_version(1)
    assert v1.info.committed_by == "system:bootstrap"
    assert v1.as_dict() == brand_schema.empty_template()


def test_empty_brand_context_invents_nothing(empty_brand):
    ctx = empty_brand.to_prompt_context("social")
    assert "noch keine Brand-Informationen" in ctx
    assert "NICHT ANGEGEBEN (nicht erfinden)" in ctx
    assert "Brandname" in ctx and "NICHT erfinden" in ctx


# ================================================================ Validierung


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(slogan="x"), "unbekannter Bereich"),
    (lambda d: d["brand_identity"].update(brandname="x"), "unbekanntes Feld"),
    (lambda d: d["brand_identity"].update(values="Ehrlichkeit"), "Liste erwartet"),
    (lambda d: d["brand_voice"].update(form_of_address="Ihr"), "nicht erlaubt"),
    (lambda d: d["target_audiences"].update(markets=["Deutschland"]), "Laendercode"),
    (lambda d: d["offer"].update(products=[{"name": "A", "farbe": "rot"}]), "unbekanntes Feld"),
    (lambda d: d["offer"].update(products=[{"description": "ohne Namen"}]), "Pflichtfeld des Eintrags"),
    (lambda d: d["brand_identity"].update(brand_name=NOT_APPLICABLE), "nicht erlaubt"),
    (lambda d: d["brand_identity"].update(mission="not_provided"), "Platzhalter bitte exakt"),
    (lambda d: d.update(meta={"schema_version": 1}), "schema_version"),
])
def test_validation_errors(brand_schema, mutate, message):
    data = brand_schema.empty_template()
    mutate(data)
    result = validate(data, brand_schema)
    assert not result.ok and any(message in e for e in result.errors), result.errors
    with pytest.raises(BrandKnowledgeError):
        BrandKnowledge.from_data(data, brand_schema)


def test_missing_fields_and_empty_lists_are_warnings_and_become_not_provided(brand_schema):
    data = brand_schema.empty_template()
    del data["brand_identity"]["history"]
    data["no_gos"]["topics"] = []
    result = validate(data, brand_schema)
    assert result.ok and len(result.warnings) == 2
    brand = BrandKnowledge.from_data(data, brand_schema)
    assert brand.status("brand_identity", "history") == NOT_PROVIDED
    assert brand.status("no_gos", "topics") == NOT_PROVIDED


def test_filled_brand_validates(brand_schema):
    assert validate(make_filled_brand_data(brand_schema), brand_schema).ok


# ================================================================ Brand-Check


def test_check_of_empty_brand_lists_all_required(empty_brand, brand_schema):
    report = empty_brand.check(["social", "routine"])
    assert report.required_completeness == 0 and not report.ok
    required = {(m.section, m.field) for m in report.missing_required}
    assert ("brand_identity", "brand_name") in required
    assert ("offer", "products|services") in required          # mindestens eins von beiden
    assert ("legal_compliance", "jurisdictions") in required
    assert "Brandname" in report.per_agent["routine"]
    assert "UNVOLLSTAENDIG" in report.to_text(brand_schema)


def test_check_of_filled_brand_finds_remaining_gaps(filled_brand):
    report = filled_brand.check(["creative", "routine", "legal"])
    missing = {m.field for m in report.missing_required}
    assert missing == {"colors", "platforms", "content_pillars"}
    assert "Farben" in report.per_agent["creative"]
    assert "routine" not in report.per_agent       # Routine-Agent braucht diese Bereiche nicht
    assert 0.8 < report.required_completeness < 1


def test_unknown_and_not_applicable(brand_schema):
    data = make_filled_brand_data(brand_schema)
    data["visual_identity"]["colors"] = UNKNOWN
    data["content"].update(platforms=NOT_APPLICABLE, content_pillars=["Wissen"])
    brand = BrandKnowledge.from_data(data, brand_schema)
    report = brand.check()
    assert [(m.field, m.status) for m in report.missing_required] == [("colors", UNKNOWN)]
    assert "Farben (Owner: unbekannt)" in brand.to_prompt_context("creative")
    assert "Social-Media-Plattformen: trifft nicht zu" in brand.to_prompt_context("social")


def test_required_any_offer(brand_schema):
    data = make_filled_brand_data(brand_schema)
    data["offer"].update(products=NOT_APPLICABLE, services=NOT_APPLICABLE)
    report = BrandKnowledge.from_data(data, brand_schema).check()
    assert any(m.field == "products|services" for m in report.missing_required)


def test_cross_checks_for_markets_and_jurisdictions(brand_schema, config):
    data = make_filled_brand_data(brand_schema)
    data["target_audiences"]["markets"] = ["DE", "BR"]
    report = BrandKnowledge.from_data(data, brand_schema).check(known_jurisdictions=config.legal.jurisdictions)
    assert any("'BR'" in w and "unbekannt" in w for w in report.warnings)
    assert any("ohne Eintrag unter legal_compliance.jurisdictions" in w for w in report.warnings)


# ================================================================ Versionierung


def test_versioning_lifecycle(repo_dir, brand_schema):
    repo = BrandRepository(repo_dir)
    assert repo.current().version == 0                     # nichts freigegeben -> leere Basis
    assert repo.bootstrap().version == 1
    with pytest.raises(BrandKnowledgeError):
        repo.bootstrap()

    _write_working(repo_dir, make_filled_brand_data(brand_schema))
    assert repo.has_uncommitted_changes()
    assert repo.current().version == 1 and repo.current().name == ""   # Arbeitskopie wirkt noch nicht

    info = repo.commit(OWNER, "owner", "Erste Angaben")
    assert info.version == 2 and not repo.has_uncommitted_changes()
    assert repo.current().name == "Testmarke"
    with pytest.raises(BrandKnowledgeError, match="Keine Aenderungen"):
        repo.commit(OWNER, "owner", "nochmal")

    assert [e["version"] for e in repo.history()] == [1, 2]
    assert repo.history()[1]["parent_hash"] == repo.history()[0]["content_hash"]
    assert any(c.startswith("brand_identity.brand_name: NOT_PROVIDED -> Testmarke") for c in repo.diff(1, 2))
    assert repo.load_version(1).name == ""                 # alte Versionen bleiben abrufbar


def test_only_owner_can_commit_and_needs_note(repo_dir):
    repo = BrandRepository(repo_dir)
    for actor in (Actor.agent("marketing"), Actor("orchestrator", "system"), Actor("mallory", "owner")):
        with pytest.raises(GovernanceViolationError):
            repo.commit(actor, "owner", "x")
    with pytest.raises(BrandKnowledgeError, match="Aenderungsnotiz"):
        repo.commit(OWNER, "owner", " ")


def test_invalid_working_copy_is_not_committed(repo_dir, brand_schema):
    repo = BrandRepository(repo_dir)
    data = make_filled_brand_data(brand_schema)
    data["brand_voice"]["form_of_address"] = "Ihr"
    _write_working(repo_dir, data)
    with pytest.raises(BrandKnowledgeError, match="ungueltig"):
        repo.commit(OWNER, "owner", "kaputt")
    assert repo.history() == []


def test_bootstrap_can_only_create_empty_template(repo_dir, brand_schema):
    _write_working(repo_dir, make_filled_brand_data(brand_schema))
    repo = BrandRepository(repo_dir)
    repo.bootstrap()
    assert repo.current().as_dict() == brand_schema.empty_template()  # Arbeitskopie ignoriert


def test_tampered_version_is_rejected(repo_dir, brand_schema):
    repo = BrandRepository(repo_dir)
    repo.bootstrap()
    _write_working(repo_dir, make_filled_brand_data(brand_schema))
    repo.commit(OWNER, "owner", "v2")
    path = repo_dir / "versions" / "v0002.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("Testmarke", "Faelschung"), encoding="utf-8")
    assert any("veraendert" in p for p in repo.verify_index())
    with pytest.raises(BrandKnowledgeError, match="nicht vertrauenswuerdig"):
        repo.current()


def test_broken_hash_chain_is_detected(repo_dir, brand_schema):
    repo = BrandRepository(repo_dir)
    repo.bootstrap()
    _write_working(repo_dir, make_filled_brand_data(brand_schema))
    repo.commit(OWNER, "owner", "v2")
    index_path = repo_dir / "versions" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["versions"][1]["parent_hash"] = "0" * 64
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert any("Hash-Kette" in p for p in repo.verify_index())


# ================================================================ Loader & Zugriff


def test_brand_is_immutable_for_agents(filled_brand):
    values = filled_brand.get("brand_identity", "values")
    values.append("Manipuliert")
    assert filled_brand.get("brand_identity", "values") == ["Ehrlichkeit"]
    data = filled_brand.as_dict()
    data["no_gos"]["statements"] = []
    assert filled_brand.forbidden_phrases == ["billig", "Garantie"]


def test_agents_get_only_their_relevant_sections(filled_brand):
    routine = filled_brand.to_prompt_context("routine")
    creative = filled_brand.to_prompt_context("creative")
    assert "Visuelle Identitaet" not in routine and "Brand Voice" in routine
    assert "Farben" in creative                                    # als NICHT ANGEGEBEN gelistet
    assert "Testmarke" in routine and "Testmarke" in creative      # Identity + No-Gos: alle
    assert "Brand Knowledge Base" in routine and filled_brand.content_hash[:12] in routine


def test_all_agents_of_a_job_share_one_brand_snapshot(make_orch, filled_brand):
    from agent_system.orchestrator import _Team
    orch = make_orch(brand=filled_brand)
    team = _Team(orch.config, MockLLMClient(), orch.brand)
    agents = [team.master, team.qa, team.legal, *team.specialists.values()]
    assert len({id(a.ctx.brand) for a in agents}) == 1


def test_orchestrator_uses_latest_committed_version(tmp_path, config, owner, brand_schema, repo_dir):
    from dataclasses import replace
    from agent_system.orchestrator import Orchestrator

    repo = BrandRepository(repo_dir)
    repo.bootstrap()
    llm = MockLLMClient()
    orch = Orchestrator(config=replace(config, brand_dir=repo_dir), llm=llm, data_dir=tmp_path / "d")

    job = orch.submit("Schreibe einen Instagram-Post", owner)
    assert job.brand_version == 1
    orch.approve(job.id, owner)

    # Owner aendert und gibt die Brand-Basis frei, NACH der Auftragsfreigabe:
    _write_working(repo_dir, make_filled_brand_data(brand_schema))
    orch.commit_brand(owner, "Brand ausgefuellt")
    with pytest.raises(OwnerApprovalRequiredError, match="Brand Knowledge Base"):
        orch.execute(job.id)
    assert job.status == TaskStatus.WAITING_FOR_OWNER              # erneute Freigabe noetig

    orch.approve(job.id, owner)
    orch.execute(job.id)
    assert job.status == TaskStatus.COMPLETED and job.brand_version == 2
    work = [c for c in llm.calls if c.purpose == "work"][0]
    assert "Testmarke" in work.prompt and "Brand Knowledge Base v2" in work.prompt
    assert orch.audit.entries(event="brand_version_committed")[0]["details"]["version"] == 2
    assert orch.audit.entries(job.id, "execution_started")[-1]["details"]["brand_version"] == 2


def test_working_copy_edits_are_not_used_before_commit(tmp_path, config, owner, brand_schema, repo_dir):
    from dataclasses import replace
    from agent_system.orchestrator import Orchestrator

    BrandRepository(repo_dir).bootstrap()
    _write_working(repo_dir, make_filled_brand_data(brand_schema))   # nicht freigegeben
    llm = MockLLMClient()
    orch = Orchestrator(config=replace(config, brand_dir=repo_dir), llm=llm, data_dir=tmp_path / "d")
    job = orch.submit("Schreibe einen Instagram-Post", owner)
    orch.approve(job.id, owner)
    orch.execute(job.id)
    assert all("Testmarke" not in c.prompt for c in llm.calls)


def test_agents_cannot_modify_brand(make_orch, config):
    assert PermissionPolicy(config).check("marketing", "modify_brand_knowledge").decision == Decision.DENY
    orch = make_orch()
    with pytest.raises(GovernanceViolationError):
        orch.commit_brand(Actor.agent("marketing"), "Ich aendere die Brand")
    assert orch.audit.entries(event="governance_violation")[0]["details"]["attempted"] == "commit_brand_knowledge"


# ================================================================ Nutzung durch QA/Legal


def test_qa_enforces_no_go_statements_and_warns_on_avoid_words(config, filled_brand):
    qa = QAAgent(config.agents["qa"], AgentContext(config, MockLLMClient(), filled_brand))

    def review(content):
        req = AgentRequest(job_id="j", step_id="s", agent_id="social", instruction="i", original_request="o")
        return qa.review(req, AgentResponse(agent_id="social", step_id="s", content=content, model="m"))

    base = "Ein ausfuehrlicher Entwurf fuer die Kampagne mit klarer Botschaft."
    assert review(base + " Jetzt billig!").verdict == QAVerdict.NEEDS_REVISION
    report = review(base + " Das ist krass gut.")
    assert report.verdict == QAVerdict.APPROVED
    assert any(i.check == "brand_voice" and i.severity == Severity.WARNING for i in report.issues)


def test_legal_mentions_brand_jurisdictions_but_does_not_assume_them(make_orch, owner, filled_brand):
    job = make_orch(brand=filled_brand).submit("Sende einen Newsletter an unsere Kundenliste", owner)
    assert job.jurisdictions == []                                  # nicht automatisch uebernommen
    assert any("Laut Brand Knowledge Base" in q and "DE" in q for q in job.open_questions)


# ================================================================ Onboarding


def test_onboarding_file_is_up_to_date_and_complete(brand_schema):
    text = (DEFAULT_BRAND_DIR / "ONBOARDING.md").read_text(encoding="utf-8")
    assert text == render_onboarding(brand_schema), "ONBOARDING.md neu erzeugen: brand onboarding --write"
    for s in brand_schema.sections:
        assert s.title in text
        for f in s.fields:
            assert f.question in text and f"`{s.name}.{f.name}`" in text
    assert "keine Kundendaten" in text and "NOT_PROVIDED" in text and "UNKNOWN" in text


def test_context_loader(repo_dir, filled_brand):
    with pytest.raises(BrandKnowledgeError):
        BrandContextLoader()
    assert BrandContextLoader(fixed=filled_brand).load() is filled_brand
    repo = BrandRepository(repo_dir)
    repo.bootstrap()
    loader = BrandContextLoader(repo)
    assert loader.load().version == 1 and loader.load().content_hash == repo.current().content_hash
