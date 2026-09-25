"""Laden und Validieren der Konfiguration (agents/models/permissions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

PACKAGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PACKAGE_DIR / "config"
DEFAULT_PROMPTS_DIR = PACKAGE_DIR / "prompts"
DEFAULT_BRAND_FILE = PACKAGE_DIR / "brand" / "brand_knowledge.yaml"

VALID_ROLES = {"master", "specialist", "qa"}
VALID_POLICIES = {"allow", "require_approval", "deny"}


@dataclass(frozen=True)
class ModelTier:
    name: str
    model_id: str
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    role: str
    model_tier: str
    prompt_file: str
    responsibilities: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    allowed_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OrchestrationSettings:
    fallback_agent: str = "marketing"
    max_retries: int = 2
    max_revisions: int = 1
    max_steps: int = 8


@dataclass
class SystemConfig:
    provider: str
    tiers: dict[str, ModelTier]
    agents: dict[str, AgentDefinition]
    orchestration: OrchestrationSettings
    action_policies: dict[str, str]
    action_descriptions: dict[str, str]
    default_policy: str
    max_llm_calls_per_job: int
    prompts_dir: Path = DEFAULT_PROMPTS_DIR
    brand_file: Path = DEFAULT_BRAND_FILE
    extra: dict[str, Any] = field(default_factory=dict)

    # -- Helfer --------------------------------------------------------------

    def specialists(self) -> dict[str, AgentDefinition]:
        return {k: v for k, v in self.agents.items() if v.role == "specialist"}

    def tier_for(self, agent_id: str) -> ModelTier:
        return self.tiers[self.agents[agent_id].model_tier]

    def load_prompt(self, agent_id: str) -> str:
        agent = self.agents[agent_id]
        path = self.prompts_dir / agent.prompt_file
        common = self.prompts_dir / "_common.md"
        text = path.read_text(encoding="utf-8").strip()
        if common.exists():
            text += "\n\n" + common.read_text(encoding="utf-8").strip()
        return text


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Konfigurationsdatei fehlt: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Ungueltiges YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} muss ein YAML-Mapping enthalten")
    return data


def load_config(
    config_dir: Path | str | None = None,
    prompts_dir: Path | str | None = None,
    brand_file: Path | str | None = None,
) -> SystemConfig:
    config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    prompts_dir = Path(prompts_dir) if prompts_dir else DEFAULT_PROMPTS_DIR

    models_raw = _read_yaml(config_dir / "models.yaml")
    agents_raw = _read_yaml(config_dir / "agents.yaml")
    perms_raw = _read_yaml(config_dir / "permissions.yaml")

    # --- Modelle ---
    tiers: dict[str, ModelTier] = {}
    for name, t in (models_raw.get("tiers") or {}).items():
        try:
            tiers[name] = ModelTier(
                name=name,
                model_id=str(t["model_id"]),
                max_tokens=int(t.get("max_tokens", 2048)),
                temperature=float(t.get("temperature", 0.5)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Ungueltige Modell-Stufe '{name}': {exc}") from exc
    if not tiers:
        raise ConfigError("models.yaml: keine Modell-Stufen definiert")

    # --- Berechtigungen ---
    default_policy = perms_raw.get("default_policy", "require_approval")
    if default_policy not in VALID_POLICIES:
        raise ConfigError(f"permissions.yaml: ungueltige default_policy '{default_policy}'")
    policies: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for action, spec in (perms_raw.get("actions") or {}).items():
        policy = (spec or {}).get("policy")
        if policy not in VALID_POLICIES:
            raise ConfigError(f"permissions.yaml: Aktion '{action}' hat ungueltige Policy '{policy}'")
        policies[action] = policy
        descriptions[action] = (spec or {}).get("description", "")

    # --- Agenten ---
    agents: dict[str, AgentDefinition] = {}
    for agent_id, a in (agents_raw.get("agents") or {}).items():
        a = a or {}
        role = a.get("role")
        if role not in VALID_ROLES:
            raise ConfigError(f"Agent '{agent_id}': ungueltige Rolle '{role}'")
        tier = a.get("model_tier")
        if tier not in tiers:
            raise ConfigError(f"Agent '{agent_id}': unbekannte Modell-Stufe '{tier}'")
        prompt_file = a.get("prompt_file") or f"{agent_id}.md"
        if not (prompts_dir / prompt_file).exists():
            raise ConfigError(f"Agent '{agent_id}': Prompt-Datei fehlt: {prompt_file}")
        allowed = frozenset(a.get("allowed_actions") or [])
        unknown = allowed - set(policies)
        if unknown:
            raise ConfigError(f"Agent '{agent_id}': unbekannte Aktionen in allowed_actions: {sorted(unknown)}")
        agents[agent_id] = AgentDefinition(
            id=agent_id,
            name=a.get("name", agent_id),
            role=role,
            model_tier=tier,
            prompt_file=prompt_file,
            responsibilities=tuple(a.get("responsibilities") or ()),
            keywords=tuple(str(k).lower() for k in (a.get("keywords") or ())),
            allowed_actions=allowed,
        )

    roles = [a.role for a in agents.values()]
    if roles.count("master") != 1:
        raise ConfigError("Es muss genau einen Master-Agenten geben")
    if roles.count("qa") != 1:
        raise ConfigError("Es muss genau einen QA-Agenten geben")

    orch_raw = agents_raw.get("orchestration") or {}
    orchestration = OrchestrationSettings(
        fallback_agent=orch_raw.get("fallback_agent", "marketing"),
        max_retries=int(orch_raw.get("max_retries", 2)),
        max_revisions=int(orch_raw.get("max_revisions", 1)),
        max_steps=int(orch_raw.get("max_steps", 8)),
    )
    fb = agents.get(orchestration.fallback_agent)
    if fb is None or fb.role != "specialist":
        raise ConfigError(f"fallback_agent '{orchestration.fallback_agent}' ist kein Spezial-Agent")

    return SystemConfig(
        provider=str(models_raw.get("provider", "mock")),
        tiers=tiers,
        agents=agents,
        orchestration=orchestration,
        action_policies=policies,
        action_descriptions=descriptions,
        default_policy=default_policy,
        max_llm_calls_per_job=int((models_raw.get("budget") or {}).get("max_llm_calls_per_job", 40)),
        prompts_dir=prompts_dir,
        brand_file=Path(brand_file) if brand_file else DEFAULT_BRAND_FILE,
    )
