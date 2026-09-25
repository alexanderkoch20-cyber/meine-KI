"""Unveraenderliche Governance-Regeln (Owner-Regel).

Diese Regeln stehen bewusst im CODE und nicht in einer YAML-Datei: Keine
Konfiguration kann sie aufweichen. ``config.py`` bricht mit einem
``ConfigError`` ab, wenn ``permissions.yaml`` versucht, eine der hier
geschuetzten Aktionen auf ``allow`` zu setzen.

Owner-Regel in Kurzform:
- Kein Agent (auch nicht der Master) beginnt einen Auftrag ohne Owner-Freigabe.
- Agenten duerfen analysieren, planen und VORSCHLAGEN - nie selbst handeln.
- Bei Fehlern, Unklarheiten oder noetigen Entscheidungen: STOPPEN und melden.
"""

from __future__ import annotations

#: Aktionen mit externer oder unumkehrbarer Wirkung bzw. Eingriffe in das
#: System selbst. Sie brauchen IMMER eine Owner-Freigabe (Policy darf
#: ``require_approval`` oder ``deny`` sein, niemals ``allow``).
OWNER_APPROVAL_ACTIONS: frozenset[str] = frozenset({
    # extern / Geld / Kommunikation
    "spend_money",
    "publish_content",
    "send_customer_message",
    "sign_contract",
    "call_external_api",
    "activate_external_service",
    "connect_external_account",
    "use_api_key",
    "generate_video",
    # Loeschen / Zuruecksetzen
    "delete_account",
    "delete_file_permanently",
    "delete_data",
    "reset_files",
    "revert_commit",
    "reset_commits",
    # Eingriffe in die Workforce
    "create_agent",
    "enable_agent",
    "disable_agent",
    "change_config",
    "change_model",
    # Auftraege
    "start_new_task",
    "extend_task",
    "retry_task",
})

#: Aktionen, die fuer Agenten GRUNDSAETZLICH verboten sind - auch mit
#: Freigabe. Governance und Berechtigungen gehoeren allein dem Owner.
AGENT_FORBIDDEN_ACTIONS: frozenset[str] = frozenset({
    "reveal_secret",
    "modify_permissions",
    "modify_governance",
    "approve_task",
    "approve_action",
})

#: Vorschlaege, die JEDER Agent machen darf (Empfehlung statt Handlung).
#: ``request_owner_decision`` stoppt den laufenden Auftrag sofort.
ALWAYS_PROPOSABLE_ACTIONS: frozenset[str] = frozenset({
    "start_new_task",
    "extend_task",
    "retry_task",
    "request_owner_decision",
})

#: Signal eines Agenten: "Hier wird eine Entscheidung des Owners gebraucht."
STOP_FOR_DECISION_ACTION = "request_owner_decision"

#: Agenten, die immer freigegeben sein muessen, damit das System arbeiten kann.
REQUIRED_AGENTS: frozenset[str] = frozenset({"master", "qa"})
