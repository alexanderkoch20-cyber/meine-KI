"""Fehlerhierarchie des Agentensystems.

Jede Fehlerklasse hat einen stabilen ``code``, damit Fehler im Job-Log,
in Tests und spaeter in einer API eindeutig maschinenlesbar sind.
"""

from __future__ import annotations


class AgentSystemError(Exception):
    """Basisklasse aller Fehler dieses Pakets."""

    code = "agent_system_error"
    #: Darf der Orchestrator den Schritt erneut versuchen?
    retryable = False


class ConfigError(AgentSystemError):
    code = "config_error"


class BrandKnowledgeError(AgentSystemError):
    code = "brand_knowledge_error"


class UnknownAgentError(AgentSystemError):
    code = "unknown_agent"


class PlanningError(AgentSystemError):
    code = "planning_error"


class AgentExecutionError(AgentSystemError):
    """Ein Spezial-Agent konnte seine Teilaufgabe nicht erfuellen."""

    code = "agent_execution_error"
    retryable = True


class LLMError(AgentSystemError):
    """Fehler beim Aufruf eines Sprachmodells (Timeout, Rate-Limit, ...)."""

    code = "llm_error"
    retryable = True


class ExternalServiceNotApprovedError(AgentSystemError):
    """Ein externer/kostenpflichtiger Dienst wurde angesprochen, ohne dass
    der Nutzer ihn freigegeben hat. Wird bewusst NICHT wiederholt."""

    code = "external_service_not_approved"


class PermissionDeniedError(AgentSystemError):
    code = "permission_denied"


class ApprovalRequiredError(AgentSystemError):
    code = "approval_required"


class InvalidStateTransitionError(AgentSystemError):
    code = "invalid_state_transition"


class JobNotFoundError(AgentSystemError):
    code = "job_not_found"


class BudgetExceededError(AgentSystemError):
    """Das LLM-Aufruf-Budget eines Jobs ist aufgebraucht (nicht wiederholbar)."""

    code = "budget_exceeded"
