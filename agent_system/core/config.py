"""Laden und Validieren der Konfiguration (agents/models/permissions/governance).

Die geladene ``SystemConfig`` ist zur Laufzeit UNVERAENDERLICH (frozen
dataclass + schreibgeschuetzte Mappings): Kein Agent kann Berechtigungen,
Modelle oder Governance-Regeln im laufenden Betrieb aendern. Aenderungen
macht ausschliesslich der Owner in den YAML-Dateien.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .errors import ConfigError
from .rules import AGENT_FORBIDDEN_ACTIONS, OWNER_APPROVAL_ACTIONS, REQUIRED_AGENTS

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
    max_retries: int = 0
    max_revisions: int = 0
    max_steps: int = 8


@dataclass(frozen=True)
class GovernanceSettings:
    owner_id: str
    owner_name: str
    approved_agents: frozenset[str]


@dataclass(frozen=True)
class SystemConfig:
    provider: str
    tiers: Mapping[str, ModelTier]
    agents: Mapping[str, AgentDefinition]
    orchestration: OrchestrationSettings
    governance: GovernanceSettings
    action_policies: Mapping[str, str]
    action_descriptions: Mapping[str, str]
    default_policy: str
    max_llm_calls_per_job: int
    #: Agenten, die in agents.yaml stehen, aber (noch) nicht vom Owner freigegeben sind.
    inactive_agents: tuple[str, ...] = ()
    #: SHA-256 ueber alle Konfigurations- und Prompt-Dateien.
    fingerprint: str = ""
    prompts_dir: Path = DEFAULT_PROMPTS_DIR
    brand_file: Path = DEFAULT_BRAND_FILE
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

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


def compute_fingerprint(*dirs: Path) -> str:
    digest = hashlib.sha256()
    for d in dirs:
        for path in sorted(p for p in d.rglob("*") if p.is_file() and p.suffix in (".yaml", ".yml", ".md")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_policies(perms_raw: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, str]]:
    default_policy = perms_raw.get("default_policy", "require_approval")
    if default_policy not in VALID_POLICIES:
        raise ConfigError(f"permissions.yaml: ungueltige default_policy '{default_policy}'")
    if default_policy == "allow":
        raise ConfigError("permissions.yaml: default_policy 'allow' ist durch die Owner-Regel verboten")
    policies: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for action, spec in (perms_raw.get("actions") or {}).items():
        policy = (spec or {}).get("policy")
        if policy not in VALID_POLICIES:
            raise ConfigError(f"permissions.yaml: Aktion '{action}' hat ungueltige Policy '{policy}'")
        # --- harte Owner-Regeln (rules.py) ---
        if action in OWNER_APPROVAL_ACTIONS and policy == "allow":
            raise ConfigError(
                f"permissions.yaml: '{action}' braucht laut Owner-Regel immer eine Freigabe "
                "und darf nicht auf 'allow' stehen"
            )
        if action in AGENT_FORBIDDEN_ACTIONS and policy != "deny":
            raise ConfigError(f"permissions.yaml: '{action}' ist fuer Agenten immer verboten ('deny')")
        policies[action] = policy
        descriptions[action] = (spec or {}).get("description", "")
    # Geschuetzte Aktionen, die in der Datei fehlen, bekommen die sichere Policy.
    for action in OWNER_APPROVAL_ACTIONS:
        policies.setdefault(action, "require_approval")
    for action in AGENT_FORBIDDEN_ACTIONS:
        policies.setdefault(action, "deny")
    return default_policy, policies, descriptions


def _load_governance(raw: dict[str, Any]) -> GovernanceSettings:
    owner = raw.get("owner") or {}
    owner_id = str(owner.get("id") or "").strip()
    if not owner_id:
        raise ConfigError("governance.yaml: owner.id fehlt")
    approved = frozenset(str(a) for a in (raw.get("approved_agents") or []))
    missing = REQUIRED_AGENTS - approved
    if missing:
        raise ConfigError(f"governance.yaml: Pflicht-Agenten nicht freigegeben: {sorted(missing)}")
    return GovernanceSettings(owner_id=owner_id, owner_name=str(owner.get("name") or owner_id),
                              approved_agents=approved)


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
    governance = _load_governance(_read_yaml(config_dir / "governance.yaml"))

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

    default_policy, policies, descriptions = _load_policies(perms_raw)

    # --- Agenten ---
    agents: dict[str, AgentDefinition] = {}
    inactive: list[str] = []
    for agent_id, a in (agents_raw.get("agents") or {}).items():
        a = a or {}
        if agent_id == governance.owner_id:
            raise ConfigError(f"Agent-ID '{agent_id}' kollidiert mit der Owner-ID")
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
        if agent_id not in governance.approved_agents:
            inactive.append(agent_id)  # neuer Agent ohne Owner-Freigabe -> inaktiv
            continue
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

    unknown_approved = governance.approved_agents - set(agents) - set(inactive)
    if unknown_approved:
        raise ConfigError(f"governance.yaml: freigegebene Agenten ohne Definition: {sorted(unknown_approved)}")

    roles = [a.role for a in agents.values()]
    if roles.count("master") != 1:
        raise ConfigError("Es muss genau einen Master-Agenten geben")
    if roles.count("qa") != 1:
        raise ConfigError("Es muss genau einen QA-Agenten geben")

    orch_raw = agents_raw.get("orchestration") or {}
    orchestration = OrchestrationSettings(
        max_retries=int(orch_raw.get("max_retries", 0)),
        max_revisions=int(orch_raw.get("max_revisions", 0)),
        max_steps=int(orch_raw.get("max_steps", 8)),
    )

    return SystemConfig(
        provider=str(models_raw.get("provider", "mock")),
        tiers=MappingProxyType(tiers),
        agents=MappingProxyType(agents),
        orchestration=orchestration,
        governance=governance,
        action_policies=MappingProxyType(policies),
        action_descriptions=MappingProxyType(descriptions),
        default_policy=default_policy,
        max_llm_calls_per_job=int((models_raw.get("budget") or {}).get("max_llm_calls_per_job", 40)),
        inactive_agents=tuple(inactive),
        fingerprint=compute_fingerprint(config_dir, prompts_dir),
        prompts_dir=prompts_dir,
        brand_file=Path(brand_file) if brand_file else DEFAULT_BRAND_FILE,
    )
